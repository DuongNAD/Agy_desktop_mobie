#!/usr/bin/env bash
# ==============================================================================
# AgyMobile - 1-Click Installer for macOS & Linux
# Antigravity 2.0 & Desktop Workspace Mobile Remote Platform
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "=============================================================================="
echo "  AgyMobile - Bộ Cài Đặt 1-Click Cho macOS & Linux"
echo "  Antigravity 2.0 & Desktop Workspace Mobile Remote Platform"
echo "=============================================================================="
echo ""

# 1. Detect Environment & Toolchains
echo "[1/4] Kiểm tra môi trường Python 3.10+ / uv..."

UV_BIN=""
if command -v uv &> /dev/null; then
    UV_BIN="$(command -v uv)"
elif [ -f "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
elif [ -f "$HOME/.cargo/bin/uv" ]; then
    UV_BIN="$HOME/.cargo/bin/uv"
fi

if [ -z "$UV_BIN" ]; then
    echo "[AgyMobile] Không tìm thấy uv. Đang tự động cài đặt uv siêu tốc..."
    if command -v curl &> /dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        if [ -f "$HOME/.local/bin/uv" ]; then
            UV_BIN="$HOME/.local/bin/uv"
        fi
    fi
fi

# 2. Setup Virtual Environment & Install Dependencies
echo "[2/4] Thiết lập Virtual Environment & Cài đặt thư viện..."
if [ -n "$UV_BIN" ]; then
    echo "[AgyMobile] Sử dụng uv package manager: $UV_BIN"
    "$UV_BIN" venv .venv --python 3.11 || "$UV_BIN" venv .venv
    "$UV_BIN" pip install -e .
else
    echo "[AgyMobile] Sử dụng Python hệ thống..."
    if command -v python3 &> /dev/null; then
        python3 -m venv .venv
        .venv/bin/pip install --upgrade pip
        .venv/bin/pip install -e .
    else
        echo "[LỖI] Không tìm thấy Python 3. Vui lòng cài đặt Python 3.10+ trước khi tiếp tục."
        exit 1
    fi
fi

# 3. Grant Executable Permissions
echo "[3/4] Cấp quyền thực thi cho các script..."
chmod +x "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/start.sh" "$SCRIPT_DIR/AgyMobile.command" 2>/dev/null || true

# 4. macOS Desktop Alias Helper
if [ "$(uname)" = "Darwin" ]; then
    echo "[4/4] Tạo biểu tượng trên macOS Desktop..."
    DESKTOP_DIR="$HOME/Desktop"
    if [ -d "$DESKTOP_DIR" ]; then
        ln -sf "$SCRIPT_DIR/AgyMobile.command" "$DESKTOP_DIR/AgyMobile.command"
        echo "[AgyMobile] ✓ Đã tạo lối tắt AgyMobile trên macOS Desktop!"
    fi
else
    echo "[4/4] Hoàn tất cấu hình môi trường Linux!"
fi

echo ""
echo "=============================================================================="
echo "  ✓ CÀI ĐẶT HOÀN TẤT THÀNH CÔNG!"
echo "  Bạn có thể khởi động ngay bây giờ bằng lệnh: ./start.sh"
echo "  Hoặc click đúp vào file AgyMobile.command (trên macOS)"
echo "=============================================================================="
echo ""

read -p "Bạn có muốn khởi động AgyMobile ngay bây giờ không? (y/n): " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    exec "$SCRIPT_DIR/start.sh"
fi
