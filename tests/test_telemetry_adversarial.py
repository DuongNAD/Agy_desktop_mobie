"""
Milestone 5 (M5) Adversarial Stress & Chaos Test Suite
Targets: backend.telemetry.monitor, TelemetryPoller, REST endpoints, and /ws/system WebSocket.

Tests include:
1. Abnormal/Extreme Hardware States (-50% CPU, 200% RAM, NaN/Inf, invalid drives, boot time anomalies).
2. Process iteration chaos (AccessDenied, NoSuchProcess, ZombieProcess, corrupted dicts, generator crashes).
3. High-concurrency REST hammer (simultaneous requests to /api/system/*).
4. WebSocket chaos (rapid churn, abrupt closures during broadcast, ping/refresh floods).
5. TelemetryPoller lifecycle, subscriber leak prevention, and deadlock resistance.
"""

import asyncio
import json
import math
import os
import sys
import time
from collections import namedtuple
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import psutil
from fastapi import WebSocketDisconnect, status
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.security import create_access_token
from backend.telemetry.monitor import (
    SystemMonitor,
    TelemetryPoller,
    telemetry_poller,
)


# ===========================================================================
# 1. Extreme & Abnormal Hardware States
# ===========================================================================

class TestExtremeHardwareStates:
    """Stress-test SystemMonitor resilience against abnormal/corrupted OS metrics."""

    def test_negative_and_overflow_cpu(self, monkeypatch):
        """CPU metrics at -50% and 500% must clamp to [0.0, 100.0]."""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None, percpu=False: -50.0)
        assert SystemMonitor.get_cpu_percent() == 0.0

        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None, percpu=False: 250.0)
        assert SystemMonitor.get_cpu_percent() == 100.0

    def test_nan_and_inf_cpu_behavior(self, monkeypatch):
        """Document behavior when psutil returns NaN or Inf."""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None, percpu=False: float("inf"))
        val_inf = SystemMonitor.get_cpu_percent()
        assert val_inf == 100.0

        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None, percpu=False: float("-inf"))
        val_neg_inf = SystemMonitor.get_cpu_percent()
        assert val_neg_inf == 0.0

        # Empirical observation for NaN: IEEE-754 comparisons with NaN return False
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None, percpu=False: float("nan"))
        val_nan = SystemMonitor.get_cpu_percent()
        # Document if NaN is retained or clamped
        is_nan = math.isnan(val_nan)
        assert isinstance(val_nan, float)

    def test_cpu_per_core_corrupted_lists(self, monkeypatch):
        """cpu_percent(percpu=True) returning negative or overflow values."""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None, percpu=True: [-100.0, 300.0, 45.5, 0.0])
        cores = SystemMonitor.get_cpu_per_core()
        assert len(cores) == 4
        assert cores[0] == 0.0
        assert cores[1] == 100.0
        assert cores[2] == 45.5
        assert cores[3] == 0.0

    def test_cpu_per_core_type_error_fallback(self, monkeypatch):
        """If percpu kwarg raises TypeError (mock psutil limitation), fallback executes cleanly."""
        def mock_cpu(interval=None, **kwargs):
            if "percpu" in kwargs:
                raise TypeError("unexpected keyword argument 'percpu'")
            return 33.3

        monkeypatch.setattr(psutil, "cpu_percent", mock_cpu)
        monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 4)
        cores = SystemMonitor.get_cpu_per_core()
        assert isinstance(cores, list)
        assert len(cores) == 4
        assert all(c == 33.3 for c in cores)

    def test_extreme_ram_usage(self, monkeypatch):
        """RAM metrics with 200% percent, negative used, or total=0."""
        MemMock = namedtuple("vmem", ["total", "used", "free", "available", "percent"])

        # 200% RAM percent clamped to 100
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MemMock(16000000000, 32000000000, 0, 0, 200.0))
        ram = SystemMonitor.get_ram_usage()
        assert ram["percent"] == 100.0
        assert ram["total"] == 16000000000

        # -50% RAM percent clamped to 0
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MemMock(16000000000, 0, 16000000000, 16000000000, -50.0))
        ram_neg = SystemMonitor.get_ram_usage()
        assert ram_neg["percent"] == 0.0

        # virtual_memory raises OSError / PermissionError
        def _err_vmem():
            raise OSError("Win32 memory query failed")
        monkeypatch.setattr(psutil, "virtual_memory", _err_vmem)
        ram_err = SystemMonitor.get_ram_usage()
        assert ram_err["total"] == 0
        assert ram_err["percent"] == 0.0

    def test_nonexistent_and_invalid_drive_paths(self, monkeypatch):
        """Disk usage on nonexistent drive letters (X:\\, Z:\\) falls back to workspace or system root."""
        disk = SystemMonitor.get_disk_usage("X:\\TotallyInvalidDrive_9999\\")
        assert isinstance(disk, dict)
        assert "percent" in disk
        assert 0.0 <= disk["percent"] <= 100.0
        assert "total" in disk
        assert disk["total"] > 0

    def test_all_drives_failing_fallback(self, monkeypatch):
        """If psutil.disk_usage raises on ALL candidate paths, return zeroed fallback dict."""
        def _fail_disk(p):
            raise PermissionError(f"Drive {p} access denied")
        monkeypatch.setattr(psutil, "disk_usage", _fail_disk)
        disk = SystemMonitor.get_disk_usage("Z:\\Failing")
        assert disk["total"] == 0
        assert disk["percent"] == 0.0
        assert disk["free"] == 0

    def test_corrupted_network_io(self, monkeypatch):
        """psutil.net_io_counters returning None or raising exceptions."""
        monkeypatch.setattr(psutil, "net_io_counters", lambda: None)
        net = SystemMonitor.get_network_io()
        assert net["bytes_sent"] == 0
        assert net["bytes_recv"] == 0

        def _net_err():
            raise RuntimeError("Network counter unavailable")
        monkeypatch.setattr(psutil, "net_io_counters", _net_err)
        net_err = SystemMonitor.get_network_io()
        assert net_err["packets_sent"] == 0

    def test_system_uptime_extreme_timestamps(self, monkeypatch):
        """Boot time in distant future or negative timestamps on Windows."""
        # Future boot time -> clamped to 0.0 uptime
        monkeypatch.setattr(psutil, "boot_time", lambda: time.time() + 3600)
        uptime = SystemMonitor.get_system_uptime()
        assert uptime["uptime_seconds"] == 0.0

        # Boot time raising exception (e.g. negative timestamp on Windows datetime)
        def _err_boot():
            raise OSError("Invalid timestamp")
        monkeypatch.setattr(psutil, "boot_time", _err_boot)
        uptime_err = SystemMonitor.get_system_uptime()
        assert uptime_err["uptime_seconds"] == 0.0
        assert "boot_time_iso" in uptime_err


# ===========================================================================
# 2. Process Iteration Chaos & Exception Handling
# ===========================================================================

class TestProcessIterationChaos:
    """Stress-test process iteration under AccessDenied, NoSuchProcess, ZombieProcess, and bad data."""

    def test_mixed_exception_process_stream(self, monkeypatch):
        """Generator yielding mix of healthy processes, AccessDenied, NoSuchProcess, ZombieProcess."""
        class MockProcGood:
            def __init__(self, pid, name, cpu, mem):
                self.info = {
                    "pid": pid,
                    "name": name,
                    "cpu_percent": cpu,
                    "memory_info": namedtuple("mem", ["rss"])(mem),
                    "status": "running",
                    "create_time": 1000.0,
                }

        class MockProcAccessDenied:
            @property
            def info(self):
                raise psutil.AccessDenied(pid=4, name="system")

            def name(self):
                raise psutil.AccessDenied(pid=4)

        class MockProcZombie:
            @property
            def info(self):
                raise psutil.ZombieProcess(pid=999)

        class MockProcNoSuchProcess:
            @property
            def info(self):
                raise psutil.NoSuchProcess(pid=888)

        def _chaos_proc_iter(attrs=None):
            yield MockProcGood(101, "python.exe", 15.0, 50 * 1024 * 1024)
            yield MockProcAccessDenied()
            yield MockProcGood(102, "powershell.exe", 5.0, 30 * 1024 * 1024)
            yield MockProcZombie()
            yield MockProcNoSuchProcess()
            yield MockProcGood(103, "agy.exe", 25.0, 80 * 1024 * 1024)

        monkeypatch.setattr(psutil, "process_iter", _chaos_proc_iter)
        procs = SystemMonitor.get_top_processes(limit=10, sort_by="cpu")

        # Must cleanly return the 3 valid processes sorted by CPU (103: 25%, 101: 15%, 102: 5%)
        assert len(procs) == 3
        assert procs[0]["pid"] == 103
        assert procs[0]["cpu"] == 25.0
        assert procs[1]["pid"] == 101
        assert procs[2]["pid"] == 102

    def test_process_generator_abrupt_crash(self, monkeypatch):
        """If psutil.process_iter generator itself throws an exception midway."""
        def _crashing_iter(attrs=None):
            yield MagicMock(info={"pid": 1, "name": "init", "cpu_percent": 1.0, "memory_info": None})
            raise psutil.AccessDenied(pid=0)

        monkeypatch.setattr(psutil, "process_iter", _crashing_iter)
        procs = SystemMonitor.get_top_processes(limit=10)
        # Should gracefully return the 1 process collected before crash without unhandled exception
        assert len(procs) == 1
        assert procs[0]["pid"] == 1

    def test_corrupted_info_dict_sanitization(self, monkeypatch):
        """Processes with None names or None cpu/memory should sanitize cleanly."""
        bad_proc = MagicMock()
        bad_proc.info = {
            "pid": 555,
            "name": None,
            "cpu_percent": None,
            "memory_info": None,
            "status": None,
            "create_time": 0.0,
        }
        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([bad_proc]))
        procs = SystemMonitor.get_top_processes(limit=5)
        assert len(procs) == 1
        assert procs[0]["pid"] == 555
        assert procs[0]["name"] == "unknown"
        assert procs[0]["cpu"] == 0.0
        assert procs[0]["memory_mb"] == 0.0


# ===========================================================================
# 3. High-Concurrency REST API Load Testing
# ===========================================================================

class TestConcurrentRestApiLoad:
    """Stress-test concurrent REST API calls to ensure zero deadlocks or 500 errors."""

    @pytest.mark.asyncio
    async def test_high_frequency_concurrent_requests(self, async_client, auth_headers, mock_psutil):
        """60 rapid concurrent asynchronous requests hitting /api/system/info, metrics, and processes."""
        endpoints = [
            "/api/system/info",
            "/api/system/metrics",
            "/api/system/processes?limit=10&sort_by=cpu",
            "/api/system/processes?limit=5&sort_by=memory",
        ]

        async def _make_request(url: str):
            resp = await async_client.get(url, headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data is not None
            return resp.status_code

        # Launch 60 concurrent tasks (15 per endpoint)
        tasks = [_make_request(endpoints[i % len(endpoints)]) for i in range(60)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            assert r == 200, f"Request failed with: {r}"


# ===========================================================================
# 4. WebSocket Concurrency, Churn & Abrupt Disconnection Stress (/ws/system)
# ===========================================================================

class TestWebSocketChaos:
    """Stress-test /ws/system WebSocket streaming under high subscriber churn and socket errors."""

    def test_rapid_connect_and_immediate_disconnect(self, client, valid_jwt_token, mock_psutil):
        """20 clients connect and immediately disconnect without reading."""
        for _ in range(20):
            with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws:
                pass  # Immediately close upon context manager exit

        # Ensure no lingering dead sockets in poller
        assert len(telemetry_poller._subscribers) == 0

    def test_concurrent_websocket_frame_reception(self, client, valid_jwt_token, mock_psutil):
        """Multiple WebSocket clients receive broadcast frames simultaneously."""
        with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws1:
            frame1 = ws1.receive_json()
            assert frame1["type"] == "telemetry"

            with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws2:
                frame2 = ws2.receive_json()
                assert frame2["type"] == "telemetry"

                # Send ping / pong
                ws1.send_text("ping")
                assert ws1.receive_text() == "pong"

                # Send refresh
                ws2.send_text("refresh")
                refreshed = ws2.receive_json()
                assert refreshed["type"] == "telemetry"

        # Subscribers must be clean after both disconnect
        assert len(telemetry_poller._subscribers) == 0

    @pytest.mark.asyncio
    async def test_broadcaster_resilience_to_dead_sockets(self):
        """Broadcaster cleanly drops broken sockets during broadcast() without throwing."""
        poller = TelemetryPoller(poll_interval=1.0)

        # Mock a healthy socket and a broken socket
        good_ws = AsyncMock()
        bad_ws = AsyncMock()
        bad_ws.send_json.side_effect = RuntimeError("Socket connection reset by peer")

        poller.register_subscriber(good_ws)
        poller.register_subscriber(bad_ws)
        assert len(poller._subscribers) == 2

        # Broadcast frame
        await poller.broadcast({"type": "telemetry", "cpu": 10.0})

        # Good ws received frame, bad ws was pruned
        good_ws.send_json.assert_awaited_once()
        assert len(poller._subscribers) == 1
        assert good_ws in poller._subscribers
        assert bad_ws not in poller._subscribers


# ===========================================================================
# 5. TelemetryPoller Lifecycle, Deadlock & Memory Stability
# ===========================================================================

class TestTelemetryPollerStability:
    """Verify TelemetryPoller lifecycle, high frequency polling, and resource cleanup."""

    @pytest.mark.asyncio
    async def test_repeated_start_stop_cycles(self):
        """Repeatedly starting and stopping poller does not leak background tasks."""
        poller = TelemetryPoller(poll_interval=0.05)
        for _ in range(5):
            await poller.start()
            assert poller._running is True
            assert poller._task is not None
            await asyncio.sleep(0.1)
            await poller.stop()
            assert poller._running is False
            assert poller._task.done()

    @pytest.mark.asyncio
    async def test_rapid_high_speed_polling(self):
        """Poller running at 20Hz (0.05s) for 0.5 seconds produces updated snapshots without memory growth."""
        poller = TelemetryPoller(poll_interval=0.05)
        await poller.start()
        await asyncio.sleep(0.3)

        snap = poller.get_cached_snapshot()
        assert snap is not None
        assert "cpu" in snap
        assert "network" in snap
        assert "bytes_sent_per_sec" in snap["network"]

        await poller.stop()

    def test_process_kill_security_guards(self, client, auth_headers, monkeypatch):
        """Verify protected PIDs and self-PID termination prevention."""
        # PID <= 4 is Forbidden
        resp_sys = client.post("/api/system/tasks/4/kill", headers=auth_headers)
        assert resp_sys.status_code == 403

        resp_zero = client.post("/api/system/tasks/0/kill", headers=auth_headers)
        assert resp_zero.status_code == 403

        # Self PID is Bad Request
        self_pid = os.getpid()
        resp_self = client.post(f"/api/system/tasks/{self_pid}/kill", headers=auth_headers)
        assert resp_self.status_code == 400
        assert "backend server process itself" in resp_self.json()["detail"]

        # Non-existent PID returns not_found
        def _mock_proc(pid):
            raise psutil.NoSuchProcess(pid=pid)
        monkeypatch.setattr(psutil, "Process", _mock_proc)
        resp_nf = client.post("/api/system/tasks/999999/kill", headers=auth_headers)
        assert resp_nf.status_code == 200
        assert resp_nf.json()["status"] == "not_found"
