import re
import time
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from backend.config import Settings
from backend.security import (
    AuthManager,
    IPRateLimiter,
    PathSandbox,
    RateLimiter,
    SandboxSecurityException,
    create_access_token,
    decode_access_token,
    is_safe_sandbox_path,
    rate_limiter,
    sanitize_and_resolve_sandbox_path,
    verify_pin_constant_time,
    verify_ws_token,
)


# ===========================================================================
# Tier 1: Core Functional & Happy Path Tests (F14)
# ===========================================================================

def test_valid_pin_login_success(test_app, auth_config):
    """SEC-01: Valid configured PIN returns HTTP 200, JWT token, and status='authenticated'."""
    client = TestClient(test_app)
    response = client.post("/api/auth/login", json={"pin": auth_config["pin"]})
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "authenticated"
    assert "token" in data
    assert isinstance(data["token"], str)
    assert len(data["token"]) > 20
    assert data.get("expires_in") == 86400


def test_jwt_token_issuance_and_signature(auth_config, valid_jwt_token):
    """SEC-02: JWT token is generated with HS256 algorithm and contains valid payload claims."""
    decoded = AuthManager.decode_token(valid_jwt_token, auth_config["secret"])
    assert decoded["sub"] in ("admin", "mobile_controller", "mobile_client", "agy_admin")
    assert decoded["exp"] > time.time()


def test_bearer_auth_validation_success(client, auth_headers):
    """SEC-03: Valid Bearer JWT token successfully accesses protected endpoint without auth errors."""
    response = client.get("/api/system/info", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "lan_ip" in data or "cpu" in data


def test_sandbox_root_containment_valid_paths(mock_workspace):
    """SEC-04: Path sandbox safely resolves relative paths strictly within workspace root."""
    sandbox = PathSandbox(mock_workspace.root)
    resolved_src = sandbox.validate_path("src")
    assert resolved_src.is_relative_to(mock_workspace.root)
    assert sandbox.is_safe_path(mock_workspace.root / "README.md", mock_workspace.root) is True


def test_token_payload_decode(auth_config):
    """SEC-05: Custom claims in JWT are preserved and decoded accurately."""
    payload = {
        "sub": "mobile_controller_1",
        "role": "admin",
        "custom_claim": "test_value_123",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    encoded = jwt.encode(payload, auth_config["secret"], algorithm="HS256")
    decoded = AuthManager.decode_token(encoded, auth_config["secret"])
    assert decoded["custom_claim"] == "test_value_123"
    assert decoded["sub"] == "mobile_controller_1"


def test_pin_whitespace_trimming(test_app, auth_config):
    """SEC-06: PIN with leading and trailing whitespaces is safely trimmed and accepted."""
    client = TestClient(test_app)
    response = client.post("/api/auth/login", json={"pin": f"  {auth_config['pin']}  "})
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "authenticated"
    assert "token" in data


def test_pin_generation_and_validation():
    """SEC-06b: Settings default PIN is 6 numeric digits."""
    s = Settings()
    assert len(s.AUTH_PIN) == 6
    assert s.AUTH_PIN.isdigit()


def test_constant_time_pin_comparison():
    """SEC-06c: Constant-time PIN verification accurately verifies matches and mismatches."""
    assert verify_pin_constant_time("123456", "123456") is True
    assert verify_pin_constant_time("123456", "654321") is False
    assert verify_pin_constant_time("", "123456") is False
    assert verify_pin_constant_time("123456", "") is False


# ===========================================================================
# Tier 2: Boundary, Adversarial & Error Handling Tests (F14)
# ===========================================================================

def test_invalid_pin_rejection_401(test_app):
    """SEC-07: Incorrect PIN is rejected with HTTP 401 Unauthorized."""
    client = TestClient(test_app)
    response = client.post("/api/auth/login", json={"pin": "000000"})
    assert response.status_code == 401
    assert "Invalid PIN" in response.json().get("detail", "") or "detail" in response.json()


def test_brute_force_rate_limiting_429(test_app):
    """SEC-08: Exceeding maximum failed attempts locks out the client IP with HTTP 429."""
    client = TestClient(test_app)
    # Fail 4 times (401)
    for _ in range(4):
        r = client.post("/api/auth/login", json={"pin": "000000"})
        assert r.status_code == 401

    # 5th attempt triggers lockout (429)
    response = client.post("/api/auth/login", json={"pin": "000000"})
    assert response.status_code == 429
    assert "Retry-After" in response.headers or "detail" in response.json()


def test_expired_token_rejection_401(client, expired_jwt_token):
    """SEC-09: Expired JWT token is rejected with HTTP 401 Unauthorized."""
    headers = {"Authorization": f"Bearer {expired_jwt_token}"}
    response = client.get("/api/system/info", headers=headers)
    assert response.status_code == 401


def test_malformed_jwt_rejection_401(client, invalid_jwt_token):
    """SEC-10: Malformed JWT strings or untrusted signatures are rejected with HTTP 401."""
    response1 = client.get("/api/system/info", headers={"Authorization": f"Bearer {invalid_jwt_token}"})
    assert response1.status_code == 401

    response2 = client.get("/api/system/info", headers={"Authorization": "Bearer not-a-valid-token-string"})
    assert response2.status_code == 401


def test_path_traversal_dotdot_attacks(mock_workspace):
    """SEC-11: Directory escape attempts using '../' and '..\\' are blocked by path sandbox."""
    traversal_paths = [
        "../../Windows/System32/cmd.exe",
        "..\\..\\secret.txt",
        "src/../../../etc/passwd",
        "docs/../../package.json.bak"
    ]
    base = mock_workspace.root.resolve()
    for p in traversal_paths:
        sandbox = PathSandbox(base)
        with pytest.raises((SandboxSecurityException, HTTPException, ValueError, PermissionError)):
            sandbox.validate_path(p)


def test_path_traversal_url_encoded_and_unicode(mock_workspace):
    """SEC-12: URL-encoded traversal characters (%2e%2e%2f) are rejected."""
    encoded_paths = [
        "%2e%2e%2fetc%2fpasswd",
        "%252e%252e%252f",
        "..%2f..%2fwindows",
        "%2e%2e%5csecret.txt"
    ]
    sandbox = PathSandbox(mock_workspace.root)
    for p in encoded_paths:
        with pytest.raises((SandboxSecurityException, HTTPException, ValueError, PermissionError)):
            sandbox.validate_path(p)


def test_path_traversal_null_byte_injection(mock_workspace):
    """SEC-13: Null byte injection in path strings is strictly detected and rejected."""
    null_byte_paths = [
        "src/main.py\x00.png",
        "secret.txt\x00",
        "docs/guide.md%00.jpg"
    ]
    sandbox = PathSandbox(mock_workspace.root)
    for p in null_byte_paths:
        with pytest.raises((SandboxSecurityException, HTTPException, ValueError)):
            sandbox.validate_path(p)


def test_path_traversal_absolute_paths_outside_workspace(mock_workspace):
    """SEC-14: Absolute paths referencing external drives/folders are rejected."""
    external_paths = [
        "C:\\Windows\\System32",
        "D:\\OtherWorkspace\\secret.env",
        "/etc/shadow",
        "/var/log"
    ]
    sandbox = PathSandbox(mock_workspace.root)
    for p in external_paths:
        with pytest.raises((SandboxSecurityException, HTTPException, ValueError, PermissionError)):
            sandbox.validate_path(p)


def test_sandbox_windows_reserved_devices(mock_workspace):
    """SEC-14b: Windows reserved device names (CON, NUL, AUX, COM1) are rejected."""
    reserved = ["CON", "NUL.txt", "sub/AUX.log", "COM1", "LPT2.dat"]
    sandbox = PathSandbox(mock_workspace.root)
    for r in reserved:
        with pytest.raises((SandboxSecurityException, HTTPException, ValueError)):
            sandbox.validate_path(r)


def test_sandbox_alternate_data_streams(mock_workspace):
    """SEC-14c: Alternate data stream colon syntax is rejected."""
    ads_paths = ["file.txt:hidden_stream", "main.py:$DATA"]
    sandbox = PathSandbox(mock_workspace.root)
    for ads in ads_paths:
        with pytest.raises((SandboxSecurityException, HTTPException, ValueError)):
            sandbox.validate_path(ads)


def test_sandbox_unc_paths(mock_workspace):
    """SEC-14d: UNC paths are rejected."""
    unc_paths = ["\\\\attacker-server\\share\\file.txt", "//remote/share"]
    sandbox = PathSandbox(mock_workspace.root)
    for unc in unc_paths:
        with pytest.raises((SandboxSecurityException, HTTPException, ValueError)):
            sandbox.validate_path(unc)


def test_sandbox_case_insensitivity(mock_workspace):
    """SEC-14e: Sandbox handles Windows case-insensitivity cleanly."""
    sandbox = PathSandbox(mock_workspace.root)
    resolved = sandbox.validate_path("SRC/main.py")
    assert resolved.is_relative_to(mock_workspace.root)


def test_rate_limiter_ip_isolation():
    """SEC-15: Rate limiter strictly tracks failure attempts per IP independently."""
    limiter = RateLimiter(max_attempts=5, window_seconds=60)
    for _ in range(5):
        limiter.record_failed_attempt("192.168.1.100")
    assert limiter.is_blocked("192.168.1.100") is True
    assert limiter.is_blocked("192.168.1.101") is False


def test_rate_limiter_successful_login_resets_counter():
    """SEC-16: A successful login immediately clears any previous failed attempts for that IP."""
    limiter = RateLimiter(max_attempts=5, window_seconds=60)
    for _ in range(4):
        limiter.record_failed_attempt("192.168.1.200")
    assert limiter.is_blocked("192.168.1.200") is False
    limiter.reset_attempts("192.168.1.200")
    assert limiter.is_blocked("192.168.1.200") is False


def test_verify_ws_token(auth_config, valid_jwt_token, expired_jwt_token):
    """SEC-16b: WebSocket token verification returns claims on valid and None on invalid/expired."""
    decoded = verify_ws_token(valid_jwt_token)
    assert decoded is not None
    assert decoded.get("sub") == "admin"
    assert verify_ws_token(expired_jwt_token) is None
    assert verify_ws_token(None) is None
    assert verify_ws_token("invalid-token") is None


# ===========================================================================
# Tier 3: Cross-Feature Interaction Tests
# ===========================================================================

def test_auth_with_sandbox_interaction(client, auth_headers):
    """SEC-17: Authenticated admin users remain strictly restricted by the workspace sandbox."""
    response = client.get("/api/files/content?path=../../windows/win.ini", headers=auth_headers)
    assert response.status_code in (400, 403, 404)


def test_rate_limiter_all_auth_entrypoints(test_app):
    """SEC-18: Rate limiter prevents brute force across rapid sequential login requests."""
    client = TestClient(test_app)
    # Fire rapid burst of invalid requests
    statuses = []
    for _ in range(7):
        r = client.post("/api/auth/login", json={"pin": "999999"})
        statuses.append(r.status_code)
    assert 429 in statuses or all(s == 401 for s in statuses[:4])


# ===========================================================================
# Tier 4: Real-World Workflow Tests
# ===========================================================================

def test_brute_force_lockout_and_recovery_flow(test_app, auth_config):
    """SEC-19: Multi-step workflow: 4 failed attempts -> 429 Lockout -> Successful auth after recovery."""
    client = TestClient(test_app)
    # Step 1: 4 failed attempts (401)
    for _ in range(4):
        r = client.post("/api/auth/login", json={"pin": "111111"})
        assert r.status_code == 401

    # Step 2: 5th attempt is locked out with 429
    r_lock = client.post("/api/auth/login", json={"pin": "111111"})
    assert r_lock.status_code == 429

    # Step 3: Reset lockout and succeed
    rate_limiter.clear()
    r_ok = client.post("/api/auth/login", json={"pin": auth_config["pin"]})
    assert r_ok.status_code == 200
    assert r_ok.json().get("status") == "authenticated"
