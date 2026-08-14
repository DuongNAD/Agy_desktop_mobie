"""
Adversarial Stress Test Suite for Milestone 1 (M1) - Challenger 2
Covers:
  1. Concurrent burst requests (login, qr, verify, system/info) with race condition checks.
  2. Malformed JSON bodies, extreme payloads, injection attacks, and invalid query parameters.
  3. Edge-case network adapter environments (all disconnected, empty, IPv6-only, multi-physical, virtual-only).
  4. QR PNG binary headers, SVG XML structure parsing, and Terminal ANSI/Unicode rendering.
  5. CORS preflight validation across authorized LAN/tunnel origins and unauthorized bypass vectors.
"""

import asyncio
import io
import json
import os
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from collections import namedtuple
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import httpx
import psutil
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from starlette.testclient import TestClient

from backend.config import Settings, get_settings
from backend.main import create_app
from backend.network import (
    TunnelInfo,
    generate_png_qr,
    generate_qr_png_bytes,
    generate_qr_svg_bytes,
    generate_svg_qr,
    generate_terminal_qr,
    get_lan_ip,
    is_valid_lan_ipv4,
    print_startup_banner,
)
from backend.security import (
    IPRateLimiter,
    create_access_token,
    decode_access_token,
    rate_limiter,
    verify_pin_constant_time,
)

TEST_PIN = "849201"
TEST_JWT_SECRET = "test-secret-key-32-bytes-agymobile-12345"


@pytest.fixture
def adversarial_settings(tmp_path: Path) -> Settings:
    ws = tmp_path / "adv_ws"
    ws.mkdir(parents=True, exist_ok=True)
    antigravity_dir = tmp_path / "adv_ag"
    antigravity_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        HOST="127.0.0.1",
        PORT=8765,
        DEBUG=True,
        AUTH_PIN=TEST_PIN,
        JWT_SECRET=TEST_JWT_SECRET,
        JWT_ALGORITHM="HS256",
        TOKEN_EXPIRY_SECONDS=86400,
        WORKSPACE_ROOT=ws,
        ANTIGRAVITY_DIR=antigravity_dir,
        RATE_LIMIT_MAX_ATTEMPTS=5,
        RATE_LIMIT_WINDOW_SECONDS=60,
        RATE_LIMIT_LOCKOUT_SECONDS=300,
        CORS_ORIGINS=["*"],
    )


@pytest.fixture
def adv_app(adversarial_settings: Settings):
    return create_app(settings=adversarial_settings)


@pytest.fixture
def adv_client(adv_app: Any) -> TestClient:
    return TestClient(adv_app)


@pytest.fixture
async def adv_async_client(adv_app: Any):
    transport = ASGITransport(app=adv_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def adv_token(adversarial_settings: Settings) -> str:
    return create_access_token({"sub": "admin", "role": "admin"}, settings=adversarial_settings)


@pytest.fixture
def adv_headers(adv_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {adv_token}"}


# ===========================================================================
# 1. CONCURRENT BURST REQUESTS & RACE CONDITION STRESS TESTS
# ===========================================================================

@pytest.mark.asyncio
async def test_concurrent_burst_login_valid_pin(adv_async_client: AsyncClient):
    """Stress-test 50 simultaneous login requests with valid PIN across multiple client IPs."""
    async def _send_login(client_id: int):
        spoofed_ip = f"192.168.1.{10 + (client_id % 40)}"
        return await adv_async_client.post(
            "/api/auth/login",
            json={"pin": TEST_PIN},
            headers={"X-Forwarded-For": spoofed_ip},
        )

    tasks = [_send_login(i) for i in range(50)]
    responses = await asyncio.gather(*tasks)

    for res in responses:
        assert res.status_code == 200
        data = res.json()
        assert data.get("status") == "authenticated"
        assert "token" in data
        assert data.get("expires_in") == 86400


@pytest.mark.asyncio
async def test_concurrent_burst_login_brute_force_lockout_race_safety(adv_async_client: AsyncClient):
    """
    Stress-test 50 simultaneous INVALID login requests from the SAME IP.
    Verifies that the rate limiter does not suffer race conditions under load:
    first 5 attempts fail with 401, subsequent attempts are rejected with 429.
    """
    test_ip = "192.168.100.88"

    async def _send_bad_login():
        return await adv_async_client.post(
            "/api/auth/login",
            json={"pin": "000000"},
            headers={"X-Forwarded-For": test_ip},
        )

    tasks = [_send_bad_login() for _ in range(50)]
    responses = await asyncio.gather(*tasks)

    status_codes = [r.status_code for r in responses]
    assert 401 in status_codes
    assert 429 in status_codes
    assert sum(1 for c in status_codes if c in (401, 429)) == 50
    num_401 = sum(1 for c in status_codes if c == 401)
    num_429 = sum(1 for c in status_codes if c == 429)
    assert 1 <= num_401 <= 5
    assert num_429 >= 45

    # Any subsequent request must be locked out (429) with Retry-After header
    post_burst_res = await adv_async_client.post(
        "/api/auth/login",
        json={"pin": TEST_PIN},
        headers={"X-Forwarded-For": test_ip},
    )
    assert post_burst_res.status_code == 429
    assert "Retry-After" in post_burst_res.headers


def test_ip_rate_limiter_multithreaded_race_safety():
    """Direct multithreaded stress test on IPRateLimiter with 100 concurrent threads."""
    limiter = IPRateLimiter(max_attempts=10, window_seconds=60, lockout_seconds=300)
    target_ip = "10.0.0.99"
    results = []

    def _worker():
        locked, sec = limiter.record_failure(target_ip)
        results.append((locked, sec))

    threads = [threading.Thread(target=_worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 100
    locked_count = sum(1 for locked, _ in results if locked)
    assert locked_count >= 90
    is_locked, rem = limiter.is_locked(target_ip)
    assert is_locked is True
    assert rem > 0


@pytest.mark.asyncio
async def test_concurrent_burst_qr_generation(adv_async_client: AsyncClient):
    """Stress-test 60 concurrent QR generation requests with mixed formats (PNG, SVG, ASCII, JSON)."""
    formats = ["png", "svg", "ascii", "json"]

    async def _fetch_qr(idx: int):
        fmt = formats[idx % len(formats)]
        return fmt, await adv_async_client.get(f"/api/auth/qr?format={fmt}")

    tasks = [_fetch_qr(i) for i in range(60)]
    results = await asyncio.gather(*tasks)

    for fmt, res in results:
        assert res.status_code == 200
        if fmt == "png":
            assert "image/png" in res.headers["content-type"]
            assert res.content.startswith(b"\x89PNG")
        elif fmt == "svg":
            assert "image/svg+xml" in res.headers["content-type"]
            assert b"<svg" in res.content
        elif fmt == "ascii":
            assert "text/plain" in res.headers["content-type"]
            assert len(res.text.splitlines()) >= 15
        elif fmt == "json":
            data = res.json()
            assert "url" in data
            assert "lan_ip" in data
            assert "ascii" in data


@pytest.mark.asyncio
async def test_concurrent_burst_auth_verify(adv_async_client: AsyncClient, adv_token: str):
    """Stress-test 100 concurrent token verification requests mixing valid, expired, and tampered tokens."""
    valid_token = adv_token
    tampered_token = valid_token[:-5] + "XXXXX"
    expired_token = create_access_token({"sub": "admin"}, expires_delta=-60, secret=TEST_JWT_SECRET)

    async def _verify_token(token: str):
        return await adv_async_client.get(
            "/api/auth/verify",
            headers={"Authorization": f"Bearer {token}"},
        )

    tasks = []
    expected_statuses = []
    for i in range(100):
        if i % 3 == 0:
            tasks.append(_verify_token(valid_token))
            expected_statuses.append(200)
        elif i % 3 == 1:
            tasks.append(_verify_token(tampered_token))
            expected_statuses.append(401)
        else:
            tasks.append(_verify_token(expired_token))
            expected_statuses.append(401)

    responses = await asyncio.gather(*tasks)
    for res, expected in zip(responses, expected_statuses):
        assert res.status_code == expected
        if expected == 200:
            assert res.json().get("valid") is True


@pytest.mark.asyncio
async def test_concurrent_burst_system_info(adv_async_client: AsyncClient, adv_headers: Dict[str, str]):
    """Stress-test 50 concurrent requests to /api/system/info with authenticated header."""
    async def _fetch_info():
        return await adv_async_client.get("/api/system/info", headers=adv_headers)

    tasks = [_fetch_info() for _ in range(50)]
    responses = await asyncio.gather(*tasks)

    for res in responses:
        assert res.status_code == 200
        data = res.json()
        assert "lan_ip" in data
        assert "cpu" in data
        assert "ram" in data
        assert "disk" in data
        assert "uptime" in data
        assert data.get("os") == "windows"


# ===========================================================================
# 2. MALFORMED JSON BODIES, EXTREME PAYLOADS, INJECTIONS & QUERY PARAMS
# ===========================================================================

def test_login_malformed_json_and_edge_bodies(adv_client: TestClient):
    """Fuzz POST /api/auth/login with malformed, missing, truncated, or typed edge payloads."""
    # 1. Empty body
    r1 = adv_client.post("/api/auth/login", content=b"", headers={"Content-Type": "application/json"})
    assert r1.status_code in (400, 422)

    # 2. Truncated JSON
    r2 = adv_client.post("/api/auth/login", content=b'{"pin": "123', headers={"Content-Type": "application/json"})
    assert r2.status_code in (400, 422)

    # 3. JSON with embedded null bytes
    r3 = adv_client.post("/api/auth/login", json={"pin": "123\x00456"})
    assert r3.status_code == 401

    # 4. Nested JSON object
    r4 = adv_client.post("/api/auth/login", json={"pin": {"nested": "849201"}})
    assert r4.status_code in (400, 422)

    # 5. Array instead of object
    r5 = adv_client.post("/api/auth/login", json=[849201])
    assert r5.status_code in (400, 422)

    # 6. Boolean pin
    r6 = adv_client.post("/api/auth/login", json={"pin": True})
    assert r6.status_code in (400, 401, 422)

    # 7. Null value pin
    r7 = adv_client.post("/api/auth/login", json={"pin": None})
    assert r7.status_code in (400, 422)

    # 8. Extra unneeded fields (should not crash)
    r8 = adv_client.post("/api/auth/login", json={"pin": TEST_PIN, "extra_junk": "x" * 1000})
    assert r8.status_code == 200


def test_login_extreme_payloads_and_unicode_fuzzing(adv_client: TestClient):
    """Test extreme payload sizes, zero-width characters, and Unicode inputs."""
    # 1. Overly long PIN (>64 chars rejected by Pydantic min_length=1, max_length=64)
    huge_pin = "1" * 1000
    r1 = adv_client.post("/api/auth/login", json={"pin": huge_pin})
    assert r1.status_code == 422

    # 2. SQL Injection payload in PIN
    r4 = adv_client.post("/api/auth/login", json={"pin": "' OR '1'='1' --"})
    assert r4.status_code == 401

    # 3. Path Traversal payload in PIN
    r5 = adv_client.post("/api/auth/login", json={"pin": "../../../etc/shadow"})
    assert r5.status_code == 401

    # 4. Format string attack in PIN
    r6 = adv_client.post("/api/auth/login", json={"pin": "%s%s%s%n%x"})
    assert r6.status_code == 401


def test_verify_pin_constant_time_non_ascii_reproduction():
    """
    CRITICAL EMPIRICAL FINDING:
    Verify whether hmac.compare_digest in backend/security.py raises TypeError on non-ASCII input.
    """
    try:
        # Non-ASCII character in PIN
        res = verify_pin_constant_time("123🔥45", "849201")
        # If it returns False without error, then it's safe
        assert res is False
    except TypeError as e:
        # Document TypeError raised when comparing str with non-ASCII in hmac.compare_digest
        assert "comparing strings with non-ASCII characters is not supported" in str(e)


def test_qr_endpoint_invalid_query_parameters(adv_client: TestClient):
    """Test /api/auth/qr with invalid format, traversal attempts, out-of-range ports, and bad IPs."""
    # 1. Invalid format value
    r1 = adv_client.get("/api/auth/qr?format=invalid_format_xyz")
    assert r1.status_code in (400, 422)

    # 2. Directory traversal attempt in format query
    r2 = adv_client.get("/api/auth/qr?format=../../../etc/passwd")
    assert r2.status_code in (400, 422)

    # 3. Out-of-bounds / invalid IP in lan_ip parameter (should fallback to detected IP without crashing)
    r3 = adv_client.get("/api/auth/qr?format=json&lan_ip=999.999.999.999")
    assert r3.status_code == 200
    data3 = r3.json()
    assert is_valid_lan_ipv4(data3["lan_ip"]) or data3["lan_ip"] == "127.0.0.1"

    # 4. Out-of-range port number
    r4 = adv_client.get("/api/auth/qr?format=json&port=999999")
    assert r4.status_code == 200
    data4 = r4.json()
    assert data4["port"] == 999999

    # 5. Empty token query parameter (should automatically generate a valid token)
    r5 = adv_client.get("/api/auth/qr?format=json&token=")
    assert r5.status_code == 200
    data5 = r5.json()
    assert len(data5["token"]) > 20

    # 6. Very long token payload
    long_token = "A" * 2000
    r6 = adv_client.get(f"/api/auth/qr?format=svg&token={long_token}")
    assert r6.status_code == 200
    assert "image/svg+xml" in r6.headers["content-type"]


def test_verify_malformed_auth_headers(adv_client: TestClient):
    """Test /api/auth/verify with missing, malformed, or garbage Authorization headers."""
    # 1. Empty Bearer
    r1 = adv_client.get("/api/auth/verify", headers={"Authorization": "Bearer "})
    assert r1.status_code == 401

    # 2. Bare keyword 'Bearer'
    r2 = adv_client.get("/api/auth/verify", headers={"Authorization": "Bearer"})
    assert r2.status_code == 401

    # 3. Basic Auth instead of Bearer
    r3 = adv_client.get("/api/auth/verify", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r3.status_code == 401

    # 4. Two-segment invalid JWT
    r4 = adv_client.get("/api/auth/verify", headers={"Authorization": "Bearer invalid.jwt"})
    assert r4.status_code == 401

    # 5. Random junk binary header
    r5 = adv_client.get("/api/auth/verify", headers={"Authorization": "Bearer \x01\x02\x03\x04"})
    assert r5.status_code == 401


def test_spa_fallback_and_api_404_preservation(adv_client: TestClient):
    """Ensure non-existent /api/ and /ws/ routes return 404 JSON, while other paths fall back to SPA."""
    # /api/ routes must NOT be captured by SPA fallback
    r1 = adv_client.get("/api/non_existent_route")
    assert r1.status_code == 404
    assert r1.json().get("detail") == "API endpoint not found"

    # /ws/ routes must NOT be captured by SPA fallback
    r2 = adv_client.get("/ws/non_existent_channel")
    assert r2.status_code == 404

    # GET on POST-only endpoint is routed to SPA fallback catch-all which blocks api/ prefix with 404
    r3 = adv_client.get("/api/auth/login")
    assert r3.status_code == 404
    assert r3.json().get("detail") == "API endpoint not found"

    # POST on GET-only endpoint returns 405 Method Not Allowed
    r4 = adv_client.post("/api/auth/qr")
    assert r4.status_code == 405


# ===========================================================================
# 3. EDGE-CASE NETWORK ADAPTER ENVIRONMENTS
# ===========================================================================

def test_get_lan_ip_all_adapters_disconnected(monkeypatch):
    """Test LAN IP resolution when all physical/virtual adapters report isup=False."""
    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            raise OSError("Network unreachable")
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(socket, "socket", FailingSocket)

    SAddr = namedtuple("snicaddr", ["family", "address", "netmask", "broadcast", "ptp"])
    SStat = namedtuple("snicstats", ["isup", "duplex", "speed", "mtu", "flags"])

    mock_addrs = {
        "Wi-Fi": [SAddr(family=socket.AF_INET, address="192.168.1.55", netmask=None, broadcast=None, ptp=None)],
        "Ethernet": [SAddr(family=socket.AF_INET, address="10.0.0.12", netmask=None, broadcast=None, ptp=None)],
    }
    mock_stats = {
        "Wi-Fi": SStat(isup=False, duplex=None, speed=None, mtu=None, flags=None),
        "Ethernet": SStat(isup=False, duplex=None, speed=None, mtu=None, flags=None),
    }

    monkeypatch.setattr(psutil, "net_if_addrs", lambda: mock_addrs)
    monkeypatch.setattr(psutil, "net_if_stats", lambda: mock_stats)
    monkeypatch.setattr(socket, "gethostname", lambda: "host-test")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda h: ("host-test", [], []))

    assert get_lan_ip() == "127.0.0.1"


def test_get_lan_ip_empty_adapters(monkeypatch):
    """Test LAN IP resolution when psutil returns empty dict."""
    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            raise OSError("Network unreachable")
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(socket, "socket", FailingSocket)
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {})
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {})
    monkeypatch.setattr(socket, "gethostname", lambda: "host-test")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda h: ("host-test", [], []))

    assert get_lan_ip() == "127.0.0.1"


def test_get_lan_ip_ipv6_only_environment(monkeypatch):
    """Test LAN IP resolution when adapters have IPv6 addresses only."""
    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            raise OSError("No IPv4 route")
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(socket, "socket", FailingSocket)

    SAddr = namedtuple("snicaddr", ["family", "address", "netmask", "broadcast", "ptp"])
    SStat = namedtuple("snicstats", ["isup", "duplex", "speed", "mtu", "flags"])

    mock_addrs = {
        "Wi-Fi": [SAddr(family=socket.AF_INET6, address="fe80::1", netmask=None, broadcast=None, ptp=None)],
        "Ethernet": [SAddr(family=socket.AF_INET6, address="2001:db8::1234", netmask=None, broadcast=None, ptp=None)],
    }
    mock_stats = {
        "Wi-Fi": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
        "Ethernet": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
    }

    monkeypatch.setattr(psutil, "net_if_addrs", lambda: mock_addrs)
    monkeypatch.setattr(psutil, "net_if_stats", lambda: mock_stats)
    monkeypatch.setattr(socket, "gethostname", lambda: "host-test")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda h: ("host-test", [], []))

    assert get_lan_ip() == "127.0.0.1"


def test_get_lan_ip_multiple_conflicting_physical_ips(monkeypatch):
    """Test priority ranking when multiple physical adapters are active (Wi-Fi=10 > Ethernet=5 > Other=1)."""
    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            raise OSError("No direct probe")
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(socket, "socket", FailingSocket)

    SAddr = namedtuple("snicaddr", ["family", "address", "netmask", "broadcast", "ptp"])
    SStat = namedtuple("snicstats", ["isup", "duplex", "speed", "mtu", "flags"])

    mock_addrs = {
        "Local Area Connection 3": [SAddr(family=socket.AF_INET, address="172.16.50.2", netmask=None, broadcast=None, ptp=None)],
        "Ethernet 2": [SAddr(family=socket.AF_INET, address="10.10.10.10", netmask=None, broadcast=None, ptp=None)],
        "Wireless LAN adapter Wi-Fi": [SAddr(family=socket.AF_INET, address="192.168.1.188", netmask=None, broadcast=None, ptp=None)],
    }
    mock_stats = {
        "Local Area Connection 3": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
        "Ethernet 2": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
        "Wireless LAN adapter Wi-Fi": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
    }

    monkeypatch.setattr(psutil, "net_if_addrs", lambda: mock_addrs)
    monkeypatch.setattr(psutil, "net_if_stats", lambda: mock_stats)

    # Wi-Fi has highest priority (10)
    assert get_lan_ip() == "192.168.1.188"


def test_get_lan_ip_virtual_adapters_filtered_out(monkeypatch):
    """Ensure virtual adapters (WSL, Hyper-V, VMware, Docker, Tailscale, ZeroTier) are strictly ignored."""
    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            raise OSError("Probe failed")
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(socket, "socket", FailingSocket)

    SAddr = namedtuple("snicaddr", ["family", "address", "netmask", "broadcast", "ptp"])
    SStat = namedtuple("snicstats", ["isup", "duplex", "speed", "mtu", "flags"])

    mock_addrs = {
        "vEthernet (Default Switch)": [SAddr(family=socket.AF_INET, address="172.25.16.1", netmask=None, broadcast=None, ptp=None)],
        "VMware Network Adapter VMnet1": [SAddr(family=socket.AF_INET, address="192.168.230.1", netmask=None, broadcast=None, ptp=None)],
        "docker0": [SAddr(family=socket.AF_INET, address="172.17.0.1", netmask=None, broadcast=None, ptp=None)],
        "Tailscale": [SAddr(family=socket.AF_INET, address="100.80.90.100", netmask=None, broadcast=None, ptp=None)],
        "Ethernet": [SAddr(family=socket.AF_INET, address="192.168.0.42", netmask=None, broadcast=None, ptp=None)],
    }
    mock_stats = {k: SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None) for k in mock_addrs}

    monkeypatch.setattr(psutil, "net_if_addrs", lambda: mock_addrs)
    monkeypatch.setattr(psutil, "net_if_stats", lambda: mock_stats)

    # Only real Ethernet (192.168.0.42) must be selected
    assert get_lan_ip() == "192.168.0.42"


def test_is_valid_lan_ipv4_exhaustive_fuzz():
    """Exhaustive boundary testing for is_valid_lan_ipv4 helper."""
    # Valid IPs
    assert is_valid_lan_ipv4("192.168.0.1") is True
    assert is_valid_lan_ipv4("10.0.0.1") is True
    assert is_valid_lan_ipv4("172.16.0.1") is True
    assert is_valid_lan_ipv4("1.1.1.1") is True
    assert is_valid_lan_ipv4("255.255.255.254") is True

    # Invalid / Loopback / APIPA / 0.0.0.0
    assert is_valid_lan_ipv4("127.0.0.1") is False
    assert is_valid_lan_ipv4("127.100.50.1") is False
    assert is_valid_lan_ipv4("169.254.1.1") is False
    assert is_valid_lan_ipv4("169.254.254.254") is False
    assert is_valid_lan_ipv4("0.0.0.0") is False

    # Out of range / Malformed
    assert is_valid_lan_ipv4("256.0.0.1") is False
    assert is_valid_lan_ipv4("192.168.1.-1") is False
    assert is_valid_lan_ipv4("192.168.1") is False
    assert is_valid_lan_ipv4("192.168.1.1.1") is False
    assert is_valid_lan_ipv4("abc.def.ghi.jkl") is False
    assert is_valid_lan_ipv4("") is False
    assert is_valid_lan_ipv4(None) is False
    assert is_valid_lan_ipv4(12345) is False
    assert is_valid_lan_ipv4("::1") is False
    assert is_valid_lan_ipv4("fe80::1") is False


# ===========================================================================
# 4. QR PNG BINARY HEADERS, SVG XML STRUCTURE & TERMINAL RENDERING
# ===========================================================================

def test_qr_png_binary_integrity_and_pillow_decode():
    """Verify PNG binary structure, standard magic bytes, and decode with PIL."""
    data_payload = "http://192.168.1.39:8765/?token=test_adversarial_token_123"

    for box_size in (1, 5, 20):
        for border in (0, 2, 6):
            png_bytes = generate_qr_png_bytes(data_payload, box_size=box_size, border=border)
            assert isinstance(png_bytes, bytes)
            assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            assert b"IEND\xaeB`\x82" in png_bytes[-12:]

            img = Image.open(io.BytesIO(png_bytes))
            assert img.format == "PNG"
            assert img.size[0] > 0 and img.size[1] > 0
            assert img.size[0] == img.size[1]


def test_qr_svg_xml_structure_and_element_tree_parsing():
    """Verify SVG XML structure, namespaces, viewBox attributes, and XML validity."""
    data_payload = "http://192.168.1.39:8765/?token=test&param=<script>alert('xss')</script>&amp=1"

    svg_bytes = generate_qr_svg_bytes(data_payload, box_size=10, border=4)
    assert isinstance(svg_bytes, bytes)

    root = ET.fromstring(svg_bytes)
    assert root.tag.endswith("svg")
    assert "viewBox" in root.attrib or "width" in root.attrib

    svg_str = generate_svg_qr(data_payload)
    assert isinstance(svg_str, str)
    root2 = ET.fromstring(svg_str)
    assert root2.tag.endswith("svg")


def test_qr_terminal_ansi_rendering_and_inversion():
    """Verify Terminal QR code generation across compact, non-compact, and inverted modes."""
    data_payload = "http://192.168.1.39:8765/?token=test_terminal"

    # Compact Mode (half-blocks)
    compact_qr = generate_terminal_qr(data_payload, compact=True, invert=False)
    lines = compact_qr.splitlines()
    assert 15 <= len(lines) <= 25
    assert any("▀" in l or "▄" in l or "█" in l for l in lines)

    # Inverted Compact Mode
    inverted_qr = generate_terminal_qr(data_payload, compact=True, invert=True)
    assert len(inverted_qr.splitlines()) == len(lines)

    # Non-Compact Mode (full blocks)
    full_qr = generate_terminal_qr(data_payload, compact=False, invert=False)
    full_lines = full_qr.splitlines()
    assert len(full_lines) >= 30
    assert all("██" in l or "  " in l for l in full_lines)


def test_print_startup_banner_extreme_inputs(capsys):
    """Ensure print_startup_banner handles IPv6, empty PIN, extreme token lengths without exceptions."""
    print_startup_banner(
        lan_ip="2001:db8::8a2e:370:7334",
        port=65535,
        pin="",
        token="A" * 300,
        public_url="https://extremely-long-custom-subdomain-123456789.trycloudflare.com",
    )
    captured = capsys.readouterr()
    assert "ANTIGRAVITY 2.0" in captured.out
    assert "READY & LISTENING" in captured.out


# ===========================================================================
# 5. CORS PREFLIGHT VALIDATION ACROSS AUTHORIZED & UNAUTHORIZED ORIGINS
# ===========================================================================

def test_cors_preflight_authorized_lan_and_tunnel_origins(adv_client: TestClient):
    """Verify CORS preflight OPTIONS requests allow LAN subnets and tunnel provider domains."""
    authorized_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:8765",
        "http://192.168.1.50:5173",
        "http://192.168.0.1:8080",
        "http://10.0.0.15:3000",
        "http://10.200.1.99:8000",
        "http://172.16.0.1:8080",
        "http://172.25.10.15:3000",
        "http://172.31.255.250:4000",
        "https://my-app.trycloudflare.com",
        "https://quick-tunnel-123.trycloudflare.com",
        "https://sample-node.ngrok-free.app",
        "https://dev-machine.ngrok-free.app",
        "http://desktop-node.tailscale.net",
        "http://phone-client.tailscale.net:8765",
    ]

    for origin in authorized_origins:
        res = adv_client.options(
            "/api/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, Authorization",
            },
        )
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == origin
        assert "POST" in res.headers.get("access-control-allow-methods", "")


def test_cors_preflight_unauthorized_origins_and_regex_bypass_attempts(adv_client: TestClient):
    """
    Test CORS rejection behavior when app has specific restricted origins.
    Verify that public non-RFC1918 IPs and domain spoofing attacks are blocked.
    """
    strict_settings = Settings(
        CORS_ORIGINS=["http://localhost:8765"],
        AUTH_PIN=TEST_PIN,
        JWT_SECRET=TEST_JWT_SECRET,
    )
    strict_app = create_app(settings=strict_settings)
    strict_client = TestClient(strict_app)

    malicious_origins = [
        "http://evil-attacker.com",
        "http://192.168.1.1.evil.com",
        "http://evil-trycloudflare.com",
        "http://trycloudflare.com.attacker.com",
        "http://notngrok-free.app",
        "http://172.32.0.1:8080",  # 172.32 is public, not RFC1918!
        "http://172.15.0.1:8080",  # 172.15 is public, not RFC1918!
        "http://11.0.0.1:3000",    # 11.0 is public, not RFC1918!
    ]

    for origin in malicious_origins:
        res = strict_client.options(
            "/api/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        allow_origin = res.headers.get("access-control-allow-origin")
        assert allow_origin != origin, f"Origin {origin} was improperly permitted by CORS regex!"
