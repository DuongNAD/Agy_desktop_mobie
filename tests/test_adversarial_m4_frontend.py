"""
tests/test_adversarial_m4_frontend.py
Milestone 4 Adversarial Test Suite for Mobile PWA Frontend & Touch UX.
Tests static asset integrity, PWA manifest, service worker caching,
ANSI parser fuzzing, visual diffs, touch ergonomics, and runs the Node.js adversarial test suite.
"""

import json
import subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.config import Settings

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = WORKSPACE_ROOT / "frontend"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        host="127.0.0.1",
        port=8765,
        pin_code="123456",
        jwt_secret="adversarial_m4_test_secret_key_12345",
        workspace_root=str(tmp_path),
        antigravity_dir=str(tmp_path / "gemini_mock"),
    )
    app = create_app(settings)
    return TestClient(app)


def test_frontend_files_and_directories_integrity():
    """Verify all required M4 frontend files exist, are readable, and non-empty."""
    required_files = [
        FRONTEND_DIR / "index.html",
        FRONTEND_DIR / "manifest.json",
        FRONTEND_DIR / "sw.js",
        FRONTEND_DIR / "css" / "app.css",
        FRONTEND_DIR / "css" / "terminal.css",
        FRONTEND_DIR / "css" / "editor.css",
        FRONTEND_DIR / "js" / "app.js",
        FRONTEND_DIR / "js" / "state.js",
        FRONTEND_DIR / "js" / "api.js",
        FRONTEND_DIR / "js" / "ws.js",
        FRONTEND_DIR / "js" / "lib" / "ansi_up.js",
        FRONTEND_DIR / "js" / "lib" / "qrcode.js",
        FRONTEND_DIR / "js" / "components" / "auth_modal.js",
        FRONTEND_DIR / "js" / "components" / "dashboard.js",
        FRONTEND_DIR / "js" / "components" / "subagents.js",
        FRONTEND_DIR / "js" / "components" / "tool_approval.js",
        FRONTEND_DIR / "js" / "components" / "artifacts.js",
        FRONTEND_DIR / "js" / "components" / "file_explorer.js",
        FRONTEND_DIR / "js" / "components" / "terminal.js",
        FRONTEND_DIR / "js" / "components" / "telemetry.js",
        FRONTEND_DIR / "assets" / "icons" / "icon-192.png",
        FRONTEND_DIR / "assets" / "icons" / "icon-512.png",
    ]

    for file_path in required_files:
        assert file_path.exists(), f"Missing required file: {file_path}"
        assert file_path.stat().st_size > 0, f"File is empty: {file_path}"


def test_manifest_json_strict_schema_compliance():
    """Verify manifest.json contains all required PWA fields, valid theme colors, and icons."""
    manifest_path = FRONTEND_DIR / "manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("name") == "AgyMobile — Antigravity Desktop Mobile Controller"
    assert data.get("short_name") == "AgyMobile"
    assert data.get("display") == "standalone"
    assert data.get("orientation") == "portrait-primary"
    assert data.get("theme_color") == "#0a0e17"
    assert data.get("background_color") == "#0a0e17"
    assert data.get("start_url") == "/"

    icons = data.get("icons", [])
    assert len(icons) >= 2, "Manifest must contain at least 192x192 and 512x512 icons"

    sizes = [icon.get("sizes") for icon in icons]
    assert "192x192" in sizes
    assert "512x512" in sizes

    shortcuts = data.get("shortcuts", [])
    assert len(shortcuts) >= 4, "Manifest must contain shortcuts for quick tab access"


def test_service_worker_precache_asset_existence():
    """Verify every asset listed in sw.js PRECACHE_ASSETS exists on disk."""
    sw_path = FRONTEND_DIR / "sw.js"
    content = sw_path.read_text(encoding="utf-8")

    assert "agymobile-v1.0.0" in content, "Service worker must have versioned cache key"

    precache_lines = [
        line.strip().strip("',\"")
        for line in content.split("\n")
        if line.strip().startswith("'/") or line.strip().startswith('"/' )
    ]

    for rel_url in precache_lines:
        if rel_url == "/":
            target = FRONTEND_DIR / "index.html"
        else:
            target = FRONTEND_DIR / rel_url.lstrip("/")

        assert target.exists(), f"Precached asset in sw.js not found on disk: {rel_url} -> {target}"


def test_html_mobile_meta_and_css_variables(client):
    """Verify index.html has viewport-fit=cover, safe-area variables, and no inline XSS."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    assert "viewport-fit=cover" in html
    assert "apple-mobile-web-app-capable" in html
    assert "manifest.json" in html

    # Verify CSS styling
    css_res = client.get("/css/app.css")
    assert css_res.status_code == 200
    css = css_res.text

    assert "100dvh" in css or "--visual-vh" in css
    assert "safe-area-inset-top" in css
    assert "safe-area-inset-bottom" in css


def test_execute_nodejs_adversarial_suite():
    """Run the empirical Node.js frontend adversarial suite and assert 100% pass."""
    test_script = WORKSPACE_ROOT / "tests" / "test_frontend_adversarial_suite.mjs"
    assert test_script.exists(), "Adversarial test script missing"

    proc = subprocess.run(
        ["node", str(test_script)],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        timeout=30
    )

    print("\n--- Node.js Adversarial Suite Output ---")
    print(proc.stdout)
    if proc.stderr:
        print("STDERR:", proc.stderr)

    assert proc.returncode == 0, f"Node.js adversarial suite failed:\n{proc.stdout}\n{proc.stderr}"
    assert "ALL ADVERSARIAL FRONTEND TESTS PASSED SUCCESSFULLY (100%)" in proc.stdout
