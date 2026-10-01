"""Installed applications under /Applications and ~/Applications.

Excludes /System and other OS-managed locations. Metadata comes from each
bundle's Info.plist plus read-only `codesign` / `lipo` probes.
"""

from __future__ import annotations

import plistlib
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .common import CommandFailed

_AUTHORITY_RE = re.compile(r"^Authority=(.+)$", re.M)
_TEAM_RE = re.compile(r"^TeamIdentifier=(.+)$", re.M)
_SIGNATURE_RE = re.compile(r"^Signature=(.+)$", re.M)
_FORMAT_RE = re.compile(r"^Format=.+?\((.+)\)$", re.M)
_DEVELOPER_ID_RE = re.compile(
    r"^Developer ID Application:\s*(.+?)\s*\(([^)]+)\)\s*$"
)
_APPLE_DEV_RE = re.compile(
    r"^Apple Development:\s*(.+?)(?:\s*\(([^)]+)\))?\s*$"
)


def application_roots() -> list[Path]:
    roots = [Path("/Applications")]
    user_apps = Path.home() / "Applications"
    if user_apps.exists():
        roots.append(user_apps)
    return roots


def discover_app_bundles(roots: list[Path] | None = None) -> list[Path]:
    """Find .app bundles under the roots without descending into other bundles."""
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots or application_roots():
        if not root.is_dir():
            continue
        root = root.resolve()
        for dirpath, dirnames, _filenames in os_walk_apps(root):
            current = Path(dirpath)
            # Skip anything under /System even if reached via symlink
            try:
                resolved = current.resolve()
            except OSError:
                dirnames[:] = []
                continue
            if str(resolved).startswith("/System/") or resolved == Path("/System"):
                dirnames[:] = []
                continue
            keep = []
            for name in dirnames:
                child = current / name
                if name.endswith(".app"):
                    try:
                        target = child.resolve()
                    except OSError:
                        continue
                    if str(target).startswith("/System/"):
                        continue
                    if target not in seen and target.is_dir():
                        seen.add(target)
                        found.append(target)
                    # Do not walk into the bundle
                    continue
                keep.append(name)
            dirnames[:] = keep
    found.sort(key=lambda path: path.name.lower())
    return found


def os_walk_apps(root: Path):
    """os.walk wrapper that tolerates permission errors."""
    import os

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=lambda _exc: None):
        yield dirpath, dirnames, filenames


def _load_plist(path: Path) -> dict:
    try:
        return plistlib.loads(path.read_bytes())
    except Exception:
        try:
            raw = subprocess.check_output(
                ["plutil", "-convert", "xml1", "-o", "-", "--", str(path)],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return plistlib.loads(raw)
        except Exception:
            return {}


def _read_bundle_info(app: Path) -> dict:
    info_path = app / "Contents" / "Info.plist"
    data = _load_plist(info_path) if info_path.is_file() else {}
    name = (
        data.get("CFBundleDisplayName")
        or data.get("CFBundleName")
        or app.stem
    )
    version = data.get("CFBundleShortVersionString") or data.get("CFBundleVersion")
    return {
        "name": str(name) if name else app.stem,
        "version": str(version) if version else None,
        "bundle_id": str(data["CFBundleIdentifier"]) if data.get("CFBundleIdentifier") else None,
        "executable": str(data["CFBundleExecutable"]) if data.get("CFBundleExecutable") else None,
    }


def parse_codesign_output(text: str) -> dict:
    """Parse `codesign -dv --verbose=4` stderr/stdout into signing fields."""
    authorities = _AUTHORITY_RE.findall(text or "")
    team_match = _TEAM_RE.search(text or "")
    signature_match = _SIGNATURE_RE.search(text or "")
    format_match = _FORMAT_RE.search(text or "")
    team_id = team_match.group(1).strip() if team_match else None
    if team_id in {"not set", "obsolete", ""}:
        team_id = None
    signature = signature_match.group(1).strip() if signature_match else None
    if signature == "adhoc":
        signed = "Ad-hoc"
    elif authorities or (signature and signature not in {"adhoc"}):
        signed = "Yes"
    elif "code object is not signed" in (text or "").lower() or "not signed at all" in (text or "").lower():
        signed = "No"
    elif text.strip():
        # codesign often omits Signature= when a CMS signature is present
        signed = "Yes" if "Authority=" in text else "No"
    else:
        signed = "Unknown"

    developer = None
    for authority in authorities:
        match = _DEVELOPER_ID_RE.match(authority) or _APPLE_DEV_RE.match(authority)
        if match:
            developer = match.group(1).strip()
            if not team_id and match.lastindex >= 2 and match.group(2):
                team_id = match.group(2).strip()
            break
    if developer is None and authorities:
        # Prefer a leaf identity that is not a certificate authority chain entry
        for authority in authorities:
            if authority.startswith(("Developer ID Certification", "Apple Worldwide", "Apple Root")):
                continue
            developer = authority
            break
        if developer is None:
            developer = authorities[0]

    app_store = False
    if any(a == "Apple Mac OS Application Signing" for a in authorities):
        app_store = True
    format_arches = None
    if format_match:
        raw = format_match.group(1).strip()
        # e.g. "x86_64 arm64" or "arm64"
        parts = [part for part in raw.replace(",", " ").split() if part not in {"universal"}]
        format_arches = ", ".join(parts) if parts else None

    return {
        "signed": signed,
        "developer": developer,
        "team_id": team_id,
        "authorities": authorities,
        "app_store_authority": app_store,
        "format_arches": format_arches,
    }


def _codesign_details(app: Path) -> dict:
    try:
        completed = subprocess.run(
            ["codesign", "-dv", "--verbose=4", str(app)],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {
            "signed": "Unknown",
            "developer": None,
            "team_id": None,
            "authorities": [],
            "app_store_authority": False,
            "format_arches": None,
        }
    text = f"{completed.stderr or ''}{completed.stdout or ''}"
    if completed.returncode not in (0, 1) and not text.strip():
        return {
            "signed": "Unknown",
            "developer": None,
            "team_id": None,
            "authorities": [],
            "app_store_authority": False,
            "format_arches": None,
        }
    return parse_codesign_output(text)


def _architecture(app: Path, executable: str | None, fallback: str | None) -> str | None:
    if executable:
        binary = app / "Contents" / "MacOS" / executable
        if binary.is_file():
            try:
                completed = subprocess.run(
                    ["lipo", "-archs", str(binary)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    return ", ".join(completed.stdout.split())
            except (subprocess.TimeoutExpired, OSError):
                pass
    return fallback


def _has_mas_receipt(app: Path) -> bool:
    return (app / "Contents" / "_MASReceipt" / "receipt").is_file()


def _location_label(app: Path) -> str:
    try:
        resolved = app.resolve()
    except OSError:
        resolved = app
    home_apps = (Path.home() / "Applications").resolve()
    if resolved == home_apps or str(resolved).startswith(str(home_apps) + "/"):
        return "User"
    return "Applications"


def inspect_app(app: Path, roots: list[Path] | None = None) -> dict:
    info = _read_bundle_info(app)
    signing = _codesign_details(app)
    mas = _has_mas_receipt(app) or bool(signing.get("app_store_authority"))
    architecture = _architecture(app, info.get("executable"), signing.get("format_arches"))
    return {
        "name": info["name"],
        "version": info["version"],
        "path": str(app),
        "bundle_id": info.get("bundle_id"),
        "app_store": "Yes" if mas else "No",
        "signed": signing.get("signed") or "Unknown",
        "developer": signing.get("developer"),
        "team_id": signing.get("team_id"),
        "architecture": architecture,
        "location": _location_label(app),
    }


def collect_installed_applications(max_workers: int = 8) -> dict:
    roots = application_roots()
    bundles = discover_app_bundles(roots)
    rows: list[dict] = []
    errors = 0
    if not bundles:
        return {
            "ok": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "roots": [str(path) for path in roots],
            "count": 0,
            "applications": [],
            "errors": 0,
        }
    workers = max(1, min(max_workers, len(bundles)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="apps") as pool:
        futures = {pool.submit(inspect_app, app): app for app in bundles}
        for future in as_completed(futures):
            try:
                rows.append(future.result())
            except Exception:
                errors += 1
    rows.sort(key=lambda row: (row.get("name") or "").lower())
    return {
        "ok": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "roots": [str(path) for path in roots],
        "count": len(rows),
        "applications": rows,
        "errors": errors,
    }


def resolve_application_path(raw: str) -> Path:
    """Return a resolved .app path under /Applications or ~/Applications."""
    if not raw or len(raw) > 4096:
        raise CommandFailed("Invalid application path", 400)
    try:
        path = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise CommandFailed("Invalid application path", 400) from exc
    if path.suffix != ".app" or not path.is_dir():
        raise CommandFailed("Path must be an application bundle", 400)
    if str(path).startswith("/System/"):
        raise CommandFailed("System applications are not listed here", 400)
    allowed = False
    for root in application_roots():
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        try:
            path.relative_to(root_resolved)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise CommandFailed("Application must be under /Applications or ~/Applications", 400)
    return path
