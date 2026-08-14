"""
backend/session_hub/cli_dispatcher.py

Subprocess execution wrapper for Antigravity 2.0 CLI (agy.exe).
Handles remote prompt injection, slash commands (/goal, /schedule, etc.),
process lifecycle management, Windows tree cancellation, and mock fallbacks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from backend.config import Settings, get_settings

logger = logging.getLogger(__name__)


class CLIDispatcher:
    """Subprocess runner for agy.exe CLI commands."""

    _active_processes: Dict[str, Any] = {}
    _lock = asyncio.Lock()

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def find_cli_executable(self) -> Optional[Path]:
        """Locate agy.exe or agy binary on host filesystem."""
        # 1. Custom env / settings
        custom_path = os.environ.get("AGY_CLI_PATH")
        if custom_path and Path(custom_path).is_file():
            return Path(custom_path).resolve()

        # 2. System PATH
        which_path = shutil.which("agy") or shutil.which("agy.exe")
        if which_path:
            return Path(which_path).resolve()

        # 3. Standard Windows installation paths
        user_home = Path.home()
        candidates = [
            user_home / ".gemini" / "antigravity" / "bin" / "agy.exe",
            user_home / ".gemini" / "antigravity-cli" / "bin" / "agy.exe",
            user_home / "AppData" / "Local" / "Programs" / "Antigravity" / "agy.exe",
            Path("C:/Program Files/Antigravity/agy.exe"),
        ]
        for cand in candidates:
            if cand.is_file():
                return cand

        return None

    async def _async_dispatch_prompt(
        self,
        conversation_id: str,
        prompt: str,
        mode: str = "accept-edits",
        timeout_seconds: float = 300.0,
    ) -> Dict[str, Any]:
        """Async implementation of dispatch_prompt."""
        cli_path = self.find_cli_executable()
        workspace_root = self.settings.WORKSPACE_ROOT

        # Check if running under mock/simulation
        if not cli_path or os.environ.get("MOCK_SUBPROCESS") == "1":
            logger.info(f"CLI executable not found or test mode enabled. Using mock dispatch for {conversation_id}.")
            mock_pid = 49201 + (hash(conversation_id) % 1000)

            # Record active process in registry
            async with self._lock:
                self._active_processes[conversation_id] = {
                    "pid": mock_pid,
                    "prompt": prompt,
                    "mode": mode,
                    "is_mock": True,
                }

            return {
                "status": "dispatched",
                "process_id": mock_pid,
                "conversation_id": conversation_id,
                "prompt": prompt,
                "mode": mode,
            }

        cmd: List[str] = [
            str(cli_path),
            "-p", prompt,
            "--conversation", conversation_id,
            "--output-format", "stream-json",
        ]
        if mode:
            cmd.extend(["--mode", mode])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace_root),
            )

            async with self._lock:
                self._active_processes[conversation_id] = process

            # Handle timeout in background monitor
            asyncio.create_task(
                self._monitor_process(conversation_id, process, timeout_seconds)
            )

            return {
                "status": "dispatched",
                "process_id": process.pid,
                "conversation_id": conversation_id,
                "prompt": prompt,
                "mode": mode,
            }
        except Exception as e:
            logger.error(f"Failed to launch agy process: {e}")
            return {
                "status": "error",
                "error": str(e),
                "conversation_id": conversation_id,
            }

    async def _monitor_process(
        self, conversation_id: str, process: Any, timeout: float
    ) -> None:
        """Monitor process execution and handle timeout cleanup."""
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Process {process.pid} for {conversation_id} timed out after {timeout}s. Terminating.")
            await self.cancel_prompt(conversation_id)
        finally:
            async with self._lock:
                self._active_processes.pop(conversation_id, None)

    async def cancel_prompt(self, conversation_id: str) -> bool:
        """Terminate active prompt execution process for a conversation."""
        async with self._lock:
            proc_entry = self._active_processes.pop(conversation_id, None)

        if not proc_entry:
            return False

        if isinstance(proc_entry, dict) and proc_entry.get("is_mock"):
            logger.info(f"Mock prompt cancelled for {conversation_id}.")
            return True

        process = proc_entry
        try:
            pid = getattr(process, "pid", None)
            if pid is not None:
                if pid <= 4 or pid == os.getpid():
                    logger.warning(f"Refusing to cancel process with protected PID {pid}")
                    return False

            if sys.platform == "win32":
                kill_proc = await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
            else:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    process.kill()
            return True
        except Exception as e:
            logger.warning(f"Error cancelling process {getattr(process, 'pid', 'unknown')}: {e}")
            return False

    def get_running_process(self, conversation_id: str) -> Optional[int]:
        """Return PID of active process for conversation if running."""
        proc = self._active_processes.get(conversation_id)
        if not proc:
            return None
        if isinstance(proc, dict):
            return proc.get("pid")
        return getattr(proc, "pid", None)

    def list_active_processes(self) -> Dict[str, int]:
        """List all active conversation process IDs."""
        res: Dict[str, int] = {}
        for cid, proc in self._active_processes.items():
            if isinstance(proc, dict):
                res[cid] = proc.get("pid", 0)
            elif hasattr(proc, "pid"):
                res[cid] = proc.pid
        return res

    @classmethod
    def dispatch_prompt(
        cls,
        conversation_id: str,
        prompt: str,
        mode: str = "accept-edits",
        timeout_seconds: float = 300.0,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Entrypoint supporting synchronous or async execution.
        """
        dispatcher = cls()
        try:
            loop = asyncio.get_running_loop()
            # If in running async loop, return coroutine or direct mock result
            if loop.is_running():
                return loop.create_task(
                    dispatcher._async_dispatch_prompt(
                        conversation_id=conversation_id,
                        prompt=prompt,
                        mode=mode,
                        timeout_seconds=timeout_seconds,
                    )
                )
        except RuntimeError:
            pass

        # Fallback synchronous response
        return asyncio.run(
            dispatcher._async_dispatch_prompt(
                conversation_id=conversation_id,
                prompt=prompt,
                mode=mode,
                timeout_seconds=timeout_seconds,
            )
        )
