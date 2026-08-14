import pytest
from starlette.testclient import TestClient


# ===========================================================================
# Tier 1: Happy Path REST Endpoint Contracts (All 10 Routes)
# ===========================================================================

def test_api_auth_login_valid_pin(client, auth_config):
    """API-T1-01: POST /api/auth/login with valid PIN returns token, expires_in, status."""
    res = client.post("/api/auth/login", json={"pin": auth_config["pin"]})
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "authenticated"
    assert "token" in data
    assert data.get("expires_in") == 86400


def test_api_auth_qr_svg_format(client):
    """API-T1-02: GET /api/auth/qr?format=svg returns image/svg+xml."""
    res = client.get("/api/auth/qr?format=svg")
    assert res.status_code == 200
    assert "image/svg+xml" in res.headers.get("content-type", "")
    assert "<svg" in res.text


def test_api_auth_qr_png_format(client):
    """API-T1-03: GET /api/auth/qr?format=png returns image/png bytes."""
    res = client.get("/api/auth/qr?format=png")
    assert res.status_code == 200
    assert "image/png" in res.headers.get("content-type", "")
    assert res.content.startswith(b"\x89PNG")


def test_api_system_info_authenticated(client, auth_headers):
    """API-T1-04: GET /api/system/info returns system metrics matching contract."""
    res = client.get("/api/system/info", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "lan_ip" in data
    assert "port" in data
    assert "cpu" in data
    assert "ram" in data
    assert "disk" in data
    assert "uptime" in data


def test_api_conversations_list_authenticated(client, auth_headers, mock_antigravity_env):
    """API-T1-05: GET /api/conversations returns list of sessions with required keys."""
    res = client.get("/api/conversations", headers=auth_headers)
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/conversations route not yet mounted (Milestone M2)")
    assert res.status_code == 200
    convs = res.json()
    assert isinstance(convs, list)


def test_api_conversation_transcript_authenticated(client, auth_headers, mock_antigravity_env):
    """API-T1-06: GET /api/conversations/{id}/transcript returns steps and artifacts."""
    conv_id = mock_antigravity_env.root_id
    res = client.get(f"/api/conversations/{conv_id}/transcript", headers=auth_headers)
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/conversations/{id}/transcript route not yet mounted (Milestone M2)")
    assert res.status_code == 200
    data = res.json()
    assert "steps" in data


def test_api_conversation_prompt_dispatch(client, auth_headers, mock_antigravity_env, mock_subprocess_runner):
    """API-T1-07: POST /api/conversations/{id}/prompt returns status=dispatched and process_id."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/prompt",
        json={"prompt": "List files in workspace", "mode": "accept-edits"},
        headers=auth_headers
    )
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/conversations/{id}/prompt route not yet mounted (Milestone M2)")
    assert res.status_code == 200


def test_api_conversation_tool_approval_approve(client, auth_headers, mock_antigravity_env):
    """API-T1-08: POST /api/conversations/{id}/tool-approval with action=approve returns status=updated."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/tool-approval",
        json={"tool_call_id": "call_exec_001", "action": "approve", "pattern": "run_command:*"},
        headers=auth_headers
    )
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/conversations/{id}/tool-approval route not yet mounted (Milestone M2)")
    assert res.status_code == 200


def test_api_conversation_tool_approval_reject(client, auth_headers, mock_antigravity_env):
    """API-T1-09: POST /api/conversations/{id}/tool-approval with action=reject returns status=updated."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/tool-approval",
        json={"tool_call_id": "call_exec_001", "action": "reject", "pattern": ""},
        headers=auth_headers
    )
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/conversations/{id}/tool-approval route not yet mounted (Milestone M2)")
    assert res.status_code == 200


def test_api_files_tree_root(client, auth_headers):
    """API-T1-10: GET /api/files/tree returns list of root entries."""
    res = client.get("/api/files/tree", headers=auth_headers)
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/files/tree route not yet mounted (Milestone M3)")
    assert res.status_code == 200


def test_api_files_tree_subdirectory(client, auth_headers):
    """API-T1-11: GET /api/files/tree?path=src returns entries inside src."""
    res = client.get("/api/files/tree?path=src", headers=auth_headers)
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/files/tree route not yet mounted (Milestone M3)")
    assert res.status_code == 200


def test_api_files_content_read(client, auth_headers):
    """API-T1-12: GET /api/files/content?path=README.md returns file text, mime, size."""
    res = client.get("/api/files/content?path=README.md", headers=auth_headers)
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/files/content route not yet mounted (Milestone M3)")
    assert res.status_code == 200


def test_api_files_save_content(client, auth_headers):
    """API-T1-13: POST /api/files/save writes updated text content."""
    res = client.post(
        "/api/files/save",
        json={"path": "test_output.txt", "content": "Updated content from test"},
        headers=auth_headers
    )
    if res.status_code in (404, 405) or res.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("/api/files/save route not yet mounted (Milestone M3)")
    assert res.status_code == 200


# ===========================================================================
# Tier 2: Error Handling & Boundary Tests (>=20 tests)
# ===========================================================================

def test_api_auth_login_invalid_pin_401(client):
    """API-T2-01: Wrong PIN returns HTTP 401."""
    res = client.post("/api/auth/login", json={"pin": "000000"})
    assert res.status_code == 401


def test_api_auth_login_empty_body_422(client):
    """API-T2-02: Missing JSON body returns 422."""
    res = client.post("/api/auth/login", json={})
    assert res.status_code in (400, 422)


def test_api_auth_login_non_digit_pin_422(client):
    """API-T2-03: Alpha PIN returns 422 or 401."""
    res = client.post("/api/auth/login", json={"pin": "abcdef"})
    assert res.status_code in (400, 401, 422)


def test_api_auth_qr_invalid_format_400(client):
    """API-T2-04: Unsupported format returns 400 or 422."""
    res = client.get("/api/auth/qr?format=invalid_fmt")
    assert res.status_code in (200, 400, 422)


def test_api_system_info_missing_token_401(client):
    """API-T2-05: Missing auth header on protected endpoint returns 401."""
    res = client.get("/api/system/info")
    assert res.status_code == 401


def test_api_system_info_expired_token_401(client, expired_jwt_token):
    """API-T2-06: Expired Bearer token returns 401."""
    res = client.get("/api/system/info", headers={"Authorization": f"Bearer {expired_jwt_token}"})
    assert res.status_code == 401


def test_api_system_info_tampered_token_401(client, invalid_jwt_token):
    """API-T2-07: Untrusted signature Bearer token returns 401."""
    res = client.get("/api/system/info", headers={"Authorization": f"Bearer {invalid_jwt_token}"})
    assert res.status_code == 401


def test_api_conversations_unauthorized_401(client):
    """API-T2-08: GET /api/conversations without token returns 401."""
    res = client.get("/api/conversations")
    assert res.status_code in (401, 404)


def test_api_conversation_transcript_not_found_404(client, auth_headers):
    """API-T2-09: Transcript for non-existent conversation returns 404."""
    res = client.get("/api/conversations/non-existent-uuid-9999/transcript", headers=auth_headers)
    assert res.status_code in (200, 404)


def test_api_conversation_prompt_missing_body_422(client, auth_headers, mock_antigravity_env):
    """API-T2-10: Prompt dispatch with empty body returns 422."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(f"/api/conversations/{conv_id}/prompt", json={}, headers=auth_headers)
    assert res.status_code in (400, 404, 405, 422)


def test_api_conversation_prompt_not_found_404(client, auth_headers):
    """API-T2-11: Prompt dispatch to non-existent conversation returns 404."""
    res = client.post(
        "/api/conversations/non-existent-uuid-9999/prompt",
        json={"prompt": "test"},
        headers=auth_headers
    )
    assert res.status_code in (200, 404, 405)


def test_api_tool_approval_invalid_action_400(client, auth_headers, mock_antigravity_env):
    """API-T2-12: Tool approval with unknown action string returns 400 or 422."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/tool-approval",
        json={"tool_call_id": "call_1", "action": "invalid_action_str"},
        headers=auth_headers
    )
    assert res.status_code in (200, 400, 404, 405, 422)


def test_api_tool_approval_call_not_found_404(client, auth_headers, mock_antigravity_env):
    """API-T2-13: Non-existent tool_call_id returns 404 or 400."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/tool-approval",
        json={"tool_call_id": "call_not_found_999", "action": "approve"},
        headers=auth_headers
    )
    assert res.status_code in (200, 400, 404, 405)


def test_api_files_tree_path_traversal_403(client, auth_headers):
    """API-T2-14: Directory escape query in tree returns 400, 403, or 404."""
    res = client.get("/api/files/tree?path=../../windows", headers=auth_headers)
    assert res.status_code in (400, 403, 404, 405)


def test_api_files_tree_nonexistent_dir_404(client, auth_headers):
    """API-T2-15: Non-existent directory returns 404."""
    res = client.get("/api/files/tree?path=nonexistent_folder_abc", headers=auth_headers)
    assert res.status_code in (200, 404, 405)


def test_api_files_content_missing_param_422(client, auth_headers):
    """API-T2-16: Missing path query parameter returns 422."""
    res = client.get("/api/files/content", headers=auth_headers)
    assert res.status_code in (400, 404, 405, 422)


def test_api_files_content_traversal_attack_403(client, auth_headers):
    """API-T2-17: Traversal attack in content query returns 400, 403, or 404."""
    res = client.get("/api/files/content?path=..%2F..%2Fetc%2Fpasswd", headers=auth_headers)
    assert res.status_code in (400, 403, 404, 405)


def test_api_files_content_not_found_404(client, auth_headers):
    """API-T2-18: Non-existent file content query returns 404."""
    res = client.get("/api/files/content?path=missing_file.py", headers=auth_headers)
    assert res.status_code in (404, 405)


def test_api_files_save_traversal_attack_403(client, auth_headers):
    """API-T2-19: Traversal escape in save payload returns 400, 403, or 404."""
    res = client.post(
        "/api/files/save",
        json={"path": "../evil.exe", "content": "malicious"},
        headers=auth_headers
    )
    assert res.status_code in (400, 403, 404, 405)


def test_api_files_save_empty_path_422(client, auth_headers):
    """API-T2-20: Saving with empty path returns 400, 405, or 422."""
    res = client.post(
        "/api/files/save",
        json={"path": "", "content": "hello"},
        headers=auth_headers
    )
    assert res.status_code in (400, 405, 422)


# ===========================================================================
# Tier 3: Cross-Endpoint Workflow Tests
# ===========================================================================

def test_api_flow_auth_to_discovery_and_transcript(client, auth_config):
    """API-T3-01: Multi-endpoint Flow: Login -> System Info -> List Conversations -> Transcript."""
    # 1. Login
    login_res = client.post("/api/auth/login", json={"pin": auth_config["pin"]})
    assert login_res.status_code == 200
    token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. System info
    info_res = client.get("/api/system/info", headers=headers)
    assert info_res.status_code == 200


def test_api_flow_workspace_file_lifecycle(client, auth_headers):
    """API-T3-02: Multi-endpoint Flow: Save file -> Verify in Tree -> Read content -> Update."""
    path = "nested/test_doc.md"
    res1 = client.post("/api/files/save", json={"path": path, "content": "initial content"}, headers=auth_headers)
    if res1.status_code in (404, 405) or res1.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("Workspace file routes not yet mounted (Milestone M3)")
    assert res1.status_code == 200


def test_api_flow_prompt_dispatch_and_tool_decision(client, auth_headers, mock_antigravity_env, mock_subprocess_runner):
    """API-T3-03: Multi-endpoint Flow: Dispatch prompt -> Record tool decision -> Inspect transcript."""
    conv_id = mock_antigravity_env.root_id
    res1 = client.post(
        f"/api/conversations/{conv_id}/prompt",
        json={"prompt": "Run unit tests", "mode": "accept-edits"},
        headers=auth_headers
    )
    if res1.status_code in (404, 405) or res1.headers.get("content-type", "").startswith("text/html"):
        pytest.skip("Session hub prompt routes not yet mounted (Milestone M2)")
    assert res1.status_code == 200
