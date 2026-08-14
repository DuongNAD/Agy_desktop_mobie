# Project: AgyMobile Comprehensive Audit, Benchmarking & Hardening

## Architecture Overview
AgyMobile is a high-performance, mobile-first progressive web application (PWA) and asynchronous Python backend designed for remote interaction, terminal emulation, telemetry monitoring, session orchestration, and workspace file management with Google Antigravity.

### Subsystem Boundaries
1. **Security & Authentication Subsystem** (`backend/security.py`, `backend/config.py`):
   - Constant-time PIN validation (`hmac.compare_digest`), HS256 HMAC-SHA256 JWT lifecycle, sliding-window IP rate limiting (5 attempts/min, 300s lockout), path canonicalization, and Windows sandbox boundary defense.
2. **Session Hub Subsystem** (`backend/session_hub/`):
   - Session discovery via read-only SQLite PRAGMAs (`sqlite_reader.py`), async byte-offset JSONL transcript streaming (`tailer.py`), interactive prompt dispatching & Windows process tree termination (`cli_dispatcher.py`), and WebSocket token streaming (`routes.py`).
3. **Terminal Subsystem** (`backend/terminal/pty_session.py`):
   - Async PowerShell PTY session manager, circular output buffer (`deque(maxlen=1000)`), input sanitization, and bi-directional WebSocket streaming (`/ws/terminal`).
4. **Telemetry Subsystem** (`backend/telemetry/monitor.py`):
   - Non-blocking system metrics collector (`psutil` thread offloading), cached hardware snapshots, process table filtering with PID shielding, and periodic WebSocket broadcasting (`/ws/system`).
5. **Workspace File Subsystem** (`backend/workspace/file_manager.py`):
   - Sandboxed tree browsing, MIME type classification, safe file viewing, atomic NTFS file persistence (`NamedTemporaryFile` + `fsync` + `replace` + retry backoff), and line-level diff generation.
6. **Mobile PWA & UX Subsystem** (`frontend/`):
   - Single-page application (SPA), Service Worker precaching (`sw.js`), standalone PWA manifest (`manifest.json`), dynamic visual viewport resizing (`visualViewport`), zero-dependency ANSI VT100/256/TrueColor parser (`ansi_up.js`), and virtual accessory touch bar.

---

## Feature Inventory
| # | Feature | Description | Milestone | Status |
|---|---------|-------------|-----------|--------|
| F01 | PIN Authentication | Constant-time PIN comparison, secure random PIN generation | M3 | DONE |
| F02 | JWT Token Lifecycle | HS256 algorithm enforcement, expiration handling, WS token query | M3 | DONE |
| F03 | IP Rate Limiter | Sliding-window 5 attempts/min, 300s lockout, Retry-After header | M3 | DONE |
| F04 | Workspace Path Sandbox | Rejection of null bytes, UNC paths, NTFS ADS, DOS device names, drive hopping | M3 | DONE |
| F05 | Subprocess Safety | Parameterized execution, safe process tree termination, PID shields | M3 | DONE |
| F06 | Session SQLite Reader | Read-only SQLite PRAGMAs, zero lock contention, header validation | M2 | DONE |
| F07 | Transcript Log Tailer | Byte-offset seeking, partial EOF line buffering, truncation reset | M1 | DONE |
| F08 | Session WebSocket | `/ws/session` token streaming, tool call requests, prompt cancellation | M1 | DONE |
| F09 | PowerShell Terminal | Subprocess PTY session, circular history buffer, interrupt handling | M2 | DONE |
| F10 | Terminal WebSocket | `/ws/terminal` bi-directional stream, resize clamping, input sanitization | M1 | DONE |
| F11 | System Monitor | Non-blocking `psutil` thread offloading, clamped metrics `[0.0, 100.0]` | M1 | DONE |
| F12 | System WebSocket | `/ws/system` periodic cached telemetry broadcast, dead socket pruning | M1 | DONE |
| F13 | Process Management | Protected PID shielding (<=4, self PID), process termination | M3 | DONE |
| F14 | Workspace File Tree | Symlink-safe traversal, depth bounding, hidden file filtering | M2 | DONE |
| F15 | Atomic File Saver | NTFS temp file replacement, `fsync`, conflict-free concurrent write | M2 | DONE |
| F16 | PWA App Shell | Service Worker offline cache, manifest standalone, icon maskable | M4 | DONE |
| F17 | Dynamic Touch Viewport | Mobile keyboard visualViewport resize, safe area insets | M4 | DONE |
| F18 | ANSI Terminal Renderer | VT100/256/TrueColor conversion, HTML entity sanitization, DOM bounding | M4 | DONE |
| F19 | WS Client Multiplexing | Exponential backoff, jitter, ping-pong RTT, `from_offset` state re-sync | M4 | DONE |
| F20 | Comprehensive Audit Report | Automated benchmark suites, 100% pass verification, audit documentation | M5 | DONE |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Concurrency & High-Load Performance | Optimize WS handlers, JSONL chunk streaming, telemetry cache/psutil worker offloading, load benchmarks | None | DONE |
| M2 | Fault Tolerance & Chaos Resilience | Chaos testing, disconnect/reconnect cleanup, malformed JSONL recovery, Windows file locks, process tree kills | M1 | DONE |
| M3 | Security Sandbox & Authentication | Constant-time PIN, JWT HS256 whitelisting, IP rate limiting, path sandbox traversal, safe subprocess execution | None | DONE |
| M4 | Mobile PWA & Terminal UX Stability | ANSI VT100 performance, visualViewport touch handling, PWA offline caching, reconnect UX | M1 | DONE |
| M5 | Comprehensive Audit & Final Verification | 100% test pass verification (Pytest, adversarial, chaos, stress), performance metrics report, final audit document | M1, M2, M3, M4 | DONE |

---

## Interface Contracts

### Session WebSocket (`/ws/session`)
- **Query Params**: `token=<jwt_token>`, `conversation_id=<uuid>`, `from_offset=<int>`
- **Client Messages**: `{"action": "subscribe" | "unsubscribe" | "send_prompt" | "tool_decision" | "cancel_prompt", ...}`
- **Server Events**: `{"type": "token_stream" | "tool_call_request" | "step_update" | "subagent_status" | "error", ...}`

### Terminal WebSocket (`/ws/terminal`)
- **Query Params**: `token=<jwt_token>`
- **Client Messages**: `{"action": "input" | "resize" | "interrupt" | "clear" | "restart" | "ping", ...}`
- **Server Events**: `{"type": "output" | "history" | "pong" | "error", "data": ...}`

### Telemetry WebSocket (`/ws/system`)
- **Query Params**: `token=<jwt_token>`
- **Server Events**: `{"type": "snapshot", "cpu_percent": float, "memory_percent": float, "network": {...}, ...}`

---

## Code Layout
```
e:/Project/Agy_desktop_mobie/
├── backend/
│   ├── config.py                 # Configuration settings
│   ├── main.py                   # FastAPI/Starlette app & route mounting
│   ├── security.py               # Auth, JWT, Rate Limiting, Path Sandbox
│   ├── session_hub/              # Session, SQLite reader, JSONL tailer, CLI dispatcher
│   │   ├── cli_dispatcher.py
│   │   ├── routes.py
│   │   ├── sqlite_reader.py
│   │   └── tailer.py
│   ├── telemetry/                # Hardware & process monitoring
│   │   └── monitor.py
│   ├── terminal/                 # PowerShell PTY session
│   │   └── pty_session.py
│   └── workspace/                # File management, sandboxed tree, atomic saver
│       └── file_manager.py
├── frontend/                     # PWA HTML, CSS, JS, Service Worker
│   ├── index.html
│   ├── manifest.json
│   ├── sw.js
│   ├── css/
│   │   ├── app.css
│   │   └── terminal.css
│   └── js/
│       ├── app.js
│       ├── ws.js
│       ├── components/
│       └── lib/
└── tests/                        # Comprehensive test & benchmark suites
    ├── test_api.py
    ├── test_security.py
    ├── test_session_hub.py
    ├── test_terminal.py
    ├── test_telemetry.py
    ├── test_workspace.py
    ├── test_websockets.py
    ├── test_static_pwa.py
    ├── test_adversarial_*.py
    └── test_frontend_adversarial_suite.mjs
```
