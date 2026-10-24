"""
AgyMobile Tunnel Manager
Auto-detect and manage Cloudflare Tunnel / Ngrok for secure remote access.
Spawns tunnel subprocess on server startup, parses public URL, and manages lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("agymobile.tunnel")


class TunnelProvider(str, Enum):
    """Supported tunnel providers."""
    CLOUDFLARE = "cloudflare"
    NGROK = "ngrok"
    NONE = "none"


@dataclass
class TunnelStatus:
    """Current tunnel connection status."""
    provider: TunnelProvider = TunnelProvider.NONE
    url: Optional[str] = None
    is_running: bool = False
    error: Optional[str] = None
    started_at: Optional[float] = None
    pid: Optional[int] = None


class TunnelManager:
    """
    Manages automatic tunnel creation for remote mobile access.
    
    Supports:
    - Cloudflare Quick Tunnel (cloudflared tunnel --url) — zero-config, free
    - Ngrok (ngrok http) — requires auth token for extended use
    
    Usage:
        tunnel = TunnelManager(port=8765)
        await tunnel.start()   # Auto-detects provider and starts tunnel
        print(tunnel.status.url)  # https://random-id.trycloudflare.com
        await tunnel.stop()
    """

    # Regex patterns to extract tunnel URLs from subprocess output
    _CF_URL_PATTERN = re.compile(
        r"https?://[a-zA-Z0-9\-]+\.trycloudflare\.com",
        re.IGNORECASE,
    )
    _NGROK_URL_PATTERN = re.compile(
        r"https?://[a-zA-Z0-9\-]+\.ngrok-free\.app",
        re.IGNORECASE,
    )
    _GENERIC_HTTPS_PATTERN = re.compile(
        r"(https://[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-\.]+(?:\.[a-z]{2,}))",
        re.IGNORECASE,
    )

    def __init__(
        self,
        port: int = 8765,
        provider: TunnelProvider = TunnelProvider.CLOUDFLARE,
        auto_fallback: bool = True,
        startup_timeout: float = 30.0,
    ) -> None:
        self.port = port
        self.preferred_provider = provider
        self.auto_fallback = auto_fallback
        self.startup_timeout = startup_timeout

        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._status = TunnelStatus()
        self._output_buffer: list[str] = []
        self._url_event = asyncio.Event()

    @property
    def status(self) -> TunnelStatus:
        """Get current tunnel status."""
        return self._status

    @property
    def url(self) -> Optional[str]:
        """Get the public tunnel URL, or None if not connected."""
        return self._status.url

    @property
    def is_running(self) -> bool:
        """Check if tunnel is currently active."""
        return self._status.is_running and self._process is not None

    @staticmethod
    def detect_cloudflared() -> Optional[str]:
        """Detect if cloudflared is installed and return its path."""
        path = shutil.which("cloudflared")
        if path:
            return path
        # Check common Windows install locations
        common_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\cloudflared\cloudflared.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\cloudflared\cloudflared.exe"),
            os.path.expandvars(r"%PROGRAMFILES(x86)%\cloudflared\cloudflared.exe"),
            r"C:\cloudflared\cloudflared.exe",
        ]
        for p in common_paths:
            if os.path.isfile(p):
                return p
        return None

    @staticmethod
    def detect_ngrok() -> Optional[str]:
        """Detect if ngrok is installed and return its path."""
        return shutil.which("ngrok")

    def detect_best_provider(self) -> TunnelProvider:
        """Auto-detect the best available tunnel provider."""
        if self.preferred_provider == TunnelProvider.CLOUDFLARE:
            if self.detect_cloudflared():
                return TunnelProvider.CLOUDFLARE
            if self.auto_fallback and self.detect_ngrok():
                logger.info("cloudflared not found, falling back to ngrok")
                return TunnelProvider.NGROK
        elif self.preferred_provider == TunnelProvider.NGROK:
            if self.detect_ngrok():
                return TunnelProvider.NGROK
            if self.auto_fallback and self.detect_cloudflared():
                logger.info("ngrok not found, falling back to cloudflared")
                return TunnelProvider.CLOUDFLARE
        return TunnelProvider.NONE

    async def start(self) -> TunnelStatus:
        """
        Start the tunnel subprocess.
        
        Returns:
            TunnelStatus with the public URL if successful.
        """
        if self.is_running:
            logger.info("Tunnel already running at %s", self._status.url)
            return self._status

        provider = self.detect_best_provider()

        if provider == TunnelProvider.NONE:
            self._status = TunnelStatus(
                provider=TunnelProvider.NONE,
                error="No tunnel provider found. Install cloudflared (winget install Cloudflare.cloudflared) or ngrok.",
            )
            logger.warning(self._status.error)
            return self._status

        try:
            if provider == TunnelProvider.CLOUDFLARE:
                await self._start_cloudflare()
            elif provider == TunnelProvider.NGROK:
                await self._start_ngrok()
        except Exception as e:
            self._status = TunnelStatus(
                provider=provider,
                error=f"Failed to start tunnel: {e}",
            )
            logger.error("Tunnel start failed: %s", e)

        return self._status

    async def _start_cloudflare(self) -> None:
        """Start a Cloudflare Quick Tunnel."""
        exe = self.detect_cloudflared()
        if not exe:
            raise RuntimeError("cloudflared not found")

        cmd = [exe, "tunnel", "--url", f"http://localhost:{self.port}"]
        logger.info("Starting Cloudflare tunnel: %s", " ".join(cmd))

        self._url_event.clear()
        self._output_buffer.clear()

        creationflags = 0
        if sys.platform == "win32":
            import subprocess
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )

        self._status = TunnelStatus(
            provider=TunnelProvider.CLOUDFLARE,
            is_running=True,
            started_at=time.time(),
            pid=self._process.pid,
        )

        # Start reader tasks for both stdout and stderr
        self._reader_task = asyncio.create_task(
            self._read_output_loop(TunnelProvider.CLOUDFLARE)
        )

        # Wait for URL to appear with timeout
        try:
            await asyncio.wait_for(self._url_event.wait(), timeout=self.startup_timeout)
        except asyncio.TimeoutError:
            if not self._status.url:
                logger.warning(
                    "Cloudflare tunnel started but URL not detected within %.0fs. "
                    "Output so far: %s",
                    self.startup_timeout,
                    "\n".join(self._output_buffer[-5:]),
                )

    async def _start_ngrok(self) -> None:
        """Start an ngrok tunnel."""
        exe = self.detect_ngrok()
        if not exe:
            raise RuntimeError("ngrok not found")

        cmd = [exe, "http", str(self.port), "--log", "stdout", "--log-format", "term"]
        logger.info("Starting ngrok tunnel: %s", " ".join(cmd))

        self._url_event.clear()
        self._output_buffer.clear()

        creationflags = 0
        if sys.platform == "win32":
            import subprocess
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=creationflags,
        )

        self._status = TunnelStatus(
            provider=TunnelProvider.NGROK,
            is_running=True,
            started_at=time.time(),
            pid=self._process.pid,
        )

        self._reader_task = asyncio.create_task(
            self._read_output_loop(TunnelProvider.NGROK)
        )

        try:
            await asyncio.wait_for(self._url_event.wait(), timeout=self.startup_timeout)
        except asyncio.TimeoutError:
            if not self._status.url:
                logger.warning("ngrok started but URL not detected within %.0fs", self.startup_timeout)

    async def _read_output_loop(self, provider: TunnelProvider) -> None:
        """Read subprocess output and extract tunnel URL."""
        streams = []
        if self._process and self._process.stdout:
            streams.append(("stdout", self._process.stdout))
        if self._process and self._process.stderr:
            streams.append(("stderr", self._process.stderr))

        async def _read_stream(name: str, stream: asyncio.StreamReader) -> None:
            try:
                while True:
                    line_bytes = await stream.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    self._output_buffer.append(line)
                    # Keep buffer bounded
                    if len(self._output_buffer) > 200:
                        self._output_buffer = self._output_buffer[-100:]

                    logger.debug("[tunnel/%s] %s", name, line)

                    # Try to extract URL
                    url = self._extract_url(line, provider)
                    if url and not self._status.url:
                        self._status.url = url
                        self._url_event.set()
                        logger.info(
                            "🌐 Tunnel URL detected: %s", url
                        )
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("Tunnel reader %s error: %s", name, e)

        tasks = [asyncio.create_task(_read_stream(name, stream)) for name, stream in streams]
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                for t in tasks:
                    t.cancel()

        # Process has exited
        self._status.is_running = False

    def _extract_url(self, line: str, provider: TunnelProvider) -> Optional[str]:
        """Extract tunnel URL from a line of output."""
        if provider == TunnelProvider.CLOUDFLARE:
            match = self._CF_URL_PATTERN.search(line)
            if match:
                return match.group(0)
        elif provider == TunnelProvider.NGROK:
            match = self._NGROK_URL_PATTERN.search(line)
            if match:
                return match.group(0)

        # Generic HTTPS URL fallback
        match = self._GENERIC_HTTPS_PATTERN.search(line)
        if match:
            url = match.group(1)
            # Filter out non-tunnel URLs
            skip_domains = ["github.com", "cloudflare.com", "ngrok.com", "google.com"]
            if not any(d in url.lower() for d in skip_domains):
                return url

        return None

    async def stop(self) -> None:
        """Stop the tunnel subprocess and clean up."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._process:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except Exception as e:
                logger.debug("Tunnel process cleanup error: %s", e)
            self._process = None

        old_url = self._status.url
        self._status = TunnelStatus()
        if old_url:
            logger.info("Tunnel stopped (was: %s)", old_url)

    async def restart(self) -> TunnelStatus:
        """Restart the tunnel."""
        await self.stop()
        await asyncio.sleep(1.0)
        return await self.start()

    def get_status_dict(self) -> dict:
        """Return tunnel status as a JSON-serializable dict."""
        return {
            "provider": self._status.provider.value,
            "url": self._status.url,
            "is_running": self._status.is_running,
            "error": self._status.error,
            "started_at": self._status.started_at,
            "pid": self._status.pid,
        }


# Module-level singleton
tunnel_manager = TunnelManager()
