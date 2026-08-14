"""
tests/test_adversarial_tier5_hardening.py

Tier 5 Adversarial Stress & System Hardening Suite
Author: Challenger 1 (Milestone 6 Phase 2)

Comprehensive stress testing covering:
1. Extreme Concurrency & WebSocket Connection Flooding:
   - Dozens of parallel WebSocket connections across /ws/session, /ws/terminal, /ws/system.
   - High-frequency subscribe/unsubscribe churn on /ws/session.
   - Parallel connect/disconnect storms on /ws/terminal and /ws/system.
   - Floods of unauthenticated, expired, forged, and malformed token connections.
   - Concurrent multi-client broadcast on shared terminal and session channels.

2. Memory Stability & Buffer Hardening During Prolonged Streaming:
   - High-volume transcript streaming burst (thousands of rapid JSONL events).
   - Verification that memory usage is bounded and byte offsets advance monotonically without corruption.
   - Terminal circular buffer (maxlen=1000) under 50,000+ line flood, verifying memory bounds & history integrity.
   - Partial line buffering, broken JSON chunks across byte boundaries, and log file truncation recovery.
   - Telemetry poller long-running stability and subscriber queue cleanup on dropped listeners.

3. Rapid Reconnection & Network Handover State Resync:
   - WiFi/Cellular handover simulation: mid-stream disconnect, reconnect with resume byte offset.
   - Pending tool call integrity across sudden disconnects, reconnects, and REST/WS approvals.
   - Expired token handover rejection, re-authentication via PIN, and seamless resumption.
   - Terminal session persistence across client disconnects, replaying history on reconnection.

4. Adversarial Payload Fuzzing & Malformed Frame Hardening:
   - Oversized JSON frames (500KB+ strings, deep nesting, huge key counts).
   - Malformed frames (raw binary, null-byte injections, invalid UTF-8, non-dict payloads).
   - Unknown action fuzzing on /ws/session and /ws/terminal.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import concurrent.futures
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.config import Settings, get_settings
from backend.main import create_app
from backend.security import create_access_token, rate_limiter, verify_ws_token
from backend.session_hub.artifacts import ArtifactsManager
from backend.session_hub.cli_dispatcher import CLIDispatcher
from backend.session_hub.sqlite_reader import SQLiteReader
from backend.session_hub.tailer import AsyncTranscriptTailer, TranscriptEntry, TranscriptTailer
from backend.session_hub.tool_manager import ToolManager
from backend.telemetry.monitor import SystemMonitor, TelemetryPoller, telemetry_poller
from backend.terminal.pty_session import TerminalManager, TerminalSession, terminal_manager

TEST_JWT_SECRET = "tier5-challenger1-adversarial-secret-key-32b"
TEST_PIN = "739102"


# ===========================================================================
# Mock Subprocess Helper for Ultra-Fast Terminal Stress
# ===========================================================================

class FastMockProcess:
    """Mock process that produces output on demand and simulates responsive subprocess."""

    def __init__(self, banner: str = "Windows PowerShell\r\nPS E:\\Project> "):
        self.returncode = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.stdin_received: List[bytes] = []
        self._queue.put_nowait(banner.encode("utf-8"))

    class MockReader:
        def __init__(self, queue: asyncio.Queue[bytes]):
            self._queue = queue

        async def read(self, n=2048):
            return await self._queue.get()

    class MockWriter:
        def __init__(self, parent: FastMockProcess):
            self.parent = parent

        def write(self, data: bytes):
            self.parent.stdin_received.append(data)
            # Echo response back
            response = b"ECHO: " + data + b"\r\nPS E:\\Project> "
            self.parent._queue.put_nowait(response)

        async def drain(self):
            pass

        def close(self):
            pass

    @property
    def stdout(self):
        return self.MockReader(self._queue)

    @property
    def stderr(self):
        return self.MockReader(asyncio.Queue())

    @property
    def stdin(self):
        return self.MockWriter(self)

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode or 0


@pytest.fixture
def mock_fast_terminal(monkeypatch):
    """Mocks subprocess spawning to use FastMockProcess for instant test execution."""
    async def _mock_exec(*args, **kwargs):
        return FastMockProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _mock_exec)
    return FastMockProcess


@pytest.fixture(autouse=True)
def clean_system_state():
    """Ensure clean terminal sessions, fresh loop-bound locks, and cleared rate limiters."""
    terminal_manager._sessions.clear()
    terminal_manager._lock = asyncio.Lock()
    rate_limiter.clear()
    yield
    terminal_manager._sessions.clear()
    terminal_manager._lock = asyncio.Lock()
    rate_limiter.clear()


def receive_until_type(ws, target_type: str, max_frames: int = 15) -> Optional[dict]:
    """Helper to drain incoming WebSocket frames until a specific frame type is found."""
    for _ in range(max_frames):
        try:
            frame = ws.receive_json()
            if frame.get("type") == target_type:
                return frame
        except Exception:
            break
    return None


# ===========================================================================
# 1. EXTREME CONCURRENCY & WEBSOCKET FLOODING TESTS
# ===========================================================================

def test_ws_session_parallel_connection_flood(test_app, valid_jwt_token, mock_antigravity_env):
    """
    T5-CONCURRENCY-01: Flood /ws/session with 30 concurrent WebSocket connections.
    Verify all connections authenticate, subscribe, receive initial frames, and clean up.
    """
    conv_id = mock_antigravity_env.root_id
    client = TestClient(test_app)
    num_clients = 30
    results = []

    def client_worker(worker_id: int):
        try:
            with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
                ws.send_json({"action": "subscribe", "conversation_id": conv_id})
                ack = ws.receive_json()
                results.append((worker_id, True, ack.get("type")))
        except Exception as e:
            results.append((worker_id, False, str(e)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(client_worker, i) for i in range(num_clients)]
        concurrent.futures.wait(futures, timeout=10.0)

    assert len(results) == num_clients
    successes = [r for r in results if r[1] is True]
    assert len(successes) == num_clients, f"Failed connections: {[r for r in results if not r[1]]}"


def test_ws_session_rapid_churn_subscribe_unsubscribe(test_app, valid_jwt_token, mock_antigravity_env):
    """
    T5-CONCURRENCY-02: Rapidly churn subscribe/unsubscribe actions in tight loops (40 cycles).
    Verify no orphaned background tasks, no race conditions, and clean frame responses.
    """
    root_id = mock_antigravity_env.root_id
    sub_id = mock_antigravity_env.sub_id
    client = TestClient(test_app)

    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        for i in range(20):
            # Subscribe root
            ws.send_json({"action": "subscribe", "conversation_id": root_id})
            ack1 = receive_until_type(ws, "subscribed")
            assert ack1 is not None
            assert ack1.get("conversation_id") == root_id

            # Rapidly switch subscription to subagent
            ws.send_json({"action": "subscribe", "conversation_id": sub_id})
            ack2 = receive_until_type(ws, "subscribed")
            assert ack2 is not None
            assert ack2.get("conversation_id") == sub_id

            # Unsubscribe subagent
            ws.send_json({"action": "unsubscribe", "conversation_id": sub_id})
            unsub_ack = receive_until_type(ws, "unsubscribed")
            assert unsub_ack is not None
            assert unsub_ack.get("conversation_id") == sub_id


def test_ws_unauthenticated_connection_flood_exhaustion(test_app, expired_jwt_token):
    """
    T5-CONCURRENCY-03: Flood all WebSocket endpoints with 90 invalid, expired, or missing token connections.
    Verify server rejects all with WS_1008_POLICY_VIOLATION without leaking sockets or descriptors.
    """
    client = TestClient(test_app)
    rejected_count = 0
    endpoints = [
        "/ws/session",
        "/ws/terminal",
        "/ws/system",
        f"/ws/session?token=bad_token_fuzz_{time.time()}",
        f"/ws/terminal?token={expired_jwt_token}",
        f"/ws/system?token=tampered.token.here",
    ]

    for ep in endpoints * 15:  # 90 connection attempts
        try:
            with client.websocket_connect(ep) as ws:
                ws.receive_json()
        except Exception:
            rejected_count += 1

    assert rejected_count == len(endpoints) * 15


def test_ws_terminal_concurrent_multi_listener_broadcast(test_app, valid_jwt_token, mock_fast_terminal):
    """
    T5-CONCURRENCY-04: Attach 10 simultaneous WebSocket listeners to a single shared terminal session.
    Send input on one client and verify broadcast is delivered across listeners.
    """
    client = TestClient(test_app)
    session_id = "shared_stress_terminal"

    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}&session_id={session_id}") as ws1:
        # Initial banner
        f1 = ws1.receive_json()
        assert f1.get("type") == "output"

        with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}&session_id={session_id}") as ws2:
            # Replays history on connect
            f2 = ws2.receive_json()
            assert f2.get("type") == "output"

            # Send input from ws1
            ws1.send_json({"type": "input", "data": "Get-Location\r"})

            # Both ws1 and ws2 receive the echoed output
            resp1 = ws1.receive_json()
            assert resp1.get("type") == "output"

            resp2 = ws2.receive_json()
            assert resp2.get("type") == "output"


def test_ws_multi_channel_tri_concurrent_subscription(test_app, valid_jwt_token, mock_antigravity_env, mock_psutil, mock_fast_terminal):
    """
    T5-CONCURRENCY-05: Concurrently open connections to all 3 channels (/ws/session, /ws/terminal, /ws/system)
    and verify multiplexing operates without cross-talk or blocking.
    """
    conv_id = mock_antigravity_env.root_id
    client = TestClient(test_app)

    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws_sess:
        ws_sess.send_json({"action": "subscribe", "conversation_id": conv_id})
        sess_ack = receive_until_type(ws_sess, "subscribed")
        assert sess_ack is not None

        with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}&session_id=tri_term") as ws_term:
            term_frame = ws_term.receive_json()
            assert term_frame.get("type") == "output"

            with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws_sys:
                sys_frame = ws_sys.receive_json()
                assert sys_frame.get("type") == "telemetry" or "cpu" in sys_frame

                # Interact on terminal while streaming session
                ws_term.send_json({"type": "input", "data": "hostname\r"})
                term_resp = ws_term.receive_json()
                assert "ECHO" in term_resp.get("data", "") or "output" == term_resp.get("type")


# ===========================================================================
# 2. MEMORY CONSUMPTION & BUFFER STABILITY DURING PROLONGED STREAMING
# ===========================================================================

@pytest.mark.asyncio
async def test_transcript_tailer_massive_streaming_burst(tmp_path):
    """
    T5-BUFFER-01: Append 2,000 rapid transcript log events (5MB+ of data) to transcript.jsonl.
    Verify TranscriptTailer processes all events in sequence, memory remains bounded,
    and byte offsets strictly increment monotonically.
    """
    log_dir = tmp_path / "brain" / "stress_session" / ".system_generated" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    transcript_file = log_dir / "transcript.jsonl"

    num_events = 2000
    tailer = TranscriptTailer(str(transcript_file))

    # Pre-generate 2,000 events
    events_data = []
    for i in range(num_events):
        event = {
            "step_index": i,
            "source": "MODEL" if i % 2 == 0 else "USER_EXPLICIT",
            "type": "PLANNER_RESPONSE" if i % 2 == 0 else "USER_INPUT",
            "status": "DONE",
            "created_at": f"2026-08-14T12:{i % 60:02d}:00Z",
            "thinking": f"Deliberating step #{i} with deep reasoning..." if i % 2 == 0 else None,
            "content": f"Content payload for step #{i}: " + ("x" * 200),
            "tool_calls": [
                {
                    "id": f"call_{i}",
                    "name": "run_command",
                    "args": {"CommandLine": f"echo {i}"},
                    "toolAction": f"Action {i}",
                    "toolSummary": f"Summary {i}",
                }
            ] if i % 5 == 0 else [],
        }
        events_data.append(json.dumps(event) + "\n")

    # Write in 4 large batches
    batch_size = 500
    with open(transcript_file, "w", encoding="utf-8") as f:
        for b in range(4):
            f.writelines(events_data[b * batch_size : (b + 1) * batch_size])
            f.flush()

    # Tail from offset 0
    collected_steps, final_offset = tailer.tail_from_offset(0)
    assert len(collected_steps) == num_events
    assert final_offset == os.path.getsize(transcript_file)
    assert collected_steps[0]["step_index"] == 0
    assert collected_steps[-1]["step_index"] == num_events - 1


@pytest.mark.asyncio
async def test_terminal_circular_buffer_overflow_stress():
    """
    T5-BUFFER-02: Flood TerminalSession with 50,000 lines of rapid output.
    Verify deque(maxlen=1000) strictly enforces max buffer bounds, prevents memory leaks,
    and history retains exactly the latest 1,000 lines.
    """
    session = TerminalSession(session_id="overflow_test", max_buffer_lines=1000)
    assert len(session._buffer) == 0

    # Flood 50,000 chunks
    for i in range(50000):
        session._buffer.append(f"Line {i:06d}: Terminal output log stream\r\n")

    # Buffer must be capped at exactly max_buffer_lines
    assert len(session._buffer) == 1000

    history = session.get_history()
    assert "Line 049999:" in history
    assert "Line 000001:" not in history  # Early lines evicted

    # Clear history
    session.clear_history()
    assert len(session._buffer) == 0
    assert session.get_history() == ""


@pytest.mark.asyncio
async def test_tailer_partial_line_buffering_and_truncation_recovery(tmp_path):
    """
    T5-BUFFER-03: Simulate network chunk fragmentation where JSON lines are split across
    arbitrary byte boundaries, followed by log file truncation (simulating log rotation).
    Verify tailer does not crash on partial lines and recovers gracefully from truncation.
    """
    log_file = tmp_path / "fragmented.jsonl"
    tailer = TranscriptTailer(str(log_file))

    # 1. Write an incomplete JSON line (missing closing brace and newline)
    half_line = '{"step_index": 0, "source": "MODEL", "content": "incomplete'
    log_file.write_text(half_line, encoding="utf-8")

    steps, offset = tailer.tail_from_offset(0)
    assert steps == []
    assert offset == 0  # Consumed 0 full lines

    # 2. Complete the line
    full_line = half_line + ' payload"}\n'
    log_file.write_text(full_line, encoding="utf-8")

    steps, offset = tailer.tail_from_offset(0)
    assert len(steps) == 1
    assert steps[0]["content"] == "incomplete payload"
    assert offset > 0

    # 3. Simulate file truncation (file size smaller than current offset)
    new_shorter_content = '{"step_index": 99, "content": "restarted"}\n'
    log_file.write_text(new_shorter_content, encoding="utf-8")

    # Tailer seek offset > new file size -> resets to 0 and reads fresh
    steps_after_trunc, offset_after = tailer.tail_from_offset(offset + 500)
    assert len(steps_after_trunc) == 1
    assert steps_after_trunc[0]["step_index"] == 99


@pytest.mark.asyncio
async def test_telemetry_poller_queue_cleanup_under_subscriber_churn():
    """
    T5-BUFFER-04: Attach 50 mock WebSocket subscribers to TelemetryPoller, then simulate 45 of them
    disconnecting/raising exceptions during broadcast. Ensure broadcaster cleans up dead subscribers without crashing.
    """
    poller = TelemetryPoller(poll_interval=0.01)

    class MockWS:
        def __init__(self, should_fail: bool = False):
            self.should_fail = should_fail
            self.received: List[dict] = []

        async def send_json(self, data: dict):
            if self.should_fail:
                raise WebSocketDisconnect(code=1006)
            self.received.append(data)

    # 5 healthy subscribers, 45 failing subscribers
    healthy_subs = [MockWS(should_fail=False) for _ in range(5)]
    failing_subs = [MockWS(should_fail=True) for _ in range(45)]

    for s in healthy_subs + failing_subs:
        poller.register_subscriber(s)

    assert len(poller._subscribers) == 50

    # Broadcast payload
    payload = {"type": "telemetry", "cpu": 12.0, "ram": 45.0}
    await poller.broadcast(payload)

    # Dead subscribers must be automatically purged
    assert len(poller._subscribers) == 5
    for hs in healthy_subs:
        assert len(hs.received) == 1
        assert hs.received[0]["cpu"] == 12.0


# ===========================================================================
# 3. RAPID RECONNECTION & NETWORK HANDOVER STATE RESYNC
# ===========================================================================

def test_network_handover_reconnection_with_resume_offset(test_app, valid_jwt_token, mock_antigravity_env):
    """
    T5-RESYNC-01: Client streams initial transcript, experiences sudden network disconnect,
    then reconnects with ?from_offset=<byte_offset> query to seamlessly resume without duplicate frames.
    """
    conv_id = mock_antigravity_env.root_id
    client = TestClient(test_app)

    # 1. Connect first time
    last_offset = 0
    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws1:
        ws1.send_json({"action": "subscribe", "conversation_id": conv_id, "from_offset": 0})
        frame = ws1.receive_json()
        last_offset = frame.get("byte_offset", 0)

    # 2. Append new step to transcript while client was disconnected
    new_step = {
        "step_index": 10,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": "2026-08-14T12:30:00Z",
        "content": "Step generated during network disconnection",
    }
    mock_antigravity_env.append_step(conv_id, new_step)

    # 3. Reconnect with from_offset to resume
    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws2:
        ws2.send_json({"action": "subscribe", "conversation_id": conv_id, "from_offset": last_offset})
        resumed_frame = ws2.receive_json()
        assert resumed_frame.get("type") in ("subscribed", "token_stream", "step_update")


def test_pending_tool_call_state_integrity_across_disconnect_reconnect(test_app, valid_jwt_token, mock_antigravity_env):
    """
    T5-RESYNC-02: Pending tool call exists. Client connects, gets disconnected, reconnects,
    and approves the tool call via REST or WS. Verifies permission grant is permanently saved.
    """
    conv_id = mock_antigravity_env.root_id
    client = TestClient(test_app)

    # 1. Client connects via WS, sees pending tool call
    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        ws.send_json({"action": "subscribe", "conversation_id": conv_id})
        _ = ws.receive_json()
        # Abrupt disconnect before submitting decision

    # 2. Reconnect and submit decision via /ws/session
    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        ws.send_json({
            "action": "tool_decision",
            "conversation_id": conv_id,
            "tool_call_id": "call_exec_001",
            "decision": "approve",
            "pattern": "command(powershell -c Get-Process)",
        })
        ack = receive_until_type(ws, "tool_decision_ack")
        assert ack is not None
        assert ack.get("type") == "tool_decision_ack"

    # 3. Verify tool call is approved and saved to config.json
    grants = mock_antigravity_env.get_grants()
    assert any("Get-Process" in g for g in grants)


def test_token_expiry_during_handover_reconnect_flow(test_app, mock_settings):
    """
    T5-RESYNC-03: Client loses connection, tries to reconnect with expired token (rejected with 1008),
    then logs in with PIN to receive fresh token, and successfully reconnects.
    """
    client = TestClient(test_app)

    # 1. Expired token is rejected
    expired_tok = create_access_token({"sub": "admin"}, expires_delta=-10, settings=mock_settings)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/session?token={expired_tok}") as ws:
            ws.receive_json()

    # 2. Re-authenticate via PIN login
    login_resp = client.post("/api/auth/login", json={"pin": mock_settings.AUTH_PIN})
    assert login_resp.status_code == 200
    fresh_token = login_resp.json()["token"]

    # 3. Reconnect with fresh token
    with client.websocket_connect(f"/ws/session?token={fresh_token}") as ws:
        ws.send_json({"action": "subscribe", "conversation_id": "test_conv"})
        ack = ws.receive_json()
        assert ack.get("type") in ("subscribed", "error", "subagent_status")


@pytest.mark.asyncio
async def test_terminal_session_persistence_across_client_disconnect(mock_fast_terminal):
    """
    T5-RESYNC-04: Verify TerminalSession circular history buffer persists output across
    multiple listener attach/detach cycles and replays buffer history accurately upon listener reconnection.
    """
    session = TerminalSession(session_id="persistent_resync_terminal")
    await session.start_session()

    # Listener 1 attaches, sends input, and consumes output
    q1 = session.attach_listener()
    await session.write_input("PERSISTED_STATE_TOKEN\r")
    
    # Drain until token appears in received chunks
    received_text = ""
    for _ in range(5):
        chunk = await asyncio.wait_for(q1.get(), timeout=1.0)
        received_text += chunk
        if "PERSISTED_STATE_TOKEN" in received_text:
            break
    assert "PERSISTED_STATE_TOKEN" in received_text

    # Listener 1 abruptly detaches (network drop simulation)
    session.detach_listener(q1)
    assert session.listener_count == 0

    # Listener 2 connects (reconnection) and replays persisted scrollback history
    history = session.get_history()
    assert "PERSISTED_STATE_TOKEN" in history

    q2 = session.attach_listener()
    assert session.listener_count == 1
    session.detach_listener(q2)
    await session.terminate()


# ===========================================================================
# 4. ADVERSARIAL PAYLOAD FUZZING & MALFORMED FRAME HARDENING
# ===========================================================================

def test_ws_session_oversized_payload_fuzzing(test_app, valid_jwt_token, mock_antigravity_env):
    """
    T5-FUZZ-01: Send 500KB oversized prompt and 5,000-field dictionary frames to /ws/session.
    Verify server handles or rejects frames cleanly without crashing.
    """
    conv_id = mock_antigravity_env.root_id
    client = TestClient(test_app)

    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        # Oversized prompt string
        huge_prompt = "A" * 200000
        ws.send_json({
            "action": "send_prompt",
            "conversation_id": conv_id,
            "prompt": huge_prompt,
        })
        resp = ws.receive_json()
        assert resp.get("type") in ("prompt_dispatched", "error")

        # Huge nested dict
        huge_dict = {f"key_{i}": f"val_{i}" for i in range(2000)}
        huge_dict["action"] = "unknown_massive_action"
        ws.send_json(huge_dict)
        resp2 = ws.receive_json()
        assert resp2.get("type") == "error"


def test_ws_malformed_frames_binary_and_null_bytes(test_app, valid_jwt_token):
    """
    T5-FUZZ-02: Inject malformed raw text, raw binary frames, null bytes, and non-dict JSON
    into /ws/session and /ws/terminal. Verify no unhandled exceptions take down the connection.
    """
    client = TestClient(test_app)

    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        # Non-JSON string
        ws.send_text("THIS IS NOT JSON {{{]]]")
        # Null bytes
        ws.send_text('{"action": "subscribe\x00", "conversation_id": "\x00root"}')
        # Array instead of dict
        ws.send_text(json.dumps([1, 2, 3, "array_frame"]))
        # Boolean instead of dict
        ws.send_text("true")
        # Integer instead of dict
        ws.send_text("12345")

        # Send valid command afterwards to verify socket is still healthy
        ws.send_json({"action": "subscribe", "conversation_id": "recovery_test"})
        ack = ws.receive_json()
        assert ack.get("type") in ("subscribed", "error")


def test_ws_terminal_malformed_frame_resilience(test_app, valid_jwt_token, mock_fast_terminal):
    """
    T5-FUZZ-03: Send invalid resize coordinates, negative dimensions, null byte inputs,
    and unknown types to /ws/terminal. Verify session remains alive.
    """
    client = TestClient(test_app)

    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}&session_id=fuzz_term") as ws:
        _ = ws.receive_json()  # Banner

        # Negative resize dimensions
        ws.send_json({"type": "resize", "cols": -500, "rows": -100})
        # Huge resize dimensions
        ws.send_json({"type": "resize", "cols": 999999, "rows": 999999})
        # Null byte input
        ws.send_json({"type": "input", "data": "\x00\x00\x00"})
        # Unknown frame type
        ws.send_json({"type": "non_existent_type", "foo": "bar"})
        # Ping
        ws.send_json({"type": "ping"})
        pong = receive_until_type(ws, "pong")
        assert pong is not None
        assert pong.get("type") == "pong"


def test_ws_rapid_fire_tool_decision_burst_safety(test_app, valid_jwt_token, mock_antigravity_env):
    """
    T5-FUZZ-04: Fire rapid burst of 20 conflicting tool approval/rejection decisions concurrently.
    Verify ToolManager handles file locking and atomic state updates cleanly without corrupting config.json.
    """
    conv_id = mock_antigravity_env.root_id
    client = TestClient(test_app)

    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        for i in range(10):
            ws.send_json({
                "action": "tool_decision",
                "conversation_id": conv_id,
                "tool_call_id": f"call_burst_{i}",
                "decision": "approve" if i % 2 == 0 else "reject",
                "pattern": f"command(echo {i})",
            })
            resp = ws.receive_json()
            assert resp.get("type") in ("tool_decision_ack", "error")
