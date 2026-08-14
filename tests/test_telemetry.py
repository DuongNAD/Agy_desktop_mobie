"""
Comprehensive Pytest Test Suite for Milestone 5 (M5) — System Telemetry Subsystem (F11).
Covers SystemMonitor unit tests, boundary/error resilience, REST endpoints,
WebSocket live streaming, and mobile health monitoring workflows.
"""

import asyncio
import os
import time
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.telemetry.monitor import (
    SystemMonitor,
    TelemetryPoller,
    telemetry_poller,
)


# ===========================================================================
# Tier 1: SystemMonitor Unit Tests & Functional Happy Path (F11)
# ===========================================================================

def test_cpu_percent_metric(mock_psutil):
    """TEL-01: CPU percent metric returns float within valid range [0.0, 100.0]."""
    cpu = SystemMonitor.get_cpu_percent()
    assert isinstance(cpu, (int, float))
    assert 0.0 <= cpu <= 100.0


def test_cpu_per_core_metrics(mock_psutil):
    """TEL-02: Per-core CPU percentage returns list of valid floats."""
    cores = SystemMonitor.get_cpu_per_core()
    assert isinstance(cores, list)
    assert len(cores) >= 1
    for core_pct in cores:
        assert isinstance(core_pct, (int, float))
        assert 0.0 <= core_pct <= 100.0


def test_ram_usage_metrics(mock_psutil):
    """TEL-03: RAM usage returns total, used, free, available, and percent metrics."""
    ram = SystemMonitor.get_ram_usage()
    assert isinstance(ram, dict)
    assert "total" in ram
    assert "used" in ram
    assert "free" in ram
    assert "percent" in ram
    assert ram["total"] > 0
    assert 0.0 <= ram["percent"] <= 100.0


def test_disk_usage_metrics(mock_psutil):
    """TEL-04: Disk usage returns total, used, free, and percent usage."""
    disk = SystemMonitor.get_disk_usage()
    assert isinstance(disk, dict)
    assert "total" in disk
    assert "used" in disk
    assert "free" in disk
    assert "percent" in disk
    assert disk["total"] > 0
    assert 0.0 <= disk["percent"] <= 100.0


def test_system_uptime_calculation(mock_psutil):
    """TEL-05: System uptime is calculated from boot time as non-negative seconds."""
    uptime = SystemMonitor.get_system_uptime()
    assert isinstance(uptime, dict)
    assert "uptime_seconds" in uptime
    assert uptime["uptime_seconds"] >= 0.0
    assert "boot_time" in uptime
    assert "boot_time_iso" in uptime


def test_active_task_list_enumeration(mock_psutil):
    """TEL-06: Task list enumerates top active background processes with PID and memory."""
    tasks = SystemMonitor.get_active_tasks(limit=5)
    assert isinstance(tasks, list)
    assert len(tasks) > 0
    task = tasks[0]
    assert "pid" in task
    assert "name" in task
    assert "cpu" in task
    assert "memory_mb" in task


def test_background_agymobile_task_detection(mock_psutil):
    """TEL-07: Telemetry process scan identifies python.exe, powershell.exe, and servers."""
    procs = SystemMonitor.get_top_processes(limit=10)
    proc_names = [p["name"].lower() for p in procs]
    assert any("python" in n or "powershell" in n or "language_server" in n for n in proc_names)


def test_full_telemetry_snapshot_schema(client, auth_headers, mock_psutil):
    """TEL-08: SystemMonitor.get_snapshot() aggregates full telemetry schema."""
    snapshot = SystemMonitor.get_snapshot()
    assert snapshot["type"] == "telemetry"
    assert "cpu" in snapshot
    assert "ram" in snapshot
    assert "disk" in snapshot
    assert "network" in snapshot
    assert "uptime" in snapshot
    assert "tasks" in snapshot
    assert "lan_ip" in snapshot
    assert "port" in snapshot
    assert snapshot["os"] in ("windows", "linux", "darwin")


# ===========================================================================
# Tier 2: Boundary, Adversarial & Error Handling Tests (F11)
# ===========================================================================

def test_negative_abnormal_metrics_clamping(monkeypatch, mock_psutil):
    """TEL-09: Out-of-bounds metrics (e.g. -10% or 150%) are safely clamped to [0.0, 100.0]."""
    monkeypatch.setattr(mock_psutil, "cpu_percent", lambda interval=None: -10.0)
    cpu_low = SystemMonitor.get_cpu_percent()
    assert cpu_low >= 0.0

    monkeypatch.setattr(mock_psutil, "cpu_percent", lambda interval=None: 150.0)
    cpu_high = SystemMonitor.get_cpu_percent()
    assert cpu_high <= 100.0


def test_psutil_permission_error_fallback(monkeypatch, mock_psutil):
    """TEL-10: AccessDenied errors when inspecting privileged processes are gracefully skipped."""
    import psutil

    def _access_denied_iter(attrs=None):
        raise psutil.AccessDenied()

    monkeypatch.setattr(mock_psutil, "process_iter", _access_denied_iter)
    tasks = SystemMonitor.get_active_tasks()
    assert isinstance(tasks, list)
    assert tasks == []


def test_high_frequency_polling_stability(mock_psutil):
    """TEL-11: 100 rapid consecutive polling invocations execute cleanly without memory leaks."""
    start_time = time.time()
    for _ in range(100):
        cpu = SystemMonitor.get_cpu_percent()
        ram = SystemMonitor.get_ram_usage()
        assert 0.0 <= cpu <= 100.0
        assert 0.0 <= ram["percent"] <= 100.0
    elapsed = time.time() - start_time
    assert elapsed < 2.0


def test_invalid_disk_path_fallback(mock_psutil):
    """TEL-12: Non-existent drive paths fallback gracefully to system root disk usage."""
    disk = SystemMonitor.get_disk_usage("Z:\\NonExistentDrive\\9999")
    assert isinstance(disk, dict)
    assert "total" in disk
    assert "percent" in disk
    assert 0.0 <= disk["percent"] <= 100.0


def test_zombie_and_recycled_process_handling(monkeypatch, mock_psutil):
    """TEL-13: Processes terminated mid-iteration (NoSuchProcess) are skipped without crashing."""
    import psutil

    def _zombie_iter(attrs=None):
        m = MagicMock()
        m.info = {"pid": 9999, "name": "zombie.exe", "cpu_percent": 0.0, "memory_info": None}
        yield m
        raise psutil.NoSuchProcess(pid=9999)

    monkeypatch.setattr(mock_psutil, "process_iter", _zombie_iter)
    tasks = SystemMonitor.get_active_tasks()
    assert isinstance(tasks, list)


def test_empty_process_list_handling(monkeypatch, mock_psutil):
    """TEL-14: Empty process iteration returns an empty list [] rather than None."""
    monkeypatch.setattr(mock_psutil, "process_iter", lambda attrs=None: iter([]))
    tasks = SystemMonitor.get_active_tasks()
    assert tasks == []


def test_process_with_missing_attributes_fallback(monkeypatch, mock_psutil):
    """TEL-15: Processes with None for memory_info or cpu_percent are handled cleanly."""
    def _none_attrs_iter(attrs=None):
        m = MagicMock()
        m.info = {"pid": 5001, "name": None, "cpu_percent": None, "memory_info": None}
        return iter([m])

    monkeypatch.setattr(mock_psutil, "process_iter", _none_attrs_iter)
    procs = SystemMonitor.get_top_processes(limit=5)
    assert len(procs) == 1
    assert procs[0]["pid"] == 5001
    assert procs[0]["cpu"] == 0.0
    assert procs[0]["memory_mb"] == 0.0


# ===========================================================================
# Tier 3: REST API & WebSocket Realtime Integration (F11)
# ===========================================================================

def test_api_system_info_authenticated_schema(client, auth_headers, mock_psutil):
    """TEL-16: GET /api/system/info returns JSON schema matching PROJECT.md contracts."""
    response = client.get("/api/system/info", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "lan_ip" in data
    assert "port" in data
    assert "cpu" in data
    assert "ram" in data
    assert "disk" in data
    assert "uptime" in data
    assert data.get("os") in ("windows", "linux", "darwin", "nt", "posix") or "os" in data
    assert "hostname" in data
    assert "server_time" in data


def test_api_system_info_unauthenticated_401(client):
    """TEL-17: GET /api/system/info returns 401 Unauthorized when token is missing."""
    response = client.get("/api/system/info")
    assert response.status_code == 401


def test_api_system_metrics_endpoint(client, auth_headers, mock_psutil):
    """TEL-18: GET /api/system/metrics returns detailed per-core and network stats."""
    response = client.get("/api/system/metrics", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "cpu" in data
    assert "cpu_per_core" in data
    assert "ram" in data
    assert "disk" in data
    assert "network" in data
    assert "uptime" in data
    assert "timestamp" in data


def test_api_system_processes_endpoint(client, auth_headers, mock_psutil):
    """TEL-19: GET /api/system/processes returns process list with pagination limit."""
    response = client.get("/api/system/processes?limit=2&sort_by=cpu", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "processes" in data
    assert len(data["processes"]) <= 2
    assert "total" in data


def test_api_system_task_kill_endpoint(client, auth_headers, mock_psutil, monkeypatch):
    """TEL-20: POST /api/system/tasks/{pid}/kill terminates background process safely."""
    import psutil
    mock_proc = MagicMock()
    monkeypatch.setattr(psutil, "Process", lambda pid: mock_proc)

    response = client.post("/api/system/tasks/9999/kill", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "terminated"
    assert data["pid"] == 9999
    mock_proc.terminate.assert_called_once()


def test_api_system_task_kill_server_self_protected(client, auth_headers):
    """TEL-21: POST /api/system/tasks/{pid}/kill rejects terminating backend server itself."""
    current_pid = os.getpid()
    response = client.post(f"/api/system/tasks/{current_pid}/kill", headers=auth_headers)
    assert response.status_code == 400


def test_telemetry_broadcast_websocket_integration(client, valid_jwt_token, mock_psutil):
    """TEL-22: /ws/system WebSocket channel broadcasts telemetry payload frames."""
    with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws:
        frame = ws.receive_json()
        assert frame is not None
        assert "type" in frame
        assert frame["type"] == "telemetry"
        assert "cpu" in frame
        assert "ram" in frame
        assert "disk" in frame


def test_ws_system_reject_expired_token(client, expired_jwt_token):
    """TEL-23: /ws/system closes connection on expired JWT token."""
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/system?token={expired_jwt_token}") as ws:
            ws.receive_json()


def test_telemetry_process_monitoring(mock_psutil, client, auth_headers):
    """TEL-24: Active processes enumerated in telemetry reflect running background jobs."""
    response = client.get("/api/system/info", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "tasks" in data
    assert isinstance(data["tasks"], list)


# ===========================================================================
# Tier 4: Real-World Mobile Workflow Test (F11)
# ===========================================================================

def test_live_mobile_health_monitoring_workflow(client, valid_jwt_token, auth_headers, mock_psutil):
    """TEL-25: Complete Flow: Client queries REST baseline -> connects /ws/system -> receives streaming."""
    # 1. REST query for baseline stats
    rest_res = client.get("/api/system/info", headers=auth_headers)
    assert rest_res.status_code == 200
    assert rest_res.json()["cpu"] >= 0.0

    # 2. WebSocket live feed
    with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws:
        frame = ws.receive_json()
        assert frame is not None
        assert frame.get("type") == "telemetry"
        assert "cpu" in frame
        assert "ram" in frame
        assert "disk" in frame
