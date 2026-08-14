# ⚡ AgyMobile — Antigravity 2.0 & Desktop Workspace Mobile Controller

> High-performance, secure, remote Progressive Web App (PWA) controller and live telemetry dashboard for Google Antigravity 2.0 and Windows desktop workspaces.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com)
[![PWA Ready](https://img.shields.io/badge/PWA-iOS%20%7C%20Android-purple.svg)](https://web.dev/progressive-web-apps/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 751/751 Passing](https://img.shields.io/badge/Tests-751%20Passing%20(100%25)-brightgreen.svg)](tests/)
[![Audit: Victory Confirmed](https://img.shields.io/badge/Audit-Victory%20Confirmed-success.svg)](COMPREHENSIVE_AUDIT_REPORT.md)

---

## 📑 Mục lục / Table of Contents

1. [Tổng quan dự án / Overview](#-tổng-quan-dự-án--overview)
2. [Kiến trúc hệ thống / Architecture & Topology](#-kiến-trúc-hệ-thống--architecture--topology)
3. [Danh mục tính năng / Feature Matrix (F01 – F20)](#-danh-mục-tính-năng--feature-matrix-f01--f20)
4. [Hướng dẫn khởi chạy / Quick Start](#-hướng-dẫn-khởi-chạy--quick-start)
5. [Cài đặt PWA trên điện thoại / Mobile PWA Setup](#-cài-đặt-pwa-trên-điện-thoại--mobile-pwa-setup)
6. [Mô hình bảo mật & Sandbox / Security Model](#-mô-hình-bảo-mật--sandbox--security-model)
7. [Khả năng chịu tải & Chịu lỗi / Benchmarks & Fault Tolerance](#-khả-năng-chịu-tải--chịu-lỗi--benchmarks--fault-tolerance)
8. [Tài liệu API & WebSocket / API Reference](#-tài-liệu-api--websocket--api-reference)
9. [Truy cập từ xa qua Internet / Remote Tunneling Guide](#-truy-cập-từ-xa-qua-internet--remote-tunneling-guide)
10. [Hướng dẫn kiểm thử tự động / Automated Testing Guide](#-hướng-dẫn-kiểm-thử-tự-động--automated-testing-guide)
11. [Cấu hình môi trường / Configuration Reference](#-cấu-hình-môi-trường--configuration-reference)
12. [Khắc phục sự cố / Troubleshooting & FAQ](#-khắc-phục-sự-cố--troubleshooting--faq)

---

## 🌟 Tổng quan dự án / Overview

**AgyMobile** biến thiết bị di động (iPhone, iPad, Android) thành trung tâm điều khiển từ xa mượt mà, trực quan cho **Google Antigravity 2.0** và máy tính Windows của bạn:

- 💬 **Live Antigravity 2.0 Session Hub**: Stream transcript thời gian thực, hiển thị khối suy nghĩ (thoughts) có thể đóng mở, cây phân cấp Subagent đa tầng, và gửi lệnh / slash command (`/goal`, `/schedule`, `/teamwork-preview`).
- 💻 **Mobile Touch Terminal (PowerShell)**: Terminal tương tác thời gian thực với bộ dựng màu ANSI 256 / 24-bit TrueColor, bộ đệm cuộn 1,000 dòng và thanh phím ảo trợ năng (`ESC`, `CTRL`, `TAB`, `CTRL+C`, `▲▼◀▶`, `CLEAR`).
- 📁 **Mobile File Explorer & Visual Diff**: Duyệt cây thư mục, tìm kiếm, xem code highlight cú pháp, chỉnh sửa mã nguồn và xem diff trực quan với cơ chế lưu nguyên tử (atomic save) an toàn trong sandbox.
- 📊 **System Telemetry Dashboard**: Đồng hồ đo SVG thời gian thực cho CPU, RAM, Disk, Uptime, và trình quản lý tác vụ nền cho phép kill process an toàn.
- ⚡ **Ghép nối 1 chạm không cần cấu hình**: Mã QR ASCII độ tương phản cao ngay trên Terminal Desktop + Trình quét mã QR Camera trực tiếp trên trình duyệt điện thoại.
- 📱 **Chuẩn PWA 100dvh**: Giao diện responsive vừa vặn màn hình di động, hỗ trợ Dynamic Island/Notch trên iOS Safari, Service Worker offline caching.

---

## 🏗 Kiến trúc hệ thống / Architecture & Topology

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

## 📦 Danh mục tính năng / Feature Matrix (F01 – F20)

| # | Tính năng | Phân hệ | Mô tả chi tiết |
|---|---|---|---|
| **F01** | Realtime Conversation List | Session Hub | Liệt kê các phiên trò chuyện Antigravity từ SQLite kèm số bước và trạng thái. |
| **F02** | Live Transcript Streaming | Session Hub | Tailing `transcript.jsonl` độ trễ thấp, hỗ trợ đóng mở khối suy nghĩ. |
| **F03** | Subagent Hierarchy Tree | Session Hub | Trực quan hóa cây quan hệ giữa Parent Agent và các Subagent theo thời gian thực. |
| **F04** | Remote Prompt & Slash Commands | Session Hub | Gửi câu lệnh và kích hoạt slash command (`/goal`, `/schedule`, `/teamwork-preview`). |
| **F05** | Remote Tool Approvals | Session Hub | Thẻ duyệt/từ chối gọi tool tương tác trực tiếp từ điện thoại. |
| **F06** | Artifacts & Plans Viewer | Session Hub | Đồng bộ và hiển thị kế hoạch (`task.md`), specs và code artifacts dạng Markdown. |
| **F07** | Mobile File Explorer | Workspace Controller | Điều hướng cây thư mục, xem và chỉnh sửa file trong sandbox mã nguồn. |
| **F08** | Fast File Diff Viewer | Workspace Controller | Xem so sánh khác biệt (diff) trực quan trước khi lưu file. |
| **F09** | Mobile Web Terminal | Terminal Subsystem | Terminal PowerShell tương tác 2 chiều với đầy đủ màu sắc ANSI VT100. |
| **F10** | Virtual Touch Utility Keys | Terminal Subsystem | Thanh phím ảo hỗ trợ `ESC`, `CTRL`, `TAB`, `CTRL+C`, phím mũi tên và xóa màn hình. |
| **F11** | System Telemetry Monitor | Telemetry Subsystem | Giám sát phần cứng CPU, RAM, Disk, Uptime và quản lý danh sách tiến trình. |
| **F12** | LAN IP Auto-Discovery | Network & Security | Tự động phát hiện địa chỉ IPv4 cục bộ trên các card mạng LAN/Wi-Fi. |
| **F13** | Dual-Mode QR Generator | Network & Security | Mã QR ASCII ngay trên terminal khởi động + REST API xuất ảnh QR PNG/SVG. |
| **F14** | PIN & JWT Authentication | Security Subsystem | Xác thực PIN 6 số với `compare_digest` chống Timing Attack, token JWT HS256. |
| **F15** | Tunneling Integration | Network Subsystem | Hỗ trợ cấu hình Cloudflare Tunnel, Tailscale và Ngrok để truy cập từ xa. |
| **F16** | Mobile PWA & Touch Layout | Frontend Shell | Layout `100dvh`, CSS safe-area, Service Worker cache-first cho ứng dụng PWA. |
| **F17** | In-App QR Camera Scanner | Frontend Shell | Quét mã QR desktop bằng camera điện thoại để đăng nhập trong 1 giây. |
| **F18** | Comprehensive Test Suite | Quality Assurance | Ma trận kiểm thử 5 cấp độ (751 bài test) đạt tỉ lệ pass 100%. |
| **F19** | 1-Click Startup Automation | Automation | Script batch 1 chạm (`start.bat`, `run.bat`) tự cài thư viện và bật trình duyệt. |
| **F20** | Full Documentation | Documentation | Tài liệu kiến trúc, đặc tả API, hướng dẫn kiểm thử và cẩm nang bảo mật. |

---

## 🚀 Hướng dẫn khởi chạy / Quick Start

### Cách 1: Khởi chạy 1 chạm trên Windows (Khuyên dùng)

Chỉ cần nhấp đúp vào `start.bat` hoặc chạy từ PowerShell / Command Prompt:

```cmd
start.bat
```

**`start.bat` tự động thực hiện:**
1. Kiểm tra môi trường Python 3.11+ hoặc `uv`.
2. Tạo và cấu hình môi trường ảo `.venv`.
3. Cài đặt / cập nhật các gói phụ thuộc cần thiết.
4. Tự phát hiện địa chỉ IP LAN và vẽ mã QR ASCII trên terminal.
5. Tự mở trình duyệt tới `http://localhost:8765`.
6. Khởi chạy máy chủ FastAPI Uvicorn trên cổng `8765`.

#### Các tùy chọn nâng cao cho `start.bat`
```cmd
start.bat --no-browser      :: Chạy server không tự mở trình duyệt
start.bat --install         :: Ép cài đặt lại toàn bộ dependencies
start.bat --port 8888       :: Đổi cổng khởi chạy sang 8888
```

---

### Cách 2: Khởi chạy siêu tốc cho lập trình viên (`run.bat`)

Khởi động tức thì trong `< 0.05s` (bỏ qua bước kiểm tra cài đặt thư viện):

```cmd
run.bat
```

---

### Cách 3: Cài đặt thủ công bằng Python

```bash
# 1. Tạo môi trường ảo
python -m venv .venv
.venv\Scripts\activate

# 2. Cài đặt gói ở chế độ editable
pip install -e .

# 3. Khởi chạy máy chủ
python -m backend.main
```

---

## 📱 Cài đặt PWA trên điện thoại / Mobile PWA Setup

### Bước 1: Kết nối thiết bị
- Đảm bảo điện thoại cùng mạng Wi-Fi với máy tính (hoặc qua [Cloudflare Tunnel](#-truy-cập-từ-xa-qua-internet--remote-tunneling-guide)).
- Quét mã QR trên màn hình terminal bằng Camera điện thoại hoặc truy cập `http://<IP_LAN>:8765`.

### Bước 2: Ghim ra màn hình chính (Add to Home Screen)

#### Trên iPhone / iPad (iOS Safari)
1. Mở liên kết trên **Safari**.
2. Bấm nút **Share** (biểu tượng hình vuông có mũi tên trỏ lên).
3. Chọn **"Add to Home Screen"** (Thêm vào Màn hình chính).
4. Bấm **"Add"**. Mở AgyMobile từ màn hình chính để dùng ở chế độ Fullscreen PWA không có thanh địa chỉ Safari!

#### Trên Android (Google Chrome)
1. Mở liên kết trên **Chrome**.
2. Bấm biểu tượng 3 chấm ở góc trên bên phải.
3. Chọn **"Install app"** hoặc **"Add to Home screen"**.
4. Xác nhận cài đặt.

---

## 🔐 Mô hình bảo mật & Sandbox / Security Model

1. **Xác thực PIN chống Timing Attack**:
   - Sử dụng `hmac.compare_digest` để so khớp mã PIN 6 chữ số, ngăn chặn tấn công trễ thời gian.
2. **Khóa chống Brute-Force (IP Rate Limiting)**:
   - Cửa sổ trượt ghi nhận số lần đăng nhập sai theo IP. 5 lần thử sai trong 60 giây sẽ bị khóa 300 giây (`HTTP 429 Retry-After: 300`).
3. **Mã hóa Token JWT HS256**:
   - Token JWT được ký bằng khóa bí mật với thuật toán HS256, tự động vô hiệu hóa token hết hạn hoặc giả mạo header.
4. **Phòng thủ Sandbox Windows Đa Lớp**:
   - Chống triệt để các kiểu tấn công duyệt đường dẫn: `../`, URL encoded (`%252e%252e`), NTFS Alternate Data Streams (`:`), UNC Paths (`\\server\share`), DOS Device Names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`), và Null Byte injection.

---

## ⚡ Khả năng chịu tải & Chịu lỗi / Benchmarks & Fault Tolerance

Theo kết quả từ [`COMPREHENSIVE_AUDIT_REPORT.md`](COMPREHENSIVE_AUDIT_REPORT.md):

- **Độ trễ API Telemetry**: `< 0.05 ms` (Nhờ cơ chế in-memory snapshot & offload `asyncio.to_thread`).
- **Lưu lượng WebSocket**: `> 10,000 messages/sec` burst capacity.
- **Rò rỉ bộ nhớ**: `< 50 KB` sau 1,000 chu kỳ kết nối/ngắt kết nối WebSocket liên tục.
- **Khắc phục xung đột khóa file Windows NTFS**: Tích hợp cơ chế ghi file tạm nguyên tử (atomic swap) với retry & exponential backoff chống lỗi `WinError 32`.
- **Đọc SQLite không chặn (Non-blocking RO)**: Áp dụng `PRAGMA query_only = ON` và `busy_timeout` loại trừ 100% rủi ro deadlock với Antigravity Desktop.

---

## 🌐 Truy cập từ xa qua Internet / Remote Tunneling Guide

Truy cập an toàn vào AgyMobile từ bất kỳ đâu qua Internet mà không cần mở port Router (NAT):

### 1. Cloudflare Tunnel (Khuyên dùng - Miễn phí & có sẵn SSL)
```bash
# 1. Cài đặt cloudflared trên Windows
winget install Cloudflare.cloudflared

# 2. Bật tunnel chuyển tiếp tới cổng 8765
cloudflared tunnel --url http://localhost:8765
```
Cloudflare sẽ cung cấp một đường dẫn HTTPS công khai (ví dụ: `https://xxxx.trycloudflare.com`). Bạn chỉ cần mở URL này trên điện thoại để truy cập bảo mật qua mã hóa SSL!

### 2. Tailscale (VPN Mesh nội bộ)
1. Cài Tailscale trên PC và điện thoại.
2. Kết nối cả hai thiết bị vào cùng tài khoản Tailscale.
3. Mở trên điện thoại: `http://<IP_Tailscale_Cua_PC>:8765`.

---

## 🧪 Hướng dẫn kiểm thử tự động / Automated Testing Guide

Dự án sở hữu ma trận kiểm thử 5 cấp độ với **751 bài test** (724 Pytest + 27 Node.js), đạt tỉ lệ pass 100%:

```powershell
# Chạy toàn bộ 724 bài test backend & integration (Pytest)
py -3.11 -m pytest tests/ -v

# Chạy bộ test giao diện PWA & bộ lọc ANSI VT100 (Node.js)
node tests/test_frontend_adversarial_suite.mjs

# Chạy riêng nhóm kiểm tra Bảo mật & Sandbox
py -3.11 -m pytest tests/test_security.py tests/test_adversarial_tier5_hardening.py -v

# Chạy nhóm Stress-test chịu tải cao & Chaos
py -3.11 -m pytest tests/test_adversarial_stress.py tests/test_adversarial_terminal_stress.py -v
```

---

## ⚙ Cấu hình môi trường / Configuration Reference

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `AGY_AUTH_PIN` / `AUTH_PIN` | `123456` | Mã PIN 6 số để đăng nhập từ di động |
| `JWT_SECRET_KEY` / `JWT_SECRET` | tự sinh | Khóa bí mật ký token JWT |
| `PORT` | `8765` | Cổng dịch vụ HTTP / WebSocket |
| `HOST` | `0.0.0.0` | Địa chỉ IP bind server |
| `WORKSPACE_ROOT` | thư mục hiện tại | Thư mục sandbox cho File Explorer |
| `ANTIGRAVITY_DIR` | `~/.gemini/antigravity` | Đường dẫn thư mục dữ liệu Antigravity 2.0 |
| `TOKEN_EXPIRY_SECONDS` | `86400` (24h) | Thời gian hết hạn của JWT Token |
| `RATE_LIMIT_MAX_ATTEMPTS` | `5` | Số lần thử sai tối đa trước khi khóa IP |
| `RATE_LIMIT_LOCKOUT_SECONDS` | `300` (5m) | Thời gian khóa IP sau khi vượt ngưỡng sai |

---

## ❓ Khắc phục sự cố / Troubleshooting & FAQ

### Q: Tường lửa Windows Defender chặn kết nối từ điện thoại qua mạng LAN.
**Khắc phục**: Mở PowerShell dưới quyền Administrator và thêm rule cho phép port 8765:
```powershell
New-NetFirewallRule -DisplayName "AgyMobile Server" -Direction Inbound -LocalPort 8765 -Protocol TCP -Action Allow
```

### Q: Camera trên trình duyệt di động không quét được mã QR.
**Khắc phục**: Trình duyệt di động chỉ cho phép mở Camera qua kết nối **HTTPS** hoặc `localhost`. Khi dùng qua mạng LAN HTTP thông thường, hãy dùng ứng dụng Camera gốc của điện thoại để quét mã QR ASCII trên terminal, hoặc bật **Cloudflare Tunnel HTTPS**.

### Q: Đổi mã PIN xác thực như thế nào?
**Khắc phục**: Đặt biến môi trường `AGY_AUTH_PIN` trước khi chạy server:
```cmd
set AGY_AUTH_PIN=654321
start.bat
```

---

## 📄 Bản quyền / License

Phát hành theo giấy phép **MIT License** — Copyright (c) 2026 AgyMobile Contributors.
