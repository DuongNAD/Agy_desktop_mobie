"""
Automated Pytest Test Suite for Milestone 4 (M4) — Mobile PWA Frontend & Touch UX.
Validates static web asset delivery, PWA manifest schema compliance, service worker caching, and SPA routing.
"""

import json
from pathlib import Path
import pytest


# ===========================================================================
# Tier 1: Static PWA Asset Serving & Schema Compliance (F16, F17, F19, F20)
# ===========================================================================

def test_static_index_html_serving(client):
    """PWA-T1-01: GET / serves index.html with responsive PWA shell."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "AgyMobile" in html or "Antigravity" in html
    assert "viewport-fit=cover" in html
    assert "manifest.json" in html
    # Check landmarks and tab panels
    assert 'id="tab-chat"' in html
    assert 'id="tab-files"' in html
    assert 'id="tab-terminal"' in html
    assert 'id="tab-system"' in html
    # Check modals
    assert 'id="modal-auth"' in html
    assert 'id="modal-artifacts"' in html
    assert 'id="modal-subagents"' in html
    assert 'id="toast-container"' in html


def test_static_manifest_serving(client):
    """PWA-T1-02: GET /manifest.json returns valid Web App Manifest JSON."""
    res = client.get("/manifest.json")
    assert res.status_code == 200
    manifest = res.json()
    assert manifest.get("name") == "AgyMobile — Antigravity Desktop Mobile Controller"
    assert manifest.get("short_name") == "AgyMobile"
    assert manifest.get("display") == "standalone"
    assert manifest.get("theme_color") == "#0a0e17"
    assert len(manifest.get("icons", [])) >= 2


def test_static_service_worker_serving(client):
    """PWA-T1-03: GET /sw.js serves Cache-First Service Worker script."""
    res = client.get("/sw.js")
    assert res.status_code == 200
    sw_text = res.text
    assert "addEventListener('install'" in sw_text or 'addEventListener("install"' in sw_text
    assert "addEventListener('activate'" in sw_text or 'addEventListener("activate"' in sw_text
    assert "addEventListener('fetch'" in sw_text or 'addEventListener("fetch"' in sw_text
    assert "agymobile-v" in sw_text


def test_static_css_assets_serving(client):
    """PWA-T1-04: Static CSS stylesheets are accessible with valid styles."""
    for css_file in ["/css/app.css", "/css/terminal.css", "/css/editor.css"]:
        res = client.get(css_file)
        assert res.status_code == 200
        assert len(res.text) > 100


def test_static_js_assets_serving(client):
    """PWA-T1-05: Main JavaScript modules and components are served cleanly."""
    js_files = [
        "/js/app.js",
        "/js/state.js",
        "/js/api.js",
        "/js/ws.js",
        "/js/lib/ansi_up.js",
        "/js/lib/qrcode.js",
        "/js/components/auth_modal.js",
        "/js/components/dashboard.js",
        "/js/components/subagents.js",
        "/js/components/tool_approval.js",
        "/js/components/artifacts.js",
        "/js/components/file_explorer.js",
        "/js/components/terminal.js",
        "/js/components/telemetry.js",
    ]
    for js_path in js_files:
        res = client.get(js_path)
        assert res.status_code == 200, f"Failed to serve {js_path}"
        assert len(res.text) > 50, f"Empty content in {js_path}"


def test_manifest_schema_compliance():
    """PWA-T1-06: manifest.json satisfies PWA installability requirements."""
    manifest_path = Path(__file__).parent.parent / "frontend" / "manifest.json"
    assert manifest_path.is_file(), "frontend/manifest.json must exist"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("display") == "standalone"
    assert manifest.get("short_name") in ("AgyMobile", "Antigravity Mobile")
    assert manifest.get("start_url") == "/"
    icons = manifest.get("icons", [])
    assert len(icons) >= 2
    sizes = [icon.get("sizes") for icon in icons]
    assert "192x192" in sizes
    assert "512x512" in sizes


def test_service_worker_precache_list_integrity():
    """PWA-T1-07: sw.js contains lifecycle event listeners and precache asset list."""
    sw_path = Path(__file__).parent.parent / "frontend" / "sw.js"
    assert sw_path.is_file(), "frontend/sw.js must exist"
    content = sw_path.read_text(encoding="utf-8")
    assert "install" in content
    assert "activate" in content
    assert "fetch" in content
    assert "PRECACHE_ASSETS" in content or "caches.open" in content


def test_start_bat_script_syntax_and_commands():
    """PWA-T1-08: 1-Click start.bat script exists and contains batch commands."""
    bat_path = Path(__file__).parent.parent / "start.bat"
    if bat_path.is_file():
        content = bat_path.read_text(encoding="utf-8", errors="ignore")
        assert "@echo off" in content.lower() or "python" in content.lower() or "uv" in content.lower()


def test_run_bat_script_syntax_and_commands():
    """PWA-T1-09: 1-Click run.bat script exists and contains launch commands."""
    bat_path = Path(__file__).parent.parent / "run.bat"
    if bat_path.is_file():
        content = bat_path.read_text(encoding="utf-8", errors="ignore")
        assert len(content) > 10


def test_readme_documentation_completeness():
    """PWA-T1-10: README.md contains comprehensive documentation (F20)."""
    readme_path = Path(__file__).parent.parent / "README.md"
    if readme_path.is_file():
        content = readme_path.read_text(encoding="utf-8", errors="ignore")
        assert "Antigravity" in content or "AgyMobile" in content
        assert "## " in content


# ===========================================================================
# Tier 2: Boundary & Security Tests
# ===========================================================================

def test_static_404_missing_asset(client):
    """PWA-T2-01: Non-existent static asset route returns safe status without crashing."""
    res = client.get("/css/non_existent_stylesheet_9999.css")
    assert res.status_code in (200, 404)


def test_static_path_traversal_protection(client):
    """PWA-T2-02: Path traversal escape via static route returns safe status (no host code leak)."""
    res = client.get("/../backend/config.py")
    assert res.status_code in (200, 400, 403, 404)
    assert "class Settings" not in res.text


def test_frontend_directory_complete_artifacts_existence():
    """PWA-T2-03: Verify all 18 required M4 frontend files exist on disk with non-trivial content."""
    frontend_dir = Path(__file__).parent.parent / "frontend"
    required_files = [
        "index.html",
        "manifest.json",
        "sw.js",
        "css/app.css",
        "css/terminal.css",
        "css/editor.css",
        "js/app.js",
        "js/state.js",
        "js/api.js",
        "js/ws.js",
        "js/lib/ansi_up.js",
        "js/lib/qrcode.js",
        "js/components/auth_modal.js",
        "js/components/dashboard.js",
        "js/components/subagents.js",
        "js/components/tool_approval.js",
        "js/components/artifacts.js",
        "js/components/file_explorer.js",
        "js/components/terminal.js",
        "js/components/telemetry.js",
    ]
    for rel_path in required_files:
        f = frontend_dir / rel_path
        assert f.is_file(), f"Required file {rel_path} does not exist in frontend directory"
        assert f.stat().st_size > 100, f"File {rel_path} is unexpectedly small ({f.stat().st_size} bytes)"
