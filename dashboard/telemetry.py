from __future__ import annotations
import threading
import time
import logging
from datetime import datetime, timezone
import psutil
from .gpu import collect_gpu
from .background import BackgroundReading
from .collectors import collect_host_info, collect_memory, collect_disk, collect_battery, classify_cores, finite, application_name
log = logging.getLogger("dashboard")

class TelemetrySampler:
    """Keeps psutil baselines so per-second deltas and CPU% are meaningful."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: dict[int, psutil.Process] = {}
        self._prev_net = psutil.net_io_counters()
        self._prev_ts = time.monotonic()
        self._primed = False
        self._disk_reading = BackgroundReading(lambda: collect_disk(self._log_once),
            {'status':'unavailable','total':None,'used':None,'free':None,'available':None,'percent':None})
        self._battery_reading = BackgroundReading(lambda: collect_battery(self._log_once),
            {'collection_status':'unavailable','present':False})
        self._self = psutil.Process()
        self._self_cpu = None
        self._self_at = None
        self._prev_interfaces = psutil.net_io_counters(pernic=True)
        self._prev_io = psutil.disk_io_counters()
        self.host = collect_host_info()
        self._logged: set[str] = set()

    def prime(self) -> None:
        with self._lock:
            psutil.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None, percpu=True)
            self._prev_interfaces = psutil.net_io_counters(pernic=True)
            self._prev_io = psutil.disk_io_counters()
            self._prev_net = psutil.net_io_counters()
            self._prev_ts = time.monotonic()
            self._refresh_processes(prime_only=True)
            self._primed = True

    def snapshot(self) -> dict:
        with self._lock:
            if not self._primed:
                self.prime_unlocked()
            started = time.monotonic()
            now = started
            elapsed = max(now - self._prev_ts, 1e-6)
            net = psutil.net_io_counters()
            down = max(0, net.bytes_recv - self._prev_net.bytes_recv) / elapsed
            up = max(0, net.bytes_sent - self._prev_net.bytes_sent) / elapsed
            self._prev_net = net
            self._prev_ts = now

            total_cpu = finite(psutil.cpu_percent(interval=None), 1)
            per_core_raw = psutil.cpu_percent(interval=None, percpu=True) or []
            per_core = [finite(value, 1) for value in per_core_raw]
            topology = self.host["topology"]
            core_types = classify_cores(
                len(per_core),
                topology["performance_cores"],
                topology["efficiency_cores"],
            )

            timings = {}
            stage = time.monotonic()
            memory = collect_memory(self._log_once)
            timings['memory_ms'] = round((time.monotonic()-stage)*1000, 1)
            disk = self._disk_reading.read()
            battery = self._battery_reading.read()
            interfaces = psutil.net_io_counters(pernic=True)
            interface_rates = []
            for name, counter in interfaces.items():
                previous = self._prev_interfaces.get(name)
                interface_rates.append({"name": name, "download_bps": max(0,counter.bytes_recv-previous.bytes_recv)/elapsed if previous else None,
                                        "upload_bps": max(0,counter.bytes_sent-previous.bytes_sent)/elapsed if previous else None})
            self._prev_interfaces = interfaces
            io = psutil.disk_io_counters()
            disk_io = {"status": "success" if io and self._prev_io else "unavailable",
                       "read_bps": max(0,io.read_bytes-self._prev_io.read_bytes)/elapsed if io and self._prev_io else None,
                       "write_bps": max(0,io.write_bytes-self._prev_io.write_bytes)/elapsed if io and self._prev_io else None}
            self._prev_io = io
            stage = time.monotonic()
            processes, process_tree = self._refresh_processes(prime_only=False)
            timings['processes_ms'] = round((time.monotonic()-stage)*1000, 1)
            stage = time.monotonic()
            gpu = collect_gpu()
            timings['gpu_ms'] = round((time.monotonic()-stage)*1000, 1)

            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "host": {
                    "hostname": self.host["hostname"],
                    "model": self.host["model"],
                    "os": self.host["os"],
                    "cpu_count": len(per_core) or self.host["cpu_count"],
                    "uptime_seconds": int(max(0, time.time() - psutil.boot_time())),
                    "machine_name": self.host.get("machine_name"),
                    "marketing_year": self.host.get("marketing_year"),
                    "model_identifier": self.host.get("model_identifier"),
                    "model_number": self.host.get("model_number"),
                    "chip": self.host.get("chip") or self.host["model"],
                    "memory": self.host.get("memory"),
                    "startup_disk": self.host.get("startup_disk"),
                    "serial_number": self.host.get("serial_number"),
                    "os_product": self.host.get("os_product"),
                    "os_version": self.host.get("os_version"),
                    "os_build": self.host.get("os_build"),
                    "architecture": self.host.get("architecture"),
                    "product_image_available": bool(self.host.get("product_image_available")),
                },
                "topology": topology,
                "cpu": {
                    "percent": total_cpu,
                    "per_core": per_core,
                    "core_types": core_types,
                },
                "memory": memory,
                "disk_io": disk_io,
                "gpu": gpu,
                "network": {
                    "download_bps": round(down, 1),
                    "upload_bps": round(up, 1),
                    "interfaces": interface_rates,
                    "scope": "All interfaces, including loopback and tunnels; not Internet-only",
                    "bytes_recv": net.bytes_recv,
                    "bytes_sent": net.bytes_sent,
                },
                "disk": disk,
                "battery": battery,
                "processes": processes,
                "process_limit": 4096,
                "processes_truncated": getattr(self, "_process_count", 0) > 4096,
                # All sampled processes (bounded at 4096). `process_tree` adds parents
                # so the Processes tab can draw a parent-child tree.
                "process_tree": process_tree,
            }

            cpu_times = self._self.cpu_times()
            cpu_seconds = cpu_times.user + cpu_times.system
            measured_at = time.monotonic()
            self_cpu = (max(0, cpu_seconds-self._self_cpu)/(measured_at-self._self_at)*100
                        if self._self_cpu is not None else None)
            self._self_cpu, self._self_at = cpu_seconds, measured_at
            payload['monitor'] = {'collection_ms': round((measured_at-started)*1000, 1),
                                  'cpu_percent': round(self_cpu, 1) if self_cpu is not None else None,
                                  'rss_bytes': self._self.memory_info().rss, 'collectors':timings}
            return payload

    def close(self):
        self._disk_reading.close()
        self._battery_reading.close()

    def prime_unlocked(self) -> None:
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self._prev_net = psutil.net_io_counters()
        self._prev_ts = time.monotonic()
        self._refresh_processes(prime_only=True)
        self._primed = True

    def _refresh_processes(self, prime_only: bool) -> tuple[list[dict], list[dict]]:
        alive: dict[int, psutil.Process] = {}
        rows: list[dict] = []
        for proc in psutil.process_iter(["pid", "ppid", "name", "memory_percent"]):
            try:
                pid = proc.info["pid"]
                if pid is None:
                    continue
                cached = self._procs.get(pid, proc)
                if not cached.is_running():
                    cached = proc
                try:
                    cpu = cached.cpu_percent(interval=None)
                except psutil.Error:
                    cached = proc
                    cpu = cached.cpu_percent(interval=None)
                alive[pid] = cached
                if prime_only:
                    continue
                name = proc.info.get("name") or "unknown"
                memory = proc.info.get("memory_percent") or 0.0
                ppid = proc.info.get("ppid")
                if ppid is None:
                    try:
                        ppid = cached.ppid()
                    except psutil.Error:
                        ppid = 0
                rows.append(
                    {
                        "pid": int(pid),
                        "create_time": cached.create_time(),
                        "application": application_name(cached, name),
                        "ppid": int(ppid or 0),
                        "name": name,
                        "cpu_percent": finite(cpu, 1),
                        "memory_percent": finite(memory, 1),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        self._procs = alive
        if prime_only:
            return [], []
        rows.sort(key=lambda row: (row["cpu_percent"], row["memory_percent"]), reverse=True)
        self._process_count = len(rows)
        top = rows[:4096]
        by_pid = {row["pid"]: row for row in rows}
        include = {row["pid"] for row in top}
        for row in top:
            parent = row["ppid"]
            guard = 0
            while parent and parent not in include and parent in by_pid and guard < 16:
                include.add(parent)
                parent = by_pid[parent]["ppid"]
                guard += 1
        tree = [by_pid[pid] for pid in include]
        tree.sort(key=lambda row: (row["cpu_percent"], row["memory_percent"]), reverse=True)
        return top, tree

    def _log_once(self, key: str, message: str) -> None:
        if key in self._logged:
            return
        self._logged.add(key)
        log.warning(message)


