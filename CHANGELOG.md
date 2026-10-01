# Changelog

All notable changes to macOS Sentinel are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-10-01

### Added

- Applications tab listing bundles from `/Applications` and `~/Applications` (not `/System`)
- Per-app version, App Store Yes/No, signed status, developer name, Team ID, architecture, and Reveal in Finder
- `GET /api/applications` inventory and `POST /api/applications/reveal` with path checks

## [1.0.0] - 2026-10-01

### Added

- Local FastAPI dashboard with Overview, Processes, Logs, Storage, System Info, and Settings
- About This Mac tile with CoreTypes product icon, chip, memory, startup disk, serial, macOS build, and architecture
- Equal-sized Overview gauges for CPU, memory distribution, storage, and power with padded dividers
- Live Apple Silicon system power draw when `PowerTelemetryData` is available
- Collapsible sidebar navigation and Tools toolbar (network test, updates, uptime, connection, theme toggle)
- Application watchlist with legend visibility preserved across live chart refreshes
- Launch Dashboard.command helper and optional menu-bar shortcut build
- Install and run documentation, including Python 3.13 setup on macOS
- Install troubleshooting for pip upgrade notices, unmet requirement pins, wrong interpreter, and PEP 668
- Homebrew install instructions (Apple Silicon and Intel PATH setup) before `python@3.13`
- MIT license (`LICENSE`)

[1.1.0]: https://github.com/greatkemo/macos-sentinel/releases/tag/v1.1.0
[1.0.0]: https://github.com/greatkemo/macos-sentinel/releases/tag/v1.0.0
