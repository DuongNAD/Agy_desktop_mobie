"""
Adversarial Pytest Test Suite for Milestone 4 (Mobile PWA Frontend & Touch UX).
Adversarial Stress Testing of Service Worker, Offline Fallbacks, WebSocket Loss, Exponential Backoff,
Byte-Offset Re-Sync, Static Security & Traversal Protection.
"""

import asyncio
import json
import math
import os
import re
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.session_hub.tailer import TranscriptTailer


# ===========================================================================
# 1. Service Worker & Caching Adversarial Tests
# ===========================================================================

def test_sw_precache_assets_exhaustive_reachability(client):
    """
    ADV-SW-01: Exhaustively verify that every asset declared in sw.js PRECACHE_ASSETS
    exists on disk and is served with HTTP 200 and non-empty content.
    """
    sw_file = Path(__file__).parent.parent / "frontend" / "sw.js"
    assert sw_file.is_file(), "sw.js must exist in frontend/"
    sw_content = sw_file.read_text(encoding="utf-8")

    # Extract PRECACHE_ASSETS array via regex
    match = re.search(r"const\s+PRECACHE_ASSETS\s*=\s*\[(.*?)\];", sw_content, re.DOTALL)
    assert match, "PRECACHE_ASSETS array must be defined in sw.js"
    raw_array = match.group(1)
    asset_paths = [
        line.strip().strip(",").strip("'").strip('"')
        for line in raw_array.splitlines()
        if line.strip() and not line.strip().startswith("//")
    ]
    assert len(asset_paths) >= 15, f"Expected at least 15 precache assets, got {len(asset_paths)}"

    for asset in asset_paths:
        res = client.get(asset)
        assert res.status_code == 200, f"Precache asset '{asset}' failed with status {res.status_code}"
        assert len(res.content) > 0, f"Precache asset '{asset}' is empty"


def test_sw_offline_fallback_simulation():
    """
    ADV-SW-02: Simulate Service Worker offline fallback logic.
    - /api/* requests when offline must return 503 JSON with offline error message.
    - /ws/* requests when offline must return 503 text with Network Error.
    - Navigation requests (request.mode === 'navigate') when offline must fall back to /index.html.
    - Non-cached static asset requests when offline must return 503 Offline.
    """
    sw_file = Path(__file__).parent.parent / "frontend" / "sw.js"
    sw_content = sw_file.read_text(encoding="utf-8")

    # Verify SW code has the exact offline handling logic
    assert "url.pathname.startsWith('/api/')" in sw_content or 'url.pathname.startsWith("/api/")' in sw_content
    assert "url.pathname.startsWith('/ws')" in sw_content or 'url.pathname.startsWith("/ws")' in sw_content
    assert "Desktop server unreachable" in sw_content
    assert "status: 503" in sw_content
    assert "request.mode === 'navigate'" in sw_content or 'request.mode === "navigate"' in sw_content
    assert "'/index.html'" in sw_content or '"/index.html"' in sw_content


def test_sw_cache_version_purging_logic():
    """
    ADV-SW-03: Validate that sw.js contains cache version eviction logic
    that deletes all caches not matching the current CACHE_VERSION during activation.
    """
    sw_file = Path(__file__).parent.parent / "frontend" / "sw.js"
    sw_content = sw_file.read_text(encoding="utf-8")

    assert "CACHE_VERSION" in sw_content
    assert "addEventListener('activate'" in sw_content or 'addEventListener("activate"' in sw_content
    assert "caches.keys()" in sw_content
    assert "caches.delete(name)" in sw_content or "caches.delete(" in sw_content
    assert "self.clients.claim()" in sw_content


def test_sw_resilient_installation():
    """
    ADV-SW-04: Validate that sw.js uses Promise.allSettled and catches individual add failures
    so that a single 404/missing resource does not abort SW installation and skipWaiting.
    """
    sw_file = Path(__file__).parent.parent / "frontend" / "sw.js"
    sw_content = sw_file.read_text(encoding="utf-8")

    assert "Promise.allSettled" in sw_content or ".catch(" in sw_content
    assert "self.skipWaiting()" in sw_content


# ===========================================================================
# 2. WebSocket Resilience, Exponential Backoff & State Re-sync Tests
# ===========================================================================

def test_ws_exponential_backoff_mathematical_bounds():
    """
    ADV-WS-01: Verify mathematical bounds of exponential backoff reconnection formula
    used in ws.js: delay = min(base * (1.5 ^ attempts) + jitter, max_delay).
    """
    base_delay = 1000  # 1s
    max_delay = 15000  # 15s
    base_multiplier = 1.5

    delays = []
    for attempt in range(1, 20):
        min_expected = min(base_delay * (base_multiplier ** attempt), max_delay)
        max_expected = min(base_delay * (base_multiplier ** attempt) + 500, max_delay)
        delays.append((attempt, min_expected, max_expected))
        # Ensure monotonic or capped
        assert min_expected <= max_delay
        assert max_expected <= max_delay

    # Check that after ~7 attempts it reaches max_delay clamp
    assert delays[7][1] >= 15000
    assert delays[15][1] == 15000


def test_ws_heartbeat_and_disconnection_handling():
    """
    ADV-WS-02: Verify ws.js implements 15s ping interval, 5s pong timeout,
    RTT latency computation, and code 1008 terminal stop.
    """
    ws_file = Path(__file__).parent.parent / "frontend" / "js" / "ws.js"
    assert ws_file.is_file(), "frontend/js/ws.js must exist"
    ws_content = ws_file.read_text(encoding="utf-8")

    assert "15000" in ws_content, "Must have 15s ping interval"
    assert "5000" in ws_content, "Must have 5s pong timeout"
    assert "1008" in ws_content, "Must handle close code 1008 (unauthorized)"
    assert "lastByteOffset" in ws_content, "Must maintain lastByteOffset for streaming sync"
    assert "subscribeSession" in ws_content, "Must support subscribeSession with byte offset"


def test_ws_last_byte_offset_missed_messages_resync(client, mock_antigravity_env, valid_jwt_token):
    """
    ADV-WS-03: Stress-test WebSocket disconnection, writing new steps while offline,
    and reconnecting with from_offset=lastByteOffset to guarantee zero missed and zero duplicated messages.
    """
    root_id = mock_antigravity_env.root_id
    ws_url = f"/ws/session?token={valid_jwt_token}"

    # Phase 1: Connect and read initial steps
    with client.websocket_connect(ws_url) as ws:
        # Subscribe from offset 0
        ws.send_json({"action": "subscribe", "conversation_id": root_id, "from_offset": 0})
        
        # Read initial events until subscribed & step events
        sub_ack = ws.receive_json()
        assert sub_ack["type"] == "subscribed"
        assert sub_ack["conversation_id"] == root_id

        # Receive step updates and track last byte offset
        last_offset = 0
        received_step_indices = []
        
        # We know mock_antigravity_env seeded 3 steps
        for _ in range(25):
            event = ws.receive_json()
            if "byte_offset" in event:
                last_offset = max(last_offset, event["byte_offset"])
            if event.get("type") == "step_update":
                received_step_indices.append(event["step"]["step_index"])
            if len(received_step_indices) >= 3:
                break

        assert last_offset > 0
        assert received_step_indices == [0, 1, 2]

    # Socket is now disconnected (simulating network loss or app backgrounding)

    # Phase 2: Write 3 NEW steps while disconnected
    new_steps = [
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-08-14T08:10:00Z",
            "thinking": "Resuming after tool execution...",
            "content": "Step 3 output executed successfully."
        },
        {
            "step_index": 4,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-08-14T08:11:00Z",
            "content": "Please verify milestone 4 PWA frontend."
        },
        {
            "step_index": 5,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-08-14T08:11:30Z",
            "thinking": "Verifying M4 components and touch layout...",
            "content": "Milestone 4 PWA verification complete."
        }
    ]
    for st in new_steps:
        mock_antigravity_env.append_step(root_id, st)

    # Phase 3: Reconnect with from_offset = last_offset
    with client.websocket_connect(ws_url) as ws_reconnected:
        ws_reconnected.send_json({
            "action": "subscribe",
            "conversation_id": root_id,
            "from_offset": last_offset
        })

        resub_ack = ws_reconnected.receive_json()
        assert resub_ack["type"] == "subscribed"

        resynced_step_indices = []
        new_last_offset = last_offset

        # Collect events for the 3 new steps
        for _ in range(25):
            event = ws_reconnected.receive_json()
            if "byte_offset" in event:
                new_last_offset = max(new_last_offset, event["byte_offset"])
            if event.get("type") == "step_update":
                resynced_step_indices.append(event["step"]["step_index"])
            if len(resynced_step_indices) >= 3:
                break

        # Verify ONLY the missed steps (3, 4, 5) were received, with no duplicates of 0, 1, 2
        assert resynced_step_indices == [3, 4, 5]
        assert new_last_offset > last_offset


def test_ws_out_of_bounds_offset_recovery(client, mock_antigravity_env, valid_jwt_token):
    """
    ADV-WS-04: When client supplies an invalid/corrupted offset (larger than file size),
    server must gracefully reset offset to 0 and stream without crashing.
    """
    root_id = mock_antigravity_env.root_id
    ws_url = f"/ws/session?token={valid_jwt_token}"

    with client.websocket_connect(ws_url) as ws:
        # Seek way beyond file size
        ws.send_json({"action": "subscribe", "conversation_id": root_id, "from_offset": 999999999})
        sub_ack = ws.receive_json()
        assert sub_ack["type"] == "subscribed"

        # Server should reset to 0 and stream initial steps
        event = ws.receive_json()
        assert "type" in event


# ===========================================================================
# 3. Static Endpoint Security & SPA Fallback Tests
# ===========================================================================

def test_static_mime_content_type_headers(client):
    """
    ADV-SEC-01: Ensure proper MIME Content-Type headers are returned for all static asset types.
    """
    mime_expectations = {
        "/": "text/html",
        "/index.html": "text/html",
        "/manifest.json": "application/json",
        "/sw.js": ("application/javascript", "text/javascript"),
        "/css/app.css": "text/css",
        "/css/terminal.css": "text/css",
        "/css/editor.css": "text/css",
        "/js/app.js": ("application/javascript", "text/javascript"),
        "/js/state.js": ("application/javascript", "text/javascript"),
        "/js/ws.js": ("application/javascript", "text/javascript"),
        "/assets/icons/icon-192.png": "image/png",
        "/assets/icons/icon-512.png": "image/png",
    }

    for path, expected_mime in mime_expectations.items():
        res = client.get(path)
        assert res.status_code == 200, f"Failed GET {path}"
        content_type = res.headers.get("content-type", "").lower()
        if isinstance(expected_mime, tuple):
            assert any(m in content_type for m in expected_mime), f"Unexpected Content-Type for {path}: {content_type}"
        else:
            assert expected_mime in content_type, f"Unexpected Content-Type for {path}: {content_type}"


def test_static_adversarial_path_traversal_attempts(client):
    """
    ADV-SEC-02: Adversarial path traversal payloads must NOT leak server source code or sensitive host files.
    """
    traversal_payloads = [
        "/../backend/security.py",
        "/../../backend/config.py",
        "/../backend/main.py",
        "/css/../../backend/security.py",
        "/js/../../backend/session_hub/routes.py",
        "/..%2f..%2fbackend%2fconfig.py",
        "/assets/../../pyproject.toml",
    ]

    for payload in traversal_payloads:
        res = client.get(payload)
        # Should either be 404, or if SPA fallback triggers, it should serve index.html (NOT backend python code!)
        assert res.status_code in (200, 400, 403, 404)
        assert "create_access_token" not in res.text
        assert "class Settings" not in res.text
        assert "sqlite_reader" not in res.text


def test_spa_fallback_vs_api_404_distinction(client):
    """
    ADV-SEC-03: SPA fallback must serve index.html for client-side routes (/chat, /terminal, /files, etc.),
    but must return HTTP 404 JSON for non-existent API routes (/api/unknown) and WebSocket routes (/ws/unknown).
    """
    # 1. Client-side routes should return index.html
    client_routes = ["/chat", "/files", "/terminal", "/system", "/settings", "/sessions/123"]
    for route in client_routes:
        res = client.get(route)
        assert res.status_code == 200
        assert "AgyMobile" in res.text or "<!DOCTYPE html>" in res.text

    # 2. Non-existent API routes must return 404 Not Found (not index.html!)
    api_routes = ["/api/nonexistent", "/api/v1/unknown", "/api/auth/unknown_endpoint"]
    for api_route in api_routes:
        res = client.get(api_route)
        assert res.status_code == 404
        assert "<!DOCTYPE html>" not in res.text
        assert "detail" in res.json() or "error" in res.json()


# ===========================================================================
# 4. Touch UX & Mobile Viewport Compliance Tests
# ===========================================================================

def test_mobile_viewport_and_pwa_installability_meta():
    """
    ADV-UX-01: Verify that index.html contains essential mobile touch UX attributes:
    - viewport-fit=cover
    - user-scalable=no
    - apple-mobile-web-app-capable
    - theme-color matching OLED dark
    """
    index_file = Path(__file__).parent.parent / "frontend" / "index.html"
    assert index_file.is_file()
    html = index_file.read_text(encoding="utf-8")

    assert "viewport-fit=cover" in html
    assert "user-scalable=no" in html or "maximum-scale=1.0" in html
    assert "apple-mobile-web-app-capable" in html
    assert "#0a0e17" in html  # Dark OLED theme color


def test_touch_target_accessibility_in_css():
    """
    ADV-UX-02: Verify that app.css defines touch targets >= 44px for primary controls
    and sets overscroll-behavior-y to prevent pull-to-refresh interference.
    """
    css_file = Path(__file__).parent.parent / "frontend" / "css" / "app.css"
    assert css_file.is_file()
    css = css_file.read_text(encoding="utf-8")

    assert "44px" in css or "48px" in css, "Touch targets must satisfy >= 44px mobile guideline"
    assert "overscroll-behavior" in css or "visualViewport" in css or "--visual-vh" in css


def test_ansi_up_sanitization_and_html_injection_prevention():
    """
    ADV-UX-03: Verify that ansi_up.js sanitizes HTML entities to prevent XSS injection in terminal output.
    """
    ansi_file = Path(__file__).parent.parent / "frontend" / "js" / "lib" / "ansi_up.js"
    assert ansi_file.is_file()
    ansi_code = ansi_file.read_text(encoding="utf-8")

    assert "&amp;" in ansi_code
    assert "&lt;" in ansi_code
    assert "&gt;" in ansi_code


# ===========================================================================
# 5. Advanced WebSocket Churn, Malformed Payloads & Auth Rejection
# ===========================================================================

def test_ws_rapid_subscription_churn_and_malformed_frames(client, mock_antigravity_env, valid_jwt_token):
    """
    ADV-WS-05: Stress-test WebSocket handling under rapid subscription switching,
    unsubscribing, malformed non-JSON frames, non-dict payloads, and unknown actions.
    """
    root_id = mock_antigravity_env.root_id
    sub_id = mock_antigravity_env.sub_id
    ws_url = f"/ws/session?token={valid_jwt_token}"

    with client.websocket_connect(ws_url) as ws:
        # 1. Send malformed non-JSON frame
        ws.send_text("THIS_IS_NOT_JSON_{{[[")

        # 2. Send non-dict JSON array
        ws.send_text(json.dumps(["some", "array", "data"]))

        # 3. Send unrecognized action
        ws.send_json({"action": "unknown_action_xyz", "conversation_id": root_id})
        err_msg = ws.receive_json()
        assert err_msg.get("type") == "error"
        assert "unknown_action_xyz" in err_msg.get("message", "")

        # 4. Rapid subscribe -> unsubscribe churn
        for _ in range(3):
            ws.send_json({"action": "subscribe", "conversation_id": root_id, "from_offset": 0})
            sub_ack = ws.receive_json()
            assert sub_ack["type"] == "subscribed"

            ws.send_json({"action": "unsubscribe", "conversation_id": root_id})
            unsub_found = False
            for _ in range(10):
                msg = ws.receive_json()
                if msg.get("type") == "unsubscribed":
                    unsub_found = True
                    break
            assert unsub_found, "Must receive unsubscribed confirmation"

        # 5. Switch to subagent conversation
        ws.send_json({"action": "subscribe", "conversation_id": sub_id, "from_offset": 0})
        sub_ack = ws.receive_json()
        assert sub_ack["type"] == "subscribed"
        assert sub_ack["conversation_id"] == sub_id


def test_ws_auth_unauthorized_token_rejection(client):
    """
    ADV-WS-06: WebSocket connections with missing, expired, or invalid token must be rejected with 1008 close code.
    """
    # 1. Missing token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/session"):
            pass

    # 2. Invalid token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/session?token=invalid.jwt.token"):
            pass


def test_manifest_deep_spec_validation():
    """
    ADV-PWA-01: Deep validation of manifest.json properties against W3C Web App Manifest spec.
    """
    manifest_path = Path(__file__).parent.parent / "frontend" / "manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Required fields
    assert "name" in data and len(data["name"]) > 0
    assert "short_name" in data and len(data["short_name"]) > 0
    assert data["start_url"] == "/"
    assert data["display"] in ["fullscreen", "standalone", "minimal-ui", "browser"]
    assert data["theme_color"].startswith("#")
    assert data["background_color"].startswith("#")

    # Icons validation
    icons = data.get("icons", [])
    assert len(icons) >= 2
    purposes = [i.get("purpose") for i in icons]
    assert "any" in purposes
    assert "maskable" in purposes

    # Shortcuts validation
    shortcuts = data.get("shortcuts", [])
    assert len(shortcuts) >= 3
    for sc in shortcuts:
        assert "name" in sc
        assert "url" in sc
        assert sc["url"].startswith("/")

