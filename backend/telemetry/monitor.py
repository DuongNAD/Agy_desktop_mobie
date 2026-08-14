"""
AgyMobile Telemetry Subsystem
System resource polling (CPU, RAM, Disk, Network, Processes),
non-blocking cached background polling with WebSocket broadcaster,
and REST endpoints.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from backend.config import Settings, get_settings
from backend.network import get_lan_ip
from backend.security import get_current_user, verify_ws_token

logger = logging.getLogger("agymobile.telemetry")


# =====================================================================
# Pydantic Schemas for Telemetry REST Endpoints
# =====================================================================

class SystemInfoPayload(BaseModel):
    lan_ip: str
    port: int
    cpu: float
    ram: float
    disk: float
    uptime: float
    os: str = "windows"
    server_time: str
    hostname: str
    tasks: List[Dict[str, Any]] = Field(default_factory=list)


class SystemMetricsPayload(BaseModel):
    cpu: float
    cpu_per_core: List[float]
    ram: Dict[str, Any]
    disk: Dict[str, Any]
    network: Dict[str, Any]
    uptime: Dict[str, Any]
    timestamp: str


class ProcessKillResponse(BaseModel):
    status: str
    pid: int


# =====================================================================
# 1. SystemMonitor Engine
# =====================================================================

class SystemMonitor:
    """
    Robust hardware & process metrics sampler.
    Guarantees defensive error handling, safe bounds clamping,
    and graceful fallbacks for restricted permissions on Windows.
    """

    @staticmethod
    def _clamp(val: Any, min_val: float = 0.0, max_val: float = 100.0) -> float:
        """Clamp any numeric metric to [min_val, max_val]."""
        try:
            f = float(val)
            if f < min_val:
                return min_val
            if f > max_val:
                return max_val
            return round(f, 2)
        except (ValueError, TypeError):
            return min_val

    @staticmethod
    def get_cpu_percent(interval: Optional[float] = None) -> float:
        """Get overall system CPU utilization percent clamped to [0.0, 100.0]."""
        try:
            val = psutil.cpu_percent(interval=interval)
            return SystemMonitor._clamp(val)
        except Exception as e:
            logger.warning(f"Error querying cpu_percent: {e}")
            return 0.0

    @staticmethod
    def get_cpu_per_core() -> List[float]:
        """Get per-core CPU utilization percentage list."""
        try:
            res = psutil.cpu_percent(interval=None, percpu=True)
            if isinstance(res, list):
                return [SystemMonitor._clamp(c) for c in res]
            elif isinstance(res, (int, float)):
                return [SystemMonitor._clamp(res)]
            return [0.0]
        except TypeError:
            # Fallback if mock psutil does not accept percpu parameter
            try:
                val = psutil.cpu_percent(interval=None)
                cpu_count = psutil.cpu_count(logical=True) or 1
                clamped = SystemMonitor._clamp(val)
                return [clamped] * cpu_count
            except Exception:
                return [0.0]
        except Exception as e:
            logger.warning(f"Error querying cpu_per_core: {e}")
            return [0.0]

    @staticmethod
    def get_ram_usage() -> Dict[str, Any]:
        """Get virtual memory usage details."""
        try:
            mem = psutil.virtual_memory()
            total = int(getattr(mem, "total", 0))
            used = int(getattr(mem, "used", 0))
            free = int(getattr(mem, "free", 0))
            available = int(getattr(mem, "available", 0))
            percent = SystemMonitor._clamp(getattr(mem, "percent", 0.0))
            return {
                "total": total,
                "used": used,
                "free": free,
                "available": available,
                "percent": percent,
            }
        except Exception as e:
            logger.warning(f"Error querying virtual_memory: {e}")
            return {
                "total": 0,
                "used": 0,
                "free": 0,
                "available": 0,
                "percent": 0.0,
            }

    @staticmethod
    def get_disk_usage(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """Get disk usage metrics with fallback to system drive or root partition."""
        settings = get_settings()
        candidates: List[Path] = []

        if path:
            candidates.append(Path(path))
        candidates.append(settings.WORKSPACE_ROOT)
        candidates.append(Path("C:\\") if os.name == "nt" else Path("/"))
        candidates.append(Path.home())

        for candidate in candidates:
            try:
                p_str = str(candidate)
                disk = psutil.disk_usage(p_str)
                total = int(getattr(disk, "total", 0))
                used = int(getattr(disk, "used", 0))
                free = int(getattr(disk, "free", 0))
                percent = SystemMonitor._clamp(getattr(disk, "percent", 0.0))
                return {
                    "total": total,
                    "used": used,
                    "free": free,
                    "percent": percent,
                    "path": p_str,
                }
            except (FileNotFoundError, OSError, PermissionError, ValueError, Exception):
                continue

        return {
            "total": 0,
            "used": 0,
            "free": 0,
            "percent": 0.0,
            "path": str(path or "/"),
        }

    @staticmethod
    def get_network_io() -> Dict[str, Any]:
        """Get network byte and packet counters."""
        try:
            net = psutil.net_io_counters()
            if net is None:
                raise ValueError("net_io_counters returned None")
            return {
                "bytes_sent": int(getattr(net, "bytes_sent", 0)),
                "bytes_recv": int(getattr(net, "bytes_recv", 0)),
                "packets_sent": int(getattr(net, "packets_sent", 0)),
                "packets_recv": int(getattr(net, "packets_recv", 0)),
                "errin": int(getattr(net, "errin", 0)),
                "errout": int(getattr(net, "errout", 0)),
                "dropin": int(getattr(net, "dropin", 0)),
                "dropout": int(getattr(net, "dropout", 0)),
            }
        except Exception as e:
            logger.debug(f"net_io_counters query fallback: {e}")
            return {
                "bytes_sent": 0,
                "bytes_recv": 0,
                "packets_sent": 0,
                "packets_recv": 0,
                "errin": 0,
                "errout": 0,
                "dropin": 0,
                "dropout": 0,
            }

    @staticmethod
    def get_system_uptime() -> Dict[str, Any]:
        """Get system uptime in seconds and boot timestamp."""
        try:
            boot = psutil.boot_time()
            uptime_sec = max(0.0, time.time() - boot)
            boot_iso = datetime.fromtimestamp(boot, timezone.utc).isoformat()
            return {
                "uptime_seconds": round(uptime_sec, 2),
                "boot_time": boot,
                "boot_time_iso": boot_iso,
            }
        except Exception as e:
            logger.warning(f"Error querying uptime: {e}")
            return {
                "uptime_seconds": 0.0,
                "boot_time": time.time(),
                "boot_time_iso": datetime.now(timezone.utc).isoformat(),
            }

    @staticmethod
    def get_top_processes(
        limit: int = 20,
        sort_by: str = "cpu",
        filter_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Enumerate processes using batch targeted attribute queries, handling
        NoSuchProcess, AccessDenied, ZombieProcess gracefully.
        Returns sorted list of processes with pid, name, cpu, memory_mb, status.
        """
        results: List[Dict[str, Any]] = []
        targeted_attrs = ["pid", "name", "cpu_percent", "memory_info", "status", "create_time"]

        try:
            try:
                proc_iterator = psutil.process_iter(attrs=targeted_attrs)
            except TypeError:
                proc_iterator = psutil.process_iter()

            if proc_iterator is None:
                return []

            for p in proc_iterator:
                try:
                    info = getattr(p, "info", None)
                    if isinstance(info, dict) and "pid" in info:
                        pid = info.get("pid", 0)
                        name = str(info.get("name") or "unknown")
                        if filter_name and filter_name.lower() not in name.lower():
                            continue
                        cpu_val = info.get("cpu_percent") if info.get("cpu_percent") is not None else 0.0
                        mem_info = info.get("memory_info")
                        status_val = str(info.get("status") or "running")
                        create_time = float(info.get("create_time") or 0.0)
                    else:
                        pid = getattr(p, "pid", 0)
                        try:
                            name = str(p.name())
                        except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                            name = "unknown"

                        if filter_name and filter_name.lower() not in name.lower():
                            continue

                        try:
                            cpu_val = p.cpu_percent(interval=None)
                        except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                            cpu_val = 0.0

                        try:
                            mem_info = p.memory_info()
                        except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                            mem_info = None

                        try:
                            status_val = str(p.status())
                        except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                            status_val = "running"

                        try:
                            create_time = float(p.create_time())
                        except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                            create_time = 0.0

                    rss = getattr(mem_info, "rss", 0) if mem_info else 0
                    memory_mb = round(rss / (1024 * 1024), 2)
                    cpu_clamped = SystemMonitor._clamp(cpu_val)

                    results.append({
                        "pid": pid,
                        "name": name,
                        "cpu": cpu_clamped,
                        "memory_mb": memory_mb,
                        "status": status_val,
                        "created": create_time,
                    })
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                    PermissionError,
                    AttributeError,
                    Exception,
                ):
                    continue
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            PermissionError,
            AttributeError,
            Exception,
        ) as e:
            logger.debug(f"Process iteration exception: {e}")

        # Sort results
        if sort_by == "memory":
            results.sort(key=lambda x: x.get("memory_mb", 0.0), reverse=True)
        else:
            results.sort(key=lambda x: x.get("cpu", 0.0), reverse=True)

        return results[:limit]

    @staticmethod
    def get_active_tasks(limit: int = 10) -> List[Dict[str, Any]]:
        """
        Enumerate active tasks relevant to Antigravity, PowerShell, Python, or top CPU processes.
        """
        processes = SystemMonitor.get_top_processes(limit=limit)
        return processes

    @staticmethod
    def get_snapshot(workspace_path: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate full telemetry snapshot."""
        settings = get_settings()
        lan_ip = get_lan_ip()
        cpu_pct = SystemMonitor.get_cpu_percent()
        cpu_cores = SystemMonitor.get_cpu_per_core()
        ram_data = SystemMonitor.get_ram_usage()
        disk_data = SystemMonitor.get_disk_usage(workspace_path)
        net_data = SystemMonitor.get_network_io()
        uptime_data = SystemMonitor.get_system_uptime()
        tasks = SystemMonitor.get_active_tasks(limit=10)

        return {
            "type": "telemetry",
            "cpu": cpu_pct,
            "cpu_per_core": cpu_cores,
            "ram": ram_data["percent"],
            "ram_details": ram_data,
            "disk": disk_data["percent"],
            "disk_details": disk_data,
            "network": net_data,
            "uptime": uptime_data["uptime_seconds"],
            "uptime_details": uptime_data,
            "tasks": tasks,
            "lan_ip": lan_ip,
            "port": settings.PORT,
            "os": "windows",
            "server_time": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
        }


# =====================================================================
# 2. TelemetryPoller (Background Async Loop & Broadcaster)
# =====================================================================

class TelemetryPoller:
    """
    Non-blocking background poller that offloads psutil calls to worker threads,
    maintains an in-memory cached snapshot, and streams updates to WebSocket clients.
    """

    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._subscribers: Set[WebSocket] = set()
        self._cached_snapshot: Dict[str, Any] = self._build_default_snapshot()
        self._last_net_io: Optional[Dict[str, int]] = None
        self._last_net_time: float = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _build_default_snapshot() -> Dict[str, Any]:
        """Generate a valid, non-blocking default snapshot structure."""
        try:
            settings = get_settings()
            port = settings.PORT
            ws_root = str(settings.WORKSPACE_ROOT)
        except Exception:
            port = 8000
            ws_root = "/"

        try:
            lan = get_lan_ip()
        except Exception:
            lan = "127.0.0.1"

        try:
            hname = socket.gethostname()
        except Exception:
            hname = "localhost"

        return {
            "type": "telemetry",
            "cpu": 0.0,
            "cpu_per_core": [0.0],
            "ram": 0.0,
            "ram_details": {
                "total": 0,
                "used": 0,
                "free": 0,
                "available": 0,
                "percent": 0.0,
            },
            "disk": 0.0,
            "disk_details": {
                "total": 0,
                "used": 0,
                "free": 0,
                "percent": 0.0,
                "path": ws_root,
            },
            "network": {
                "bytes_sent": 0,
                "bytes_recv": 0,
                "packets_sent": 0,
                "packets_recv": 0,
                "errin": 0,
                "errout": 0,
                "dropin": 0,
                "dropout": 0,
                "bytes_sent_per_sec": 0,
                "bytes_recv_per_sec": 0,
            },
            "uptime": 0.0,
            "uptime_details": {
                "uptime_seconds": 0.0,
                "boot_time": time.time(),
                "boot_time_iso": datetime.now(timezone.utc).isoformat(),
            },
            "tasks": [],
            "lan_ip": lan,
            "port": port,
            "os": "windows",
            "server_time": datetime.now(timezone.utc).isoformat(),
            "hostname": hname,
        }

    async def start(self):
        """Start background polling task."""
        if self._running:
            return
        self._running = True
        # Populate initial snapshot in thread to avoid startup latency
        try:
            self._cached_snapshot = await asyncio.to_thread(self._collect_full_snapshot_sync)
        except Exception as e:
            logger.warning(f"Initial telemetry snapshot collection error: {e}")
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"TelemetryPoller background task started (interval: {self.poll_interval}s)")

    async def stop(self):
        """Stop background polling task and clear subscribers."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._subscribers.clear()
        logger.info("TelemetryPoller background task stopped")

    def _collect_full_snapshot_sync(self) -> Dict[str, Any]:
        """Synchronous collection executed in threadpool."""
        snapshot = SystemMonitor.get_snapshot()

        # Compute network transfer rates (bytes/sec)
        now = time.time()
        net = snapshot.get("network", {})
        curr_bytes_sent = net.get("bytes_sent", 0)
        curr_bytes_recv = net.get("bytes_recv", 0)

        if self._last_net_io and self._last_net_time > 0:
            dt = max(0.1, now - self._last_net_time)
            sent_rate = max(0, int((curr_bytes_sent - self._last_net_io.get("bytes_sent", 0)) / dt))
            recv_rate = max(0, int((curr_bytes_recv - self._last_net_io.get("bytes_recv", 0)) / dt))
            snapshot["network"]["bytes_sent_per_sec"] = sent_rate
            snapshot["network"]["bytes_recv_per_sec"] = recv_rate
        else:
            snapshot["network"]["bytes_sent_per_sec"] = 0
            snapshot["network"]["bytes_recv_per_sec"] = 0

        self._last_net_io = {"bytes_sent": curr_bytes_sent, "bytes_recv": curr_bytes_recv}
        self._last_net_time = now

        return snapshot

    async def _poll_loop(self):
        """Asynchronous background loop."""
        while self._running:
            try:
                snapshot = await asyncio.to_thread(self._collect_full_snapshot_sync)
                self._cached_snapshot = snapshot
                await self.broadcast(snapshot)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry poller loop error: {e}")

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    def get_cached_snapshot(self) -> Dict[str, Any]:
        """Get instant in-memory cached snapshot (guaranteed non-blocking O(1) lookup)."""
        if not self._cached_snapshot:
            self._cached_snapshot = self._build_default_snapshot()
        return self._cached_snapshot

    async def get_snapshot_async(self) -> Dict[str, Any]:
        """Retrieve fresh snapshot asynchronously offloaded to a worker thread."""
        snapshot = await asyncio.to_thread(self._collect_full_snapshot_sync)
        self._cached_snapshot = snapshot
        return snapshot

    def register_subscriber(self, ws: WebSocket):
        """Register active WebSocket client for telemetry updates."""
        self._subscribers.add(ws)

    def unregister_subscriber(self, ws: WebSocket):
        """Unregister disconnected WebSocket client."""
        self._subscribers.discard(ws)

    async def broadcast(self, payload: dict):
        """Broadcast telemetry frame concurrently to all connected WebSocket subscribers."""
        if not self._subscribers:
            return

        subscribers = list(self._subscribers)
        dead_sockets: Set[WebSocket] = set()

        async def _send_single(ws: WebSocket):
            try:
                await ws.send_json(payload)
            except Exception:
                dead_sockets.add(ws)

        await asyncio.gather(*[_send_single(ws) for ws in subscribers], return_exceptions=True)

        if dead_sockets:
            self._subscribers.difference_update(dead_sockets)


# Global singleton instance
telemetry_poller = TelemetryPoller(poll_interval=2.0)


# =====================================================================
# 3. REST API Routers
# =====================================================================

telemetry_router = APIRouter(tags=["System Telemetry"])


@telemetry_router.get("/api/system/info", response_model=SystemInfoPayload)
async def get_system_info(current_user: dict = Depends(get_current_user)):
    """Retrieve host telemetry and server configuration."""
    snapshot = telemetry_poller.get_cached_snapshot()
    settings = get_settings()

    return SystemInfoPayload(
        lan_ip=snapshot.get("lan_ip") or get_lan_ip(),
        port=snapshot.get("port") or settings.PORT,
        cpu=float(snapshot.get("cpu", 0.0)),
        ram=float(snapshot.get("ram", 0.0)),
        disk=float(snapshot.get("disk", 0.0)),
        uptime=float(snapshot.get("uptime", 0.0)),
        os=str(snapshot.get("os", "windows")),
        server_time=str(snapshot.get("server_time") or datetime.now(timezone.utc).isoformat()),
        hostname=str(snapshot.get("hostname") or socket.gethostname()),
        tasks=snapshot.get("tasks", []),
    )


@telemetry_router.get("/api/system/metrics", response_model=SystemMetricsPayload)
async def get_system_metrics(current_user: dict = Depends(get_current_user)):
    """Retrieve detailed hardware and network metrics."""
    snapshot = telemetry_poller.get_cached_snapshot()

    return SystemMetricsPayload(
        cpu=float(snapshot.get("cpu", 0.0)),
        cpu_per_core=snapshot.get("cpu_per_core", []),
        ram=snapshot.get("ram_details", {}),
        disk=snapshot.get("disk_details", {}),
        network=snapshot.get("network", {}),
        uptime=snapshot.get("uptime_details", {}),
        timestamp=str(snapshot.get("server_time") or datetime.now(timezone.utc).isoformat()),
    )


@telemetry_router.get("/api/system/processes")
async def get_system_processes(
    limit: int = Query(20, ge=1, le=200),
    sort_by: str = Query("cpu", regex="^(cpu|memory)$"),
    filter: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Retrieve host process list sorted by CPU or Memory."""
    procs = await asyncio.to_thread(
        SystemMonitor.get_top_processes,
        limit=limit,
        sort_by=sort_by,
        filter_name=filter,
    )
    return {"processes": procs, "total": len(procs)}


@telemetry_router.post("/api/system/tasks/{pid}/kill", response_model=ProcessKillResponse)
@telemetry_router.post("/api/system/processes/{pid}/kill", response_model=ProcessKillResponse)
async def kill_system_task(pid: int, current_user: dict = Depends(get_current_user)):
    """Safely terminate a background process or task by PID."""
    # Safeguards: Cannot kill server itself or system root PIDs
    current_pid = os.getpid()
    if pid == current_pid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot terminate the AgyMobile backend server process itself",
        )
    if pid <= 4:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Protected system process PID cannot be terminated",
        )

    try:
        proc = psutil.Process(pid)
        proc.terminate()
        return ProcessKillResponse(status="terminated", pid=pid)
    except psutil.NoSuchProcess:
        return ProcessKillResponse(status="not_found", pid=pid)
    except (psutil.AccessDenied, PermissionError) as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied when attempting to terminate PID {pid}: {e}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to terminate process PID {pid}: {e}",
        )


# =====================================================================
# 4. WebSocket Realtime Router (/ws/system)
# =====================================================================

telemetry_ws_router = APIRouter(tags=["System Telemetry WebSocket"])


@telemetry_ws_router.websocket("/ws/system")
async def ws_system_endpoint(websocket: WebSocket, token: Optional[str] = Query(None)):
    """
    Authenticated WebSocket streaming channel for real-time system metrics.
    Transmits an initial snapshot immediately upon connection, then streams frames periodically.
    """
    token_payload = verify_ws_token(token)
    if not token_payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    await websocket.accept()

    # Immediately push cached snapshot for instant mobile UI gauge rendering
    initial_snapshot = telemetry_poller.get_cached_snapshot()
    await websocket.send_json(initial_snapshot)

    # Register for periodic background broadcasts
    telemetry_poller.register_subscriber(websocket)

    try:
        while True:
            # Keep connection open and listen for any client messages or pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            elif data == "refresh":
                await websocket.send_json(telemetry_poller.get_cached_snapshot())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"/ws/system client exception: {e}")
    finally:
        telemetry_poller.unregister_subscriber(websocket)
