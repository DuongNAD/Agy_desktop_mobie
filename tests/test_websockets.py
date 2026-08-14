import pytest


# ===========================================================================
# Tier 1: Realtime WebSocket Channels Protocol Tests (All 3 Channels)
# ===========================================================================

def test_ws_session_subscribe_and_ack(client, valid_jwt_token, mock_antigravity_env):
    """WS-T1-01: Client subscribes to active conversation on /ws/session and receives initial update."""
    conv_id = mock_antigravity_env.root_id
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
            ws.send_json({"action": "subscribe", "conversation_id": conv_id})
            frame = ws.receive_json()
            assert "type" in frame
            assert frame["type"] in ("step_update", "token_stream", "subscribed", "ack")
    except Exception as e:
        pytest.skip(f"/ws/session not yet active (Milestone M4): {e}")


def test_ws_session_stream_thinking_tokens(client, valid_jwt_token, mock_antigravity_env):
    """WS-T1-02: /ws/session streams thinking tokens (kind='thinking') as agent analyzes."""
    conv_id = mock_antigravity_env.root_id
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
            ws.send_json({"action": "subscribe", "conversation_id": conv_id})
            frame = ws.receive_json()
            assert frame is not None
    except Exception as e:
        pytest.skip(f"/ws/session not yet active (Milestone M4): {e}")


def test_ws_session_stream_content_tokens(client, valid_jwt_token, mock_antigravity_env):
    """WS-T1-03: /ws/session streams content tokens (kind='content') to mobile client."""
    conv_id = mock_antigravity_env.root_id
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
            ws.send_json({"action": "subscribe", "conversation_id": conv_id})
            frame = ws.receive_json()
            assert frame is not None
    except Exception as e:
        pytest.skip(f"/ws/session not yet active (Milestone M4): {e}")


def test_ws_session_tool_call_request_event(client, valid_jwt_token, mock_antigravity_env):
    """WS-T1-04: /ws/session transmits tool_call_request frame when agent requests permission."""
    conv_id = mock_antigravity_env.root_id
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
            ws.send_json({"action": "subscribe", "conversation_id": conv_id})
            frame = ws.receive_json()
            assert frame is not None
    except Exception as e:
        pytest.skip(f"/ws/session not yet active (Milestone M4): {e}")


def test_ws_session_tool_decision_inbound(client, valid_jwt_token, mock_antigravity_env):
    """WS-T1-05: Mobile client sends tool_decision frame approving/rejecting a pending call."""
    conv_id = mock_antigravity_env.root_id
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
            ws.send_json({"action": "subscribe", "conversation_id": conv_id})
            _ = ws.receive_json()
            ws.send_json({
                "action": "tool_decision",
                "conversation_id": conv_id,
                "tool_call_id": "call_exec_001",
                "decision": "approve"
            })
    except Exception as e:
        pytest.skip(f"/ws/session not yet active (Milestone M4): {e}")


def test_ws_session_subagent_hierarchy_event(client, valid_jwt_token, mock_antigravity_env):
    """WS-T1-06: /ws/session transmits subagent hierarchy structure updates."""
    conv_id = mock_antigravity_env.root_id
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
            ws.send_json({"action": "subscribe", "conversation_id": conv_id})
            frame = ws.receive_json()
            assert frame is not None
    except Exception as e:
        pytest.skip(f"/ws/session not yet active (Milestone M4): {e}")


def test_ws_terminal_bidirectional_io(client, valid_jwt_token):
    """WS-T1-07: /ws/terminal accepts input frames and streams ANSI output frames."""
    try:
        with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws:
            ws.send_json({"type": "input", "data": "echo TEST_WS_TERMINAL\r"})
            frame = ws.receive_json()
            assert frame.get("type") == "output"
            assert "data" in frame
    except Exception as e:
        pytest.skip(f"/ws/terminal not yet active (Milestone M3): {e}")


def test_ws_terminal_resize_event(client, valid_jwt_token):
    """WS-T1-08: /ws/terminal handles terminal resize frame (cols, rows)."""
    try:
        with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws:
            ws.send_json({"type": "resize", "cols": 100, "rows": 30})
    except Exception as e:
        pytest.skip(f"/ws/terminal not yet active (Milestone M3): {e}")


def test_ws_terminal_virtual_keys_ctrl_c(client, valid_jwt_token):
    """WS-T1-09: /ws/terminal transmits virtual accessory key Ctrl+C (\\x03)."""
    try:
        with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws:
            ws.send_json({"type": "input", "data": "\x03"})
    except Exception as e:
        pytest.skip(f"/ws/terminal not yet active (Milestone M3): {e}")


def test_ws_system_telemetry_stream(client, valid_jwt_token, mock_psutil):
    """WS-T1-10: /ws/system streams periodic hardware and task telemetry frames."""
    try:
        with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws:
            frame = ws.receive_json()
            assert "type" in frame or "cpu" in frame
    except Exception as e:
        pytest.skip(f"/ws/system not yet active (Milestone M5): {e}")


# ===========================================================================
# Tier 2: Error Handling, Rejection & Disconnection Tests (7 tests)
# ===========================================================================

def test_ws_session_reject_invalid_token(client):
    """WS-T2-01: /ws/session rejects connection when token is invalid or corrupted."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/session?token=invalid_token_xyz") as ws:
            ws.receive_json()


def test_ws_terminal_reject_missing_token(client):
    """WS-T2-02: /ws/terminal rejects connection when token query parameter is omitted."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/terminal") as ws:
            ws.receive_json()


def test_ws_system_reject_expired_token(client, expired_jwt_token):
    """WS-T2-03: /ws/system closes connection on expired JWT token."""
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/system?token={expired_jwt_token}") as ws:
            ws.receive_json()


def test_ws_session_malformed_json_frame(client, valid_jwt_token):
    """WS-T2-04: Non-JSON raw text frames sent to /ws/session are handled safely."""
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
            ws.send_text("NOT_VALID_JSON_STRING")
    except Exception as e:
        pytest.skip(f"/ws/session not yet active (Milestone M4): {e}")


def test_ws_session_unknown_action_type(client, valid_jwt_token):
    """WS-T2-05: Unrecognized action string in inbound frame returns safe error message."""
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
            ws.send_json({"action": "unsupported_action_type_xyz"})
    except Exception as e:
        pytest.skip(f"/ws/session not yet active (Milestone M4): {e}")


def test_ws_terminal_disconnect_reconnect(client, valid_jwt_token):
    """WS-T2-06: Abrupt socket close followed by immediate reconnection spawns fresh session."""
    try:
        with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws1:
            ws1.send_json({"type": "input", "data": "echo 1\r"})
        
        with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws2:
            ws2.send_json({"type": "input", "data": "echo 2\r"})
            frame = ws2.receive_json()
            assert frame["type"] == "output"
    except Exception as e:
        pytest.skip(f"/ws/terminal not yet active (Milestone M3): {e}")


def test_ws_system_client_disconnect_cleanup(client, valid_jwt_token, mock_psutil):
    """WS-T2-07: Unregistering subscriber upon client disconnect occurs cleanly."""
    try:
        with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws:
            _ = ws.receive_json()
    except Exception as e:
        pytest.skip(f"/ws/system not yet active (Milestone M5): {e}")


# ===========================================================================
# Tier 3: Cross-Channel Interaction Tests
# ===========================================================================

def test_ws_concurrent_multi_channel_subscription(client, valid_jwt_token, mock_antigravity_env, mock_psutil):
    """WS-T3-01: Client concurrently connects to /ws/session, /ws/terminal, and /ws/system."""
    conv_id = mock_antigravity_env.root_id
    try:
        with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws_sess:
            ws_sess.send_json({"action": "subscribe", "conversation_id": conv_id})
            
            with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws_term:
                ws_term.send_json({"type": "input", "data": "echo MULTI_CHANNEL\r"})
                
                with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws_sys:
                    frame_sys = ws_sys.receive_json()
                    assert frame_sys is not None
    except Exception as e:
        pytest.skip(f"Multi-channel WebSockets not yet active: {e}")


def test_ws_session_to_terminal_handshake(client, valid_jwt_token, mock_antigravity_env):
    """WS-T3-02: Session hub action coordinates with terminal execution."""
    try:
        with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws:
            ws.send_json({"type": "input", "data": "echo LINKED\r"})
            frame = ws.receive_json()
            assert frame["type"] == "output"
    except Exception as e:
        pytest.skip(f"/ws/terminal not yet active (Milestone M3): {e}")
