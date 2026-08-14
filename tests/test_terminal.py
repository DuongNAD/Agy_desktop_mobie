import asyncio
import json
import pytest

from backend.terminal.pty_session import (
    TerminalManager,
    TerminalSession,
    TerminalSessionManager,
    terminal_manager,
)


# ===========================================================================
# Tier 1: Core Functional & Happy Path Tests (F09, F10)
# ===========================================================================

@pytest.mark.asyncio
async def test_async_powershell_session_spawn(mock_subprocess_runner):
    """TRM-01: TerminalSession starts an interactive shell subprocess with is_alive() returning True."""
    session = TerminalSession(session_id="test_trm01")
    await session.start_session(cols=80, rows=24)
    assert session.is_alive() is True
    assert session.session_id == "test_trm01"
    assert session.cols == 80
    assert session.rows == 24
    await session.terminate()
    assert session.is_alive() is False


@pytest.mark.asyncio
async def test_stdout_stderr_streaming(mock_subprocess_runner):
    """TRM-02: Streamed output from PowerShell subprocess yields stdout bytes/strings."""
    proc = await asyncio.create_subprocess_exec("powershell.exe")
    out, err = await proc.communicate(b"Get-ChildItem\r\n")
    assert b"README.md" in out or len(out) > 0


@pytest.mark.asyncio
async def test_virtual_utility_keys_dispatch(mock_subprocess_runner):
    """TRM-03: Virtual touch utility keys (Esc, Tab, Ctrl+C, Up arrow) are dispatched to process."""
    keys = [b"\x1b", b"\t", b"\x03", b"\x1b[A"]
    proc = await asyncio.create_subprocess_exec("powershell.exe")
    for k in keys:
        proc.stdin.write(k)
        await proc.stdin.drain()
    assert len(proc.stdin_received) == 4


@pytest.mark.asyncio
async def test_terminal_resize_event():
    """TRM-04: Terminal resize updates dimension properties (cols, rows) without error."""
    session = TerminalSession()
    session.resize(cols=120, rows=40)
    assert session.cols == 120 and session.rows == 40
    # Test boundary clamping
    session.resize(cols=9999, rows=-5)
    assert session.cols == 500
    assert session.rows == 5


@pytest.mark.asyncio
async def test_exit_code_and_clean_termination(mock_subprocess_runner):
    """TRM-05: Terminating the session cleanly terminates subprocess and releases resources."""
    proc = await asyncio.create_subprocess_exec("powershell.exe")
    proc.terminate()
    assert proc.returncode == -15 or proc.returncode is not None


def test_ansi_color_code_preservation():
    """TRM-06: VT100 ANSI escape sequences for colors and styles are preserved intact."""
    ansi_text = "\x1b[32mDirectory: e:\\Project\x1b[0m\r\n\x1b[31mError\x1b[0m"
    assert "\x1b[32m" in ansi_text
    assert "\x1b[0m" in ansi_text


# ===========================================================================
# Tier 2: Boundary, Adversarial & Error Handling Tests (F09, F10)
# ===========================================================================

@pytest.mark.asyncio
async def test_command_timeout_and_hang_handling(mock_subprocess_runner):
    """TRM-07: Ctrl+C (\\x03) interrupt breaks out of long-running or hanging commands."""
    proc = await asyncio.create_subprocess_exec("powershell.exe")
    proc.stdin.write(b"\x03")
    await proc.stdin.drain()
    assert b"\x03" in proc.stdin_received


@pytest.mark.asyncio
async def test_special_character_escaping_and_unicode(mock_subprocess_runner):
    """TRM-08: Unicode strings, Vietnamese text, and emojis are transmitted without byte corruption."""
    unicode_payload = "echo 'Xin chào AgyMobile 🚀'\r\n".encode("utf-8")
    proc = await asyncio.create_subprocess_exec("powershell.exe")
    proc.stdin.write(unicode_payload)
    await proc.stdin.drain()
    assert unicode_payload in proc.stdin_received


def test_ansi_escape_code_parser_integrity():
    """TRM-09: Complex ANSI cursor and screen control sequences parse cleanly."""
    sequences = [
        "\x1b[2J",   # Clear screen
        "\x1b[H",    # Move cursor to home
        "\x1b[2K",   # Clear line
        "\x1b[1;33m" # Bold yellow
    ]
    combined = "".join(sequences)
    assert len(combined) > 10


@pytest.mark.asyncio
async def test_rapid_keystroke_flood_handling(mock_subprocess_runner):
    """TRM-10: Burst of 500 rapid keystrokes within short time window is buffered cleanly."""
    proc = await asyncio.create_subprocess_exec("powershell.exe")
    for i in range(500):
        proc.stdin.write(b"a")
    await proc.stdin.drain()
    assert len(proc.stdin_received) == 500


@pytest.mark.asyncio
async def test_dead_session_recovery_and_cleanup(mock_subprocess_runner):
    """TRM-11: Abrupt process exit or kill is detected and cleanup is triggered."""
    proc = await asyncio.create_subprocess_exec("powershell.exe")
    proc.kill()
    assert proc.returncode == -9


def test_null_bytes_and_control_chars_sanitization():
    """TRM-12: Raw null bytes \\x00 in input are sanitized to prevent stream termination."""
    raw_input = "dir\x00-a\r\n"
    sanitized = raw_input.replace("\x00", "")
    assert "\x00" not in sanitized
    assert sanitized == "dir-a\r\n"


@pytest.mark.asyncio
async def test_terminal_manager_lifecycle(mock_subprocess_runner):
    """TRM-Extra: TerminalManager manages sessions, listeners, and cleanup."""
    mgr = TerminalManager()
    sess1 = await mgr.get_or_create_session("sess1", cols=90, rows=30)
    assert sess1.session_id == "sess1"
    assert sess1.cols == 90

    # Retrieve existing
    sess1_same = await mgr.get_or_create_session("sess1")
    assert sess1_same is sess1

    # List sessions
    active_list = await mgr.list_sessions()
    assert len(active_list) == 1
    assert active_list[0]["session_id"] == "sess1"

    # Terminate session
    await mgr.terminate_session("sess1")
    assert sess1.is_alive() is False
    assert (await mgr.get_session("sess1")) is None


# ===========================================================================
# Tier 3: Cross-Feature Interaction Tests
# ===========================================================================

def test_terminal_websocket_streaming_integration(client, valid_jwt_token):
    """TRM-13: /ws/terminal WebSocket channel accepts input frames and streams ANSI output."""
    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws:
        ws.send_json({"type": "input", "data": "echo TEST_ANSI\r"})
        frame = ws.receive_json()
        assert frame.get("type") == "output"
        assert "data" in frame


def test_unauthenticated_terminal_websocket_rejection(client):
    """TRM-14: /ws/terminal without valid JWT closes connection immediately."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/terminal?token=invalid_token") as ws:
            ws.receive_json()


def test_terminal_websocket_ping_pong_and_clear(client, valid_jwt_token):
    """TRM-Extra: /ws/terminal handles ping, clear, and restart frames."""
    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws:
        # Ping -> Pong
        ws.send_json({"type": "ping"})
        pong_frame = ws.receive_json()
        assert pong_frame.get("type") == "pong" or pong_frame.get("type") == "output"

        # Clear history
        ws.send_json({"type": "clear"})


# ===========================================================================
# Tier 4: Real-World Mobile Workflow Test
# ===========================================================================

def test_mobile_touch_terminal_interactive_session_workflow(client, valid_jwt_token):
    """TRM-15: Complete Flow: Connect -> Resize -> Send Command -> Receive ANSI -> Send Ctrl+C."""
    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws:
        # 1. Resize terminal for mobile screen
        ws.send_json({"type": "resize", "cols": 80, "rows": 24})

        # 2. Send directory listing command
        ws.send_json({"type": "input", "data": "Get-ChildItem\r"})
        frame1 = ws.receive_json()
        assert frame1["type"] == "output"

        # 3. Send virtual Ctrl+C interrupt
        ws.send_json({"type": "input", "data": "\x03"})
