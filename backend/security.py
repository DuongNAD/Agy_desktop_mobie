"""
AgyMobile Security Module
Provides PIN verification, JWT token lifecycle management, in-memory IP rate limiting,
and Windows-hardened workspace path sandboxing.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
import threading
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import jwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import Settings, get_settings

# Security Constants
JWT_ISSUER: str = "agymobile-auth"
security_scheme = HTTPBearer(auto_error=False)

RESERVED_DEVICE_NAMES: Set[str] = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    "CONIN$", "CONOUT$", "CLOCK$",
}


# =====================================================================
# 0. Custom Exceptions
# =====================================================================

class SandboxSecurityException(HTTPException):
    """Exception raised for workspace path sandbox violations."""
    def __init__(self, status_code: int = status.HTTP_403_FORBIDDEN, detail: str = "Sandbox security violation"):
        super().__init__(status_code=status_code, detail=detail)


# =====================================================================
# 1. PIN & Token Cryptography
# =====================================================================

def verify_pin_constant_time(provided_pin: Any, configured_pin: Any) -> bool:
    """
    Verify provided PIN against configured PIN in constant time
    to prevent timing side-channel attacks.
    Safely handles non-ASCII, bytes, numbers, or edge types without unhandled TypeErrors.
    """
    if provided_pin is None or configured_pin is None:
        return False
    try:
        if isinstance(provided_pin, (bytes, bytearray)):
            p = provided_pin.decode("utf-8", errors="replace").strip()
        elif isinstance(provided_pin, (int, float)):
            p = str(provided_pin).strip()
        elif isinstance(provided_pin, str):
            p = provided_pin.strip()
        else:
            return False

        if isinstance(configured_pin, (bytes, bytearray)):
            c = configured_pin.decode("utf-8", errors="replace").strip()
        elif isinstance(configured_pin, (int, float)):
            c = str(configured_pin).strip()
        elif isinstance(configured_pin, str):
            c = configured_pin.strip()
        else:
            return False

        if not p or not c:
            return False

        return hmac.compare_digest(p.encode("utf-8"), c.encode("utf-8"))
    except Exception:
        return False


def create_access_token(
    data: Optional[dict] = None,
    expires_delta: Optional[int] = None,
    settings: Optional[Settings] = None,
    secret: Optional[str] = None,
) -> str:
    """Create a signed HMAC-SHA256 JWT access token."""
    s = settings or get_settings()
    now = int(time.time())
    exp = now + (expires_delta if expires_delta is not None else s.TOKEN_EXPIRY_SECONDS)

    payload = {
        "sub": "admin",
        "role": "admin",
        "iat": now,
        "exp": exp,
        "iss": JWT_ISSUER,
    }
    if data:
        payload.update(data)

    jwt_secret = (
        secret
        or (settings.JWT_SECRET if settings else None)
        or os.environ.get("JWT_SECRET_KEY")
        or os.environ.get("AGY_JWT_SECRET")
        or os.environ.get("JWT_SECRET")
        or s.JWT_SECRET
    )
    algorithm = s.JWT_ALGORITHM or "HS256"
    return jwt.encode(payload, jwt_secret, algorithm=algorithm)


def decode_access_token(
    token: str,
    secret: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> dict:
    """Decode and strictly validate a JWT access token."""
    if not token or not isinstance(token, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication token string",
            headers={"WWW-Authenticate": "Bearer"},
        )

    s = settings or get_settings()
    jwt_secret = (
        secret
        or (settings.JWT_SECRET if settings else None)
        or os.environ.get("JWT_SECRET_KEY")
        or os.environ.get("AGY_JWT_SECRET")
        or os.environ.get("JWT_SECRET")
        or s.JWT_SECRET
    )

    allowed_algorithms = [s.JWT_ALGORITHM] if s.JWT_ALGORITHM else ["HS256"]
    if "HS256" not in allowed_algorithms:
        allowed_algorithms.append("HS256")

    try:
        decode_kwargs: Dict[str, Any] = {
            "algorithms": allowed_algorithms,
            "options": {
                "verify_signature": True,
                "verify_exp": True,
            },
        }
        return jwt.decode(token, jwt_secret, **decode_kwargs)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (jwt.InvalidAlgorithmError, jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(exc)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication token validation error: {str(exc)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthManager:
    """Convenience class wrapper for authentication operations."""
    @staticmethod
    def create_token(
        data: Optional[dict] = None,
        secret: Optional[str] = None,
        expires_in: int = 86400,
        settings: Optional[Settings] = None,
    ) -> str:
        return create_access_token(
            data=data,
            expires_delta=expires_in,
            settings=settings,
            secret=secret,
        )

    @staticmethod
    def decode_token(
        token: str,
        secret: Optional[str] = None,
        settings: Optional[Settings] = None,
    ) -> dict:
        return decode_access_token(token, secret=secret, settings=settings)

    @staticmethod
    def verify_pin(provided_pin: str, configured_pin: str) -> bool:
        return verify_pin_constant_time(provided_pin, configured_pin)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme),
) -> dict:
    """FastAPI route dependency to authenticate requests via Bearer token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    app_state = getattr(getattr(request, "app", None), "state", None)
    active_settings = getattr(app_state, "settings", None) or get_settings()

    return decode_access_token(credentials.credentials, settings=active_settings)


def verify_ws_token(
    token: Optional[str],
    secret: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Optional[dict]:
    """Validate token for WebSocket connections (query param or handshake frame)."""
    if not token:
        return None
    try:
        return decode_access_token(token, secret=secret, settings=settings)
    except Exception:
        return None


# =====================================================================
# 2. In-Memory Anti-Brute-Force IP Rate Limiter
# =====================================================================

class IPRateLimiter:
    """Thread-safe In-Memory Sliding Window Rate Limiter with Lockout."""

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 60,
        lockout_seconds: int = 300,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._failures: Dict[str, List[float]] = defaultdict(list)
        self._lockouts: Dict[str, float] = {}
        self._lock = threading.Lock()

    def is_locked(self, ip: str) -> Tuple[bool, int]:
        """Check if an IP is locked out. Returns (is_locked, remaining_seconds)."""
        with self._lock:
            now = time.time()
            lockout_expiry = self._lockouts.get(ip, 0)
            if now < lockout_expiry:
                remaining = int(lockout_expiry - now) + 1
                return True, remaining
            elif ip in self._lockouts:
                del self._lockouts[ip]
                self._failures.pop(ip, None)
            return False, 0

    def is_blocked(self, ip: str) -> bool:
        """Helper for test assertions: return True if IP is currently blocked/locked."""
        is_locked, _ = self.is_locked(ip)
        return is_locked

    def record_failure(self, ip: str) -> Tuple[bool, int]:
        """Record a failed login attempt. Returns (is_now_locked, retry_after_seconds)."""
        with self._lock:
            now = time.time()
            lockout_expiry = self._lockouts.get(ip, 0)
            if now < lockout_expiry:
                return True, int(lockout_expiry - now) + 1

            # Keep only timestamps in current window
            attempts = [t for t in self._failures[ip] if now - t < self.window_seconds]
            attempts.append(now)
            self._failures[ip] = attempts

            if len(attempts) >= self.max_attempts:
                expiry = now + self.lockout_seconds
                self._lockouts[ip] = expiry
                self._failures.pop(ip, None)
                return True, self.lockout_seconds

            return False, 0

    def record_failed_attempt(self, ip: str) -> bool:
        """Helper for test assertions: record failure and return True if now blocked."""
        is_now_locked, _ = self.record_failure(ip)
        return is_now_locked

    def reset(self, ip: str) -> None:
        """Reset failures and lockouts for an IP upon successful login."""
        with self._lock:
            self._failures.pop(ip, None)
            self._lockouts.pop(ip, None)

    def reset_attempts(self, ip: str) -> None:
        """Alias for reset."""
        self.reset(ip)

    def cleanup(self) -> None:
        """Purge expired records to conserve memory."""
        with self._lock:
            now = time.time()
            expired_locks = [ip for ip, exp in self._lockouts.items() if now >= exp]
            for ip in expired_locks:
                del self._lockouts[ip]
                self._failures.pop(ip, None)

            empty_ips = []
            for ip, timestamps in self._failures.items():
                active = [t for t in timestamps if now - t < self.window_seconds]
                if active:
                    self._failures[ip] = active
                else:
                    empty_ips.append(ip)
            for ip in empty_ips:
                del self._failures[ip]

    def clear(self) -> None:
        """Completely clear all tracked state."""
        with self._lock:
            self._failures.clear()
            self._lockouts.clear()


# Alias class
RateLimiter = IPRateLimiter

# Singleton instance
rate_limiter = RateLimiter()
login_rate_limiter = rate_limiter


def get_client_ip(request: Request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For if present."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def enforce_login_rate_limit(
    request: Request,
    limiter: Optional[RateLimiter] = None,
) -> str:
    """FastAPI helper to check rate limit prior to processing login."""
    lim = limiter or rate_limiter
    client_ip = get_client_ip(request)
    is_locked, remaining_seconds = lim.is_locked(client_ip)
    if is_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. IP locked for {remaining_seconds} seconds.",
            headers={"Retry-After": str(remaining_seconds)},
        )
    return client_ip


# =====================================================================
# 3. Windows-Hardened Workspace Path Sandbox
# =====================================================================

def sanitize_and_resolve_sandbox_path(
    user_path: Union[str, Path],
    root_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Sanitize and resolve a user-supplied filesystem path, ensuring it strictly
    remains within the authorized root directory.

    Rejects:
      - Null bytes (\\x00) and encoded null bytes
      - URL-encoded directory traversal attempts (%2e%2e, %252e, etc.)
      - UNC network paths (\\\\server\\share, //server/share, \\\\?\\, \\\\.\\)
      - NTFS Alternate Data Streams (colon syntax)
      - Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9, CONIN$, CONOUT$, CLOCK$)
      - Directory traversal (../) escaping root
      - Drive hopping (C:\\, D:\\ when root is E:\\)
    """
    s = get_settings()
    root_resolved = Path(root_dir).resolve() if root_dir else s.WORKSPACE_ROOT.resolve()

    if user_path is None:
        return root_resolved

    raw_path = str(user_path).strip()
    if not raw_path:
        return root_resolved

    # 1. Null byte check (raw and URL-decoded)
    if "\x00" in raw_path:
        raise SandboxSecurityException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: Null byte detected in file path",
        )

    decoded_path = raw_path
    prev = None
    iter_count = 0
    while "%" in decoded_path and decoded_path != prev and iter_count < 6:
        prev = decoded_path
        iter_count += 1
        try:
            decoded_path = urllib.parse.unquote(decoded_path)
        except Exception:
            break

    if "\x00" in decoded_path:
        raise SandboxSecurityException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: Encoded null byte detected in file path",
        )

    # 2. UNC path & Win32 device namespace check
    if (
        raw_path.startswith(("\\\\", "//", "\\??\\", "//?/"))
        or decoded_path.startswith(("\\\\", "//", "\\??\\", "//?/"))
    ):
        raise SandboxSecurityException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: UNC network paths and device namespaces are strictly prohibited",
        )

    # 3. Alternate Data Streams (ADS) colon check
    clean_path = decoded_path
    remainder = (
        clean_path[2:]
        if (len(clean_path) >= 2 and clean_path[1] == ":" and clean_path[0].isalpha())
        else clean_path
    )
    if ":" in remainder:
        raise SandboxSecurityException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: Alternate data streams and invalid colon syntax are prohibited",
        )

    raw_remainder = (
        raw_path[2:]
        if (len(raw_path) >= 2 and raw_path[1] == ":" and raw_path[0].isalpha())
        else raw_path
    )
    if ":" in raw_remainder:
        raise SandboxSecurityException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: Alternate data streams and invalid colon syntax are prohibited",
        )

    # 4. Windows reserved device names check
    parts = re.split(r"[\\/]", clean_path)
    for part in parts:
        if not part:
            continue
        stem = Path(part).stem.upper()
        base_stem = part.split(".")[0].strip().upper()
        if stem in RESERVED_DEVICE_NAMES or base_stem in RESERVED_DEVICE_NAMES:
            raise SandboxSecurityException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid path: Reserved Windows device name '{part}' is prohibited",
            )

    # 5. Resolve relative or absolute path
    p = Path(clean_path)
    if p.is_absolute():
        target = p.resolve()
    else:
        target = (root_resolved / p).resolve()

    # 6. Verify sandbox containment
    root_str_lower = str(root_resolved).lower().rstrip("\\/")
    target_str_lower = str(target).lower().rstrip("\\/")
    try:
        common = os.path.commonpath([root_str_lower, target_str_lower])
        if common != root_str_lower:
            raise SandboxSecurityException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Requested path is outside sandbox root '{root_resolved}'",
            )
    except ValueError:
        raise SandboxSecurityException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: Path drive does not match sandbox root drive '{root_resolved}'",
        )

    return target


def is_safe_sandbox_path(
    user_path: Union[str, Path],
    root_dir: Optional[Union[str, Path]] = None,
) -> bool:
    """Return True if path is safe and inside sandbox, False otherwise."""
    try:
        sanitize_and_resolve_sandbox_path(user_path, root_dir=root_dir)
        return True
    except (HTTPException, SandboxSecurityException, ValueError, PermissionError):
        return False


class PathSandbox:
    """Path Sandbox validator class."""

    def __init__(self, root_dir: Optional[Union[str, Path]] = None) -> None:
        s = get_settings()
        self.root = Path(root_dir).resolve() if root_dir else s.WORKSPACE_ROOT.resolve()

    def validate_path(self, path_str: Union[str, Path]) -> Path:
        return sanitize_and_resolve_sandbox_path(path_str, root_dir=self.root)

    def is_safe_path(
        self,
        target_path: Union[str, Path],
        root_dir: Optional[Union[str, Path]] = None,
    ) -> bool:
        check_root = Path(root_dir).resolve() if root_dir else self.root
        return is_safe_sandbox_path(target_path, root_dir=check_root)


# =====================================================================
# 4. Dynamic OTP & Email Authentication Subsystem
# =====================================================================

class OTPRecord:
    def __init__(self, otp: str, user: str = "admin", email: Optional[str] = None, ttl_seconds: int = 300) -> None:
        self.otp = otp
        self.user = user
        self.email = email.strip().lower() if email else None
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl_seconds
        self.attempts = 0
        self.max_attempts = 5

    def is_valid(self, provided_otp: str) -> bool:
        if time.time() > self.expires_at:
            return False
        if self.attempts >= self.max_attempts:
            return False
        self.attempts += 1
        return hmac.compare_digest(self.otp.strip().encode("utf-8"), str(provided_otp).strip().encode("utf-8"))


class OTPManager:
    """Thread-safe dynamic OTP generation, caching, and verification engine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._otps: Dict[str, OTPRecord] = {}
        self._latest_otp: Optional[str] = None
        self._latest_otp_time: float = 0

    def generate_otp(
        self,
        email: Optional[str] = None,
        user: str = "admin",
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[str, int]:
        """Generate a cryptographically secure 6-digit numeric OTP."""
        settings = get_settings()
        ttl = ttl_seconds if ttl_seconds is not None else settings.OTP_EXPIRY_SECONDS
        
        # 6-digit cryptographic numeric code
        otp_code = f"{secrets.randbelow(900000) + 100000:06d}"
        
        record = OTPRecord(otp=otp_code, user=user, email=email, ttl_seconds=ttl)
        with self._lock:
            now = time.time()
            self._otps = {k: v for k, v in self._otps.items() if v.expires_at > now}
            self._otps[otp_code] = record
            if email:
                self._otps[f"email:{email.strip().lower()}"] = record
            self._latest_otp = otp_code
            self._latest_otp_time = now
            
        return otp_code, ttl

    def get_current_active_otp(self) -> str:
        """Get the latest non-expired OTP for desktop console/pairing banner."""
        with self._lock:
            if self._latest_otp:
                rec = self._otps.get(self._latest_otp)
                if rec and time.time() < rec.expires_at:
                    return self._latest_otp
        # Generate fresh if expired
        otp, _ = self.generate_otp(user="admin")
        return otp

    def verify_otp(self, provided_otp: str, email: Optional[str] = None) -> bool:
        """Verify OTP with constant-time matching and attempt throttling."""
        if not provided_otp or not str(provided_otp).strip():
            return False
        clean_otp = str(provided_otp).strip()
        now = time.time()
        
        with self._lock:
            self._otps = {k: v for k, v in self._otps.items() if v.expires_at > now}
            
            record = self._otps.get(clean_otp)
            if not record and email:
                record = self._otps.get(f"email:{email.strip().lower()}")
                
            if not record:
                return False
                
            if email and record.email:
                if record.email != email.strip().lower():
                    return False
                    
            valid = record.is_valid(clean_otp)
            if valid:
                self._otps.pop(clean_otp, None)
                if record.email:
                    self._otps.pop(f"email:{record.email}", None)
                if self._latest_otp == clean_otp:
                    self._latest_otp = None
                return True
            return False

    def send_email_otp(self, to_email: str, otp_code: str, ttl_seconds: int = 300) -> bool:
        """Send OTP to target email address via SMTP or stdout fallback."""
        settings = get_settings()
        clean_email = to_email.strip()
        
        if not settings.SMTP_HOST:
            print(f"\n[AgyMobile OTP] ===================================================")
            print(f"[AgyMobile OTP] Email dispatch to: {clean_email}")
            print(f"[AgyMobile OTP] 6-Digit OTP Code : >>> {otp_code} <<< (Valid: {ttl_seconds}s)")
            print(f"[AgyMobile OTP] ===================================================\n")
            return True
            
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        sender = settings.SMTP_FROM or settings.SMTP_USER or "noreply@agymobile.local"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🔑 Mã xác thực AgyMobile: {otp_code}"
        msg["From"] = sender
        msg["To"] = clean_email

        text_content = f"Mã xác thực AgyMobile của bạn là: {otp_code}\nMã có hiệu lực trong {ttl_seconds // 60} phút."
        html_content = f"""
        <div style="font-family: Arial, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 24px; border-radius: 12px; max-width: 480px; margin: auto;">
            <h2 style="color: #38bdf8; margin-top: 0;">AgyMobile Remote Authentication</h2>
            <p style="color: #94a3b8; font-size: 14px;">Bạn đang đăng nhập vào phiên điều khiển Antigravity Desktop từ thiết bị di động.</p>
            <div style="background-color: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; text-align: center; margin: 20px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 6px; color: #38bdf8;">{otp_code}</span>
            </div>
            <p style="color: #64748b; font-size: 12px;">Mã OTP có hiệu lực trong {ttl_seconds // 60} phút. Không chia sẻ mã này cho người khác.</p>
        </div>
        """
        msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        def _send() -> None:
            try:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
                if settings.SMTP_TLS:
                    server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(sender, [clean_email], msg.as_string())
                server.quit()
            except Exception as e:
                print(f"[AgyMobile Auth] Failed to send email to {clean_email}: {e}")

        threading.Thread(target=_send, daemon=True).start()
        return True


otp_manager = OTPManager()

