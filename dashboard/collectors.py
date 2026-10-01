from __future__ import annotations
import json
import ctypes
import os
import plistlib
import platform
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import psutil
from .common import CommandFailed
from .native_capacity import volume_capacity

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
VM_LINE_RE = re.compile(r'^(?:"([^"]+)"|([^:]+)):\s+(\d+)\.?\s*$')
PAGE_SIZE_RE = re.compile(r"page size of (\d+) bytes", re.IGNORECASE)
NO_UPDATE_RE = re.compile(
    r"no new software available|no updates are available|no updates available",
    re.IGNORECASE,
)
UPDATE_LABEL_RE = re.compile(r"^\*\s+Label:\s*(.+)$", re.MULTILINE)
UPDATE_TITLE_RE = re.compile(
    r"Title:\s*(?P<title>.+?),\s*Version:\s*(?P<version>[^,]+),\s*"
    r"Size:\s*(?P<size>[^,]+),\s*Recommended:\s*(?P<recommended>YES|NO)",
    re.IGNORECASE,
)
ACTION_RE = re.compile(r"Action:\s*([^,\n]+)", re.IGNORECASE)

# networkQuality -c reports throughput in bits per second (see `man networkQuality`).
JITTER_SAMPLE_KEYS = (
    "il_h2_req_resp",
    "il_tcp_handshake_443",
    "il_tls_handshake",
    "lud_foreign_h2_req_resp",
    "lud_foreign_tcp_handshake_443",
    "lud_foreign_tls_handshake",
    "lud_self_h2_req_resp",
)


SYSTEM_INFO_TYPES = [
    "SPHardwareDataType",
    "SPSoftwareDataType",
    "SPDisplaysDataType",
    "SPStorageDataType",
    "SPNetworkDataType",
    "SPApplicationsDataType",
]

CORETYPES_ICONS = Path(
    "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources"
)
SI_MACHINE_ATTRIBUTES = Path(
    "/System/Library/PrivateFrameworks/ServerInformation.framework"
    "/Versions/A/Resources/en.lproj/SIMachineAttributes.plist"
)

# Marketing introduction years when SIMachineAttributes has no entry (Apple Silicon era).
MODEL_MARKETING_YEARS = {
    "Mac13,1": "2022",
    "Mac13,2": "2022",
    "Mac14,13": "2023",
    "Mac14,14": "2023",
    "Mac14,3": "2023",
    "Mac14,5": "2023",
    "Mac15,14": "2025",
    "Mac16,9": "2025",
}

def finite(value, digits: int | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    if digits is None:
        return number
    return round(number, digits)


def sysctl_int(key: str) -> int | None:
    try:
        output = subprocess.check_output(
            ["sysctl", "-n", key],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
        return int(output)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError):
        return None


def sysctl_text(key: str) -> str | None:
    try:
        output = subprocess.check_output(
            ["sysctl", "-n", key],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
        return output or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def collect_host_info() -> dict:
    p_cores = sysctl_int("hw.perflevel0.physicalcpu") or 0
    e_cores = sysctl_int("hw.perflevel1.physicalcpu") or 0
    chip = sysctl_text("machdep.cpu.brand_string") or platform.processor() or "Unknown CPU"
    product = "macOS"
    version = ""
    build = ""
    try:
        product = subprocess.check_output(
            ["sw_vers", "-productName"], text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip() or product
        version = subprocess.check_output(
            ["sw_vers", "-productVersion"], text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()
        build = subprocess.check_output(
            ["sw_vers", "-buildVersion"], text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        mac = platform.mac_ver()[0]
        if mac:
            version = mac
    if version and build:
        os_name = f"{product} {version} ({build})"
    elif version:
        os_name = f"{product} {version}".strip()
    else:
        os_name = product

    machine_name = None
    model_identifier = sysctl_text("hw.model")
    model_number = None
    memory = None
    serial_number = None
    startup_disk = None
    try:
        raw = subprocess.check_output(
            ["system_profiler", "-json", "SPHardwareDataType", "SPSoftwareDataType"],
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        if len(raw) <= 2 * 1024 * 1024:
            data = json.loads(raw.decode("utf-8", errors="replace"))
            hardware = (data.get("SPHardwareDataType") or [{}])[0]
            software = (data.get("SPSoftwareDataType") or [{}])[0]
            if isinstance(hardware, dict):
                machine_name = hardware.get("machine_name") or machine_name
                model_identifier = hardware.get("machine_model") or model_identifier
                model_number = hardware.get("model_number") or model_number
                chip = hardware.get("chip_type") or chip
                memory = hardware.get("physical_memory") or memory
                serial_number = hardware.get("serial_number") or serial_number
            if isinstance(software, dict):
                startup_disk = software.get("boot_volume") or startup_disk
                os_version = software.get("os_version")
                # Prefer sw_vers; fall back to profiler "macOS 27.0.1 (26A434)" style.
                if isinstance(os_version, str) and os_version.strip() and not version:
                    os_name = os_version.strip()
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        pass

    marketing_year = _marketing_year(model_identifier)
    icon_name = resolve_product_icon_name(machine_name, model_identifier)
    return {
        "hostname": platform.node() or "localhost",
        "model": chip,
        "os": os_name,
        "cpu_count": psutil.cpu_count(logical=True) or 0,
        "topology": {
            "performance_cores": p_cores,
            "efficiency_cores": e_cores,
            "physical_cores": psutil.cpu_count(logical=False) or 0,
            "logical_cores": psutil.cpu_count(logical=True) or 0,
        },
        "machine_name": machine_name or "Mac",
        "marketing_year": marketing_year,
        "model_identifier": model_identifier,
        "model_number": model_number,
        "chip": chip,
        "memory": memory,
        "startup_disk": startup_disk,
        "serial_number": serial_number,
        "os_product": product,
        "os_version": version or None,
        "os_build": build or None,
        "architecture": platform.machine() or None,
        "product_icon": icon_name,
        "product_image_available": bool(icon_name),
    }


def _marketing_year(model_identifier: str | None) -> str | None:
    if not model_identifier:
        return None
    if model_identifier in MODEL_MARKETING_YEARS:
        return MODEL_MARKETING_YEARS[model_identifier]
    try:
        with SI_MACHINE_ATTRIBUTES.open("rb") as handle:
            attrs = plistlib.load(handle)
        entry = attrs.get(model_identifier) if isinstance(attrs, dict) else None
        local = entry.get("_LOCALIZABLE_") if isinstance(entry, dict) else None
        if not isinstance(local, dict):
            return None
        for key in ("marketingModel", "description"):
            text = local.get(key)
            if not isinstance(text, str):
                continue
            match = re.search(r"\b(19|20)\d{2}\b", text)
            if match:
                return match.group(0)
    except (OSError, plistlib.InvalidFileException, ValueError):
        pass
    return None


def resolve_product_icon_name(machine_name: str | None, model_identifier: str | None) -> str | None:
    """Pick a CoreTypes.bundle product icon for this Mac; None if unavailable."""
    if not CORETYPES_ICONS.is_dir():
        return None
    name = (machine_name or "").casefold()
    model = (model_identifier or "").casefold()
    candidates: list[str] = []
    if "mac studio" in name or model.startswith("mac13,") or model.startswith("mac14,13") or model.startswith("mac14,14"):
        candidates.append("com.apple.macstudio.icns")
    elif "mac mini" in name or model.startswith("macmini") or model.startswith("mac14,3") or model.startswith("mac14,12") or model.startswith("mac16,10") or model.startswith("mac16,15"):
        candidates.extend(
            [
                "com.apple.macmini-2020.icns",
                "com.apple.macmini-2018.icns",
                "com.apple.macmini.icns",
            ]
        )
    elif "mac pro" in name or model.startswith("macpro") or model.startswith("mac14,8"):
        candidates.extend(
            [
                "com.apple.macpro-2019.icns",
                "com.apple.macpro-cylinder.icns",
                "com.apple.macpro.icns",
            ]
        )
    elif "macbook air" in name or "macbookair" in model:
        candidates.extend(
            [
                "com.apple.macbookair-13-2022-midnight.icns",
                "com.apple.macbookair-13-2022-space-gray.icns",
                "com.apple.macbookair-13-2022-silver.icns",
                "com.apple.macbookair-2018-space-gray.icns",
                "com.apple.macbookair.icns",
            ]
        )
    elif "macbook pro" in name or "macbookpro" in model:
        candidates.extend(
            [
                "com.apple.macbookpro-16-2021-space-gray.icns",
                "com.apple.macbookpro-14-2021-space-gray.icns",
                "com.apple.macbookpro-16-space-gray.icns",
                "com.apple.macbookpro-15-retina-touchid-space-gray.icns",
                "com.apple.macbookpro-15.icns",
            ]
        )
    elif "imac pro" in name:
        candidates.append("com.apple.imacpro-2017.icns")
    elif "imac" in name or model.startswith("imac"):
        candidates.extend(
            [
                "com.apple.imac-2021-silver.icns",
                "com.apple.imac-unibody-27-no-optical.icns",
                "com.apple.imac-aluminum-24.icns",
            ]
        )
    elif "macbook" in name:
        candidates.extend(
            [
                "com.apple.macbook-retina-space-gray.icns",
                "com.apple.macbook-unibody.icns",
            ]
        )
    for candidate in candidates:
        path = CORETYPES_ICONS / candidate
        try:
            if path.is_file() and path.resolve().is_relative_to(CORETYPES_ICONS.resolve()):
                return candidate
        except (OSError, ValueError):
            continue
    return None


def product_icon_path(icon_name: str | None) -> Path | None:
    if not icon_name or "/" in icon_name or "\\" in icon_name or ".." in icon_name:
        return None
    if not icon_name.startswith("com.apple.") or not icon_name.endswith(".icns"):
        return None
    path = CORETYPES_ICONS / icon_name
    try:
        resolved = path.resolve()
        if resolved.is_file() and resolved.is_relative_to(CORETYPES_ICONS.resolve()):
            return resolved
    except (OSError, ValueError):
        return None
    return None


def render_product_icon_png(icon_name: str | None, max_edge: int = 512) -> bytes | None:
    """Convert an allowlisted CoreTypes .icns to PNG via sips."""
    source = product_icon_path(icon_name)
    if source is None:
        return None
    edge = max(64, min(int(max_edge), 1024))
    try:
        with tempfile.TemporaryDirectory(prefix="sentinel-icon-") as tmp:
            out = Path(tmp) / "product.png"
            subprocess.check_output(
                [
                    "sips",
                    "-Z",
                    str(edge),
                    "-s",
                    "format",
                    "png",
                    str(source),
                    "--out",
                    str(out),
                ],
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            data = out.read_bytes()
            if not data or len(data) > 8 * 1024 * 1024:
                return None
            return data
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def classify_cores(count: int, performance: int, efficiency: int) -> list[str]:
    # Counts do not establish the order of psutil logical CPU indices.
    return ["Core"] * count


def parse_vm_stat(text: str) -> dict:
    page_match = PAGE_SIZE_RE.search(text)
    page_size = int(page_match.group(1)) if page_match else 4096
    pages: dict[str, int] = {}
    for line in text.splitlines():
        match = VM_LINE_RE.match(line.strip())
        if not match:
            continue
        key = (match.group(1) or match.group(2) or "").strip()
        pages[key] = int(match.group(3))

    def page_bytes(key: str) -> int:
        return pages.get(key, 0) * page_size

    return {
        "ok": bool(pages),
        "page_size": page_size,
        "active": page_bytes("Pages active"),
        "wired": page_bytes("Pages wired down"),
        "compressed": page_bytes("Pages occupied by compressor"),
        "free": page_bytes("Pages free"),
        "inactive": page_bytes("Pages inactive"),
        "speculative": page_bytes("Pages speculative"),
    }


def collect_memory(log_once) -> dict:
    vm = psutil.virtual_memory()
    breakdown = {
        "ok": False,
        "page_size": None,
        "active": int(getattr(vm, "active", 0) or 0),
        "wired": int(getattr(vm, "wired", 0) or 0),
        "compressed": 0,
        "free": int(vm.free),
        "inactive": int(getattr(vm, "inactive", 0) or 0),
    }
    try:
        raw = subprocess.check_output(
            ["vm_stat"], text=True, stderr=subprocess.DEVNULL, timeout=3
        )
        parsed = parse_vm_stat(raw)
        if parsed["ok"]:
            breakdown = parsed
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        log_once("vm_stat", f"vm_stat failed: {exc}")
    return {
        "total": int(vm.total),
        "used": max(0, int(vm.total)-int(vm.available)),
        "usage_basis": "total minus psutil available estimate",
        "free": int(vm.free),
        "available": int(vm.available),
        "percent": round(max(0, int(vm.total)-int(vm.available))/vm.total*100, 1) if vm.total else 0.0,
        "swap": psutil.swap_memory()._asdict(),
        "pressure": collect_pressure(),
        "breakdown_status": "success" if breakdown["ok"] else "partial",
        "active": breakdown["active"],
        "wired": breakdown["wired"],
        "compressed": breakdown["compressed"],
        "vm_free": breakdown["free"],
        "inactive": breakdown["inactive"],
        "speculative": breakdown.get("speculative", 0),
        "other": max(0, int(vm.total)-sum(breakdown.get(k,0) for k in ("active","wired","compressed","free","inactive","speculative"))),
        "page_size": breakdown.get("page_size"),
    }


def storage_metrics(total, physical_free, available=None, volume_used=None):
    """Use one capacity scope and denominator. Reclaimable is headroom, not a sum of files."""
    total=max(0,int(total))
    physical_free=min(total,max(0,int(physical_free)))
    valid_available=isinstance(available,int) and not isinstance(available,bool) and physical_free<=available<=total
    effective=available if valid_available else physical_free
    used=total-effective
    return {"status":"success" if valid_available else "partial", "total":total,
            "used":used, "free":physical_free, "available":available if valid_available else None,
            "reclaimable":effective-physical_free if valid_available else None,
            "physical_used":total-physical_free, "data_volume_used":volume_used,
            "percent":round(used/total*100,1) if total else 0.0,
            "basis":"macos_available" if valid_available else "physical_free",
            "source":"Foundation volume capacity" if valid_available else "Filesystem capacity (reclaimable estimate unavailable)"}


def collect_disk(log_once) -> dict:
    mount="/System/Volumes/Data" if os.path.isdir("/System/Volumes/Data") else "/"
    try:
        usage=psutil.disk_usage(mount)
        values={}
        try:
            values=volume_capacity(mount)
        except (OSError,AttributeError,ValueError) as exc:
            log_once("capacity", f"Foundation capacity unavailable: {exc}")
        result=storage_metrics(values.get("total",usage.total),values.get("free",usage.free),values.get("available"),int(usage.used))
        result.update(mount=mount,checked_at=datetime.now(timezone.utc).isoformat())
        return result
    except OSError as exc:
        log_once("disk",f"disk_usage failed: {exc}")
        return {"status":"unavailable","mount":mount,"total":None,"used":None,"free":None,"available":None,"reclaimable":None,"percent":None}


def parse_pmset(text: str) -> dict:
    source_match = re.search(r"Now drawing from '([^']+)'", text)
    source = source_match.group(1) if source_match else "Unknown"
    percent_match = re.search(r"(\d+)%", text)
    percent = int(percent_match.group(1)) if percent_match else None
    status_match = re.search(r"%;\s*([^;]+);", text)
    status = status_match.group(1).strip().lower() if status_match else None
    remaining_match = re.search(r"(\d+:\d+)\s+remaining|\(no estimate\)", text, re.IGNORECASE)
    if remaining_match and remaining_match.group(1):
        time_remaining = remaining_match.group(1)
    elif remaining_match:
        time_remaining = "no estimate"
    else:
        time_remaining = None
    present = percent is not None or "InternalBattery" in text
    charging = bool(
        status
        and "discharging" not in status
        and (re.search(r"\bcharging\b", status) or "finishing charge" in status)
    )
    if status and "discharging" in status:
        power_state = "Battery"
        charging = False
    elif "battery power" in source.lower():
        power_state = "Battery"
    else:
        power_state = "AC"
    if not present:
        status = "no battery"
        charging = False
        power_state = "AC" if "ac" in source.lower() else power_state
    return {
        "collection_status": "success",
        "present": present,
        "percent": percent,
        "status": status or "unknown",
        "source": source,
        "power_state": power_state,
        "charging": charging,
        "time_remaining": time_remaining,
        "cycle_count": None,
        "health": None,
        "condition": None,
        "max_capacity_percent": None,
        "design_capacity_mah": None,
        "max_capacity_mah": None,
        "current_capacity_mah": None,
        "voltage_mv": None,
        "amperage_ma": None,
        "temperature_c": None,
        "adapter_watts": None,
        "system_power_w": None,
        "system_voltage_mv": None,
        "system_current_ma": None,
        "ups_installed": None,
        "wake_on_lan": None,
        "auto_restart_on_power_loss": None,
        "disk_sleep_minutes": None,
        "display_sleep_minutes": None,
    }


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number > 0 else None


def _signed_int(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _profiler_yes_no(text: str, label: str):
    match = re.search(rf"{re.escape(label)}:\s*(Yes|No)\b", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "yes"


def enrich_battery_hardware(base: dict) -> dict:
    """Add AppleSmartBattery / UPS / AC power details when available."""
    result = dict(base)
    try:
        raw = subprocess.check_output(
            ["/usr/sbin/ioreg", "-a", "-r", "-c", "AppleSmartBattery"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        if raw and len(raw) <= 2 * 1024 * 1024:
            registry = plistlib.loads(raw)
            entries = registry if isinstance(registry, list) else [registry]
            battery = next((item for item in entries if isinstance(item, dict)), None)
            if battery:
                result["cycle_count"] = _positive_int(battery.get("CycleCount"))
                health = battery.get("BatteryHealth") or battery.get("PermanentFailureStatus")
                if isinstance(health, str) and health.strip():
                    result["health"] = health.strip()
                condition = battery.get("BatteryHealthCondition") or battery.get("legacyBatteryInfo", {})
                if isinstance(condition, str) and condition.strip():
                    result["condition"] = condition.strip()
                design = _positive_int(battery.get("DesignCapacity"))
                maximum = _positive_int(
                    battery.get("AppleRawMaxCapacity")
                    or battery.get("MaxCapacity")
                )
                current = _positive_int(
                    battery.get("AppleRawCurrentCapacity")
                    or battery.get("CurrentCapacity")
                )
                result["design_capacity_mah"] = design
                result["max_capacity_mah"] = maximum
                result["current_capacity_mah"] = current
                if design and maximum:
                    result["max_capacity_percent"] = round(100 * maximum / design, 1)
                voltage = _positive_int(battery.get("Voltage"))
                amperage = _signed_int(battery.get("InstantAmperage"))
                if amperage is None:
                    amperage = _signed_int(battery.get("Amperage"))
                temperature = battery.get("Temperature")
                result["voltage_mv"] = voltage
                result["amperage_ma"] = amperage
                if isinstance(temperature, (int, float)) and not isinstance(temperature, bool) and temperature > 0:
                    # IOKit reports decidegrees Celsius.
                    result["temperature_c"] = round(float(temperature) / 100.0, 1)
                adapter = battery.get("AdapterDetails")
                if isinstance(adapter, dict):
                    watts = _positive_int(adapter.get("Watts") or adapter.get("AdapterWatts"))
                    result["adapter_watts"] = watts
                telemetry = battery.get("PowerTelemetryData")
                if isinstance(telemetry, dict):
                    power_in = telemetry.get("SystemPowerIn")
                    if isinstance(power_in, (int, float)) and not isinstance(power_in, bool) and power_in > 0:
                        # Apple Silicon reports milliwatts.
                        result["system_power_w"] = round(float(power_in) / 1000.0, 1)
                    voltage_in = telemetry.get("SystemVoltageIn")
                    if isinstance(voltage_in, (int, float)) and not isinstance(voltage_in, bool) and voltage_in > 0:
                        result["system_voltage_mv"] = int(voltage_in)
                    current_in = telemetry.get("SystemCurrentIn")
                    if isinstance(current_in, (int, float)) and not isinstance(current_in, bool) and current_in != 0:
                        result["system_current_ma"] = int(current_in)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError, plistlib.InvalidFileException):
        pass
    try:
        raw = subprocess.check_output(
            ["system_profiler", "SPPowerDataType"],
            stderr=subprocess.DEVNULL,
            timeout=3,
            text=True,
        )
        if "UPS Installed: Yes" in raw:
            result["ups_installed"] = True
        elif "UPS Installed: No" in raw:
            result["ups_installed"] = False
        watts_match = re.search(r"Wattage \(W\):\s*(\d+)", raw)
        if watts_match and result.get("adapter_watts") is None:
            result["adapter_watts"] = int(watts_match.group(1))
        health_match = re.search(r"Condition:\s*(.+)", raw)
        if health_match and not result.get("condition"):
            result["condition"] = health_match.group(1).strip()
        cycle_match = re.search(r"Cycle Count:\s*(\d+)", raw)
        if cycle_match and result.get("cycle_count") is None:
            result["cycle_count"] = int(cycle_match.group(1))
        max_pct_match = re.search(r"Maximum Capacity:\s*(\d+)%", raw)
        if max_pct_match and result.get("max_capacity_percent") is None:
            result["max_capacity_percent"] = float(max_pct_match.group(1))
        result["wake_on_lan"] = _profiler_yes_no(raw, "Wake on LAN")
        result["auto_restart_on_power_loss"] = _profiler_yes_no(raw, "Automatic Restart on Power Loss")
        disk_sleep = re.search(r"Disk Sleep Timer \(Minutes\):\s*(\d+)", raw)
        if disk_sleep:
            result["disk_sleep_minutes"] = int(disk_sleep.group(1))
        display_sleep = re.search(r"Display Sleep Timer \(Minutes\):\s*(\d+)", raw)
        if display_sleep:
            result["display_sleep_minutes"] = int(display_sleep.group(1))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass
    return result


def collect_battery(log_once) -> dict:
    empty = {
        "collection_status": "unavailable",
        "present": False,
        "percent": None,
        "status": "unknown",
        "source": "Unknown",
        "power_state": "AC",
        "charging": False,
        "time_remaining": None,
        "cycle_count": None,
        "health": None,
        "condition": None,
        "max_capacity_percent": None,
        "design_capacity_mah": None,
        "max_capacity_mah": None,
        "current_capacity_mah": None,
        "voltage_mv": None,
        "amperage_ma": None,
        "temperature_c": None,
        "adapter_watts": None,
        "system_power_w": None,
        "system_voltage_mv": None,
        "system_current_ma": None,
        "ups_installed": None,
        "wake_on_lan": None,
        "auto_restart_on_power_loss": None,
        "disk_sleep_minutes": None,
        "display_sleep_minutes": None,
    }
    try:
        raw = subprocess.check_output(
            ["pmset", "-g", "batt"], text=True, stderr=subprocess.DEVNULL, timeout=3
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        log_once("pmset", f"pmset failed: {exc}")
        return empty
    return enrich_battery_hardware(parse_pmset(raw))


def parse_softwareupdate(text: str) -> dict:
    cleaned = ANSI_RE.sub("", text)
    updates: list[dict] = []
    blocks = re.split(r"(?=^\*\s+Label:)", cleaned, flags=re.MULTILINE)
    for block in blocks:
        label_match = UPDATE_LABEL_RE.search(block)
        if not label_match:
            continue
        title_match = UPDATE_TITLE_RE.search(block)
        action_match = ACTION_RE.search(block)
        updates.append(
            {
                "label": label_match.group(1).strip(),
                "title": title_match.group("title").strip() if title_match else label_match.group(1).strip(),
                "version": title_match.group("version").strip() if title_match else None,
                "size": title_match.group("size").strip() if title_match else None,
                "recommended": (title_match.group("recommended").upper() == "YES") if title_match else None,
                "action": action_match.group(1).strip() if action_match else None,
            }
        )
    no_updates = NO_UPDATE_RE.search(cleaned) is not None
    if not updates and not no_updates and "found the following" not in cleaned.lower():
        return {"ok": False, "status": "failure", "pending": None, "count": None,
                "summary": "Update status unknown: unrecognized command output.", "updates": []}
    pending = bool(updates) or (not no_updates and "found the following" in cleaned.lower())
    if no_updates and not updates:
        pending = False
    if pending and updates:
        summary = f"{len(updates)} update{'s' if len(updates) != 1 else ''} available"
    elif not pending:
        summary = "No new software available."
    else:
        summary = "Updates may be available, but no labels were parsed."
    return {
        "ok": True,
        "status": "success" if updates or no_updates else "partial",
        "pending": pending,
        "count": len(updates),
        "summary": summary,
        "updates": updates,
    }


def extract_json(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _float_list(value) -> list[float]:
    if not isinstance(value, list):
        return []
    numbers: list[float] = []
    for item in value:
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            continue
    return numbers


def latency_jitter_ms(samples: list[float]) -> float | None:
    """Mean absolute deviation of a latency sample list, in milliseconds."""
    if len(samples) < 2:
        return None
    mean = sum(samples) / len(samples)
    deviation = sum(abs(sample - mean) for sample in samples) / len(samples)
    return round(deviation, 2)


def responsiveness_label(rpm: float | None) -> str:
    if rpm is None:
        return "Unknown"
    if rpm >= 1000:
        return "High"
    if rpm >= 200:
        return "Medium"
    return "Low"


def _mbps_from_bits(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) / 1_000_000, 2)
    except (TypeError, ValueError):
        return None


def parse_network_quality(stdout: str) -> dict:
    payload = extract_json(stdout)
    if payload is None:
        return parse_network_quality_text(stdout)

    downlink = _mbps_from_bits(payload.get("dl_throughput"))
    uplink = _mbps_from_bits(payload.get("ul_throughput"))
    rpm_raw = payload.get("responsiveness")
    if rpm_raw is None:
        parts = []
        for key in ("dl_responsiveness", "ul_responsiveness"):
            if isinstance(payload.get(key), (int, float)):
                parts.append(float(payload[key]))
        rpm_raw = sum(parts) / len(parts) if parts else None
    try:
        rpm = round(float(rpm_raw), 1) if rpm_raw is not None else None
    except (TypeError, ValueError):
        rpm = None

    latency = payload.get("base_rtt")
    try:
        latency_ms = round(float(latency), 2) if latency is not None else None
    except (TypeError, ValueError):
        latency_ms = None

    jitter = None
    jitter_source = None
    for key in JITTER_SAMPLE_KEYS:
        samples = _float_list(payload.get(key))
        jitter = latency_jitter_ms(samples)
        if jitter is not None:
            jitter_source = key
            break

    if payload.get("error_code") and downlink is None and uplink is None:
        raise CommandFailed(f"networkQuality failed with error_code {payload.get('error_code')}")
    if downlink is None and uplink is None and rpm is None:
        raise CommandFailed("networkQuality JSON did not include throughput or responsiveness")

    return {
        "ok": True,
        "downlink_mbps": downlink,
        "uplink_mbps": uplink,
        "throughput_mbps": downlink,
        "responsiveness_rpm": rpm,
        "responsiveness_label": responsiveness_label(rpm),
        "latency_ms": latency_ms,
        "jitter_ms": jitter,
        "jitter_source": jitter_source,
        "interface": payload.get("interface_name"),
        "started": payload.get("start_date"),
        "finished": payload.get("end_date"),
    }


def parse_network_quality_text(text: str) -> dict:
    downlink = re.search(r"Downlink capacity:\s*([\d.]+)\s*Mbps", text, re.IGNORECASE)
    uplink = re.search(r"Uplink capacity:\s*([\d.]+)\s*Mbps", text, re.IGNORECASE)
    rpm = re.search(
        r"Responsiveness:\s*(?:Low|Medium|High)?\s*\(?\s*([\d.]+)\s*RPM",
        text,
        re.IGNORECASE,
    )
    latency = re.search(r"Idle Latency:\s*([\d.]+)\s*milliseconds", text, re.IGNORECASE)
    if not any((downlink, uplink, rpm)):
        excerpt = " ".join(text.split())[:240]
        raise CommandFailed(
            "Could not parse networkQuality output"
            + (f": {excerpt}" if excerpt else "")
        )
    rpm_value = round(float(rpm.group(1)), 1) if rpm else None
    return {
        "ok": True,
        "downlink_mbps": round(float(downlink.group(1)), 2) if downlink else None,
        "uplink_mbps": round(float(uplink.group(1)), 2) if uplink else None,
        "throughput_mbps": round(float(downlink.group(1)), 2) if downlink else None,
        "responsiveness_rpm": rpm_value,
        "responsiveness_label": responsiveness_label(rpm_value),
        "latency_ms": round(float(latency.group(1)), 2) if latency else None,
        "jitter_ms": None,
        "jitter_source": None,
        "interface": None,
        "started": None,
        "finished": None,
    }



def _plist_or_json(args: list[str]) -> dict:
    """Prefer JSON when `diskutil` accepts it, otherwise read the plist form."""
    try:
        raw = subprocess.check_output(args, stderr=subprocess.PIPE, timeout=20)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise CommandFailed(f"{' '.join(args)} failed: {exc}") from exc
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {"items": payload}
        except json.JSONDecodeError:
            pass
    try:
        payload = plistlib.loads(raw)
    except Exception as exc:
        raise CommandFailed(f"Could not parse {' '.join(args)}") from exc
    return payload if isinstance(payload, dict) else {"items": payload}


def _parse_df(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        rows.append(
            {
                "filesystem": parts[0],
                "size": parts[1],
                "used": parts[2],
                "available": parts[3],
                "capacity": parts[4],
                "mount": " ".join(parts[8:]),
            }
        )
    return rows


def _as_int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else None
    return None


def collect_apfs() -> dict:
    # `diskutil list -json` is not accepted on every macOS release (`-json` is
    # treated as a disk name). Try it, then fall back to the plist listing.
    listing: dict = {}
    list_error = None
    for args in (["diskutil", "list", "-json"], ["diskutil", "list", "-plist"]):
        try:
            listing = _plist_or_json(args)
            list_error = None
            break
        except CommandFailed as exc:
            list_error = exc.message
    apfs: dict = {}
    try:
        apfs = _plist_or_json(["diskutil", "apfs", "list", "-plist"])
    except CommandFailed as exc:
        list_error = list_error or exc.message

    containers = []
    for container in apfs.get("Containers") or []:
        if not isinstance(container, dict):
            continue
        ceiling = _as_int(container.get("CapacityCeiling")) or 0
        free = _as_int(container.get("CapacityFree")) or 0
        volumes = []
        for volume in container.get("Volumes") or []:
            if not isinstance(volume, dict):
                continue
            used = _as_int(volume.get("CapacityInUse")) or 0
            role = volume.get("Roles")
            if isinstance(role, list):
                role_text = ", ".join(str(item) for item in role)
            else:
                role_text = str(role or "")
            volumes.append(
                {
                    "name": volume.get("Name") or volume.get("DeviceIdentifier"),
                    "device": volume.get("DeviceIdentifier"),
                    "role": role_text,
                    "used_bytes": used,
                    "filevault": bool(volume.get("FileVault")),
                }
            )
        containers.append(
            {
                "device": container.get("ContainerReference"),
                "uuid": container.get("APFSContainerUUID"),
                "size_bytes": ceiling,
                "free_bytes": free,
                "used_bytes": max(0, ceiling - free),
                "purgeable_bytes": None,
                "volumes": volumes,
            }
        )
    containers.sort(key=lambda item: item["size_bytes"], reverse=True)
    for mount in ("/System/Volumes/Data", "/"):
        try:
            info = _plist_or_json(["diskutil", "info", "-plist", mount])
        except CommandFailed:
            continue
        purgeable = None
        for key in ("PurgeableSpace", "APFSVolumePurgeableSpace", "APFSPurgeable"):
            amount = _as_int(info.get(key))
            if amount:
                purgeable = amount
                break
        reference = info.get("APFSContainerReference")
        try:
            capacity=volume_capacity(mount)
        except (OSError,AttributeError,ValueError):
            capacity={}
        for container in containers:
            if container["device"] == reference and container.get("available_bytes") is None:
                available=capacity.get("available")
                free=capacity.get("free")
                if isinstance(available,int) and isinstance(free,int) and free<=available<=container["size_bytes"]:
                    container["available_bytes"]=available
                    container["reclaimable_bytes"]=available-free
            if container["device"] == reference and purgeable:
                container["purgeable_bytes"] = purgeable

    df_text = ""
    try:
        df_text = subprocess.check_output(["df", "-h"], text=True, stderr=subprocess.DEVNULL, timeout=8)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        df_text = ""
    return {
        "ok": bool(containers),
        "status": "partial" if list_error and containers else ("unavailable" if not containers else "success"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "list_note": list_error,
        "disks": listing.get("AllDisks") or listing.get("WholeDisks") or [],
        "containers": containers,
        "mounts": _parse_df(df_text),
    }


def _scalar_pairs(obj: dict) -> list[dict]:
    pairs = []
    for key, value in obj.items():
        if key.startswith("_") or isinstance(value, (dict, list)):
            continue
        if value is None or value == "":
            continue
        label = str(key).replace("_", " ")
        pairs.append({"key": label[:1].upper() + label[1:], "value": value})
    return pairs


def app_kind(path: str | None, obtained_from: str | None) -> str:
    """Apple-supplied apps are system; App Store and other installs are third-party."""
    source = (obtained_from or "").strip().lower()
    location = path or ""
    if source == "apple" or location.startswith(("/System/", "/usr/", "/Library/Apple/")):
        return "system"
    return "third-party"


def _app_row(item: dict) -> dict:
    path = item.get("path") or item.get("location")
    obtained_from = item.get("obtained_from")
    return {
        "name": item.get("_name") or item.get("name") or "Unknown",
        "version": item.get("version") or item.get("bundle_version"),
        "path": path,
        "obtained_from": obtained_from,
        "kind": app_kind(path, obtained_from),
    }


def collect_system_info() -> dict:
    try:
        raw = subprocess.check_output(
            ["system_profiler", "-json", *SYSTEM_INFO_TYPES],
            stderr=subprocess.DEVNULL,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandFailed("system_profiler timed out after 180 seconds", 504) from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        raise CommandFailed(f"system_profiler failed: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise CommandFailed("system_profiler did not return JSON") from exc
    if not isinstance(data, dict):
        raise CommandFailed("system_profiler JSON was not an object")

    hardware = data.get("SPHardwareDataType") or []
    software = data.get("SPSoftwareDataType") or []
    displays = []
    for item in data.get("SPDisplaysDataType") or []:
        if not isinstance(item, dict):
            continue
        cards = []
        for display in item.get("spdisplays_ndrvs") or []:
            if isinstance(display, dict):
                cards.append(
                    {
                        "name": display.get("_name") or "Display",
                        "fields": _scalar_pairs(display),
                    }
                )
        displays.append(
            {
                "name": item.get("_name") or "Graphics",
                "fields": _scalar_pairs(item),
                "displays": cards,
            }
        )
    storage = []
    for item in data.get("SPStorageDataType") or []:
        if isinstance(item, dict):
            storage.append({"name": item.get("_name") or "Volume", "fields": _scalar_pairs(item)})
    network = []
    for item in data.get("SPNetworkDataType") or []:
        if not isinstance(item, dict):
            continue
        addresses = []
        for iface in item.get("IPv4", {}).get("Addresses", []) if isinstance(item.get("IPv4"), dict) else []:
            addresses.append(str(iface))
        network.append(
            {
                "name": item.get("_name") or item.get("interface") or "Interface",
                "interface": item.get("interface"),
                "fields": _scalar_pairs(item),
                "addresses": addresses,
            }
        )
    applications = [_app_row(item) for item in data.get("SPApplicationsDataType") or [] if isinstance(item, dict)]
    applications.sort(key=lambda row: (row["name"] or "").lower())
    return {
        "ok": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "hardware": {
            "name": hardware[0].get("_name") if hardware and isinstance(hardware[0], dict) else "Hardware",
            "fields": _scalar_pairs(hardware[0]) if hardware and isinstance(hardware[0], dict) else [],
        },
        "software": {
            "name": software[0].get("_name") if software and isinstance(software[0], dict) else "Software",
            "fields": _scalar_pairs(software[0]) if software and isinstance(software[0], dict) else [],
        },
        "displays": displays,
        "storage": storage,
        "network": network,
        "applications": applications,
    }



def application_name(proc, fallback):
    try:
        executable = proc.exe()
        match = re.search(r"/([^/]+)\.app/", executable)
        return match.group(1) if match else fallback
    except psutil.Error:
        return fallback

def collect_pressure():
    """XNU exports dispatch flags (1/2/4), not internal VM pressure enum values.

    Sources: apple-oss-distributions/xnu bsd/kern/kern_memorystatus_notify.c
    (sysctl_memorystatus_vm_pressure_level) and bsd/sys/event_private.h.
    This is a read-only query; never invoke memory_pressure stress modes.
    """
    source = "kern.memorystatus_vm_pressure_level"
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        query = libc.sysctlbyname
        query.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        query.restype = ctypes.c_int
        value = ctypes.c_uint32()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if query(source.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            raise OSError(ctypes.get_errno(), "OS pressure query unavailable")
        level = {1:"Normal", 2:"Warning", 4:"Critical"}.get(value.value)
        return {"status":"success" if level else "unavailable", "level":level,
                "raw":value.value, "source":source, "reason":None if level else "Unrecognized OS pressure value"}
    except (OSError, AttributeError):
        return {"status":"unavailable", "level":None, "source":source, "reason":"OS pressure query is unavailable or permission restricted"}
