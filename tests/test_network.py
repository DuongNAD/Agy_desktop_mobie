"""
Tests for Network Discovery, QR Code Generation, and Tunneling Hooks (backend/network.py)
"""

import io
import json
import socket
from unittest.mock import MagicMock, patch

import psutil
import pytest
from starlette.testclient import TestClient

from backend.network import (
    TunnelInfo,
    generate_png_qr,
    generate_qr_png_bytes,
    generate_qr_svg_bytes,
    generate_svg_qr,
    generate_terminal_qr,
    get_active_tunnel_url,
    get_all_tunnels_status,
    get_cloudflare_status,
    get_lan_ip,
    get_ngrok_status,
    get_tailscale_status,
    is_valid_lan_ipv4,
    print_startup_banner,
)


# =====================================================================
# Subsystem 1: LAN IPv4 Validation & Resolution Tests
# =====================================================================

def test_is_valid_lan_ipv4():
    """Verify that private and standard routable IPv4 addresses pass, while loopback, link-local, and bad IPs fail."""
    assert is_valid_lan_ipv4("192.168.1.100") is True
    assert is_valid_lan_ipv4("10.0.0.1") is True
    assert is_valid_lan_ipv4("172.16.0.50") is True
    assert is_valid_lan_ipv4("172.31.255.254") is True
    assert is_valid_lan_ipv4("8.8.8.8") is True

    # Loopback
    assert is_valid_lan_ipv4("127.0.0.1") is False
    assert is_valid_lan_ipv4("127.0.1.1") is False

    # Link-local / APIPA
    assert is_valid_lan_ipv4("169.254.1.1") is False
    assert is_valid_lan_ipv4("169.254.255.255") is False

    # 0.0.0.0
    assert is_valid_lan_ipv4("0.0.0.0") is False

    # Invalid octets / formats
    assert is_valid_lan_ipv4("256.0.0.1") is False
    assert is_valid_lan_ipv4("192.168.1") is False
    assert is_valid_lan_ipv4("invalid-ip") is False
    assert is_valid_lan_ipv4("") is False
    assert is_valid_lan_ipv4(None) is False


def test_get_lan_ip_override():
    """Explicit override IP is returned if valid."""
    assert get_lan_ip("192.168.1.99") == "192.168.1.99"
    # Invalid override falls back to detection
    detected = get_lan_ip("invalid_ip")
    assert is_valid_lan_ipv4(detected) or detected == "127.0.0.1"


def test_get_lan_ip_udp_probe(monkeypatch):
    """Tier 1: UDP routing socket probe successfully detects primary LAN IP."""
    class MockSocket:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, timeout):
            pass
        def connect(self, addr):
            pass
        def getsockname(self):
            return ("192.168.1.150", 54321)

    monkeypatch.setattr(socket, "socket", MockSocket)
    assert get_lan_ip() == "192.168.1.150"


def test_get_lan_ip_psutil_fallback(monkeypatch):
    """Tier 2: psutil interface scan prioritizes Wi-Fi / Ethernet over virtual adapters."""
    # Force UDP probe to fail
    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            raise OSError("Network unreachable")
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(socket, "socket", FailingSocket)

    from collections import namedtuple
    SAddr = namedtuple("snicaddr", ["family", "address", "netmask", "broadcast", "ptp"])
    SStat = namedtuple("snicstats", ["isup", "duplex", "speed", "mtu", "flags"])

    mock_addrs = {
        "vEthernet (WSL)": [SAddr(family=socket.AF_INET, address="172.28.0.1", netmask=None, broadcast=None, ptp=None)],
        "VMware Network Adapter VMnet8": [SAddr(family=socket.AF_INET, address="192.168.199.1", netmask=None, broadcast=None, ptp=None)],
        "Wi-Fi": [SAddr(family=socket.AF_INET, address="192.168.1.75", netmask=None, broadcast=None, ptp=None)],
        "Ethernet": [SAddr(family=socket.AF_INET, address="10.0.0.25", netmask=None, broadcast=None, ptp=None)],
    }
    mock_stats = {
        "vEthernet (WSL)": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
        "VMware Network Adapter VMnet8": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
        "Wi-Fi": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
        "Ethernet": SStat(isup=True, duplex=None, speed=None, mtu=None, flags=None),
    }

    monkeypatch.setattr(psutil, "net_if_addrs", lambda: mock_addrs)
    monkeypatch.setattr(psutil, "net_if_stats", lambda: mock_stats)

    # Should select Wi-Fi (priority 10) -> 192.168.1.75
    assert get_lan_ip() == "192.168.1.75"


def test_get_lan_ip_localhost_fallback(monkeypatch):
    """Tier 4: Total network failure falls back to 127.0.0.1."""
    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            raise OSError("Network down")
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(socket, "socket", FailingSocket)
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {})
    monkeypatch.setattr(socket, "gethostname", lambda: "mock-host")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda h: ("mock-host", [], []))

    assert get_lan_ip() == "127.0.0.1"


# =====================================================================
# Subsystem 2: Terminal ASCII / Unicode QR Tests
# =====================================================================

def test_generate_terminal_qr_compact():
    """Compact QR generation produces ~19 lines using 2-in-1 half-block Unicode characters."""
    qr_text = generate_terminal_qr("http://192.168.1.39:8765/?token=test", compact=True)
    lines = qr_text.splitlines()

    # Compact QR is typically 17-21 lines
    assert 15 <= len(lines) <= 25
    # Contains Unicode block / half-block chars
    assert any("█" in line or "▀" in line or "▄" in line for line in lines)


def test_generate_terminal_qr_non_compact():
    """Non-compact QR produces standard 2-char full block per module."""
    qr_text = generate_terminal_qr("http://192.168.1.39:8765/?token=test", compact=False)
    lines = qr_text.splitlines()
    assert len(lines) >= 30
    assert all("██" in line or "  " in line for line in lines)


def test_print_startup_banner_execution(capsys):
    """print_startup_banner executes without error and prints formatted banner."""
    print_startup_banner(
        lan_ip="192.168.1.39",
        port=8765,
        pin="849201",
        token="jwt_token_sample_1234567890",
        pairing_url="http://192.168.1.39:8765/?token=jwt_token_sample_1234567890"
    )
    captured = capsys.readouterr()
    assert "ANTIGRAVITY 2.0" in captured.out or "AGYMOBILE" in captured.out
    assert "849201" in captured.out
    assert "192.168.1.39:8765" in captured.out


# =====================================================================
# Subsystem 3: REST QR Image Generator Tests (PNG & SVG)
# =====================================================================

def test_generate_qr_png_bytes():
    """PNG generator returns valid binary PNG starting with standard magic bytes."""
    png_bytes = generate_qr_png_bytes("http://192.168.1.39:8765/?token=test")
    assert isinstance(png_bytes, bytes)
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png_bytes) > 200

    # Alias check
    alias_bytes = generate_png_qr("http://192.168.1.39:8765/?token=test")
    assert alias_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_generate_qr_svg_bytes_and_string():
    """SVG generator returns valid vector XML with <svg and <path> elements."""
    svg_bytes = generate_qr_svg_bytes("http://192.168.1.39:8765/?token=test")
    assert isinstance(svg_bytes, bytes)
    assert b"<svg" in svg_bytes
    assert b"</svg>" in svg_bytes

    svg_str = generate_svg_qr("http://192.168.1.39:8765/?token=test")
    assert isinstance(svg_str, str)
    assert "<svg" in svg_str
    assert "</svg>" in svg_str


def test_qr_rest_endpoint_formats(client):
    """REST API /api/auth/qr returns correct content types across format query options."""
    # SVG format
    res_svg = client.get("/api/auth/qr?format=svg")
    assert res_svg.status_code == 200
    assert "image/svg+xml" in res_svg.headers.get("content-type", "")

    # PNG format
    res_png = client.get("/api/auth/qr?format=png")
    assert res_png.status_code == 200
    assert "image/png" in res_png.headers.get("content-type", "")

    # JSON format
    res_json = client.get("/api/auth/qr?format=json")
    assert res_json.status_code == 200
    assert "url" in res_json.json()


# =====================================================================
# Subsystem 4: Internet Tunneling Hooks Tests
# =====================================================================

def test_ngrok_status_when_active(monkeypatch):
    """Mock active ngrok API response at 127.0.0.1:4040."""
    import urllib.request
    mock_payload = {
        "tunnels": [
            {"name": "agy", "public_url": "https://xyz123.ngrok-free.app", "proto": "https"}
        ]
    }

    class MockResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps(mock_payload).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0.8: MockResponse())

    info = get_ngrok_status()
    assert info.provider == "ngrok"
    assert info.status == "active"
    assert info.public_url == "https://xyz123.ngrok-free.app"


def test_ngrok_status_when_inactive(monkeypatch):
    """When ngrok is not running, return inactive or not_installed."""
    import urllib.request
    def _fail(*args, **kwargs):
        raise ConnectionRefusedError("Connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", _fail)

    info = get_ngrok_status()
    assert info.provider == "ngrok"
    assert info.status in ("inactive", "not_installed")


def test_cloudflare_status_query():
    """Verify get_cloudflare_status returns TunnelInfo for cloudflare."""
    info = get_cloudflare_status()
    assert isinstance(info, TunnelInfo)
    assert info.provider == "cloudflare"
    assert info.status in ("active", "inactive", "not_installed")


def test_tailscale_status_query():
    """Verify get_tailscale_status returns TunnelInfo for tailscale."""
    info = get_tailscale_status()
    assert isinstance(info, TunnelInfo)
    assert info.provider == "tailscale"
    assert info.status in ("active", "inactive", "not_installed")


def test_get_all_tunnels_status():
    """get_all_tunnels_status returns a list with entries for all 3 providers."""
    tunnels = get_all_tunnels_status()
    assert len(tunnels) == 3
    providers = {t.provider for t in tunnels}
    assert providers == {"ngrok", "tailscale", "cloudflare"}


def test_get_active_tunnel_url(monkeypatch):
    """get_active_tunnel_url returns URL of the first active tunnel provider."""
    mock_tunnels = [
        TunnelInfo(provider="ngrok", status="inactive"),
        TunnelInfo(provider="tailscale", status="active", public_url="http://100.64.1.2"),
        TunnelInfo(provider="cloudflare", status="inactive"),
    ]
    monkeypatch.setattr("backend.network.get_all_tunnels_status", lambda: mock_tunnels)
    assert get_active_tunnel_url() == "http://100.64.1.2"
