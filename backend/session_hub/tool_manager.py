"""
backend/session_hub/tool_manager.py

Tool approval and rejection manager for Antigravity 2.0.
Inspects transcripts for PENDING_APPROVAL tool calls and safely updates
permission grant rules in ~/.gemini/config/config.json via atomic writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from backend.config import Settings, get_settings
from backend.session_hub.tailer import TranscriptTailer

logger = logging.getLogger(__name__)


class ToolManager:
    """Manager for pending tool calls and permission grants."""

    _lock = asyncio.Lock()

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def _resolve_config_path(self, explicit_path: Optional[Union[str, Path]] = None) -> Path:
        """Locate the master ~/.gemini/config/config.json file."""
        if explicit_path:
            return Path(explicit_path).resolve()

        gemini_env = os.environ.get("GEMINI_HOME")
        if gemini_env:
            return (Path(gemini_env) / "config" / "config.json").resolve()

        antigravity_env = os.environ.get("ANTIGRAVITY_HOME")
        if antigravity_env:
            return (Path(antigravity_env).parent / "config" / "config.json").resolve()

        try:
            return (self.settings.ANTIGRAVITY_DIR.parent / "config" / "config.json").resolve()
        except Exception:
            return (Path.home() / ".gemini" / "config" / "config.json").resolve()

    def get_pending_tool_calls(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Inspect transcript for any tool calls with status == PENDING_APPROVAL."""
        tailer = TranscriptTailer(conversation_id)
        steps, _ = tailer.tail_from_offset(0)

        pending: List[Dict[str, Any]] = []
        for step in steps:
            if step.get("status") == "PENDING_APPROVAL":
                for tc in step.get("tool_calls", []):
                    pending.append({
                        "conversation_id": conversation_id,
                        "step_index": step.get("step_index"),
                        "tool_call_id": tc.get("id") or tc.get("call_id") or f"call_{step.get('step_index')}",
                        "name": tc.get("name"),
                        "args": tc.get("args", {}),
                        "thinking": step.get("thinking"),
                        "content": step.get("content"),
                        "status": "PENDING_APPROVAL",
                        "created_at": step.get("created_at"),
                    })

        return pending

    def derive_pattern_from_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Derive standard Antigravity permission grant pattern from tool call details."""
        if tool_name == "run_command":
            cmd = args.get("CommandLine", "").strip()
            if cmd:
                return f"command({cmd})"
            return "command(*)"

        if tool_name in ("view_file", "read_file"):
            p = args.get("AbsolutePath") or args.get("path") or "*"
            return f"read_file({p})"

        if tool_name in ("write_to_file", "replace_file_content", "multi_replace_file_content"):
            p = args.get("TargetFile") or args.get("path") or "*"
            return f"write_file({p})"

        if tool_name == "read_url_content":
            url = args.get("Url", "*")
            return f"read_url({url})"

        if tool_name.startswith("mcp_"):
            return f"mcp({tool_name})"

        return f"{tool_name}(*)"

    def update_permission_grants_atomic(
        self, pattern: str, config_path: Optional[Union[str, Path]] = None
    ) -> bool:
        """
        Atomically add pattern to userSettings.globalPermissionGrants.allow
        in config.json using tempfile + flush + fsync + os.replace.
        """
        target_path = self._resolve_config_path(config_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Read existing config or initialize skeleton
        data: Dict[str, Any] = {
            "userSettings": {
                "autoExecutionPolicy": "CASCADE_COMMANDS_AUTO_EXECUTION_EAGER",
                "artifactReviewMode": "ARTIFACT_REVIEW_MODE_TURBO",
                "globalPermissionGrants": {
                    "allow": []
                }
            }
        }

        if target_path.is_file():
            try:
                raw_text = target_path.read_text(encoding="utf-8")
                if raw_text.strip():
                    data = json.loads(raw_text)
            except Exception as e:
                logger.warning(f"Failed to read existing config at {target_path}: {e}")

        # Ensure dictionary hierarchy exists
        user_settings = data.setdefault("userSettings", {})
        grants = user_settings.setdefault("globalPermissionGrants", {})
        allow_list = grants.setdefault("allow", [])

        if not isinstance(allow_list, list):
            allow_list = []
            grants["allow"] = allow_list

        clean_pattern = pattern.strip()
        if clean_pattern and clean_pattern not in allow_list:
            allow_list.append(clean_pattern)

        # 2. Atomic write with fsync and os.replace
        token = secrets.token_hex(4)
        tmp_path = target_path.parent / f"{target_path.name}.tmp.{token}"

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            max_retries = 5
            last_err = None
            for attempt in range(max_retries):
                try:
                    os.replace(tmp_path, target_path)
                    last_err = None
                    break
                except (PermissionError, OSError) as exc:
                    last_err = exc
                    if attempt < max_retries - 1:
                        time.sleep(0.02 * (2 ** attempt))

            if last_err is not None:
                raise last_err

            logger.info(f"Successfully updated permission grants with '{clean_pattern}' at {target_path}")
            return True
        except Exception as e:
            logger.error(f"Failed atomic write to {target_path}: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False

    def handle_decision(
        self,
        conversation_id: str,
        tool_call_id: str,
        action: str,
        pattern: Optional[str] = None,
        config_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """
        Handle approve / reject action for a tool call.
        If action is approve, updates config.json with grant pattern.
        """
        clean_action = str(action).strip().lower()
        if clean_action not in ("approve", "reject"):
            raise ValueError(f"Invalid tool approval action: '{action}'. Must be 'approve' or 'reject'.")

        grant_pattern = pattern
        if clean_action == "approve":
            # If pattern not explicitly supplied, find pending tool call and derive
            if not grant_pattern:
                pending_calls = self.get_pending_tool_calls(conversation_id)
                matched = next(
                    (tc for tc in pending_calls if tc.get("tool_call_id") == tool_call_id),
                    None
                )
                if matched:
                    grant_pattern = self.derive_pattern_from_tool_call(
                        matched.get("name", ""), matched.get("args", {})
                    )
                else:
                    grant_pattern = f"tool({tool_call_id})"

            if grant_pattern:
                self.update_permission_grants_atomic(grant_pattern, config_path=config_path)

        return {
            "status": "updated",
            "tool_call_id": tool_call_id,
            "action": clean_action,
            "pattern": grant_pattern or "",
            "conversation_id": conversation_id,
        }
