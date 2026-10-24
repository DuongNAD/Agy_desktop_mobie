"""
AgyMobile Configuration Module
Pydantic-Settings v2 based configuration loader with automatic secret generation,
path resolution, and multi-alias environment loading.
"""

from __future__ import annotations

import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Union

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _generate_default_pin() -> str:
    """Generate a cryptographically secure 6-digit numeric PIN."""
    return f"{secrets.randbelow(900000) + 100000:06d}"


def _generate_default_secret() -> str:
    """Generate a cryptographically secure 256-bit (32-byte) hex secret."""
    return secrets.token_hex(32)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server Binding
    HOST: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("AGY_HOST", "HOST"),
    )
    PORT: int = Field(
        default=8765,
        validation_alias=AliasChoices("AGY_PORT", "PORT"),
    )
    DEBUG: bool = Field(
        default=False,
        validation_alias=AliasChoices("AGY_DEBUG", "DEBUG"),
    )

    # Authentication & Cryptography
    AUTH_PIN: str = Field(
        default_factory=_generate_default_pin,
        validation_alias=AliasChoices("AGY_AUTH_PIN", "AUTH_PIN", "PIN"),
    )
    JWT_SECRET: str = Field(
        default_factory=_generate_default_secret,
        validation_alias=AliasChoices("AGY_JWT_SECRET", "JWT_SECRET", "JWT_SECRET_KEY"),
    )
    JWT_ALGORITHM: str = Field(
        default="HS256",
        validation_alias=AliasChoices("AGY_JWT_ALGORITHM", "JWT_ALGORITHM"),
    )
    TOKEN_EXPIRY_SECONDS: int = Field(
        default=86400,
        validation_alias=AliasChoices("AGY_TOKEN_EXPIRY_SECONDS", "TOKEN_EXPIRY_SECONDS"),
    )

    # OTP & Email Authentication
    AUTH_OTP_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices("AGY_AUTH_OTP_ENABLED", "AUTH_OTP_ENABLED", "OTP_ENABLED"),
    )
    OTP_EXPIRY_SECONDS: int = Field(
        default=300,
        validation_alias=AliasChoices("AGY_OTP_EXPIRY_SECONDS", "OTP_EXPIRY_SECONDS"),
    )
    ADMIN_EMAIL: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AGY_ADMIN_EMAIL", "ADMIN_EMAIL"),
    )
    SMTP_HOST: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AGY_SMTP_HOST", "SMTP_HOST"),
    )
    SMTP_PORT: int = Field(
        default=587,
        validation_alias=AliasChoices("AGY_SMTP_PORT", "SMTP_PORT"),
    )
    SMTP_USER: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AGY_SMTP_USER", "SMTP_USER"),
    )
    SMTP_PASSWORD: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AGY_SMTP_PASSWORD", "SMTP_PASSWORD"),
    )
    SMTP_FROM: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AGY_SMTP_FROM", "SMTP_FROM"),
    )
    SMTP_TLS: bool = Field(
        default=True,
        validation_alias=AliasChoices("AGY_SMTP_TLS", "SMTP_TLS"),
    )

    # Cross-Origin Resource Sharing
    CORS_ORIGINS: Union[str, List[str]] = Field(
        default=["*"],
        validation_alias=AliasChoices("AGY_CORS_ORIGINS", "CORS_ORIGINS"),
    )

    # Workspace & Antigravity Directories
    WORKSPACE_ROOT: Path = Field(
        default_factory=lambda: Path("e:/Project").resolve(),
        validation_alias=AliasChoices("AGY_WORKSPACE_ROOT", "WORKSPACE_ROOT"),
    )
    ANTIGRAVITY_DIR: Path = Field(
        default_factory=lambda: Path(os.path.expanduser("~/.gemini/antigravity")).resolve(),
        validation_alias=AliasChoices("AGY_ANTIGRAVITY_DIR", "ANTIGRAVITY_DIR", "ANTIGRAVITY_HOME"),
    )

    # Rate Limiting
    RATE_LIMIT_MAX_ATTEMPTS: int = Field(
        default=5,
        validation_alias=AliasChoices("AGY_RATE_LIMIT_MAX", "RATE_LIMIT_MAX_ATTEMPTS", "RATE_LIMIT_LOGIN_MAX_ATTEMPTS"),
    )
    RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        validation_alias=AliasChoices("AGY_RATE_LIMIT_WINDOW", "RATE_LIMIT_WINDOW_SECONDS", "RATE_LIMIT_LOGIN_WINDOW_SECONDS"),
    )
    RATE_LIMIT_LOCKOUT_SECONDS: int = Field(
        default=300,
        validation_alias=AliasChoices("AGY_RATE_LIMIT_LOCKOUT", "RATE_LIMIT_LOCKOUT_SECONDS"),
    )

    # Tunnel Configuration
    TUNNEL_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices("AGY_TUNNEL_ENABLED", "TUNNEL_ENABLED"),
        description="Auto-start tunnel on server startup for remote access",
    )
    TUNNEL_PROVIDER: str = Field(
        default="cloudflare",
        validation_alias=AliasChoices("AGY_TUNNEL_PROVIDER", "TUNNEL_PROVIDER"),
        description="Tunnel provider: cloudflare, ngrok, or none",
    )
    TUNNEL_STARTUP_TIMEOUT: int = Field(
        default=30,
        validation_alias=AliasChoices("AGY_TUNNEL_TIMEOUT", "TUNNEL_STARTUP_TIMEOUT"),
        description="Max seconds to wait for tunnel URL detection",
    )

    # Terminal Session Persistence
    TERMINAL_IDLE_TIMEOUT: int = Field(
        default=1800,
        validation_alias=AliasChoices("AGY_TERMINAL_IDLE_TIMEOUT", "TERMINAL_IDLE_TIMEOUT"),
        description="Seconds before idle terminal sessions are cleaned up (default: 30 min)",
    )
    TERMINAL_MAX_SESSIONS: int = Field(
        default=10,
        validation_alias=AliasChoices("AGY_TERMINAL_MAX_SESSIONS", "TERMINAL_MAX_SESSIONS"),
        description="Maximum concurrent terminal sessions",
    )

    @field_validator("CORS_ORIGINS", mode="after")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        """Normalize CORS origins from JSON list or comma-separated string."""
        if isinstance(v, str):
            v_trimmed = v.strip()
            if v_trimmed.startswith("[") and v_trimmed.endswith("]"):
                import json
                try:
                    return json.loads(v_trimmed)
                except Exception:
                    pass
            return [origin.strip() for origin in v_trimmed.split(",") if origin.strip()]
        return v

    @field_validator("AUTH_PIN", mode="after")
    @classmethod
    def validate_pin(cls, v: str) -> str:
        """Ensure the authentication PIN is numeric or non-empty string."""
        cleaned = str(v).strip()
        if not cleaned:
            raise ValueError("AUTH_PIN cannot be empty")
        return cleaned

    @field_validator("WORKSPACE_ROOT", "ANTIGRAVITY_DIR", mode="after")
    @classmethod
    def resolve_paths(cls, v: Union[str, Path]) -> Path:
        """Resolve ~ user directories and canonicalize absolute filesystem paths."""
        return Path(os.path.expanduser(str(v))).resolve()


@lru_cache()
def get_settings() -> Settings:
    """Singleton getter for application configuration."""
    return Settings()
