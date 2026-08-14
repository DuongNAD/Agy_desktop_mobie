"""
AgyMobile Network Discovery, QR Code Engine, and Tunneling Hooks
Author: AgyMobile Team
Module: backend.network
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple
import urllib.request

import psutil
import qrcode
import qrcode.constants
import qrcode.image.svg


# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------

VIRTUAL_IFACE_KEYWORDS = [
    "loopback", "vethernet", "wsl", "hyper-v", "vmnet", "virtualbox",
    "docker", "tailscale", "zerotier", "wireguard", "vpn", "bluetooth",
    "teredo", "npcap", "pcap", "host-only"
]

DEFAULT_PROBE_TARGETS = [
    ("8.8.8.8", 80),
    ("1.1.1.1", 80),
    ("10.255.255.255", 1),
]


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class TunnelInfo:
    provider: str                    # "cloudflare" | "tailscale" | "ngrok" | "none"
    status: str                      # "active" | "inactive" | "not_installed" | "error"
    public_url: Optional[str] = None # e.g. "https://xxx.trycloudflare.com" or "https://xxx.ngrok-free.app"
    latency_ms: Optional[float] = None
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Subsystem 1: LAN IPv4 Auto-Detection
# ---------------------------------------------------------------------------

def is_valid_lan_ipv4(ip: str) -> bool:
    """Validate that an IPv4 address is not loopback, link-local, or 0.0.0.0."""
    if not ip or not isinstance(ip, str):
        return False
    parts = ip.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
        if any(o < 0 or o > 255 for o in octets):
            return False
    except ValueError:
        return False

    # Filter out loopback (127.0.0.0/8)
    if octets[0] == 127:
        return False
    # Filter out APIPA / link-local (169.254.0.0/16)
    if octets[0] == 169 and octets[1] == 254:
        return False
    # Filter out 0.0.0.0
    if octets[0] == 0:
        return False

    return True


def get_lan_ip(override_ip: Optional[str] = None) -> str:
    """
    Resolve the primary LAN IPv4 address using a 4-tier fallback cascade:
    1. UDP socket probe (packetless routing table lookup)
    2. psutil interface scan with virtual adapter filtering and priority scoring
    3. socket.gethostbyname_ex(hostname) RFC1918 filter
    4. Fallback to '127.0.0.1'
    """
    if override_ip and is_valid_lan_ipv4(override_ip):
        return override_ip

    # Tier 1: UDP Socket Probe
    for host, port in DEFAULT_PROBE_TARGETS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.3)
                s.connect((host, port))
                ip = s.getsockname()[0]
                if is_valid_lan_ipv4(ip):
                    return ip
        except Exception:
            continue

    # Tier 2: psutil interface scan
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        candidates: List[Tuple[int, str]] = []

        for iface_name, addr_list in addrs.items():
            iface_lower = iface_name.lower()
            if any(kw in iface_lower for kw in VIRTUAL_IFACE_KEYWORDS):
                continue
            iface_stat = stats.get(iface_name)
            if iface_stat and not iface_stat.isup:
                continue

            for addr in addr_list:
                if addr.family == socket.AF_INET and is_valid_lan_ipv4(addr.address):
                    priority = 1
                    if any(w in iface_lower for w in ["wi-fi", "wlan", "wireless", "airport"]):
                        priority = 10
                    elif any(w in iface_lower for w in ["ethernet", "eth", "en", "lan"]):
                        priority = 5
                    candidates.append((priority, addr.address))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
    except Exception:
        pass

    # Tier 3: socket.gethostbyname_ex
    try:
        hostname = socket.gethostname()
        _, _, ips = socket.gethostbyname_ex(hostname)
        for ip in ips:
            if is_valid_lan_ipv4(ip):
                return ip
    except Exception:
        pass

    # Tier 4: Fallback
    return "127.0.0.1"


# ---------------------------------------------------------------------------
# Subsystem 2: Terminal ASCII / Unicode QR Code Generator
# ---------------------------------------------------------------------------

def generate_terminal_qr(
    data: str,
    invert: bool = False,
    compact: bool = True,
    border: int = 2,
) -> str:
    """
    Generate high-contrast ASCII/Unicode QR string for console output.
    When compact=True, encodes 2 vertical modules into half-block characters
    (▀, ▄, █, space) reducing terminal height by 50%.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    rows = len(matrix)
    cols = len(matrix[0]) if rows > 0 else 0

    if not compact:
        lines = []
        for r in range(rows):
            line_chars = []
            for c in range(cols):
                is_dark = matrix[r][c]
                if invert:
                    is_dark = not is_dark
                line_chars.append("██" if is_dark else "  ")
            lines.append("".join(line_chars))
        return "\n".join(lines)

    lines = []
    for r in range(0, rows, 2):
        line = []
        for c in range(cols):
            top_dark = matrix[r][c]
            bot_dark = matrix[r + 1][c] if (r + 1) < rows else False
            if invert:
                top_dark = not top_dark
                bot_dark = not bot_dark

            # Standard dark terminal mapping:
            # QR dark (True) -> Black (0), QR light (False) -> White (1)
            top_white = not top_dark
            bot_white = not bot_dark

            if top_white and bot_white:
                line.append("█")
            elif top_white and not bot_white:
                line.append("▀")
            elif not top_white and bot_white:
                line.append("▄")
            else:
                line.append(" ")
        lines.append("".join(line))
    return "\n".join(lines)


def print_startup_banner(
    lan_ip: str,
    port: int,
    pin: str,
    token: Optional[str] = None,
    public_url: Optional[str] = None,
    pairing_url: Optional[str] = None,
) -> None:
    """Print framed server startup banner with compact QR code to console."""
    # Ensure stdout handles UTF-8 on Windows
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    target_url = pairing_url or public_url or f"http://{lan_ip}:{port}"
    if not pairing_url and token:
        qr_payload = f"{target_url}/?token={token}"
    else:
        qr_payload = target_url

    qr_ascii = generate_terminal_qr(qr_payload, compact=True, border=2)

    width = 68
    sep = "─" * (width - 2)

    print(f"\n┌{sep}┐")
    print(f"│{'ANTIGRAVITY 2.0 - DESKTOP MOBILE CONTROLLER (AgyMobile)'.center(width - 2)}│")
    print(f"├{sep}┤")
    for line in qr_ascii.splitlines():
        print(f"│{line.center(width - 2)}│")
    print(f"├{sep}┤")
    print(f"│  ► Mobile URL : {target_url:<47}│")
    print(f"│  ► Access PIN : {pin:<47}│")
    if token:
        masked_tok = token[:12] + "..." + token[-6:] if len(token) > 20 else token
        print(f"│  ► Auto-Token : {masked_tok:<47}│")
    print(f"│  ► Status     : READY & LISTENING{' ' * 30}│")
    print(f"└{sep}┘\n")


# ---------------------------------------------------------------------------
# Subsystem 3: REST QR Image Generator (PNG & SVG)
# ---------------------------------------------------------------------------

def generate_qr_png_bytes(
    data: str,
    box_size: int = 10,
    border: int = 4,
) -> bytes:
    """Generate PNG bytes buffer for QR code."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_png_qr(data: str, box_size: int = 10, border: int = 4) -> bytes:
    """Alias for generate_qr_png_bytes."""
    return generate_qr_png_bytes(data, box_size=box_size, border=border)


def generate_qr_svg_bytes(
    data: str,
    box_size: int = 10,
    border: int = 4,
) -> bytes:
    """Generate SVG XML bytes buffer for QR code using SvgPathImage."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


def generate_svg_qr(data: str, box_size: int = 10, border: int = 4) -> str:
    """Generate SVG string for QR code."""
    raw_bytes = generate_qr_svg_bytes(data, box_size=box_size, border=border)
    return raw_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# Subsystem 4: Internet Tunneling Hooks
# ---------------------------------------------------------------------------

def get_ngrok_status() -> TunnelInfo:
    """Detect ngrok status via local inspection API at 127.0.0.1:4040."""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:4040/api/tunnels",
            headers={"User-Agent": "AgyMobile-Monitor"},
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=0.8) as response:
            data = json.loads(response.read().decode("utf-8"))
            latency = (time.perf_counter() - t0) * 1000.0
            tunnels = data.get("tunnels", [])
            if tunnels:
                pub_url = tunnels[0].get("public_url")
                return TunnelInfo(
                    provider="ngrok",
                    status="active",
                    public_url=pub_url,
                    latency_ms=round(latency, 2),
                    details={"count": len(tunnels)},
                )
            return TunnelInfo(provider="ngrok", status="inactive", details={"msg": "No active tunnels"})
    except Exception as e:
        installed = shutil.which("ngrok") is not None
        return TunnelInfo(
            provider="ngrok",
            status="inactive" if installed else "not_installed",
            details={"error": str(e)},
        )


def get_tailscale_status() -> TunnelInfo:
    """Detect Tailscale status via CLI or CGNAT network adapter (100.64.0.0/10)."""
    ts_bin = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    if os.path.exists(ts_bin) if not shutil.which("tailscale") else True:
        try:
            res = subprocess.run(
                [ts_bin, "status", "--json"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                data = json.loads(res.stdout)
                self_node = data.get("Self", {})
                ips = self_node.get("TailscaleIPs", [])
                dns = self_node.get("DNSName", "")
                state = data.get("BackendState", "Running")
                return TunnelInfo(
                    provider="tailscale",
                    status="active" if state == "Running" else "inactive",
                    public_url=f"http://{ips[0]}" if ips else None,
                    details={"dns": dns, "ips": ips, "state": state},
                )
        except Exception:
            pass

    # Check network interfaces for 100.x Tailscale IP
    try:
        for iface, addrs in psutil.net_if_addrs().items():
            if "tailscale" in iface.lower():
                for addr in addrs:
                    if addr.family == socket.AF_INET and addr.address.startswith("100."):
                        return TunnelInfo(
                            provider="tailscale",
                            status="active",
                            public_url=f"http://{addr.address}",
                            details={"iface": iface},
                        )
    except Exception:
        pass

    return TunnelInfo(provider="tailscale", status="not_installed")


def get_cloudflare_status() -> TunnelInfo:
    """Detect Cloudflare Tunnel (cloudflared) status."""
    cf_bin = shutil.which("cloudflared")
    if not cf_bin:
        return TunnelInfo(provider="cloudflare", status="not_installed")

    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            if "cloudflared" in (proc.info.get("name") or "").lower():
                return TunnelInfo(
                    provider="cloudflare",
                    status="active",
                    details={"pid": proc.pid},
                )
    except Exception:
        pass

    return TunnelInfo(provider="cloudflare", status="inactive")


def get_all_tunnels_status() -> List[TunnelInfo]:
    """Query status of all supported tunnel providers."""
    return [
        get_ngrok_status(),
        get_tailscale_status(),
        get_cloudflare_status(),
    ]


def get_active_tunnel_url() -> Optional[str]:
    """Return public URL of the first active tunnel provider, if any."""
    for tunnel in get_all_tunnels_status():
        if tunnel.status == "active" and tunnel.public_url:
            return tunnel.public_url
    return None
