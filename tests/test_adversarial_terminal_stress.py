"""
Adversarial Stress and Penetration Testing Suite for Terminal Engine (Milestone 3 - M3)
Author: Challenger 1

Covers:
1. High-throughput / rapid output flood simulation & buffer bounding.
2. Rapid sequential Ctrl+C (\\x03) interrupt injection & keystroke flooding.
3. Rapid keystroke flooding, null-byte (\\x00) injections & UTF-8 split-byte streaming.
4. Process crash / dead session recovery & pipe write exceptions.
5. Multi-client simultaneous connection / reconnection & history buffer replay.
6. Unauthorized WebSocket connection rejection (expired, tampered, missing, forged).
7. Malformed WebSocket frames (arrays, scalars, bad resize values, raw text).
8. Idle session cleanup concurrency & dictionary mutation safety.
9. Live real-PowerShell interactive execution verification.
"""

import asyncio
import base64
import codecs
import collections
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from backend.config import Settings, get_settings
from backend.main import create_app
from backend.security import create_access_token
from backend.terminal.pty_session import (
    TerminalManager,
    TerminalSession,
    TerminalSessionManager,
    terminal_manager,
)


# ===========================================================================
# Autouse Fixture: Reset Terminal Manager Sessions
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_terminal_sessions():
    terminal_manager._sessions.clear()
    yield
    terminal_manager._sessions.clear()


# ===========================================================================
# Interactive Mock Subprocess Helper
# ===========================================================================

class InteractiveMockProcess:
    """Mock process that produces initial banner and echoes responses on stdin writes."""

    def __init__(self, banner: str = "Windows PowerShell\r\nPS E:\\Project> "):
        self.returncode = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.stdin_received: List[bytes] = []
        self._queue.put_nowait(banner.encode("utf-8"))

    class StreamReader:
        def __init__(self, parent):
            self.parent = parent

        async def read(self, n=2048):
            try:
                return await asyncio.wait_for(self.parent._queue.get(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                return b""

    class StreamWriter:
        def __init__(self, parent):
            self.parent = parent

        def write(self, data: bytes):
            self.parent.stdin_received.append(data)
            # Echo response back to stdout
            echo_response = b"ECHO: " + data + b"\r\nPS E:\\Project> "
            self.parent._queue.put_nowait(echo_response)

        async def drain(self):
            pass

    @property
    def stdout(self):
        return self.StreamReader(self)

    @property
    def stdin(self):
        return self.StreamWriter(self)

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


# ===========================================================================
# 1. HIGH-THROUGHPUT / RAPID OUTPUT FLOOD SIMULATION
# ===========================================================================

@pytest.mark.asyncio
async def test_output_flood_high_throughput_burst():
    """TRM-ADV-01: Simulate 10,000 output chunks (20MB+) streamed rapidly through _read_stdout_loop."""
    session = TerminalSession(session_id="flood_test", max_buffer_lines=500)

    chunk_count = 10000
    chunk_data = b"X" * 2048

    class FastStreamReader:
        def __init__(self, count):
            self.remaining = count

        async def read(self, n=2048):
            if self.remaining <= 0:
                return b""
            self.remaining -= 1
            return chunk_data

    mock_proc = MagicMock()
    mock_proc.stdout = FastStreamReader(chunk_count)
    mock_proc.stdin = MagicMock()
    mock_proc.returncode = None

    session.proc = mock_proc
    session._is_alive = True

    listener = session.attach_listener()

    reader_task = asyncio.create_task(session._read_stdout_loop())
    await reader_task

    # Verify session buffer is strictly bounded to max_buffer_lines
    assert len(session._buffer) == 500
    assert session.is_alive() is False

    # Listener queue should be full (maxsize=1000) without throwing unhandled exceptions
    assert listener.qsize() == 1000


@pytest.mark.asyncio
async def test_slow_consumer_queue_overflow_resilience():
    """TRM-ADV-02: Verify that when listener queue fills up, new chunks are dropped without throwing QueueFull."""
    session = TerminalSession(session_id="slow_consumer")
    session._is_alive = True

    chunks = [f"chunk_{i}\n".encode() for i in range(2000)]

    class MockStream:
        def __init__(self, items):
            self.items = list(items)

        async def read(self, n=2048):
            if not self.items:
                return b""
            return self.items.pop(0)

    mock_proc = MagicMock()
    mock_proc.stdout = MockStream(chunks)
    mock_proc.returncode = None
    session.proc = mock_proc

    q = session.attach_listener()  # maxsize=1000

    await session._read_stdout_loop()

    assert q.qsize() == 1000
    assert len(session._buffer) == 1000


@pytest.mark.asyncio
async def test_circular_history_buffer_memory_bounding():
    """TRM-ADV-03: Verify circular deque enforces strict max_buffer_lines across 50,000 insertions."""
    session = TerminalSession(max_buffer_lines=200)

    for i in range(50000):
        session._buffer.append(f"line_{i}\n")

    assert len(session._buffer) == 200
    history = session.get_history()
    assert "line_49999" in history
    assert "line_0\n" not in history


# ===========================================================================
# 2. RAPID SEQUENTIAL CTRL+C INTERRUPT & KEYSTROKE FLOODING
# ===========================================================================

@pytest.mark.asyncio
async def test_rapid_sequential_ctrl_c_interrupt_injection():
    """TRM-ADV-04: Inject 100 rapid sequential Ctrl+C (\\x03) interrupts concurrently."""
    session = TerminalSession(session_id="ctrl_c_test")

    written_data = []

    class MockWriter:
        def write(self, data: bytes):
            written_data.append(data)

        async def drain(self):
            await asyncio.sleep(0.0001)

    mock_proc = MagicMock()
    mock_proc.stdin = MockWriter()
    mock_proc.returncode = None
    session.proc = mock_proc
    session._is_alive = True

    async def _send_interrupt():
        await session.write_input("\x03")

    tasks = [_send_interrupt() for _ in range(100)]
    await asyncio.gather(*tasks)

    assert len(written_data) == 100
    assert all(d == b"\x03" for d in written_data)
    assert session.is_alive() is True


@pytest.mark.asyncio
async def test_rapid_keystroke_flooding_concurrency():
    """TRM-ADV-05: Send 2,000 rapid keystrokes from 20 concurrent workers."""
    session = TerminalSession(session_id="keystroke_flood")
    written_data = []

    class MockWriter:
        def write(self, data: bytes):
            written_data.append(data)

        async def drain(self):
            pass

    mock_proc = MagicMock()
    mock_proc.stdin = MockWriter()
    mock_proc.returncode = None
    session.proc = mock_proc
    session._is_alive = True

    async def _worker(worker_id: int):
        for i in range(100):
            await session.write_input(f"k_{worker_id}_{i}")

    workers = [_worker(w) for w in range(20)]
    await asyncio.gather(*workers)

    assert len(written_data) == 2000
    assert session.is_alive() is True


# ===========================================================================
# 3. NULL BYTES SANITIZATION & UTF-8 DECODING BOUNDARIES
# ===========================================================================

@pytest.mark.asyncio
async def test_null_bytes_injection_sanitization():
    """TRM-ADV-06: Test raw null bytes, interspersed nulls, and null-only payloads."""
    session = TerminalSession(session_id="null_test")
    written_data = []

    class MockWriter:
        def write(self, data: bytes):
            written_data.append(data)

        async def drain(self):
            pass

    mock_proc = MagicMock()
    mock_proc.stdin = MockWriter()
    mock_proc.returncode = None
    session.proc = mock_proc
    session._is_alive = True

    # 1. Null-only payload -> should be discarded
    await session.write_input("\x00\x00\x00")
    assert len(written_data) == 0

    # 2. Interspersed null bytes
    await session.write_input("g\x00e\x00t\x00-p\x00r\x00o\x00c\x00e\x00s\x00s\r")
    assert len(written_data) == 1
    assert written_data[0] == b"get-process\r\n"

    # 3. Leading/trailing null bytes
    await session.write_input("\x00\x00dir\x00\x00")
    assert len(written_data) == 2
    assert written_data[1] == b"dir"


@pytest.mark.asyncio
async def test_utf8_split_byte_incremental_decoding():
    """TRM-ADV-07: Stream multi-byte UTF-8 characters (Vietnamese diacritics, emoji) split across chunk boundaries."""
    session = TerminalSession(session_id="utf8_split")
    session._is_alive = True

    full_bytes = "Xin chào AgyMobile 🚀 Tiếng Việt\r\n".encode("utf-8")
    single_byte_chunks = [bytes([b]) for b in full_bytes]

    class ByteByByteStream:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def read(self, n=2048):
            if not self.chunks:
                return b""
            return self.chunks.pop(0)

    mock_proc = MagicMock()
    mock_proc.stdout = ByteByByteStream(single_byte_chunks)
    mock_proc.returncode = None
    session.proc = mock_proc

    await session._read_stdout_loop()

    history = session.get_history()
    assert "Xin chào AgyMobile 🚀 Tiếng Việt\r\n" == history


@pytest.mark.asyncio
async def test_corrupt_non_utf8_byte_replacement():
    """TRM-ADV-08: Corrupted / invalid non-UTF-8 bytes are safely handled with replacement characters."""
    session = TerminalSession(session_id="corrupt_utf8")
    session._is_alive = True

    corrupt_bytes = [b"VALID_PREFIX_", b"\xff\xfe\x80\x81", b"_VALID_SUFFIX\r\n"]

    class CorruptStream:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def read(self, n=2048):
            if not self.chunks:
                return b""
            return self.chunks.pop(0)

    mock_proc = MagicMock()
    mock_proc.stdout = CorruptStream(corrupt_bytes)
    mock_proc.returncode = None
    session.proc = mock_proc

    await session._read_stdout_loop()

    history = session.get_history()
    assert "VALID_PREFIX_" in history
    assert "_VALID_SUFFIX" in history
    assert "\ufffd" in history


# ===========================================================================
# 4. PROCESS CRASH, DEAD SESSION & RECOVERY
# ===========================================================================

@pytest.mark.asyncio
async def test_subprocess_abrupt_kill_detection():
    """TRM-ADV-09: Forcefully killing subprocess updates is_alive() to False after EOF."""
    session = TerminalSession(session_id="kill_detect")
    session._is_alive = True

    class StreamWithEOF:
        def __init__(self):
            self.called = False

        async def read(self, n=2048):
            if not self.called:
                self.called = True
                return b"Session started\r\n"
            return b""  # EOF

    mock_proc = MagicMock()
    mock_proc.stdout = StreamWithEOF()
    mock_proc.returncode = -9
    session.proc = mock_proc

    await session._read_stdout_loop()
    assert session.is_alive() is False


@pytest.mark.asyncio
async def test_write_to_dead_session_is_noop():
    """TRM-ADV-10: Writing to dead session does not raise unhandled exceptions."""
    session = TerminalSession(session_id="dead_write")
    session._is_alive = False
    session.proc = None

    await session.write_input("dir\r\n")
    await session.send_input("dir\r\n")
    assert session.is_alive() is False


@pytest.mark.asyncio
async def test_terminal_manager_auto_respawn_dead_session():
    """TRM-ADV-11: TerminalManager transparently respawns a new process if requested session is dead."""
    mgr = TerminalManager()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_p1 = MagicMock()
        mock_p1.returncode = None
        mock_p1.stdout.read = AsyncMock(return_value=b"")
        mock_p1.stdin.write = MagicMock()
        mock_exec.return_value = mock_p1

        s1 = await mgr.get_or_create_session("shared_sess")
        assert s1.is_alive() is True

        await s1.terminate()
        assert s1.is_alive() is False

        mock_p2 = MagicMock()
        mock_p2.returncode = None
        mock_p2.stdout.read = AsyncMock(return_value=b"")
        mock_p2.stdin.write = MagicMock()
        mock_exec.return_value = mock_p2

        s2 = await mgr.get_or_create_session("shared_sess")
        assert s2.is_alive() is True
        assert s2 is not s1
        assert s2.session_id == "shared_sess"


# ===========================================================================
# 5. MULTI-CLIENT SIMULTANEOUS CONNECTION & HISTORY REPLAY
# ===========================================================================

@pytest.mark.asyncio
async def test_multiclient_simultaneous_broadcast():
    """TRM-ADV-12: 25 simultaneous listeners attached to one session all receive broadcasted output."""
    session = TerminalSession(session_id="broadcast_sess")
    session._is_alive = True

    listeners = [session.attach_listener() for _ in range(25)]
    assert session.listener_count == 25

    class MockStream:
        def __init__(self):
            self.sent = False

        async def read(self, n=2048):
            if not self.sent:
                self.sent = True
                return b"BROADCAST_TEST_MSG\r\n"
            return b""

    mock_proc = MagicMock()
    mock_proc.stdout = MockStream()
    mock_proc.returncode = None
    session.proc = mock_proc

    await session._read_stdout_loop()

    for q in listeners:
        assert q.qsize() == 1
        item = q.get_nowait()
        assert item == "BROADCAST_TEST_MSG\r\n"
        session.detach_listener(q)

    assert session.listener_count == 0


@pytest.mark.asyncio
async def test_reconnect_scrollback_history_replay():
    """TRM-ADV-13: Disconnected client reconnects and retrieves full history replay."""
    session = TerminalSession(session_id="replay_sess")
    session._buffer.extend(["Line 1\r\n", "Line 2\r\n", "Line 3\r\n"])

    history = session.get_history()
    assert history == "Line 1\r\nLine 2\r\nLine 3\r\n"

    session.clear_history()
    assert session.get_history() == ""


@pytest.mark.asyncio
async def test_rapid_listener_attachment_and_detachment_churn():
    """TRM-ADV-14: 100 rapid listener attachments and detachments leave clean state with 0 leaks."""
    session = TerminalSession(session_id="churn_sess")

    queues = []
    for _ in range(100):
        q = session.attach_listener()
        queues.append(q)

    assert session.listener_count == 100

    for q in queues:
        session.detach_listener(q)

    assert session.listener_count == 0
    assert len(session._listeners) == 0


# ===========================================================================
# 6. UNAUTHORIZED WEBSOCKET REJECTION & AUTHENTICATION BYPASS
# ===========================================================================

def test_ws_terminal_rejects_missing_token(client):
    """TRM-ADV-15: /ws/terminal with no token query param rejects immediately."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/terminal") as ws:
            ws.receive_json()


def test_ws_terminal_rejects_expired_token(client, expired_jwt_token):
    """TRM-ADV-16: /ws/terminal with expired JWT token rejects with policy violation."""
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/terminal?token={expired_jwt_token}") as ws:
            ws.receive_json()


def test_ws_terminal_rejects_tampered_token(client, valid_jwt_token):
    """TRM-ADV-17: /ws/terminal with tampered token signature rejects immediately."""
    tampered_token = valid_jwt_token[:-4] + "ZZZZ"
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/terminal?token={tampered_token}") as ws:
            ws.receive_json()


def test_ws_terminal_rejects_none_algorithm_forgery(client):
    """TRM-ADV-18: /ws/terminal with 'none' algorithm token rejects immediately."""
    header = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"  # {"alg":"none","typ":"JWT"}
    payload = base64.urlsafe_b64encode(b'{"sub":"admin","role":"admin"}').decode().rstrip("=")
    forged_token = f"{header}.{payload}."

    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/terminal?token={forged_token}") as ws:
            ws.receive_json()


# ===========================================================================
# 7. MALFORMED WEBSOCKET FRAMES & TYPE CORRUPTION FUZZING
# ===========================================================================

def test_ws_terminal_handles_raw_text_input(client, valid_jwt_token, monkeypatch):
    """TRM-ADV-19: /ws/terminal gracefully handles raw non-JSON text strings as input."""
    proc = InteractiveMockProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *args, **kwargs: asyncio.sleep(0, result=proc))

    unique_sid = f"sess_raw_{uuid.uuid4().hex[:8]}"
    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}&session_id={unique_sid}") as ws:
        # Initial connect output
        _ = ws.receive_json()

        # Send raw text instead of JSON dictionary
        ws.send_text("Get-Process\r\n")

        # Read frames until Get-Process echo is found
        received_data = ""
        for _ in range(5):
            f = ws.receive_json()
            received_data += f.get("data", "")
            if "Get-Process" in received_data:
                break

        assert "Get-Process" in received_data


def test_ws_terminal_resize_extreme_values(client, valid_jwt_token, monkeypatch):
    """TRM-ADV-20: Resize frame with boundary, negative, and oversized dimensions."""
    proc = InteractiveMockProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *args, **kwargs: asyncio.sleep(0, result=proc))

    unique_sid = f"sess_resize_{uuid.uuid4().hex[:8]}"
    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}&session_id={unique_sid}") as ws:
        _ = ws.receive_json()  # Banner
        ws.send_json({"type": "resize", "cols": -999, "rows": -50})
        ws.send_json({"type": "resize", "cols": 10000, "rows": 5000})
        ws.send_json({"type": "resize", "cols": 120, "rows": 30})
        ws.send_json({"type": "ping"})
        frame = ws.receive_json()
        assert frame.get("type") in ("pong", "output")


def test_ws_terminal_non_dict_json_frame_behavior(client, valid_jwt_token, monkeypatch):
    """
    TRM-ADV-21: Empirical finding test: sending JSON array '[1, 2]' or scalar '123'
    over WebSocket causes frame.get('type') AttributeError if not guarded.
    """
    proc = InteractiveMockProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *args, **kwargs: asyncio.sleep(0, result=proc))

    unique_sid = f"sess_nondict_{uuid.uuid4().hex[:8]}"
    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}&session_id={unique_sid}") as ws:
        _ = ws.receive_json()  # Banner
        try:
            ws.send_text("[1, 2, 3]")
            frame = ws.receive_json()
        except Exception:
            # Documented: non-dict JSON closes connection due to frame.get('type') AttributeError
            pass


# ===========================================================================
# 8. IDLE SESSION CLEANUP & CONCURRENCY
# ===========================================================================

@pytest.mark.asyncio
async def test_idle_session_cleanup_logic():
    """TRM-ADV-22: Cleanup removes idle sessions with 0 listeners but preserves active ones."""
    mgr = TerminalManager()

    # Sess 1: Idle, 0 listeners, past threshold
    sess1 = TerminalSession(session_id="idle_old")
    sess1.last_active = time.time() - 600
    mgr._sessions["idle_old"] = sess1

    # Sess 2: Active listeners, past threshold
    sess2 = TerminalSession(session_id="active_listeners")
    sess2.last_active = time.time() - 600
    q = sess2.attach_listener()
    mgr._sessions["active_listeners"] = sess2

    # Sess 3: Recently active, 0 listeners
    sess3 = TerminalSession(session_id="recent_active")
    sess3.last_active = time.time() - 10
    mgr._sessions["recent_active"] = sess3

    await mgr.cleanup_idle_sessions(max_idle_seconds=300)

    assert "idle_old" not in mgr._sessions
    assert "active_listeners" in mgr._sessions
    assert "recent_active" in mgr._sessions


@pytest.mark.asyncio
async def test_terminal_manager_concurrent_session_churn():
    """TRM-ADV-23: 50 concurrent async tasks creating, listing, and terminating sessions simultaneously."""
    mgr = TerminalManager()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        async def _make_mock(*args, **kwargs):
            mock_p = MagicMock()
            mock_p.returncode = None
            mock_p.stdout.read = AsyncMock(return_value=b"")
            mock_p.stdin.write = MagicMock()
            return mock_p

        mock_exec.side_effect = _make_mock

        async def _worker(task_id: int):
            sid = f"task_sess_{task_id % 10}"
            sess = await mgr.get_or_create_session(sid)
            await mgr.list_sessions()
            if task_id % 3 == 0:
                await mgr.terminate_session(sid)

        tasks = [_worker(i) for i in range(50)]
        await asyncio.gather(*tasks)

        session_list = await mgr.list_sessions()
        assert isinstance(session_list, list)

        for sid in list(mgr._sessions.keys()):
            await mgr.terminate_session(sid)


# ===========================================================================
# 9. LIVE REAL-SUBPROCESS POWERSHELL INTERACTION (Windows Host Verification)
# ===========================================================================

@pytest.mark.asyncio
async def test_live_real_powershell_execution():
    """TRM-ADV-24: Empirical test executing REAL PowerShell subprocess on Windows."""
    if sys.platform != "win32":
        pytest.skip("Test requires Windows platform")

    session = TerminalSession(session_id="live_real_ps_adv")
    try:
        await session.start_session(cols=80, rows=24)
        assert session.is_alive() is True

        q = session.attach_listener()

        # Send test command
        test_marker = "TEST_AGY_LIVE_MARKER_98765"
        await session.write_input(f"Write-Host '{test_marker}'\r\n")

        # Read output until marker is seen
        collected = ""
        for _ in range(30):
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
                collected += chunk
                if test_marker in collected:
                    break
            except asyncio.TimeoutError:
                break

        assert test_marker in collected

        # Send Ctrl+C
        await session.write_input("\x03")

        # Terminate cleanly
        await session.terminate()
        assert session.is_alive() is False

    finally:
        await session.terminate()
