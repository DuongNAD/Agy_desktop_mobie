"""
AgyMobile SSH Terminal Session Manager
Provides SSH client sessions via asyncssh, allowing mobile users to connect
to remote VPS/servers directly from the AgyMobile interface.
"""

from __future__ import annotations

import asyncio
import codecs
import collections
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.security import get_current_user, verify_ws_token

logger = logging.getLogger("agymobile.ssh")


# =====================================================================
# SSH Connection Profile
# =====================================================================

@dataclass
class SSHConnectionProfile:
    """Stored SSH connection profile."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    host: str = ""
    port: int = 22
    username: str = "root"
    auth_method: str = "password"  # "password" | "key"
    # Note: password/key stored in memory only, never persisted to disk
    password: Optional[str] = None
    private_key: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class SSHSession:
    """
    Manages an individual SSH connection session using asyncssh.
    Provides similar interface to TerminalSession for unified WebSocket handling.
    """

    def __init__(
        self,
        session_id: str,
        profile: SSHConnectionProfile,
        max_buffer_lines: int = 1000,
    ) -> None:
        self.session_id = session_id
        self.profile = profile
        self.max_buffer_lines = max_buffer_lines

        self._conn: Optional[Any] = None  # asyncssh.SSHClientConnection
        self._process: Optional[Any] = None  # asyncssh.SSHClientProcess
        self._buffer: collections.deque[str] = collections.deque(maxlen=max_buffer_lines)
        self._listeners: Set[asyncio.Queue[str]] = set()
        self._reader_task: Optional[asyncio.Task] = None
        self._is_alive: bool = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.last_active: float = time.time()
        self.cols: int = 80
        self.rows: int = 24

    def is_alive(self) -> bool:
        """Return True if SSH connection is active."""
        return self._is_alive and self._conn is not None

    async def connect(self, cols: int = 80, rows: int = 24) -> None:
        """Establish SSH connection and open interactive shell."""
        try:
            import asyncssh
        except ImportError:
            raise RuntimeError(
                "asyncssh is not installed. Run: pip install asyncssh"
            )

        self.cols = cols
        self.rows = rows

        connect_kwargs: Dict[str, Any] = {
            "host": self.profile.host,
            "port": self.profile.port,
            "username": self.profile.username,
            "known_hosts": None,  # Accept all host keys for mobile convenience
        }

        if self.profile.auth_method == "password" and self.profile.password:
            connect_kwargs["password"] = self.profile.password
        elif self.profile.auth_method == "key" and self.profile.private_key:
            connect_kwargs["client_keys"] = [
                asyncssh.import_private_key(self.profile.private_key)
            ]

        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(**connect_kwargs),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            raise ConnectionError(f"SSH connection to {self.profile.host}:{self.profile.port} timed out (15s)")
        except Exception as e:
            raise ConnectionError(f"SSH connection failed: {e}")

        # Open interactive PTY session
        try:
            self._process = await self._conn.create_process(
                term_type="xterm-256color",
                term_size=(cols, rows),
                encoding=None,  # Raw bytes mode for proper UTF-8 handling
            )
        except Exception as e:
            if self._conn:
                self._conn.close()
                self._conn = None
            raise ConnectionError(f"Failed to open SSH shell: {e}")

        self._is_alive = True
        self.last_active = time.time()
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info(
            "SSH connected: %s@%s:%d (session: %s)",
            self.profile.username, self.profile.host, self.profile.port, self.session_id,
        )

    async def _read_loop(self) -> None:
        """Continuously read SSH output and broadcast to listeners."""
        try:
            while self._is_alive and self._process:
                stdout = self._process.stdout
                if not stdout:
                    break

                chunk = await stdout.read(4096)
                if not chunk:
                    break

                if isinstance(chunk, bytes):
                    text = self._decoder.decode(chunk, final=False)
                else:
                    text = chunk

                if text:
                    self._buffer.append(text)
                    self.last_active = time.time()
                    for q in list(self._listeners):
                        try:
                            q.put_nowait(text)
                        except asyncio.QueueFull:
                            try:
                                q.get_nowait()
                                q.put_nowait(text)
                            except Exception:
                                pass
                        except Exception:
                            pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("SSH read loop error: %s", e)
        finally:
            self._is_alive = False

    async def write_input(self, data: str) -> None:
        """Send input to the SSH shell."""
        if not self.is_alive() or not self._process or not self._process.stdin:
            return
        self.last_active = time.time()
        try:
            self._process.stdin.write(data.encode("utf-8") if isinstance(data, str) else data)
        except Exception as e:
            logger.debug("SSH write error: %s", e)

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the SSH PTY."""
        self.cols = max(10, min(500, int(cols)))
        self.rows = max(5, min(200, int(rows)))
        if self._process:
            try:
                self._process.change_terminal_size(self.cols, self.rows)
            except Exception:
                pass

    def get_history(self) -> str:
        """Return buffered scrollback history."""
        return "".join(self._buffer)

    def clear_history(self) -> None:
        """Clear scrollback buffer."""
        self._buffer.clear()

    def attach_listener(self) -> asyncio.Queue[str]:
        """Attach a WebSocket listener queue."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._listeners.add(q)
        return q

    def detach_listener(self, q: asyncio.Queue[str]) -> None:
        """Detach a WebSocket listener queue."""
        self._listeners.discard(q)

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    async def disconnect(self) -> None:
        """Close the SSH connection."""
        self._is_alive = False

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._process:
            try:
                self._process.close()
            except Exception:
                pass
            self._process = None

        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        logger.info("SSH disconnected: session %s", self.session_id)


class SSHManager:
    """
    Manages SSH connection profiles and active sessions.
    Thread/event-loop safe via asyncio.Lock.
    """

    def __init__(self) -> None:
        self._profiles: Dict[str, SSHConnectionProfile] = {}
        self._sessions: Dict[str, SSHSession] = {}
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_lock(self) -> asyncio.Lock:
        """Get or create event-loop-bound lock."""
        curr_loop = None
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if self._lock is None or (
            curr_loop is not None
            and (self._lock_loop is None or self._lock_loop != curr_loop or self._lock_loop.is_closed())
        ):
            self._lock = asyncio.Lock()
            self._lock_loop = curr_loop
        return self._lock

    # -----------------------------------------------------------------
    # Profile Management
    # -----------------------------------------------------------------

    def add_profile(self, profile: SSHConnectionProfile) -> SSHConnectionProfile:
        """Add or update a connection profile."""
        self._profiles[profile.id] = profile
        return profile

    def get_profile(self, profile_id: str) -> Optional[SSHConnectionProfile]:
        """Get a connection profile by ID."""
        return self._profiles.get(profile_id)

    def list_profiles(self) -> List[Dict[str, Any]]:
        """List all connection profiles (without sensitive data)."""
        return [
            {
                "id": p.id,
                "name": p.name,
                "host": p.host,
                "port": p.port,
                "username": p.username,
                "auth_method": p.auth_method,
                "has_password": p.password is not None,
                "has_key": p.private_key is not None,
                "created_at": p.created_at,
            }
            for p in self._profiles.values()
        ]

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a connection profile."""
        return self._profiles.pop(profile_id, None) is not None

    # -----------------------------------------------------------------
    # Session Management
    # -----------------------------------------------------------------

    async def connect(
        self,
        profile_id: str,
        cols: int = 80,
        rows: int = 24,
    ) -> SSHSession:
        """Create a new SSH session from a profile."""
        profile = self.get_profile(profile_id)
        if not profile:
            raise ValueError(f"SSH profile '{profile_id}' not found")

        session_id = f"ssh-{profile_id}-{str(uuid.uuid4())[:4]}"

        async with self._get_lock():
            session = SSHSession(
                session_id=session_id,
                profile=profile,
            )
            await session.connect(cols=cols, rows=rows)
            self._sessions[session_id] = session
            return session

    async def connect_direct(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: Optional[str] = None,
        private_key: Optional[str] = None,
        name: Optional[str] = None,
        cols: int = 80,
        rows: int = 24,
    ) -> SSHSession:
        """Create a new SSH session with direct connection parameters."""
        profile = SSHConnectionProfile(
            name=name or f"{username}@{host}",
            host=host,
            port=port,
            username=username,
            auth_method="key" if private_key else "password",
            password=password,
            private_key=private_key,
        )
        # Store profile for reconnection
        self._profiles[profile.id] = profile

        session_id = f"ssh-{profile.id}-{str(uuid.uuid4())[:4]}"

        async with self._get_lock():
            session = SSHSession(
                session_id=session_id,
                profile=profile,
            )
            await session.connect(cols=cols, rows=rows)
            self._sessions[session_id] = session
            return session

    async def get_session(self, session_id: str) -> Optional[SSHSession]:
        """Get an active SSH session."""
        session = self._sessions.get(session_id)
        if session and session.is_alive():
            return session
        return None

    async def disconnect_session(self, session_id: str) -> None:
        """Disconnect and remove an SSH session."""
        async with self._get_lock():
            session = self._sessions.pop(session_id, None)
            if session:
                await session.disconnect()

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active SSH sessions."""
        return [
            {
                "session_id": sid,
                "host": sess.profile.host,
                "port": sess.profile.port,
                "username": sess.profile.username,
                "name": sess.profile.name,
                "is_alive": sess.is_alive(),
                "listeners": sess.listener_count,
                "last_active": sess.last_active,
            }
            for sid, sess in self._sessions.items()
        ]

    async def cleanup_dead_sessions(self) -> None:
        """Remove sessions that are no longer alive."""
        async with self._get_lock():
            dead = [sid for sid, s in self._sessions.items() if not s.is_alive()]
            for sid in dead:
                sess = self._sessions.pop(sid, None)
                if sess:
                    await sess.disconnect()

    async def shutdown_all(self) -> None:
        """Disconnect all SSH sessions."""
        async with self._get_lock():
            for sess in self._sessions.values():
                await sess.disconnect()
            self._sessions.clear()


# Module-level singleton
ssh_manager = SSHManager()


# =====================================================================
# Pydantic Request/Response Models
# =====================================================================

class SSHConnectRequest(BaseModel):
    host: str = Field(..., min_length=1, description="SSH server hostname or IP")
    port: int = Field(22, ge=1, le=65535, description="SSH port")
    username: str = Field("root", min_length=1, description="SSH username")
    password: Optional[str] = Field(None, description="Password (optional if using key)")
    private_key: Optional[str] = Field(None, description="PEM private key string (optional)")
    name: Optional[str] = Field(None, description="Connection display name")
    save_profile: bool = Field(True, description="Save as reusable connection profile")


class SSHProfileResponse(BaseModel):
    id: str
    name: str
    host: str
    port: int
    username: str
    auth_method: str
    has_password: bool = False
    has_key: bool = False


class SSHSessionResponse(BaseModel):
    session_id: str
    host: str
    port: int
    username: str
    name: str
    is_alive: bool


# =====================================================================
# REST API Router
# =====================================================================

ssh_router = APIRouter(prefix="/api/ssh", tags=["SSH"])


@ssh_router.post("/connect", response_model=SSHSessionResponse)
async def ssh_connect(body: SSHConnectRequest, user: dict = Depends(get_current_user)):
    """Create a new SSH connection."""
    try:
        session = await ssh_manager.connect_direct(
            host=body.host,
            port=body.port,
            username=body.username,
            password=body.password,
            private_key=body.private_key,
            name=body.name,
        )
        return SSHSessionResponse(
            session_id=session.session_id,
            host=session.profile.host,
            port=session.profile.port,
            username=session.profile.username,
            name=session.profile.name,
            is_alive=session.is_alive(),
        )
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="asyncssh is not installed. Run: pip install asyncssh",
        )
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSH connection error: {e}")


@ssh_router.get("/sessions")
async def list_ssh_sessions(user: dict = Depends(get_current_user)):
    """List all active SSH sessions."""
    return await ssh_manager.list_sessions()


@ssh_router.delete("/sessions/{session_id}")
async def disconnect_ssh(session_id: str, user: dict = Depends(get_current_user)):
    """Disconnect an SSH session."""
    session = await ssh_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="SSH session not found")
    await ssh_manager.disconnect_session(session_id)
    return {"status": "disconnected", "session_id": session_id}


@ssh_router.get("/profiles")
async def list_ssh_profiles(user: dict = Depends(get_current_user)):
    """List saved SSH connection profiles."""
    return ssh_manager.list_profiles()


@ssh_router.delete("/profiles/{profile_id}")
async def delete_ssh_profile(profile_id: str, user: dict = Depends(get_current_user)):
    """Delete a saved SSH connection profile."""
    if not ssh_manager.delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "deleted", "profile_id": profile_id}


# =====================================================================
# WebSocket Endpoint Router
# =====================================================================

ssh_ws_router = APIRouter(tags=["SSH"])


@ssh_ws_router.websocket("/ws/ssh")
async def ws_ssh(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    # Direct connect params (alternative to session_id)
    host: Optional[str] = Query(None),
    port: int = Query(22),
    username: str = Query("root"),
    password: Optional[str] = Query(None),
    cols: int = Query(80),
    rows: int = Query(24),
):
    """
    WebSocket endpoint for interactive SSH terminal streaming.
    
    Can connect to:
    1. An existing SSH session via ?session_id=...
    2. A new SSH connection via ?host=...&username=...&password=...
    """
    # 1. Authenticate
    user = verify_ws_token(token)
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    session: Optional[SSHSession] = None

    try:
        # 2. Get or create SSH session
        if session_id:
            session = await ssh_manager.get_session(session_id)
            if not session:
                await websocket.send_json({
                    "type": "error",
                    "message": f"SSH session '{session_id}' not found or disconnected",
                })
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                return
        elif host:
            try:
                session = await ssh_manager.connect_direct(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    cols=cols,
                    rows=rows,
                )
                await websocket.send_json({
                    "type": "connected",
                    "session_id": session.session_id,
                    "host": host,
                    "username": username,
                })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": f"SSH connection failed: {e}",
                })
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                return
        else:
            await websocket.send_json({
                "type": "error",
                "message": "Provide session_id or host to connect",
            })
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # 3. Attach listener and replay history
        listener_queue = session.attach_listener()
        history = session.get_history()
        if history:
            await websocket.send_json({"type": "output", "data": history})

        # 4. Outbound sender task
        async def _sender_loop():
            try:
                while True:
                    chunk = await listener_queue.get()
                    await websocket.send_json({"type": "output", "data": chunk})
            except (asyncio.CancelledError, WebSocketDisconnect, Exception):
                pass

        sender_task = asyncio.create_task(_sender_loop())

        # 5. Inbound receiver loop
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break

                text_data = msg.get("text")
                if not text_data:
                    continue

                try:
                    frame = json.loads(text_data)
                except Exception:
                    frame = {"type": "input", "data": text_data}

                if not isinstance(frame, dict):
                    continue

                msg_type = frame.get("type")
                if msg_type == "input":
                    await session.write_input(frame.get("data", ""))
                elif msg_type == "resize":
                    await session.resize(
                        frame.get("cols", 80),
                        frame.get("rows", 24),
                    )
                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "clear":
                    session.clear_history()

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            sender_task.cancel()
            try:
                await sender_task
            except (asyncio.CancelledError, Exception):
                pass
            session.detach_listener(listener_queue)

    except Exception as e:
        logger.error("SSH WebSocket error: %s", e)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass
