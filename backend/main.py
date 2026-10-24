"""
AgyMobile Backend Server Entrypoint
FastAPI server core with lifespan management, CORS middleware, REST endpoints, and static SPA fallback.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import psutil
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from backend.config import Settings, get_settings
from backend.network import (
    generate_png_qr,
    generate_qr_png_bytes,
    generate_qr_svg_bytes,
    generate_svg_qr,
    generate_terminal_qr,
    get_lan_ip,
    print_startup_banner,
)
from backend.security import (
    create_access_token,
    decode_access_token,
    get_client_ip,
    get_current_user,
    otp_manager,
    rate_limiter,
    verify_pin_constant_time,
)
from backend.session_hub import session_router, session_ws_router
from backend.telemetry import telemetry_poller, telemetry_router, telemetry_ws_router
from backend.terminal import terminal_router
from backend.terminal.ssh_session import ssh_manager, ssh_router, ssh_ws_router
from backend.tunnel_manager import TunnelManager, TunnelProvider, tunnel_manager
from backend.workspace import workspace_router

SERVER_START_TIME = time.time()
FRONTEND_DIR = (Path(__file__).parent.parent / "frontend").resolve()


# =====================================================================
# Request / Response Schemas
# =====================================================================

class LoginRequest(BaseModel):
    pin: str = Field(..., min_length=1, max_length=64, description="Numeric PIN code")


class OTPRequest(BaseModel):
    email: Optional[str] = Field(None, description="Target email for OTP")


class OTPResponse(BaseModel):
    status: str = "success"
    message: str
    expires_in: int
    email: Optional[str] = None
    otp: Optional[str] = None


class OTPVerifyRequest(BaseModel):
    otp: str = Field(..., min_length=4, max_length=16, description="Numeric OTP code")
    email: Optional[str] = Field(None, description="Associated email if sent via email")


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    token_type: str = "bearer"
    status: str = "authenticated"


class VerifyResponse(BaseModel):
    valid: bool = True
    user: str = "admin"
    role: str = "admin"
    status: str = "authenticated"


class SystemInfoResponse(BaseModel):
    lan_ip: str
    port: int
    cpu: float
    ram: float
    disk: float
    uptime: float
    os: str = "windows"
    server_time: str
    hostname: str


# =====================================================================
# Lifespan Context Manager
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup Phase ---
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    lan_ip = get_lan_ip()
    port = settings.PORT
    pin = settings.AUTH_PIN

    pairing_token = create_access_token(
        data={"sub": "admin", "type": "pairing"},
        expires_delta=settings.TOKEN_EXPIRY_SECONDS,
        settings=settings,
    )
    pairing_url = f"http://{lan_ip}:{port}/?token={pairing_token}"

    active_otp = otp_manager.get_current_active_otp()
    print_startup_banner(
        lan_ip=lan_ip,
        port=port,
        pin=pin,
        token=pairing_token,
        pairing_url=pairing_url,
        otp=active_otp,
    )

    await telemetry_poller.start()

    # 4. Auto-start tunnel for remote access (if enabled and provider available)
    tunnel_url = None
    if settings.TUNNEL_ENABLED and settings.TUNNEL_PROVIDER != "none":
        try:
            provider = TunnelProvider(settings.TUNNEL_PROVIDER.lower())
            tunnel_manager.port = settings.PORT
            tunnel_manager.preferred_provider = provider
            tunnel_manager.startup_timeout = float(settings.TUNNEL_STARTUP_TIMEOUT)

            tunnel_status = await tunnel_manager.start()
            if tunnel_status.url:
                tunnel_url = tunnel_status.url
                print(f"\n  🌐 \033[1;36mTunnel URL (Remote Access):\033[0m")
                print(f"     \033[1;97m{tunnel_url}\033[0m")
                print(f"     Provider: {tunnel_status.provider.value}")
                print(f"     \033[90mShare this URL to access from anywhere!\033[0m\n")
            elif tunnel_status.error:
                print(f"\n  ⚠️  Tunnel: {tunnel_status.error}\n")
            else:
                print(f"\n  ⚠️  Tunnel started but URL not detected yet.\n")
        except Exception as e:
            print(f"\n  ⚠️  Tunnel auto-start skipped: {e}\n")

    yield

    # --- Shutdown Phase ---
    await tunnel_manager.stop()
    await ssh_manager.shutdown_all()
    await telemetry_poller.stop()


# =====================================================================
# Application Factory
# =====================================================================

def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Application factory for FastAPI app."""
    active_settings = settings or get_settings()

    app = FastAPI(
        title="AgyMobile Desktop Controller",
        description="Antigravity 2.0 & Desktop Workspace Mobile Controller Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = active_settings

    # CORS Middleware
    origins = (
        active_settings.CORS_ORIGINS
        if isinstance(active_settings.CORS_ORIGINS, list)
        else [active_settings.CORS_ORIGINS]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|.*\.trycloudflare\.com|.*\.ngrok-free\.app|.*\.tailscale\.net)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # -----------------------------------------------------------------
    # REST Endpoints
    # -----------------------------------------------------------------

    @app.post("/api/auth/login", response_model=LoginResponse, tags=["Authentication"])
    async def login(request: Request, body: LoginRequest):
        """Authenticate with PIN and receive JWT access token."""
        client_ip = get_client_ip(request)
        is_locked, remaining = rate_limiter.is_locked(client_ip)
        if is_locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed login attempts. IP locked for {remaining} seconds.",
                headers={"Retry-After": str(remaining)},
            )

        current_pin = (
            active_settings.AUTH_PIN
            if active_settings
            else os.environ.get("AGY_AUTH_PIN", os.environ.get("AUTH_PIN", "123456"))
        )

        if not verify_pin_constant_time(body.pin, current_pin):
            is_now_locked, lock_duration = rate_limiter.record_failure(client_ip)
            if is_now_locked:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many failed login attempts. IP locked for {lock_duration} seconds.",
                    headers={"Retry-After": str(lock_duration)},
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid PIN code",
            )

        rate_limiter.reset(client_ip)
        token = create_access_token(
            data={"sub": "admin", "role": "admin"},
            expires_delta=active_settings.TOKEN_EXPIRY_SECONDS,
            settings=active_settings,
        )
        return LoginResponse(
            token=token,
            expires_in=active_settings.TOKEN_EXPIRY_SECONDS,
            token_type="bearer",
            status="authenticated",
        )

    @app.post("/api/auth/otp/request", response_model=OTPResponse, tags=["Authentication"])
    async def request_otp(request: Request, body: OTPRequest):
        """Generate and optionally email a 6-digit OTP code."""
        client_ip = get_client_ip(request)
        is_locked, remaining = rate_limiter.is_locked(client_ip)
        if is_locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests. Please wait {remaining} seconds.",
                headers={"Retry-After": str(remaining)},
            )

        target_email = body.email or active_settings.ADMIN_EMAIL
        otp_code, ttl = otp_manager.generate_otp(email=target_email, ttl_seconds=active_settings.OTP_EXPIRY_SECONDS)

        if target_email:
            otp_manager.send_email_otp(target_email, otp_code, ttl_seconds=ttl)
            msg = f"Mã OTP 6 số đã được gửi tới {target_email} (Hiệu lực: {ttl}s)"
        else:
            msg = f"Mã OTP 6 số đã được tạo thành công trên máy chủ (Hiệu lực: {ttl}s)"

        is_local = client_ip in ("127.0.0.1", "localhost", "::1") or not active_settings.SMTP_HOST
        return OTPResponse(
            status="success",
            message=msg,
            expires_in=ttl,
            email=target_email,
            otp=otp_code if is_local else None,
        )

    @app.post("/api/auth/otp/verify", response_model=LoginResponse, tags=["Authentication"])
    async def verify_otp_endpoint(request: Request, body: OTPVerifyRequest):
        """Verify dynamic OTP code and return access token."""
        client_ip = get_client_ip(request)
        is_locked, remaining = rate_limiter.is_locked(client_ip)
        if is_locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed attempts. IP locked for {remaining} seconds.",
                headers={"Retry-After": str(remaining)},
            )

        if not otp_manager.verify_otp(body.otp, email=body.email):
            is_now_locked, lock_duration = rate_limiter.record_failure(client_ip)
            if is_now_locked:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many failed OTP attempts. IP locked for {lock_duration} seconds.",
                    headers={"Retry-After": str(lock_duration)},
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Mã OTP không chính xác hoặc đã hết hạn",
            )

        rate_limiter.reset(client_ip)
        token = create_access_token(
            data={"sub": "admin", "role": "admin", "auth_method": "otp"},
            expires_delta=active_settings.TOKEN_EXPIRY_SECONDS,
            settings=active_settings,
        )
        return LoginResponse(
            token=token,
            expires_in=active_settings.TOKEN_EXPIRY_SECONDS,
            token_type="bearer",
            status="authenticated",
        )

    @app.get("/api/auth/otp/current", tags=["Authentication"])
    async def get_current_otp(request: Request):
        """Get current active pairing OTP."""
        client_ip = get_client_ip(request)
        is_local = client_ip in ("127.0.0.1", "localhost", "::1")
        if not is_local:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                try:
                    decode_access_token(auth_header[7:], settings=active_settings)
                except Exception:
                    raise HTTPException(status_code=403, detail="Forbidden")
            else:
                raise HTTPException(status_code=403, detail="Forbidden from external IP without auth")

        otp = otp_manager.get_current_active_otp()
        return {
            "otp": otp,
            "expires_in": active_settings.OTP_EXPIRY_SECONDS,
            "status": "active",
        }

    @app.get("/api/auth/qr", tags=["Authentication"])
    async def get_qr_code(
        format: Literal["png", "svg", "ascii", "json"] = Query("png", description="QR format: png, svg, ascii, json"),
        token: Optional[str] = None,
        lan_ip: Optional[str] = None,
        port: Optional[int] = None,
    ):
        """Generate pairing QR code for instant mobile camera pairing."""
        ip = get_lan_ip(lan_ip)
        server_port = port or active_settings.PORT
        auth_token = token or create_access_token(
            data={"sub": "admin", "type": "pairing"},
            expires_delta=active_settings.TOKEN_EXPIRY_SECONDS,
            settings=active_settings,
        )
        pairing_url = f"http://{ip}:{server_port}/?token={auth_token}"

        if format == "svg":
            svg_content = generate_qr_svg_bytes(pairing_url)
            return Response(
                content=svg_content,
                media_type="image/svg+xml",
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )
        elif format == "ascii":
            ascii_content = generate_terminal_qr(pairing_url, compact=True)
            return PlainTextResponse(content=ascii_content)
        elif format == "json":
            return {
                "url": pairing_url,
                "lan_ip": ip,
                "port": server_port,
                "token": auth_token,
                "ascii": generate_terminal_qr(pairing_url, compact=True),
            }
        else:
            png_bytes = generate_qr_png_bytes(pairing_url)
            return Response(
                content=png_bytes,
                media_type="image/png",
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )

    @app.get("/api/auth/verify", response_model=VerifyResponse, tags=["Authentication"])
    async def verify_auth(current_user: dict = Depends(get_current_user)):
        """Verify validity of current Authorization Bearer token."""
        return VerifyResponse(
            valid=True,
            user=current_user.get("sub", "admin"),
            role=current_user.get("role", "admin"),
            status="authenticated",
        )

    # -----------------------------------------------------------------
    # Milestone M2: Session Hub Routers
    # -----------------------------------------------------------------
    app.include_router(session_router)
    app.include_router(session_ws_router)

    # -----------------------------------------------------------------
    # Milestone M3: Workspace & Terminal Routers
    # -----------------------------------------------------------------
    app.include_router(workspace_router)
    app.include_router(terminal_router)

    # -----------------------------------------------------------------
    # Milestone M5: System Telemetry Routers
    # -----------------------------------------------------------------
    app.include_router(telemetry_router)
    app.include_router(telemetry_ws_router)

    # -----------------------------------------------------------------
    # SSH Remote Terminal Routers
    # -----------------------------------------------------------------
    app.include_router(ssh_router)
    app.include_router(ssh_ws_router)

    # -----------------------------------------------------------------
    # Tunnel Status API
    # -----------------------------------------------------------------

    @app.get("/api/tunnel/status", tags=["Tunnel"])
    async def get_tunnel_status(current_user: dict = Depends(get_current_user)):
        """Get current tunnel connection status."""
        return tunnel_manager.get_status_dict()

    @app.post("/api/tunnel/restart", tags=["Tunnel"])
    async def restart_tunnel(current_user: dict = Depends(get_current_user)):
        """Restart the tunnel connection."""
        status = await tunnel_manager.restart()
        return tunnel_manager.get_status_dict()

    @app.post("/api/tunnel/stop", tags=["Tunnel"])
    async def stop_tunnel(current_user: dict = Depends(get_current_user)):
        """Stop the tunnel connection."""
        await tunnel_manager.stop()
        return {"status": "stopped"}

    # -----------------------------------------------------------------
    # Terminal Session Management API
    # -----------------------------------------------------------------
    from backend.terminal.pty_session import terminal_manager as term_mgr

    @app.get("/api/terminal/sessions", tags=["Terminal"])
    async def list_terminal_sessions(current_user: dict = Depends(get_current_user)):
        """List all active terminal sessions with metadata."""
        return await term_mgr.list_sessions()

    # -----------------------------------------------------------------
    # Static Files & SPA Fallback Route
    # -----------------------------------------------------------------

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API endpoint not found")

        target_file = (FRONTEND_DIR / full_path).resolve()
        if (
            target_file.is_file()
            and str(target_file).lower().startswith(str(FRONTEND_DIR.resolve()).lower())
        ):
            return FileResponse(target_file)

        index_file = FRONTEND_DIR / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)

        return HTMLResponse(
            "<!DOCTYPE html><html><head><title>AgyMobile Server</title></head>"
            "<body style='font-family:sans-serif;padding:2rem;background:#121212;color:#eee;'>"
            "<h2>AgyMobile Server Active</h2>"
            f"<p>Backend server running on port {active_settings.PORT}.</p>"
            "</body></html>"
        )

    return app


app = create_app()


def run_server():
    """CLI launcher command for pyproject.toml scripts."""
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info",
    )


if __name__ == "__main__":
    run_server()
