"""Local real-time macOS System Dashboard.

Telemetry is pushed over WebSocket once a second. Native `networkQuality`
and `softwareupdate` checks run in non-blocking subprocesses.

    python3.13 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    python app.py

Then open http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import plistlib
import platform
import re
import signal
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info < (3, 13):
    raise SystemExit(
        f"macOS Sentinel requires Python 3.13 or newer (found {platform.python_version()}).\n"
        "macOS often ships with an older python3. Install 3.13, then recreate the venv:\n"
        "  brew install python@3.13\n"
        "  python3.13 -m venv .venv\n"
        "  source .venv/bin/activate\n"
        "  python -m pip install --upgrade pip\n"
        "  python -m pip install -r requirements.txt\n"
        "See README.md (Install troubleshooting) for pip warnings and unmet requirements."
    )
import psutil
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dashboard.security import LocalSession
from dashboard.runtime import TelemetryHub
from dashboard.sessions import SessionStore
from dashboard.telemetry import TelemetrySampler

from dashboard.common import CommandFailed
from dashboard.installed_apps import collect_installed_applications, resolve_application_path
from dashboard.collectors import (
    finite,
    parse_softwareupdate,
    parse_network_quality,
    collect_apfs,
    collect_system_info,
    render_product_icon_png,
    NO_UPDATE_RE,
)

HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("SENTINEL_PORT", "8000"))
INDEX_PATH = ROOT / "index.html"
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
NETWORK_QUALITY_TIMEOUT = 120
SOFTWARE_UPDATE_TIMEOUT = 180

log = logging.getLogger("dashboard")

class SingleFlight:
    """Share one in-flight coroutine across concurrent callers."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def do(self, factory):
        async with self._lock:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(factory())
            task = self._task
        return await asyncio.shield(task)


command_slots = asyncio.Semaphore(4)

async def run_command(args: list[str], timeout: float) -> tuple[int, str, str]:
    async with command_slots:
        return await _run_command(args, timeout)

async def _run_command(args: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise CommandFailed(f"{args[0]} is not available on this Mac") from exc
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise CommandFailed(f"{args[0]} timed out after {int(timeout)} seconds", 504) from None
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    return proc.returncode or 0, stdout, stderr


def command_excerpt(stdout: str, stderr: str) -> str:
    combined = "\n".join(part.strip() for part in (stderr, stdout) if part and part.strip())
    collapsed = " ".join(combined.split())
    return collapsed[:400]


async def execute_network_quality() -> dict:
    started = time.perf_counter()
    log.info("Starting networkQuality -c")
    code, stdout, stderr = await run_command(["networkQuality", "-c"], NETWORK_QUALITY_TIMEOUT)
    try:
        parsed = parse_network_quality(stdout or stderr)
    except CommandFailed:
        if code != 0:
            detail = command_excerpt(stdout, stderr) or f"networkQuality exited {code}"
            raise CommandFailed(detail) from None
        raise
    if code != 0 and parsed.get("downlink_mbps") is None and parsed.get("uplink_mbps") is None:
        detail = command_excerpt(stdout, stderr) or f"networkQuality exited {code}"
        raise CommandFailed(detail)
    parsed["duration_s"] = round(time.perf_counter() - started, 1)
    parsed["checked_at"] = datetime.now(timezone.utc).isoformat()
    return parsed


async def execute_software_updates() -> dict:
    started = time.perf_counter()
    log.info("Starting softwareupdate -l")
    code, stdout, stderr = await run_command(["softwareupdate", "-l"], SOFTWARE_UPDATE_TIMEOUT)
    combined = "\n".join(part for part in (stdout, stderr) if part)
    parsed = parse_softwareupdate(combined)
    if code != 0 and not parsed["updates"] and not NO_UPDATE_RE.search(combined):
        detail = command_excerpt(stdout, stderr) or f"softwareupdate exited {code}"
        raise CommandFailed(detail)
    parsed["duration_s"] = round(time.perf_counter() - started, 1)
    parsed["checked_at"] = datetime.now(timezone.utc).isoformat()
    parsed["exit_code"] = code
    return parsed


sampler = None
hub = TelemetryHub()
sessions = SessionStore(os.environ.get("SENTINEL_DATA_DIR", str(Path(__file__).resolve().parent / ".sentinel-data")))
session_save_error = None
network_flight = SingleFlight()
software_flight = SingleFlight()
apfs_flight = SingleFlight()
log_clients = 0
SESSION_TOKEN = secrets.token_urlsafe(32)

async def broadcast_loop(executor):
    global sampler
    loop = asyncio.get_running_loop()
    sampler = await loop.run_in_executor(executor, TelemetrySampler)
    await loop.run_in_executor(executor, sampler.prime)
    await asyncio.sleep(hub.interval)
    while True:
        started = time.monotonic()
        try:
            payload = await loop.run_in_executor(executor, sampler.snapshot)
            payload["sample_interval"] = hub.effective_interval
            payload["requested_interval"] = hub.interval
            payload["adaptive_sampling"] = hub.adaptive
            hub.publish(payload)
            hub.next_interval(payload)
        except Exception:
            log.exception("telemetry snapshot failed")
        await asyncio.sleep(max(0.05, hub.effective_interval-(time.monotonic()-started)))

async def save_automatic_session():
    global session_save_error
    if sessions.enabled and hub.history:
        try:
            snapshot = hub.snapshot_session()
            await asyncio.to_thread(sessions.save, snapshot, 'Automatic session', sessions.include_apps, True)
            session_save_error = None
        except (OSError, ValueError) as exc:
            session_save_error = str(exc)[:200]
            log.warning('Automatic session save failed: %s', exc)

async def session_save_loop():
    while True:
        await asyncio.sleep(60)
        await save_automatic_session()

@asynccontextmanager
async def lifespan(app: FastAPI):
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telemetry")
    task = asyncio.create_task(broadcast_loop(executor))
    save_task = asyncio.create_task(session_save_loop())
    try:
        yield
    finally:
        task.cancel()
        save_task.cancel()
        await asyncio.gather(task, save_task, return_exceptions=True)
        await save_automatic_session()
        await scans.close()
        for flight in (network_flight, software_flight):
            if flight._task and not flight._task.done():
                flight._task.cancel()
                await asyncio.gather(flight._task, return_exceptions=True)
        if sampler is not None and hasattr(sampler, "close"):
            sampler.close()
        executor.shutdown(wait=False, cancel_futures=True)

app = FastAPI(title="macOS Sentinel", version=APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(LocalSession, token=SESSION_TOKEN, port=PORT)
app.mount("/static", StaticFiles(directory=INDEX_PATH.parent / "static"), name="static")

@app.get("/")
async def index() -> HTMLResponse:
    html = INDEX_PATH.read_text(encoding="utf-8")
    html = html.replace("__SESSION_TOKEN__", SESSION_TOKEN).replace("__APP_VERSION__", APP_VERSION)
    return HTMLResponse(html)


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


class ProcessIdentity(BaseModel):
    create_time: float = Field(gt=0, allow_inf_nan=False)

@app.post("/api/process/kill/{pid}")
async def kill_process(pid: int, identity: ProcessIdentity) -> dict:
    if pid <= 1 or pid == os.getpid():
        raise HTTPException(status_code=400, detail="Refusing to signal that process")
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        if proc.create_time() != identity.create_time or not proc.is_running():
            raise HTTPException(status_code=409, detail="Process identity changed. Refresh and select it again.")
    except psutil.NoSuchProcess:
        raise HTTPException(status_code=404, detail=f"No process with pid {pid}") from None
    except psutil.AccessDenied:
        raise HTTPException(status_code=403, detail=f"Permission denied reading pid {pid}") from None
    try:
        proc.terminate()
    except (ProcessLookupError, psutil.NoSuchProcess):
        raise HTTPException(status_code=404, detail=f"No process with pid {pid}") from None
    except (PermissionError, psutil.AccessDenied):
        raise HTTPException(status_code=403, detail=f"Permission denied sending SIGTERM to {name} ({pid})") from None
    return {"ok": True, "pid": pid, "name": name, "signal": "SIGTERM"}


@app.get("/api/network-quality")
async def network_quality() -> dict:
    try:
        return await network_flight.do(execute_network_quality)
    except CommandFailed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@app.get("/api/software-updates")
async def software_updates() -> dict:
    try:
        return await software_flight.do(execute_software_updates)
    except CommandFailed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


# --- Logs tab: /ws/logs -------------------------------------------------------
# One `/usr/bin/log stream --style ndjson` process per browser socket. The process group
# is terminated when the socket closes so Console streams do not leak.


def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None or not proc.pid:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        proc.terminate()


@app.websocket("/ws/logs")
async def logs_websocket(ws: WebSocket) -> None:
    global log_clients
    if log_clients >= 4:
        await ws.close(code=1013)
        return
    log_clients += 1
    await ws.accept(subprotocol="sentinel")
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/log",
            "stream",
            "--style",
            "ndjson",
            "--level",
            "debug",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        log_clients -= 1
        await ws.send_text(json.dumps({"error": "log is not available on this Mac"}))
        await ws.close()
        return

    async def pump() -> None:
        assert proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    err = b""
                    # stderr is continuously drained to avoid pipe deadlocks.
                    message = err.decode("utf-8", errors="replace").strip() or "log stream ended"
                    await ws.send_text(json.dumps({"error": message[:400]}))
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text.startswith("{"):
                    continue
                try:
                    json.loads(text)
                except json.JSONDecodeError:
                    continue
                await ws.send_text(text)
        except Exception:
            return

    async def receive():
        while True:
            await ws.receive_text()
    async def drain_stderr():
        if proc.stderr:
            while await proc.stderr.read(4096):
                pass
    pump_task = asyncio.create_task(pump())
    receive_task = asyncio.create_task(receive())
    stderr_task = asyncio.create_task(drain_stderr())
    try:
        await asyncio.wait([pump_task, receive_task], return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("logs websocket closed", exc_info=True)
    finally:
        log_clients -= 1
        for task in (pump_task, receive_task, stderr_task):
            task.cancel()
        await asyncio.gather(pump_task, receive_task, stderr_task, return_exceptions=True)
        try:
            await ws.close()
        except Exception:
            pass
        _terminate_process_group(proc)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=2)
        except TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                proc.kill()
            await proc.communicate()
        except Exception:
            pass


# --- Storage tab --------------------------------------------------------------

@app.get("/api/storage/apfs")
async def storage_apfs() -> dict:
    try:
        return await apfs_flight.do(lambda: asyncio.to_thread(collect_apfs))
    except CommandFailed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@app.get("/api/storage/directories")
async def storage_directories() -> dict:
    # Compatibility endpoint now returns a progressive scan snapshot.
    return await scans.start()


# --- System Info tab ----------------------------------------------------------

SYSTEM_INFO_TTL = 300
_system_info_cache: dict = {"expires": 0.0, "payload": None}
_system_info_lock = asyncio.Lock()


async def get_system_info(force=False) -> dict:
    async with _system_info_lock:
        now = time.monotonic()
        cached = _system_info_cache.get("payload")
        if not force and cached is not None and now < float(_system_info_cache.get("expires") or 0):
            payload = dict(cached)
            payload["cached"] = True
            return payload
        payload = await asyncio.to_thread(collect_system_info)
        payload["cached"] = False
        _system_info_cache["payload"] = payload
        _system_info_cache["expires"] = time.monotonic() + SYSTEM_INFO_TTL
        return payload


@app.get("/api/system-info")
async def system_info(force: bool = False) -> dict:
    try:
        return await get_system_info(force)
    except CommandFailed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


# --- Applications tab (/Applications and ~/Applications only) -----------------

APPLICATIONS_TTL = 300
_applications_cache: dict = {"expires": 0.0, "payload": None}
_applications_lock = asyncio.Lock()


async def get_installed_applications(force: bool = False) -> dict:
    async with _applications_lock:
        now = time.monotonic()
        cached = _applications_cache.get("payload")
        if not force and cached is not None and now < float(_applications_cache.get("expires") or 0):
            payload = dict(cached)
            payload["cached"] = True
            return payload
        payload = await asyncio.to_thread(collect_installed_applications)
        payload["cached"] = False
        _applications_cache["payload"] = payload
        _applications_cache["expires"] = time.monotonic() + APPLICATIONS_TTL
        return payload


@app.get("/api/applications")
async def installed_applications(force: bool = False) -> dict:
    try:
        return await get_installed_applications(force)
    except CommandFailed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


class ApplicationRevealRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


@app.post("/api/applications/reveal")
async def reveal_application(body: ApplicationRevealRequest) -> dict:
    try:
        target = str(resolve_application_path(body.path))
    except CommandFailed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    code, _, error = await run_command(["/usr/bin/open", "-R", target], timeout=5)
    if code:
        raise HTTPException(502, error[:180] or "Finder could not reveal this application")
    return {"ok": True}


_product_image_cache: dict[str, bytes | None] = {"key": "", "png": None}


@app.get("/api/host/product-image")
async def host_product_image() -> Response:
    """Serve a PNG derived from the local CoreTypes product icon for this Mac."""
    icon_name = None
    if sampler is not None:
        icon_name = (getattr(sampler, "host", {}) or {}).get("product_icon")
    cache_key = icon_name or ""
    if _product_image_cache["key"] != cache_key:
        png = await asyncio.to_thread(render_product_icon_png, icon_name, 512)
        _product_image_cache["key"] = cache_key
        _product_image_cache["png"] = png
    png = _product_image_cache["png"]
    if not png:
        raise HTTPException(status_code=404, detail="Product image unavailable")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# --- Process inspector --------------------------------------------------------


@app.get("/api/process/{pid}/details")
async def process_details(pid: int) -> dict:
    if pid <= 0:
        raise HTTPException(status_code=400, detail="Invalid pid")
    try:
        proc = psutil.Process(pid)
        def safe(call, default=None):
            try:
                return call()
            except psutil.Error:
                return default

        with proc.oneshot():
            meta = {
                "pid": pid,
                "ppid": safe(proc.ppid, 0),
                "name": safe(proc.name, "unknown"),
                "status": safe(proc.status, "unknown"),
                "username": safe(proc.username, None),
                "cmdline": safe(proc.cmdline, []),
                "threads": safe(proc.num_threads, None),
                "memory_percent": finite(safe(proc.memory_percent, 0), 1),
                "create_time": None,
            }
            created = safe(proc.create_time)
            if created:
                meta["create_time"] = datetime.fromtimestamp(created, timezone.utc).isoformat()
    except psutil.NoSuchProcess:
        raise HTTPException(status_code=404, detail=f"No process with pid {pid}") from None
    except psutil.AccessDenied:
        raise HTTPException(status_code=403, detail=f"Permission denied reading pid {pid}") from None
    try:
        code, stdout, stderr = await run_command(["lsof", "-n", "-P", "-p", str(pid)], timeout=12)
    except CommandFailed as exc:
        return {
            "ok": True,
            "process": meta,
            "files": [],
            "sockets": [],
            "files_truncated": False,
            "sockets_truncated": False,
            "lsof_exit": None,
            "lsof_error": exc.message,
        }
    files: list[str] = []
    sockets: list[str] = []
    for line in stdout.splitlines()[1:]:
        if not line.strip():
            continue
        parts = line.split()
        kind = parts[4].upper() if len(parts) > 4 else ""
        upper = f" {line.upper()} "
        if kind in {"IPV4", "IPV6", "UNIX"} or " TCP " in upper or " UDP " in upper:
            sockets.append(line)
        else:
            files.append(line)
    return {
        "ok": True,
        "process": meta,
        "files": files[:250],
        "sockets": sockets[:120],
        "files_truncated": len(files) > 250,
        "sockets_truncated": len(sockets) > 120,
        "lsof_exit": code,
        "lsof_error": None if code == 0 else command_excerpt("", stderr),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if len(hub.queues) >= 32:
        await ws.close(code=1013)
        return
    await ws.accept(subprotocol="sentinel")
    queue = hub.subscribe()
    async def sender():
        while True:
            payload = await queue.get()
            await asyncio.wait_for(ws.send_json(payload), timeout=3)
    async def receiver():
        while True:
            await ws.receive_text()
    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        hub.queues.discard(queue)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close()
        except Exception:
            pass


from dashboard.storage import ScanManager
scans = ScanManager()

class ScanRequest(BaseModel):
    path: str | None = Field(default=None, max_length=4096)
    force: bool = False

class Settings(BaseModel):
    adaptive: bool | None = None
    interval: float = Field(ge=1, le=10, allow_inf_nan=False)

@app.post("/api/settings")
async def settings(body: Settings):
    hub.interval = body.interval
    hub.effective_interval = body.interval
    hub.idle_since = None
    if body.adaptive is not None:
        hub.adaptive = body.adaptive
    return {"interval": hub.interval, "adaptive":hub.adaptive}

class AlertRule(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: bool
    threshold: float = Field(ge=1, le=100, allow_inf_nan=False)
    duration_seconds: int = Field(ge=5, le=300, strict=True)

class AlertSettings(BaseModel):
    model_config = {'extra': 'forbid'}
    cpu: AlertRule
    gpu: AlertRule
    memory: AlertRule

@app.get("/api/alerts")
async def get_alerts():
    return {'rules': hub.alert_settings()}

@app.post("/api/alerts")
async def set_alerts(body: AlertSettings):
    hub.configure_alerts({key: getattr(body, field).model_dump() for key, field in
                          [('CPU', 'cpu'), ('GPU', 'gpu'), ('Memory usage', 'memory')]})
    return {'rules': hub.alert_settings()}

@app.get("/api/history")
async def history(seconds: int = 300):
    return {"samples": hub.series(max(30,min(3600,seconds))), "events": list(hub.events), "interval": hub.interval}

class SaveSession(BaseModel):
    name: str = Field(default='Saved session', min_length=1, max_length=80)
    include_apps: bool = False

class PersistenceSettings(BaseModel):
    enabled: bool
    include_apps: bool = False

@app.get('/api/sessions')
async def list_sessions():
    return {'sessions':await asyncio.to_thread(sessions.list), 'enabled':sessions.enabled,
            'include_apps':sessions.include_apps, 'error':session_save_error}

@app.post('/api/sessions/settings')
async def persistence_settings(body: PersistenceSettings):
    await asyncio.to_thread(sessions.configure, body.enabled, body.include_apps)
    return {'enabled':sessions.enabled, 'include_apps':sessions.include_apps}

@app.post('/api/sessions')
async def save_session(body: SaveSession):
    if not hub.history:
        raise HTTPException(409, 'Waiting for telemetry before saving')
    try:
        return await asyncio.to_thread(sessions.save, hub.snapshot_session(), body.name, body.include_apps)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@app.get('/api/sessions/{identity}')
async def read_session(identity: str):
    try:
        return await asyncio.to_thread(sessions.read, identity)
    except (OSError, ValueError):
        raise HTTPException(404, 'Saved session not found or unreadable')

@app.delete('/api/sessions/{identity}')
async def delete_session(identity: str):
    try:
        await asyncio.to_thread(sessions.delete, identity)
    except (OSError, ValueError):
        raise HTTPException(404, 'Saved session not found')
    return {'ok':True}

@app.get('/api/investigate')
async def investigate(at: float):
    if not math.isfinite(at):
        raise HTTPException(422, 'Invalid timestamp')
    rows = list(hub.app_history)
    if not rows:
        raise HTTPException(404, 'No application snapshots available')
    closest = min(rows, key=lambda p:abs(datetime.fromisoformat(p['timestamp']).timestamp()-at))
    distance = abs(datetime.fromisoformat(closest['timestamp']).timestamp()-at)
    if distance > max(3, closest['interval']*1.5):
        raise HTTPException(404, 'No application snapshot near that time (gap or expired history)')
    return closest

@app.get("/api/export")
async def diagnostic_export():
    # Allowlist only aggregate measurements. No host, PID, names, paths, IPs or logs.
    return {"schema": 1, "redacted": True, "exported_at": datetime.now(timezone.utc).isoformat(),
            "samples": list(hub.history), "events": list(hub.events), "interval": hub.interval,
            "limitations": ["Memory usage is not OS pressure", "Network totals include loopback and tunnels", "No identifying inventories or logs included"]}

@app.post("/api/storage/scans")
async def start_scan(body: ScanRequest):
    return await scans.start(body.path, body.force)

class RevealRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)

@app.post("/api/storage/reveal")
async def reveal_storage(body: RevealRequest):
    target = scans.allowed(body.path)
    code, _, error = await run_command(['/usr/bin/open', '-R', target], timeout=5)
    if code:
        raise HTTPException(502, error[:180] or 'Finder could not reveal this directory')
    return {'ok': True}

@app.get("/api/storage/scans")
async def scan_status():
    return scans.snapshot()

@app.delete("/api/storage/scans")
async def cancel_scan():
    return await scans.cancel()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Open http://%s:%s", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
