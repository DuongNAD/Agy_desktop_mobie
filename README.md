# ⚡ AgyMobile — Antigravity 2.0 & Desktop Workspace Mobile Controller

> High-performance, secure, remote Progressive Web App (PWA) controller and live telemetry dashboard for Google Antigravity 2.0 and Windows desktop workspaces.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com)
[![PWA Ready](https://img.shields.io/badge/PWA-iOS%20%7C%20Android-purple.svg)](https://web.dev/progressive-web-apps/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 751/751 Passing](https://img.shields.io/badge/Tests-751%20Passing%20(100%25)-brightgreen.svg)](tests/)
[![Audit: Victory Confirmed](https://img.shields.io/badge/Audit-Victory%20Confirmed-success.svg)](COMPREHENSIVE_AUDIT_REPORT.md)

---

## 📑 Table of Contents

1. [Overview & Key Highlights](#-overview--key-highlights)
2. [System Architecture & Topology](#-system-architecture--topology)
3. [Feature Inventory (F01 – F20)](#-feature-inventory-f01--f20)
4. [Quick Start](#-quick-start)
5. [Mobile PWA Installation Guide (iOS / Android)](#-mobile-pwa-installation-guide)
6. [Security & Sandbox Architecture](#-security--sandbox-architecture)
7. [Performance & Concurrency Benchmarks](#-performance--concurrency-benchmarks)
8. [API & WebSocket Specifications](#-api--websocket-specifications)
9. [Internet Remote Access & Tunneling Guide](#-internet-remote-access--tunneling-guide)
10. [5-Tier Automated Testing Guide](#-5-tier-automated-testing-guide)
11. [Configuration Reference](#-configuration-reference)
12. [Troubleshooting & FAQ](#-troubleshooting--faq)
13. [License](#-license)

---

## 🌟 Overview & Key Highlights

**AgyMobile** transforms any modern smartphone or tablet (iPhone, iPad, Android phone/tablet) into an intuitive, touch-optimized remote command center for **Google Antigravity 2.0** and your Windows development environment:

- 💬 **Live Antigravity 2.0 Session Hub**: Real-time transcript streaming, collapsible thought accordions, interactive multi-agent hierarchy tree, and remote prompt / slash-command dispatching (`/goal`, `/schedule`, `/teamwork-preview`).
- 💻 **Mobile Touch Terminal**: Interactive Windows PowerShell console with full ANSI 256-color & 24-bit TrueColor rendering, 1,000-line circular scrollback buffer, and mobile accessory toolbar (`ESC`, `CTRL`, `TAB`, `CTRL+C`, `▲▼◀▶`, `CLEAR`).
- 📁 **Workspace File Explorer & Visual Diff**: Browse directory trees, search files, view syntax-highlighted code, perform touch-friendly code edits, and review side-by-side / inline diffs with atomic saves inside the workspace sandbox.
- 📊 **System Telemetry Dashboard**: Real-time circular SVG gauges for CPU (aggregate & per-core), RAM, Disk, Network I/O, Uptime, and task manager with one-tap process termination.
- ⚡ **Zero-Friction Pairing**: High-contrast terminal ASCII QR code on desktop startup + in-browser mobile camera QR scanner for instant 1-second authenticated login.
- 📱 **Mobile-First PWA (100dvh)**: Fullscreen responsive app shell, iOS Safari Dynamic Island / Notch safe-area inset handling, Cache-First Service Worker, and offline resiliency.

---

## 🏗 System Architecture & Topology

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Mobile Client (iOS Safari / Android Chrome)          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Progressive Web App (PWA) / Responsive Touch UI (100dvh)         │  │
│  │ ├─ Antigravity Live Dashboard (Streaming, Thoughts, Subagents)   │  │
│  │ ├─ File Explorer & Mobile Editor / Diff Viewer                   │  │
│  │ ├─ Touch Terminal with VT100 ANSI & Virtual Keys Toolbar         │  │
│  │ ├─ System Telemetry Monitor (CPU, RAM, Disk, Background Tasks)   │  │
│  │ └─ QR Camera Scanner & 6-Digit PIN Auth Keypad                   │  │
│  └─────────────────────────────────┬────────────────────────────────┘  │
└────────────────────────────────────┼───────────────────────────────────┘
                                     │ WebSocket / SSE / HTTPS (LAN or Tunnel)
┌────────────────────────────────────▼───────────────────────────────────┐
│                    AgyMobile Backend Server (FastAPI + AsyncIO)         │
│  ┌──────────────────────────────┬───────────────────────────────────┐  │
│  │ Security & Auth Layer        │ Realtime Multiplexer (WebSockets) │  │
│  │ ├─ PIN/JWT Token Manager     │ ├─ /ws/session (Chat/Stream/Tool) │  │
│  │ ├─ LAN IP Auto-Discovery     │ ├─ /ws/terminal (PowerShell PTY)  │  │
│  │ ├─ QR Code Generator (PNG/SVG)│ ├─ /ws/system (Telemetry stats)   │  │
│  │ └─ Workspace Path Sandbox    │ └─ SSE Fallback Streamer          │  │
│  ├──────────────────────────────┼───────────────────────────────────┤  │
│  │ Antigravity Session Engine   │ Desktop Workspace Controller      │  │
│  │ ├─ SQLite DB Reader (RO)     │ ├─ Async PowerShell Subprocess    │  │
│  │ ├─ Transcript TailerEngine   │ ├─ File System Explorer & Diff    │  │
│  │ ├─ Artifacts Synchronizer    │ ├─ PSUtil Telemetry Poller        │  │
│  │ └─ CLI Dispatch & Tool Grant │ └─ Tunneling Manager (Cloudflare) │  │
│  └──────────────────────────────┴───────────────────────────────────┘  │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │ Local IPC / File Watcher / CLI
┌────────────────────────────────────▼───────────────────────────────────┐
│                       Host Environment (Windows Desktop)               │
│  ├─ Antigravity 2.0 Core (~/.gemini/antigravity, CLI: agy.EXE)         │
│  ├─ Workspace File System (e:/Project/...)                             │
│  └─ Windows System Metrics & PowerShell 7/5.1                          │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Feature Inventory (F01 – F20)

| # | Feature | Subsystem | Description |
|---|---|---|---|
| **F01** | Realtime Conversation List | Session Hub | Live enumeration of active Antigravity conversations, step counts, and statuses from SQLite. |
| **F02** | Live Transcript Streaming | Session Hub | Low-latency tailing of `transcript.jsonl` with collapsible thinking accordions and token streaming. |
| **F03** | Subagent Hierarchy Tree | Session Hub | Interactive visualization of parent-child subagent trees with live status indicators. |
| **F04** | Remote Prompt & Slash Commands | Session Hub | Dispatch prompts and trigger slash commands (`/goal`, `/schedule`, `/teamwork-preview`) via CLI. |
| **F05** | Remote Tool Approvals | Session Hub | Interactive tool call approval cards; update permission grant policies on the fly. |
| **F06** | Artifacts & Plans Viewer | Session Hub | Live synchronization and Markdown rendering of plans (`task.md`), specs, and code diffs. |
| **F07** | Mobile File Explorer | Workspace Controller | Fast tree navigation, file viewing, and mobile touch code editor within the project sandbox. |
| **F08** | Fast File Diff Viewer | Workspace Controller | Side-by-side / inline visual diff highlighting for workspace modifications before saving. |
| **F09** | Mobile Web Terminal | Terminal Subsystem | Bi-directional interactive Windows PowerShell console with full ANSI VT100 colors. |
| **F10** | Virtual Touch Utility Keys | Terminal Subsystem | Mobile accessory toolbar with `ESC`, `CTRL`, `TAB`, `CTRL+C`, arrow keys, and screen clear. |
| **F11** | System Telemetry Monitor | Telemetry Subsystem | Real-time circular SVG gauges for CPU, RAM, Disk, Uptime, and task manager with kill actions. |
| **F12** | LAN IP Auto-Discovery | Network & Security | Automatic detection of local IPv4 address across multi-adapter environments. |
| **F13** | Dual-Mode QR Code Generator | Network & Security | High-contrast terminal ASCII QR code on startup + REST endpoint for PNG/SVG QR pairing codes. |
| **F14** | PIN & JWT Authentication | Security Subsystem | Constant-time 6-digit PIN verification, HMAC-SHA256 JWT tokens, and IP brute-force protection. |
| **F15** | Tunneling Integration | Network Subsystem | Native configuration guides and status hooks for Cloudflare Tunnel, Tailscale, and Ngrok. |
| **F16** | Mobile PWA & Touch Layout | Frontend Shell | Responsive `100dvh` layout, CSS safe-area insets, bottom navigation, and Service Worker caching. |
| **F17** | In-App QR Camera Scanner | Frontend Shell | In-browser mobile camera scanner to scan desktop QR code for 1-second authenticated login. |
| **F18** | Comprehensive Test Suite | Quality Assurance | 5-tier test matrix (751 test cases across 25 test suites) verifying 100% pass rate. |
| **F19** | 1-Click Startup Automation | Automation | Production Windows batch scripts (`start.bat`, `run.bat`) with auto-install and browser launch. |
| **F20** | Full Documentation | Documentation | Complete architectural documentation, API contracts, setup guides, and troubleshooting manual. |

---

## 🚀 Quick Start

### Option A: 1-Click Startup on Windows (Recommended)

Double-click `start.bat` in the project root or run from Command Prompt / PowerShell:

```cmd
start.bat
```

**`start.bat` automatically handles:**
1. Checks for Python 3.11+ or Astral `uv`.
2. Creates and manages the `.venv` virtual environment.
3. Automatically installs or updates dependencies (`fastapi`, `uvicorn`, `psutil`, `qrcode`, `pyjwt`).
4. Detects LAN IP and renders a high-contrast Unicode QR code in the terminal.
5. Launches the default desktop browser to `http://localhost:8765`.
6. Starts the Uvicorn ASGI server on `0.0.0.0:8765`.

#### Optional Flags for `start.bat`
```cmd
start.bat --no-browser      :: Start server without opening default browser
start.bat --install         :: Force reinstallation of project dependencies
start.bat --port 8888       :: Run server on a custom port
```

---

### Option B: Fast Runner (Instant Developer Launch)

For daily development without dependency checks (< 0.05s startup):

```cmd
run.bat
```

---

### Option C: Manual Setup via uv / pip

```bash
# 1. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate

# 2. Install package in editable mode
pip install -e .

# 3. Launch server
python -m backend.main
```

---

## 📱 Mobile PWA Installation Guide

AgyMobile is designed as a Progressive Web App (PWA) with native mobile ergonomics, standalone display mode, and offline asset caching.

### Step 1: Connect to Server
- Ensure your mobile phone is connected to the same Wi-Fi network as your PC (or use [Tunneling](#-internet-remote-access--tunneling-guide)).
- Scan the ASCII QR code displayed in your desktop terminal, or navigate to `http://<LAN_IP>:8765/?token=<TOKEN>`.

### Step 2: Add to Home Screen

#### iOS Safari (iPhone / iPad)
1. Open the URL in **Safari**.
2. Tap the **Share** icon (square with arrow pointing up) on the bottom toolbar.
3. Scroll down and tap **"Add to Home Screen"** (+ icon).
4. Tap **"Add"** in the top-right corner.
5. Launch **AgyMobile** from your home screen — it will open in standalone fullscreen mode without Safari browser chrome!

#### Android Chrome
1. Open the URL in **Google Chrome**.
2. Tap the **Menu** (three dots in top-right corner).
3. Tap **"Install app"** or **"Add to Home screen"**.
4. Confirm installation. The AgyMobile icon will appear in your app drawer and home screen.

### Step 3: In-App QR Camera Pairing
If you ever get logged out, open the login modal and tap **"Scan QR Code"**. Grant camera permission to point your phone camera at the desktop terminal and log in instantly without typing PINs!

---

## 🔐 Security & Sandbox Architecture

AgyMobile implements defense-in-depth security engineered for local network and tunneling scenarios:

### 1. Constant-Time PIN Verification
- 6-digit numeric PIN configured via `AGY_AUTH_PIN` or `Settings.AUTH_PIN` (default `123456`).
- Verified using `hmac.compare_digest` to eliminate timing side-channel attacks.

### 2. Cryptographic JWT Access Tokens
- Signed with HMAC-SHA256 using `JWT_SECRET`.
- Configurable expiration (default: 86400s / 24 hours).
- Transmitted via `Authorization: Bearer <token>` HTTP header or `?token=...` query parameter for WebSockets.

### 3. In-Memory Anti-Brute-Force Rate Limiter
- Tracks failed login attempts per client IP address using a sliding window.
- 5 consecutive failures within 60 seconds triggers a **300-second (5-minute) lockout** (`HTTP 429 Retry-After: 300`).

### 4. Windows-Hardened Workspace Path Sandbox
- Enforces strict canonical path containment inside `WORKSPACE_ROOT`.
- Prevents directory traversal (`../`, `%252e%252e`), null-byte injection, and symlink escapes.
- Blocks Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`, `CONIN$`, `CONOUT$`) and NTFS Alternate Data Streams (`:`).

### 5. Safe Subprocess Execution
- Subprocesses executed via parameterized argument lists (`asyncio.create_subprocess_exec`, no `shell=True`).
- Safe process tree termination (`taskkill /F /T`) with protected PID guards (blocks termination of PID 0, PID 4 System, and server PID).

---

## ⚡ Performance & Concurrency Benchmarks

Empirical metrics from the [Comprehensive Audit Report](COMPREHENSIVE_AUDIT_REPORT.md):

| Metric / Subsystem | Measured Value | SLA / Requirement | Status |
|---|---|---|---|
| **Telemetry API Latency** (`/api/system/metrics`) | **< 0.05 ms** (In-memory cached snapshot) | < 50 ms | **EXCEEDED** |
| **Telemetry Polling Lag** | Offloaded to worker threads via `asyncio.to_thread` | Zero event loop lag | **PASS** |
| **WebSocket Throughput** (`/ws/*`) | **> 10,000 msg/sec** burst capacity | High throughput | **PASS** |
| **Terminal Backpressure** | Oldest-chunk eviction on `QueueFull` (capped at 1,000 chunks) | Bounded memory | **PASS** |
| **Long-Running Memory Drift** | **< 50 KB** drift across 1,000 rapid connect/broadcast cycles | Zero memory leaks | **VERIFIED** |
| **Windows File Lock Collision Resilience** | 5-attempt retry loop with exponential backoff on `WinError 32` | Zero write failures | **PASS** |
| **SQLite Read-Only Concurrency** | `PRAGMA query_only = ON` + `busy_timeout = 3000ms` | Zero lock deadlocks | **PASS** |

---

## 📡 API & WebSocket Specifications

### 1. REST API Endpoints

#### Authentication
- `POST /api/auth/login` — Body: `{"pin": "123456"}` → Response: `{"token": "jwt...", "expires_in": 86400, "status": "authenticated"}`
- `GET /api/auth/qr` — Query: `?format=png` (`svg`, `ascii`, `json`) → Response: QR code image bytes or pairing JSON.
- `GET /api/auth/verify` — Header: `Authorization: Bearer <token>` → Response: `{"valid": true, "user": "admin"}`

#### System Telemetry (F11)
- `GET /api/system/info` — Basic system telemetry snapshot (CPU %, RAM %, Disk %, Uptime, LAN IP).
- `GET /api/system/metrics` — Granular hardware breakdown (per-core CPU, RAM bytes, partition list, network I/O).
- `GET /api/system/processes?limit=20&sort_by=cpu` — Top active desktop processes.
- `POST /api/system/tasks/{pid}/kill` — Terminate a background task with protected PID safeguards.

#### Antigravity Session Hub (F01–F06)
- `GET /api/conversations?limit=50` — List active Antigravity conversations from SQLite.
- `GET /api/conversations/{id}/transcript?from_offset=0` — Fetch transcript steps and artifacts.
- `POST /api/conversations/{id}/prompt` — Dispatch prompts or slash commands to Antigravity CLI.
- `POST /api/conversations/{id}/tool-approval` — Submit tool approval or rejection decisions.
- `GET /api/conversations/{id}/artifacts` — List markdown plans and artifacts.
- `GET /api/conversations/{id}/subagents` — Nested subagent hierarchy tree.

#### Workspace File Manager (F07–F08)
- `GET /api/files/tree?path=src&depth=1` — Sandboxed directory tree enumeration.
- `GET /api/files/content?path=src/main.py` — Read file content with UTF-8 encoding.
- `POST /api/files/save` — Atomic file save (`.tmp` swap with retry backoff).
- `POST /api/files/diff` — Generate unified diff between existing file and new content.

---

### 2. Real-Time WebSocket Channels

#### A. Session Channel (`/ws/session?token=<token>`)
- **Inbound Actions**:
  - `{"action": "subscribe", "conversation_id": "<uuid>", "from_offset": 0}`
  - `{"action": "send_prompt", "conversation_id": "<uuid>", "prompt": "...", "mode": "accept-edits"}`
  - `{"action": "tool_decision", "conversation_id": "<uuid>", "tool_call_id": "...", "decision": "approve"|"reject"}`
- **Outbound Events**:
  - `{"type": "step_update", "conversation_id": "...", "step": {...}}`
  - `{"type": "token_stream", "conversation_id": "...", "delta": "...", "kind": "thinking"|"content"}`
  - `{"type": "tool_call_request", "conversation_id": "...", "tool_call": {...}}`
  - `{"type": "subagent_status", "conversation_id": "...", "hierarchy": [...]}`

#### B. Terminal Channel (`/ws/terminal?token=<token>`)
- **Inbound Actions**:
  - `{"type": "input", "data": "Get-Process\r"}`
  - `{"type": "resize", "cols": 80, "rows": 24}`
- **Outbound Events**:
  - `{"type": "output", "data": "\x1b[32mPS E:\\Project>\x1b[0m "}`

#### C. System Telemetry Channel (`/ws/system?token=<token>`)
- **Outbound Broadcast**:
  - Periodic frames (every 2.0s):
    `{"type": "telemetry", "cpu": 14.5, "ram": 38.9, "disk": 45.0, "uptime": 3600.0, "tasks": [...]}`

---

## 🌐 Internet Remote Access & Tunneling Guide

Access AgyMobile securely over the Internet without port forwarding:

### 1. Cloudflare Tunnel (Zero Trust — Recommended)
```bash
# 1. Install cloudflared on Windows
winget install Cloudflare.cloudflared

# 2. Start a quick tunnel to port 8765
cloudflared tunnel --url http://localhost:8765
```
Cloudflare will output a public HTTPS URL (e.g. `https://random-id.trycloudflare.com`). Open this URL on your phone for immediate encrypted access with valid SSL!

### 2. Tailscale (Mesh VPN)
1. Install Tailscale on your desktop PC and mobile device.
2. Join both devices to your private Tailnet.
3. Access AgyMobile directly at `http://<desktop-tailscale-ip>:8765`.

### 3. Ngrok
```bash
ngrok http 8765
```

---

## 🧪 5-Tier Automated Testing Guide

AgyMobile includes an exhaustive 5-tier test matrix with **751 automated tests (724 Pytest backend & adversarial test cases + 27 Node.js hermetic frontend tests)** achieving a **100% pass rate**:

```powershell
# 1. Run all 724 backend & integration tests (Pytest)
py -3.11 -m pytest tests/ -v

# 2. Run hermetic Node.js ANSI / PWA test suite (27 tests)
node tests/test_frontend_adversarial_suite.mjs

# 3. Targeted test suites:
# Security & Sandbox Hardening (F01–F05)
py -3.11 -m pytest tests/test_security.py tests/test_adversarial_tier5_hardening.py -v

# Concurrency & Stress Benchmarks
py -3.11 -m pytest tests/test_adversarial_stress.py tests/test_adversarial_terminal_stress.py -v
```

---

## ⚙ Configuration Reference

Configuration can be set via environment variables or `.env` file:

| Variable | Default | Description |
|---|---|---|
| `AGY_AUTH_PIN` / `AUTH_PIN` | `123456` | 6-digit numeric PIN for mobile authentication |
| `JWT_SECRET_KEY` / `JWT_SECRET` | auto-generated | Secret key for signing JWT tokens |
| `PORT` | `8765` | Server port |
| `HOST` | `0.0.0.0` | Server bind host address |
| `WORKSPACE_ROOT` | current directory | Target workspace root for File Explorer |
| `ANTIGRAVITY_DIR` | `~/.gemini/antigravity` | Path to Antigravity 2.0 configuration folder |
| `TOKEN_EXPIRY_SECONDS` | `86400` (24h) | JWT access token validity period |
| `RATE_LIMIT_MAX_ATTEMPTS` | `5` | Failed attempts before IP lockout |
| `RATE_LIMIT_LOCKOUT_SECONDS` | `300` (5m) | Duration of IP lockout |

---

## ❓ Troubleshooting & FAQ

### Q: Windows Defender Firewall blocks incoming mobile connections on LAN.
**Solution**: Run PowerShell as Administrator and add an inbound firewall rule:
```powershell
New-NetFirewallRule -DisplayName "AgyMobile Server" -Direction Inbound -LocalPort 8765 -Protocol TCP -Action Allow
```

### Q: In-app camera QR scanner is not working on mobile browser.
**Solution**: Mobile browsers require **HTTPS** or `localhost` to access camera hardware. When connecting over raw LAN IP (`http://192.168.x.x`), use your phone's native Camera app to scan the terminal ASCII QR code, or use a Cloudflare HTTPS tunnel.

### Q: How do I change the 6-digit authentication PIN?
**Solution**: Set the `AGY_AUTH_PIN` environment variable before starting:
```cmd
set AGY_AUTH_PIN=987654
start.bat
```

---

## 📄 License

MIT License — Copyright (c) 2026 AgyMobile Contributors.
