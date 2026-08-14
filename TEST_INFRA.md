# E2E Test Infra: AgyMobile

## Test Philosophy
- Opaque-box, requirement-driven testing covering functional contracts, edge-case chaos, Windows concurrency, security isolation, and mobile UX.
- Methodology: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial Testing + Real-World Workload Testing + Adversarial Hardening.

## Feature Inventory & Test Mapping
| # | Feature | Requirement | Tier 1 (Functional) | Tier 2 (Boundary) | Tier 3 (Pairwise) | Tier 4 (Workload) |
|---|---------|-------------|:-------------------:|:-----------------:|:-----------------:|:-----------------:|
| 1 | F01 PIN Auth | R3 | 5 | 5 | ✓ | ✓ |
| 2 | F02 JWT Lifecycle | R3 | 5 | 5 | ✓ | ✓ |
| 3 | F03 IP Rate Limiter | R3 | 5 | 5 | ✓ | ✓ |
| 4 | F04 Workspace Sandbox | R3 | 5 | 5 | ✓ | ✓ |
| 5 | F05 Subprocess Safety | R3 | 5 | 5 | ✓ | ✓ |
| 6 | F06 SQLite Reader | R2 | 5 | 5 | ✓ | ✓ |
| 7 | F07 Transcript Tailer | R1, R2 | 5 | 5 | ✓ | ✓ |
| 8 | F08 Session WebSocket | R1, R2 | 5 | 5 | ✓ | ✓ |
| 9 | F09 Terminal PTY | R2, R4 | 5 | 5 | ✓ | ✓ |
| 10 | F10 Terminal WebSocket | R1, R4 | 5 | 5 | ✓ | ✓ |
| 11 | F11 System Monitor | R1 | 5 | 5 | ✓ | ✓ |
| 12 | F12 System WebSocket | R1 | 5 | 5 | ✓ | ✓ |
| 13 | F13 Process Management | R3 | 5 | 5 | ✓ | ✓ |
| 14 | F14 File Tree | R2, R3 | 5 | 5 | ✓ | ✓ |
| 15 | F15 Atomic File Saver | R2 | 5 | 5 | ✓ | ✓ |
| 16 | F16 PWA App Shell | R4 | 5 | 5 | ✓ | ✓ |
| 17 | F17 Dynamic Viewport | R4 | 5 | 5 | ✓ | ✓ |
| 18 | F18 ANSI VT100 Renderer | R4 | 5 | 5 | ✓ | ✓ |
| 19 | F19 WS Multiplexing | R1, R4 | 5 | 5 | ✓ | ✓ |
| 20 | F20 Audit & Benchmarks | R5 | 5 | 5 | ✓ | ✓ |

## Test Execution Architecture
- **Pytest Full Suite**:
  ```powershell
  py -3.11 -m pytest tests/ -v
  ```
- **Hermetic Node.js Frontend Suite**:
  ```powershell
  node tests/test_frontend_adversarial_suite.mjs
  ```
- **Targeted Subsystem Suites**:
  - Security & Sandbox: `pytest tests/test_security.py tests/test_adversarial_tier5_hardening.py -v`
  - Concurrency & WebSockets: `pytest tests/test_websockets.py tests/test_session_hub.py -v`
  - Terminal & Subprocesses: `pytest tests/test_terminal.py -v`
  - Workspace & Atomic Storage: `pytest tests/test_workspace.py tests/test_adversarial_m3_workspace.py -v`
  - PWA & Mobile UX: `pytest tests/test_static_pwa.py tests/test_adversarial_m4_frontend.py tests/test_adversarial_pwa.py -v`
