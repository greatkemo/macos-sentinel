# macOS Sentinel

A local macOS system dashboard with a FastAPI backend and an offline-capable browser interface. CPU, memory, network and process telemetry come from the Mac running the server. The dark sidebar layout and existing Overview, Processes, Logs, Storage and System Info views are retained.

## Requirements

- macOS (Apple Silicon or Intel)
- Python **3.13** (the project is exercised with 3.13)
- A modern browser on the same Mac
- Optional: Node.js only if you want to rebuild Tailwind CSS or run the browser smoke tests

The app shells out to read-only native tools such as `sysctl`, `sw_vers`, `vm_stat`, `pmset`, `ioreg`, `diskutil`, `df`, `du`, `log`, `lsof`, `networkQuality`, `softwareupdate`, `system_profiler`, and `sips`. Missing or restricted data is reported as unavailable/partial rather than fabricated.

## Install

```sh
git clone https://github.com/greatkemo/macos-system-dashboard.git
cd macos-system-dashboard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

You do **not** need `npm install` for normal use: Chart.js, Lucide, and compiled Tailwind CSS are already vendored under `static/`.

## Run

With the virtualenv active:

```sh
python app.py
```

Then open **http://127.0.0.1:8000** or **http://localhost:8000**.

Stop the server with Ctrl+C.

### Faster local launch

After install, you can double-click **Launch Dashboard.command** in the project folder. It reuses a healthy existing server when possible, opens the browser, and refuses an occupied non-dashboard port. It expects the `.venv` setup above; it does not install dependencies.

### Port and process notes

- Default bind: `127.0.0.1:8000` (localhost only)
- Another port: `SENTINEL_PORT=8001 python app.py`
- Run **one** server worker: telemetry, sessions, cache, and scan ownership are process-local
- After backend changes, restart the server
- After a restart, reload the page — the session token rotates each run
- Frontend responses use `no-store`

### Optional: rebuild CSS / run tests

```sh
npm ci
npm run build:css
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
```

Software-update and bandwidth-consuming network-quality checks run only when their toolbar buttons are pressed. Opening Storage starts a directory scan; System Info starts an inventory lookup. There is no installation, deletion, or automatic system cleanup feature.

## Features

- **Overview:** four-row live HUD — three top tiles (About This Mac, assessment, diagnostics); equal-sized left-aligned circular instrument tiles (CPU ring, memory distribution doughnut, storage ring, power ring) with a padded vertical divider and details on the right; CPU/GPU/network charts; then the correlated performance timeline. A Tools toolbar hosts network test, software updates, sample age, uptime and connection status. Sampling, alerts, exports and saved sessions live under **Settings**.
- **Settings:** sampling interval, adaptive idle sampling, compact view, alert rules, saved sessions/comparisons and exports.
- **Overview instruments:** overall and per-logical-core CPU, physical memory categories as a doughnut with legend, macOS-compatible startup storage availability, power/battery (AC vs charge ring, hardware details and live system draw when Apple Silicon power telemetry is present), swap, actual OS memory-pressure state where available, disk read/write throughput, per-interface throughput, and CPU/network/GPU history. Host identity includes machine name, marketing year when known, chip, memory, startup disk, serial number, macOS version with build, and CPU architecture.
- **History:** 30 seconds, 5 minutes, 30 minutes or 1 hour, using sample timestamps on a time-scaled axis. At most 3,600 aggregate samples are retained in server memory for up to one hour. Rendering downsamples to roughly 300 points; export retains the selected window's samples. Gaps are shown rather than joined as continuous measurements. Live history survives browser reconnects but not server restarts; optional saved sessions persist separately.
- **Sampling:** choose 1, 2, 5 or 10 seconds. This is a server-wide setting shared by open tabs and resets on restart. Battery/disk capacity are collected at most every 15 seconds. CPU baselines and live collection run on one dedicated worker thread; storage and battery refresh separately.
- **Events:** CPU usage above 85% or memory usage above 80% continuously for 15 seconds records an event. Events reset after recovery and do not count missing sample intervals as sustained usage. At most 100 events are retained; the UI shows the newest 20. These are in-app events, not macOS notifications.
- **Processes:** all readable sampled processes, bounded at 4,096, searched before pagination; 100 rows per page; CPU/memory/name sorting in flat view; parent-child tree; application grouping. Grouping uses the first `.app` bundle in the executable path, falling back to process name. It is an approximation, not macOS coalition accounting. Selecting a group filters its member processes. Group termination is disabled.
- **Inspector:** open files/sockets from `lsof`. Older responses cannot replace the most recently selected process. Truncation is disclosed. Rows support Enter/Space. Process termination uses SIGTERM, a confirmation dialog, and PID plus creation-time validation. A reused PID returns 409. The server refuses PID <= 1 and itself.
- **Logs:** a native NDJSON unified-log stream while the tab is open, with level/subsystem/process/category/message filters, pause, clear and auto-scroll. Pause discards incoming lines rather than suspending the native command. At most 2,000 records are retained and 800 rendered, at most four times per second. Four concurrent log clients are allowed. EOF, disconnect and cancellation close the socket and reap the process group.
- **Storage:** APFS container breakdown plus a progressive directory scan of `/Applications`, `~/Library`, `~/Downloads` and `~/Documents`. Each folder appears as it is scanned, including available child sizes. Cancel terminates the active scan subprocess. Partial results and errors remain visible. Once scanning finishes, click a child directory to drill down, or Root folders to return.
- **System Info:** hardware/software, displays, storage, network and searchable application inventory. Five-minute cache, timestamp and explicit refresh. Application search covers the full returned inventory; rendering caps at 500 matches and asks you to narrow the search.
- **Export:** selected aggregate history as CSV, or a redacted JSON diagnostic export containing aggregate history, threshold events, sampling interval and limitations. Neither includes hostnames, serial numbers, process names/PIDs/arguments, paths, IPs, logs or application inventory. Exports occur only when clicked. Automatic persistence is off by default and can be enabled under Saved sessions. No telemetry is sent to a remote service.
- **Layout:** compact-view switch, responsive horizontal navigation on narrow screens, keyboard process inspection, focus restoration and a focus-trapped termination modal, reduced-motion support and selected navigation/tab states.

## Measurement semantics and limits

- Overall CPU is 0–100% across the machine. Process CPU uses 100% **per logical core**, so a multithreaded process can exceed 100%. Its small bar caps at 100%; the number is not capped.
- Core counts come from sysctl; they do **not** establish psutil's P/E index ordering. Per-core bars use neutral logical-core labels. No GPU, temperature, fan or power metric is invented when the hardware does not expose it.
- Power comes from `pmset` plus read-only `ioreg` `AppleSmartBattery` and `system_profiler SPPowerDataType`. Desktops without an internal battery show an AC ring and mains details (UPS presence, Wake on LAN, sleep timers when reported). Laptops show charge percent on the ring with cycle count, condition/health, capacity, voltage, amperage and temperature when IORegistry exposes them. Adapter wattage appears when the charger reports it. Live **system draw** (watts), plus input voltage/current when present, come from Apple Silicon `PowerTelemetryData` (`SystemPowerIn` in milliwatts); absent telemetry is omitted, never fabricated.
- This Mac identity uses `system_profiler` hardware/software overview plus `sw_vers` (including build). The optional product image is converted with `sips` from an allowlisted `CoreTypes.bundle` `.icns` matched to the machine family (for example Mac Studio → `com.apple.macstudio.icns`). It is not the 3D About This Mac scene; missing icons are omitted rather than substituted with unrelated artwork. Marketing year uses a small model-identifier map and, when present, `SIMachineAttributes`. Serial numbers appear only in the live local UI, not in redacted exports.
- Memory used is consistently calculated as total minus psutil’s available-memory estimate; its percentage is that same used amount divided by total. This includes compressed memory rather than mixing psutil’s active+wired `used` field with its differently defined percentage. This is an estimate, not exact Activity Monitor app-memory accounting, and is **not memory pressure**. The separate OS-pressure field queries the read-only `kern.memorystatus_vm_pressure_level` sysctl. XNU exports dispatch flags 1/2/4 for Normal/Warning/Critical; unsupported values and denied queries are unavailable. See Apple's [sysctl implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus_notify.c) and [pressure constants](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/event_private.h). No memory stress command is run.
- Memory categories include wired, active, compressed, free, inactive, speculative and residual other memory. The Overview memory tile renders those categories as a doughnut with a side legend. Native/psutil snapshots are not atomic and can differ slightly. `vm_stat` failure produces a partial breakdown. Swap has its own used/total values.
- Storage capacities and directory sizes use decimal kB/MB/GB for comparison with System Settings. Memory uses binary KiB/MiB/GiB; throughput is bytes/second. `networkQuality` separately reports decimal Mbps converted from bits/second. Its displayed jitter is mean absolute deviation of supplied latency samples, not a dedicated tool jitter measurement.
- Network totals include loopback and tunnel interfaces; they are not Internet-only traffic and may count the same traffic at multiple interface layers. Per-interface values make that scope visible. Disk I/O is the OS aggregate, not per-process attribution.
- Overview storage queries Foundation’s `NSURLVolumeAvailableCapacityForImportantUsageKey` for `/System/Volumes/Data` (or `/` as fallback). Used = total − macOS available, and percent = used/total. Physical free and estimated reclaimable headroom (available − physical free) are shown separately. This closely tracks System Settings; estimates and update times can differ. When the API is unavailable, the UI explicitly falls back to physical allocation and leaves the available/reclaimable estimates unknown. The Storage tab labels APFS physical allocation separately and includes macOS availability for the startup container. No files are purged or deleted. The native bridge uses only system frameworks, without requiring Swift or PyObjC.
- GPU utilization comes from driver-provided `IOAccelerator` `PerformanceStatistics` via unprivileged `ioreg`, with a two-second timeout. The GPU tile, history and aggregate CSV/JSON exports include device utilization. Renderer/tiler percentages are not additive. Shared GPU RAM is not dedicated VRAM and must not be added to system RAM usage. Unsupported/invalid counters appear as unavailable, never a fabricated zero; a real idle zero is valid. Multiple GPUs are shown individually without inventing an aggregate. These driver counters are not a stable cross-hardware API.
- Directory sizes are allocated blocks reported by `du -x -d 1 -k`. Traversal stays on the starting filesystem and does not follow ordinary child symlinks. Requested drill-down paths are resolved and checked against the configured roots. Like other filesystem tools, this is not a security boundary against a hostile local user changing the filesystem concurrently. A path has a 90-second timeout. Child results retain the largest 200 entries with a truncation flag. Permission errors, file changes and timeouts make results partial. No Full Disk Access permission is requested automatically.
- Directory cache TTL is 120 seconds, bounded at 16 target sets. Only one scan runs at a time. Refresh bypasses cache when idle; controls prevent conflicting refresh/drill-down during a running scan. A cancellation retains collected results. Leaving Storage does not cancel a scan; use Cancel explicitly.
- A stale indicator appears when sample age exceeds max(5 seconds, 3 sampling intervals), even if the socket remains open. Up to 32 telemetry clients are allowed. Each has a one-sample queue and a three-second send timeout, so a slow client cannot hold up sampling.
- Unknown update output is an explicit failure/unknown status, never “up to date.” APFS and directory results have explicit `success`, `partial`, `unavailable`, `failure`, `running` or `cancelled` states as applicable. Results cannot expose information the server user lacks permission to read.

## Security model

The server binds to `127.0.0.1`. Host headers must be exactly `127.0.0.1:<port>` or `localhost:<port>`. Every API request requires a random per-run token; state-changing HTTP requests also require a trusted Origin. Both WebSockets require a trusted Origin and token in a subprotocol, keeping the token out of URLs and access logs. The bootstrap HTML supplies the token to same-origin JavaScript. It is never written to disk.

Responses prohibit framing, disable sniffing and referrers, and constrain scripts/connections to the same origin with CSP. Inline scripts and runtime CSS compilation are not used. Local vendor assets include pinned versions, checksums and licenses.

This protects against untrusted web origins; it is not multi-user authentication against software already running on the Mac, which can fetch the bootstrap page. Do not expose the server through a public port/reverse proxy. A browser page with this session can read local system information and signal processes permitted to the server user. The process identity checks reduce PID-reuse races; macOS does not provide a fully atomic PID-plus-birthtime signal syscall through this application.

## Code layout

| File | Responsibility |
| --- | --- |
| `app.py` | API routes, command lifecycle, caches, startup/shutdown |
| `dashboard/security.py` | Host, Origin, session and response-header controls |
| `dashboard/collectors.py` | Native parsing and read-only system collectors |
| `dashboard/native_capacity.py` | Read-only Foundation capacity bridge |
| `dashboard/gpu.py` | Validated GPU driver counters and unavailable handling |
| `dashboard/telemetry.py` | Dedicated-thread sampler and process identities |
| `dashboard/runtime.py` | Bounded fan-out, aggregate history and alert state |
| `dashboard/storage.py` | Progressive scans, cancellation, cache and path constraints |
| `dashboard/common.py` | Shared command exception |
| `index.html` | Accessible page structure and visual styles |
| `static/dashboard.js` | Existing views, process/log UI and authenticated transport |
| `static/diagnostics.js` | History, diagnostics, settings and export controls |
| `static/core.js` | Pure, testable data helpers |
| `static/source.css`, `tailwind.config.cjs` | Compiled CSS inputs |
| `static/vendor/` | Pinned libraries, checksums and licenses |
| `tests/` | Python, JavaScript and synthetic browser checks |

## API summary

API errors use `{"detail":"..."}`. At most four async command checks/inspectors execute concurrently. Send `X-Sentinel-Token` on all `/api/` requests. POST/DELETE requests also need the local Origin. API documentation pages are disabled.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/` | Session bootstrap HTML |
| WS | `/ws` | Latest telemetry; subprotocols `sentinel`, then token |
| WS | `/ws/logs` | Bounded-client native log streaming |
| POST | `/api/process/kill/{pid}` | Body `{"create_time":123.45}`; SIGTERM with identity validation |
| GET | `/api/process/{pid}/details` | Metadata and bounded rendered lsof files/sockets |
| GET | `/api/network-quality` | Explicit network test, timeout 120s; shared concurrent run |
| GET | `/api/software-updates` | Explicit update check, timeout 180s; shared concurrent run |
| GET | `/api/storage/apfs` | Shared concurrent APFS query, timestamp and status |
| POST | `/api/storage/scans` | Body `{"path":null,"force":false}`; start/join scan or use cache |
| GET | `/api/storage/scans` | Current progress snapshot |
| DELETE | `/api/storage/scans` | Cancel current scan and retain partial results |
| GET | `/api/storage/directories` | Compatibility route returning a progressive scan snapshot |
| GET | `/api/system-info?force=false` | Cached inventory; `force=true` bypasses cache; timeout 180s |
| GET | `/api/host/product-image` | PNG from local CoreTypes product icon for this Mac; 404 if unavailable |
| GET | `/api/history?seconds=300` | Aggregate samples (window clamped 30–3600s), events and interval |
| POST | `/api/settings` | Body `{"interval":2}`; allowed range 1–10 seconds |
| GET | `/api/export` | Allowlisted, redacted aggregate diagnostic JSON |

## Tests and frontend development

The normal unit tests use mocks for process termination and heavy commands. One storage test scans only a tiny temporary directory. No test signals a real user process, runs a bandwidth test or checks Apple updates.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
```

For CSS development and the optional browser smoke test, install the pinned dev dependencies:

```sh
npm ci
npm run build:css
npm test
```

The browser fixture substitutes synthetic telemetry, storage, inventory and command results. It uses port 8000; stop any real dashboard on that port first. In one terminal:

```sh
PYTHONPATH=. .venv/bin/python tests/smoke_server.py
```

In another terminal, with Google Chrome installed:

```sh
npm run test:browser
```

The smoke test uses an isolated headless browser profile and checks local-only loading, session-protected telemetry/settings, search/pagination/groups, modal behavior, inventory search, partial scan results/cancellation, export downloads, unknown update output and narrow layout. Screenshots go to `/private/tmp/sentinel-overview.png` and `/private/tmp/sentinel-mobile.png`. Native hardware behavior is separate from the fixture tests.

The browser fixture may also run on another port: `SENTINEL_PORT=8001 PYTHONPATH=. .venv/bin/python tests/smoke_server.py`, then `SENTINEL_TEST_URL=http://127.0.0.1:8001 npm run test:browser`. This avoids interrupting a real dashboard on port 8000.

### Correlated timeline and watchlists

The overview overlays CPU, GPU and memory percentages with disk read/write MiB/s on a separate axis. The History selector sets the window; Pause freezes the chart time while collection continues. Threshold events in that window are listed below it. Missing samples remain gaps.

Watch up to eight exact application/process names. CPU history and summed RSS percentages are collected in the current browser tab for at most one hour/3,600 samples per name. Names alone persist in local storage; measurements are not persisted or included in aggregate exports. Application groups follow names across PID changes, may combine unrelated same-name processes, and may count shared RSS more than once. Absent processes have unknown chart values, not zero; truncated process lists are identified as partial.

### Performance assessment

The live assessment uses explicit heuristics: CPU/GPU ≥85% for 15 seconds of continuous samples, macOS Warning/Critical memory pressure, and available storage below 10% of capacity. Sampling gaps reset the sustained-load assessment. Stale telemetry clears the assessment and process leaders. Current CPU and summed RSS leaders are contextual evidence, not causal attribution. Disk throughput and allocated swap do not independently trigger slowdown claims. Assessment remains live when charts are paused.

### Storage investigation

Breadcrumbs navigate within configured roots. Reveal opens Finder with that directory selected, through an authenticated POST and the same resolved-path allowlist as scanning. Complete scans compare totals and matching child paths against the last complete baseline (16 directories maximum, server memory only). Partial/cancelled scans do not replace complete baselines; omitted children are not considered deleted. Cached scans retain their original comparison and timestamps; Refresh requests a new scan.

### Collector reliability and overhead

Storage and battery readings refresh on independent single-flight daemon workers, at most every 15 seconds after completion. They no longer block CPU/process/network sampling. A stuck check cannot accumulate queued work; each collector has at most one worker. Shutdown prevents new refreshes, and daemon workers do not delay process exit. Initial readings are unavailable until the first refresh finishes. The diagnostics panel shows reading age, refresh status and last check duration; ages above 45 seconds are labelled stale. Failed refreshes retain the previous reading with its original age.

Monitor overhead reports snapshot wall time, server-process CPU (100% per logical core) and RSS. CPU begins with a warm-up sample. These measurements exclude browser and child-process resource use, and collection duration excludes asynchronous storage/battery work. GPU, memory and process collection still run on the live sampling worker; this change isolates the slower 15-second collectors, not every source.

### Configurable in-app alerts

Configure CPU, GPU and memory usage rules independently in the overview. Thresholds accept 1–100%, with 5–300 seconds of continuous readings strictly above the threshold. Each breach emits one event; a reading at/below threshold, unavailable measurement, or sampling gap rearms tracking. Saving rules resets pending durations and keeps past events. Rules are shared by connected tabs and reset on server restart. These controls affect the event list and timeline, not the separate fixed-rule performance assessment. No desktop notifications are sent.

## Saved sessions and comparisons

Open **Saved sessions and comparisons** to save the current rolling history. Aggregate recording is the default; the application-names checkbox explicitly includes historical top-app rankings. Automatic saving is off initially. Enable it and press **Apply autosave settings** to save every minute and on clean shutdown. The checkbox for application names also controls autosave inclusion. Disabling autosave keeps existing saves; individual saves can be deleted in the panel.

Files live in `.sentinel-data` beside the project (override with `SENTINEL_DATA_DIR`). The directory is created with owner-only permissions, as are JSON files. At most 10 sessions of up to 12 MiB each are retained, replacing the oldest save when full. A running server updates one automatic session file; each restart begins another. Live data remains capped at one hour/3,600 aggregate samples and one hour/1,800 app snapshots. Autosave is a rolling snapshot, not an unlimited recording. Abrupt exits may lose the last minute. Restarts preserve saved files and autosave preferences, not live history, alert rules or sampling preferences.

Select sessions A and B to compare a metric by elapsed time. Means are sample-weighted; sampling rates, missing measurements and unequal recording durations affect comparison. Aggregate exports continue to exclude app names. Saved sessions can contain names only when explicitly included, and never contain process IDs, executable paths or command lines.

## Investigating a spike

Click the live performance timeline, inspect its CPU peak, or enter a local date/time. The panel shows the recorded top five CPU and top five memory application groups nearest that sample, with an explicit error for gaps/expired records. These rankings are sampled evidence, not causal attribution. They use aggregate process CPU (100% per logical core) and summed RSS, which can double-count shared memory. GPU cannot be attributed to applications. Saved sessions support clicking a trace or inspecting session A's peak if app snapshots were included.

## Reducing and measuring overhead

**Slow sampling when idle** is opt-in. After 30 continuous seconds of CPU and GPU both below 15%, normal OS memory pressure, and no pending threshold breach, sampling backs off to at least five seconds. Detected activity restores the selected interval. Brief work between slow samples can be missed. Unknown GPU/pressure or telemetry gaps prevent establishing an idle period. A slower manual interval is preserved.

Diagnostics now break down memory, process and GPU collector wall time. Browser diagnostics show the last telemetry-handler duration, supported long-task counts and optional browser-estimated JS heap. These are partial measurements, not total browser CPU/RAM. Hidden tabs keep recording but skip chart rendering and most DOM updates. Server CPU excludes child processes. No unsupported total-overhead claim is made.

## Launch and reconnect

Double-click **Launch Dashboard.command** in the project to start the local server and open the browser. It reuses a healthy existing server, serializes concurrent launches and refuses an occupied non-dashboard port. It requires the existing `.venv` setup; it does not install dependencies or configure login startup.

The built **dist/Sentinel Menu.app** adds a menu-bar shortcut with **Open Dashboard**, **Show Project Folder**, and **Quit Menu Bar Shortcut**. The server continues running when the shortcut quits. Rebuild after moving the project:

```sh
.venv/bin/python -B desktop/build.py
```

The native shortcut uses Apple's [NSStatusBar API](https://developer.apple.com/documentation/appkit/nsstatusbar/statusitem%28withlength%3A%29) and requires Apple's command-line tools to build. It is a local unsigned build, not a distributable signed installer. No login item is installed. When the server restarts, click **Reload page** in the dashboard connection indicator to obtain a fresh session; saved recordings and browser watch names remain available.

Additional end-to-end checks (fixture server on port 8001):

```sh
SENTINEL_TEST_URL=http://127.0.0.1:8001 npm run test:milestones
```
