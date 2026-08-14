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
from typing import List, Union

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
