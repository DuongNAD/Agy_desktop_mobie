#!/usr/bin/env bash
# ==============================================================================
# AgyMobile - 1-Click Setup for Android (Termux)
# ==============================================================================

set -e

echo ""
echo "=============================================================================="
echo "  AgyMobile - Bộ Cài Đặt Cho Android (Termux)"
echo "=============================================================================="
echo ""

echo "[1/3] Cập nhật gói và cài đặt Python, Nodejs, Git..."
pkg update -y
pkg install -y python python-pip git nodejs

echo "[2/3] Cài đặt các gói Python cần thiết..."
pip install --upgrade pip
pip install fastapi uvicorn pydantic pydantic-settings psutil pyjwt qrcode[pil]

echo "[3/3] Hoàn tất cài đặt!"
echo "Để chạy AgyMobile trên Android Termux, gõ:"
echo "python -m uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8765"
echo ""
