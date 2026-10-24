"""
tests/test_otp_auth.py
Verification suite for Dynamic OTP, Email OTP, and Multi-Platform Auth mechanisms.
"""

import time
import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.security import OTPManager, otp_manager, verify_pin_constant_time


def test_otp_generation_and_constant_time_verification():
    mgr = OTPManager()
    otp, ttl = mgr.generate_otp(user="admin", ttl_seconds=10)
    assert len(otp) == 6
    assert otp.isdigit()
    assert ttl == 10

    # Wrong OTP fails
    assert mgr.verify_otp("000000") is False

    # Correct OTP succeeds
    assert mgr.verify_otp(otp) is True

    # Single-use: cannot be reused
    assert mgr.verify_otp(otp) is False


def test_otp_expiration():
    mgr = OTPManager()
    otp, ttl = mgr.generate_otp(user="admin", ttl_seconds=1)
    time.sleep(1.1)
    # Should be expired
    assert mgr.verify_otp(otp) is False


def test_email_otp_lifecycle():
    mgr = OTPManager()
    email = "developer@example.com"
    otp, ttl = mgr.generate_otp(email=email, ttl_seconds=60)
    assert len(otp) == 6

    # Verify with email
    assert mgr.verify_otp(otp, email=email) is True


def test_api_otp_endpoints():
    app = create_app()
    client = TestClient(app)

    # 1. Request OTP
    res = client.post("/api/auth/otp/request", json={"email": "user@domain.com"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "expires_in" in data
    otp_code = data.get("otp")

    # 2. If otp returned, verify it
    if otp_code:
        verify_res = client.post("/api/auth/otp/verify", json={"otp": otp_code, "email": "user@domain.com"})
        assert verify_res.status_code == 200
        verify_data = verify_res.json()
        assert "token" in verify_data
        assert verify_data["status"] == "authenticated"

    # 3. Invalid OTP should fail with 401
    bad_res = client.post("/api/auth/otp/verify", json={"otp": "999999", "email": "user@domain.com"})
    assert bad_res.status_code == 401
