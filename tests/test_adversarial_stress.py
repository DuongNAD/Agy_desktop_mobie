"""
Adversarial Penetration and Stress Testing Suite for Milestone 1 (M1)
Covers:
- High-concurrency race condition stress testing on IPRateLimiter and FastAPI endpoints
- Empirical timing attack analysis on constant-time PIN comparison
- Exhaustive Windows path traversal payloads (UNC, Device names, NTFS ADS, Junction/Symlink escapes, Encodings, Null bytes)
- JWT forgery, none-algorithm, signature tampering, and cryptographic bypass attempts
"""

import asyncio
import base64
import concurrent.futures
import json
import os
import secrets
import statistics
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import List, Tuple

import httpx
import jwt
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
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

try:
    import _winapi
except ImportError:
    _winapi = None


# ===========================================================================
# 1. HIGH-CONCURRENCY RACE CONDITION STRESS TESTS (RateLimiter)
# ===========================================================================

def test_concurrent_rate_limiter_brute_force_exact_lockout():
    """
    Stress-test IPRateLimiter under 100 concurrent worker threads attempting brute force simultaneously.
    Verify that thread locks prevent race conditions, and exactly max_attempts failures are allowed
    before all subsequent requests are blocked.
    """
    max_attempts = 5
    limiter = IPRateLimiter(max_attempts=max_attempts, window_seconds=60, lockout_seconds=300)
    target_ip = "192.168.1.99"

    num_threads = 100
    results: List[Tuple[bool, int]] = []
    results_lock = threading.Lock()

    def worker_attempt():
        locked, wait_time = limiter.record_failure(target_ip)
        with results_lock:
            results.append((locked, wait_time))

    threads = [threading.Thread(target=worker_attempt) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == num_threads
    is_locked, remaining = limiter.is_locked(target_ip)
    assert is_locked is True
    assert remaining > 0

    unlocked_count = sum(1 for locked, _ in results if not locked)
    locked_count = sum(1 for locked, _ in results if locked)

    assert unlocked_count == max_attempts - 1, (
        f"Expected exactly {max_attempts - 1} unlocked attempts under high concurrency, "
        f"got {unlocked_count} unlocked and {locked_count} locked."
    )
    assert locked_count == num_threads - (max_attempts - 1)


def test_concurrent_multi_ip_rate_limiting_stress():
    """
    Stress-test IPRateLimiter with 50 distinct IPs generating 10 concurrent requests each (500 total).
    Verify that per-IP isolation holds perfectly under thread contention.
    """
    limiter = IPRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=300)
    num_ips = 50
    requests_per_ip = 10
    ip_list = [f"10.0.0.{i}" for i in range(1, num_ips + 1)]

    def worker(ip: str):
        for _ in range(requests_per_ip):
            limiter.record_failure(ip)

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(worker, ip) for ip in ip_list]
        concurrent.futures.wait(futures)

    for ip in ip_list:
        locked, rem = limiter.is_locked(ip)
        assert locked is True, f"IP {ip} should be locked out but was not"
        assert rem > 0


def test_concurrent_rate_limiter_cleanup_during_active_traffic():
    """
    Stress-test memory cleanup running concurrently with active traffic recording and querying.
    Ensures no deadlock or dict mutation during iteration exceptions occur.
    """
    limiter = IPRateLimiter(max_attempts=5, window_seconds=1, lockout_seconds=1)
    stop_signal = threading.Event()
    exceptions = []

    def traffic_writer():
        ip_idx = 0
        while not stop_signal.is_set():
            try:
                ip = f"172.16.0.{ip_idx % 250}"
                limiter.record_failure(ip)
                ip_idx += 1
                time.sleep(0.001)
            except Exception as e:
                exceptions.append(e)

    def traffic_reader():
        ip_idx = 0
        while not stop_signal.is_set():
            try:
                ip = f"172.16.0.{ip_idx % 250}"
                limiter.is_locked(ip)
                ip_idx += 1
                time.sleep(0.001)
            except Exception as e:
                exceptions.append(e)

    def janitor():
        while not stop_signal.is_set():
            try:
                limiter.cleanup()
                time.sleep(0.005)
            except Exception as e:
                exceptions.append(e)

    writers = [threading.Thread(target=traffic_writer) for _ in range(5)]
    readers = [threading.Thread(target=traffic_reader) for _ in range(5)]
    cleaners = [threading.Thread(target=janitor) for _ in range(2)]

    all_threads = writers + readers + cleaners
    for t in all_threads:
        t.start()

    time.sleep(0.5)
    stop_signal.set()

    for t in all_threads:
        t.join()

    assert len(exceptions) == 0, f"Exceptions raised during concurrent operations: {exceptions}"


@pytest.mark.asyncio
async def test_concurrent_async_api_brute_force_lockout():
    """
    Fire 50 concurrent async HTTP requests to /api/auth/login from the same IP using AsyncClient.
    Verify that 429 lockout happens deterministically.
    """
    app = create_app()
    limiter = rate_limiter
    limiter.clear()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        tasks = [
            ac.post("/api/auth/login", json={"pin": f"00000{i}"}, headers={"X-Forwarded-For": "198.51.100.55"})
            for i in range(50)
        ]
        responses = await asyncio.gather(*tasks)

    status_codes = [r.status_code for r in responses]
    status_401_count = status_codes.count(401)
    status_429_count = status_codes.count(429)

    assert status_401_count <= 5
    assert status_429_count >= 45
    assert status_401_count + status_429_count == 50


# ===========================================================================
# 2. TIMING ATTACK & SIDE-CHANNEL PIN VERIFICATION TESTS
# ===========================================================================

def test_pin_verification_timing_attack_resistance():
    """
    Simulate a timing attack on PIN verification.
    Measure 25,000 iterations comparing:
    - Target: '584920'
    - Candidate 1: '584929' (5/6 characters matching prefix)
    - Candidate 2: '000000' (0/6 characters matching prefix)
    - Candidate 3: '584920' (exact match)
    Verify that mean verification times are within nanosecond noise margins and exhibit no prefix divergence.
    """
    configured_pin = "584920"
    candidate_match5 = "584929"
    candidate_match0 = "000000"
    candidate_exact = "584920"

    iterations = 25000

    # Warmup
    for _ in range(2000):
        verify_pin_constant_time(candidate_match5, configured_pin)
        verify_pin_constant_time(candidate_match0, configured_pin)
        verify_pin_constant_time(candidate_exact, configured_pin)

    # Timing Candidate 1 (5 chars match)
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        verify_pin_constant_time(candidate_match5, configured_pin)
    dur_match5 = (time.perf_counter_ns() - t0) / iterations

    # Timing Candidate 2 (0 chars match)
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        verify_pin_constant_time(candidate_match0, configured_pin)
    dur_match0 = (time.perf_counter_ns() - t0) / iterations

    # Timing Candidate 3 (Exact match)
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        verify_pin_constant_time(candidate_exact, configured_pin)
    dur_exact = (time.perf_counter_ns() - t0) / iterations

    diff_ratio = abs(dur_match5 - dur_match0) / max(dur_match5, dur_match0)
    print(f"\n[Timing Benchmark] match5: {dur_match5:.2f}ns, match0: {dur_match0:.2f}ns, exact: {dur_exact:.2f}ns, diff_ratio: {diff_ratio:.4f}")
    assert diff_ratio < 0.35, f"Timing difference between 5-char prefix and 0-char prefix is too large: ratio {diff_ratio}"


def test_pin_verification_malformed_and_adversarial_types():
    """
    Test PIN verification with adversarial input types and payloads.
    """
    pin = "123456"
    assert verify_pin_constant_time(None, pin) is False
    assert verify_pin_constant_time(pin, None) is False
    assert verify_pin_constant_time("", pin) is False
    assert verify_pin_constant_time("   ", pin) is False
    assert verify_pin_constant_time("\x00123456", pin) is False
    assert verify_pin_constant_time("123456\x00", pin) is False
    assert verify_pin_constant_time("123456" * 1000, pin) is False
    assert verify_pin_constant_time("123456\r\n", pin) is True
    assert verify_pin_constant_time(" 123456 ", pin) is True
    assert verify_pin_constant_time("123 456", pin) is False


# ===========================================================================
# 3. SOPHISTICATED WINDOWS PATH TRAVERSAL PENETRATION TESTS
# ===========================================================================

ADVERSARIAL_WINDOWS_PATH_PAYLOADS = [
    # Classic dot-dot
    r"..\..\Windows\System32",
    r"..\..\Windows\System32\cmd.exe",
    r"..\..\..\..\..\..\..\..\Windows\win.ini",
    r"sub/../../../../../../Windows/System32",
    "E:/Project/../../Windows",
    "E:/Project/../../Windows/System32/config/SAM",
    r"E:\Project\..\..\Windows",
    # Extended-length device prefix & local device paths
    r"\\?\C:\Windows",
    r"\\?\C:\Windows\System32\calc.exe",
    r"\\.\C:\Windows",
    r"\??\C:\Windows",
    r"\\?\UNC\127.0.0.1\c$\Windows",
    r"\\localhost\c$\Windows",
    r"\\127.0.0.1\c$\secret",
    # NTFS Alternate Data Streams (ADS)
    r"E:\Project\test.txt:hidden",
    r"test.txt:hidden_stream",
    r"test.txt:$DATA",
    r"test.txt:secret:$DATA",
    r"folder:stream",
    r"file.txt::$INDEX_ALLOCATION",
    r"COM1:",
    r"NUL:",
    r"AUX:",
    r"PRN:",
    # Windows Reserved Device Names (with and without extensions)
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM9",
    "LPT1",
    "LPT9",
    "aux.txt",
    "AUX.JSON",
    "nul.exe",
    "com1.dat",
    "lpt1.log",
    "con.doc",
    "prn.pdf",
    "sub/nested/aux.txt",
    r"sub\nested\COM1.dat",
    # URL-encoded variants
    "..%2f..%2f",
    "..%5c..%5c",
    "%2e%2e%2f%2e%2e%2f",
    "%2e%2e%5c%2e%2e%5c",
    "%252e%252e%252f",
    "%252e%252e%255c",
    "..%252f..%252f",
    "%2e%2e%2fWindows%2fSystem32",
    # Null-byte injections
    "foo.txt\x00.bar",
    "foo.txt\x00",
    "\x00../../Windows",
    "safe.txt%00.exe",
    "safe.txt%2500.exe",
    # Absolute paths on other drives
    r"C:\Windows",
    r"C:\Users",
    r"D:\SecretData\passwords.txt",
    r"Z:\NetworkShare\backup.bak",
    "/etc/shadow",
    "/var/run/docker.sock",
    "/root/.ssh/id_rsa",
]


@pytest.mark.parametrize("payload", ADVERSARIAL_WINDOWS_PATH_PAYLOADS)
def test_path_sandbox_rejects_adversarial_payloads(mock_workspace, payload):
    """
    Empirically verify that every single adversarial Windows path traversal payload is strictly rejected
    and cannot escape the authorized workspace root directory.
    """
    sandbox = PathSandbox(mock_workspace.root)
    assert sandbox.is_safe_path(payload) is False

    with pytest.raises((SandboxSecurityException, HTTPException, ValueError, PermissionError)):
        sandbox.validate_path(payload)


def test_path_sandbox_ntfs_junction_escape_detection(tmp_path):
    """
    Empirically create an NTFS directory junction inside the sandbox pointing outside,
    and verify that PathSandbox resolves the junction target and blocks the escape!
    """
    if _winapi is None or not hasattr(_winapi, "CreateJunction"):
        pytest.skip("NTFS junction creation (_winapi) not available on this platform")

    sandbox_root = tmp_path / "sandbox_root"
    outside_target = tmp_path / "outside_forbidden"
    sandbox_root.mkdir()
    outside_target.mkdir()

    outside_file = outside_target / "confidential.txt"
    outside_file.write_text("top_secret", encoding="utf-8")

    junction_link = sandbox_root / "junction_escape"
    try:
        _winapi.CreateJunction(str(outside_target), str(junction_link))
    except Exception as e:
        pytest.skip(f"Could not create NTFS junction: {e}")

    sandbox = PathSandbox(sandbox_root)

    # 1. is_safe_path must return False for any path through the junction
    assert sandbox.is_safe_path("junction_escape/confidential.txt") is False

    # 2. validate_path must raise SandboxSecurityException
    with pytest.raises((SandboxSecurityException, HTTPException, ValueError)):
        sandbox.validate_path("junction_escape/confidential.txt")


def test_path_sandbox_case_normalization_and_relative_navigation(tmp_path):
    """
    Test legitimate in-bounds subpaths with mixed casing, safe dot-segments, and whitespace.
    """
    sandbox_root = tmp_path / "workspace"
    sandbox_root.mkdir()
    sub_dir = sandbox_root / "SubFolder" / "Nested"
    sub_dir.mkdir(parents=True)
    test_file = sub_dir / "target.py"
    test_file.write_text("print('hello')", encoding="utf-8")

    sandbox = PathSandbox(sandbox_root)

    p1 = sandbox.validate_path("SubFolder/Nested/target.py")
    assert p1 == test_file.resolve()

    p2 = sandbox.validate_path("subfolder/nested/../nested/target.py")
    assert p2 == test_file.resolve()

    p3 = sandbox.validate_path("./SubFolder/./Nested/./target.py")
    assert p3 == test_file.resolve()


# ===========================================================================
# 4. JWT FORGERY, NONE-ALGORITHM & CRYPTOGRAPHIC BYPASS TESTS
# ===========================================================================

def test_jwt_none_algorithm_attack_rejected(auth_config):
    """
    Attempt JWT 'none' algorithm bypass:
    Create a token with {"alg": "none"} and no signature.
    Verify decode_access_token strictly rejects it.
    """
    payload = {
        "sub": "admin",
        "role": "admin",
        "iss": "agymobile-auth",
        "exp": int(time.time()) + 3600,
    }
    header_b64 = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"  # {"alg":"none","typ":"JWT"}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    forged_token = f"{header_b64}.{payload_b64}."

    with pytest.raises(HTTPException) as exc_info:
        AuthManager.decode_token(forged_token, secret=auth_config["secret"])
    assert exc_info.value.status_code == 401


def test_jwt_signature_tampering_rejected(auth_config, valid_jwt_token):
    """
    Take a valid JWT token, tamper with the signature part, and verify immediate 401 rejection.
    """
    parts = valid_jwt_token.split(".")
    assert len(parts) == 3

    tampered_sig = parts[2][:-4] + "AAAA"
    tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"

    with pytest.raises(HTTPException) as exc_info:
        AuthManager.decode_token(tampered_token, secret=auth_config["secret"])
    assert exc_info.value.status_code == 401


def test_jwt_payload_tampering_rejected(auth_config, valid_jwt_token):
    """
    Take a valid JWT token, modify payload (e.g. change sub), leave original signature.
    Verify signature validation fails with 401.
    """
    parts = valid_jwt_token.split(".")
    decoded_payload = json.loads(base64.urlsafe_b64decode(parts[1] + "==").decode())
    decoded_payload["sub"] = "hacked_admin"
    tampered_payload_b64 = base64.urlsafe_b64encode(json.dumps(decoded_payload).encode()).decode().rstrip("=")
    tampered_token = f"{parts[0]}.{tampered_payload_b64}.{parts[2]}"

    with pytest.raises(HTTPException) as exc_info:
        AuthManager.decode_token(tampered_token, secret=auth_config["secret"])
    assert exc_info.value.status_code == 401


def test_jwt_wrong_secret_key_rejected(auth_config, valid_jwt_token):
    """
    Validate token with a different secret key. Must reject with 401.
    """
    wrong_secret = "wrong-secret-key-that-does-not-match-at-all-12345"
    with pytest.raises(HTTPException) as exc_info:
        AuthManager.decode_token(valid_jwt_token, secret=wrong_secret)
    assert exc_info.value.status_code == 401


def test_jwt_algorithm_confusion_asymmetric_rejection(auth_config):
    """
    Attempt algorithm substitution: token signed with HS512 when expecting HS256.
    """
    payload = {
        "sub": "admin",
        "exp": int(time.time()) + 3600,
        "iss": "agymobile-auth",
    }
    token_hs512 = jwt.encode(payload, auth_config["secret"], algorithm="HS512")
    settings_hs256 = Settings(JWT_SECRET=auth_config["secret"], JWT_ALGORITHM="HS256")
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token_hs512, secret=auth_config["secret"], settings=settings_hs256)
    assert exc_info.value.status_code == 401


def test_jwt_malformed_token_delimiters():
    """
    Test tokens with corrupt structure: missing dots, extra dots, empty string, None.
    """
    assert verify_ws_token("") is None
    assert verify_ws_token(None) is None
    assert verify_ws_token("header.payload") is None
    assert verify_ws_token("header.payload.signature.extra") is None
    assert verify_ws_token("....") is None


# ===========================================================================
# 5. END-TO-END REST PENETRATION SUITE
# ===========================================================================

def test_api_brute_force_lockout_enforcement_end_to_end(test_app):
    """
    Test rapid sequential PIN brute force on the live FastAPI test client.
    Verify that 429 lockout is enforced after exactly 5 failures, and Retry-After header is returned.
    """
    client = TestClient(test_app)

    for attempt in range(1, 5):
        r = client.post("/api/auth/login", json={"pin": f"99990{attempt}"})
        assert r.status_code == 401, f"Attempt {attempt} should return 401"

    r5 = client.post("/api/auth/login", json={"pin": "999905"})
    assert r5.status_code == 429
    assert "Retry-After" in r5.headers

    r6 = client.post("/api/auth/login", json={"pin": "000000"})
    assert r6.status_code == 429


def test_api_spoofed_forwarded_ip_rate_limiting(test_app):
    """
    Test rate limiting when X-Forwarded-For header is present.
    Verify that different X-Forwarded-For IPs are isolated.
    """
    client = TestClient(test_app)
    ip_a = "203.0.113.10"
    ip_b = "203.0.113.20"

    for _ in range(5):
        client.post("/api/auth/login", json={"pin": "000000"}, headers={"X-Forwarded-For": ip_a})

    r_a = client.post("/api/auth/login", json={"pin": "000000"}, headers={"X-Forwarded-For": ip_a})
    assert r_a.status_code == 429

    r_b = client.post("/api/auth/login", json={"pin": "000000"}, headers={"X-Forwarded-For": ip_b})
    assert r_b.status_code == 401
