"""
backend/session_hub/tailer.py

Asynchronous and synchronous byte-offset file tailer for Antigravity 2.0 transcript logs.
Provides resilient streaming, partial line buffering at EOF, truncation detection,
argument JSON normalization, and typed WebSocket event mapping.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union

from backend.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ToolCallInfo:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    call_id: Optional[str] = None
    tool_action: Optional[str] = None
    tool_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id or self.call_id or "",
            "call_id": self.call_id or self.id or "",
            "name": self.name,
            "args": self.args,
            "tool_action": self.tool_action,
            "tool_summary": self.tool_summary,
        }


@dataclass
class TranscriptEntry:
    step_index: int
    source: str
    type: str
    status: str
    created_at: str
    byte_offset_start: int
    byte_offset_end: int
    content: Optional[str] = None
    thinking: Optional[str] = None
    tool_calls: List[ToolCallInfo] = field(default_factory=list)
    exit_code: Optional[int] = None
    error: Optional[str] = None
    error_code: Optional[int] = None
    truncated_fields: Optional[List[str]] = None
    raw_dict: Dict[str, Any] = field(default_factory=dict)

    def to_step_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "step_index": self.step_index,
            "source": self.source,
            "type": self.type,
            "status": self.status,
            "created_at": self.created_at,
            "content": self.content,
            "thinking": self.thinking,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "exit_code": self.exit_code,
            "error": self.error,
            "error_code": self.error_code,
        }
        if self.truncated_fields:
            d["truncated_fields"] = self.truncated_fields
        return d


class AsyncTranscriptTailer:
    """Async & sync byte-offset file tailer for transcript.jsonl."""

    def __init__(
        self,
        file_path_or_conversation_id: Union[str, Path],
        base_dir: Optional[Union[str, Path]] = None,
        chunk_size: int = 65536,
        min_poll_interval: float = 0.02,
        max_poll_interval: float = 0.5,
    ) -> None:
        raw_target = str(file_path_or_conversation_id)

        if base_dir:
            self.base_dir = Path(base_dir).resolve()
        else:
            env_gemini = os.environ.get("GEMINI_HOME")
            if env_gemini:
                self.base_dir = (Path(env_gemini) / "antigravity" / "brain").resolve()
            else:
                try:
                    self.base_dir = (get_settings().ANTIGRAVITY_DIR / "brain").resolve()
                except Exception:
                    self.base_dir = (Path.home() / ".gemini" / "antigravity" / "brain").resolve()

        if raw_target.endswith(".jsonl") or Path(raw_target).is_file() or "/" in raw_target or "\\" in raw_target:
            self.filepath = Path(raw_target).resolve()
            # Extract conversation_id from parent path if possible
            parts = self.filepath.parts
            self.conversation_id = parts[-4] if len(parts) >= 4 and parts[-2] == "logs" else self.filepath.stem
        else:
            self.conversation_id = raw_target
            self.filepath = (
                self.base_dir
                / self.conversation_id
                / ".system_generated"
                / "logs"
                / "transcript.jsonl"
            )

        self.chunk_size = chunk_size
        self.min_poll_interval = min_poll_interval
        self.max_poll_interval = max_poll_interval

        self._buffer = bytearray()
        self._emitted_offset = 0
        self._is_stopped = False
        self._poll_interval = min_poll_interval

    @property
    def current_offset(self) -> int:
        return self._emitted_offset

    def stop(self) -> None:
        """Signal the tailer loop to stop."""
        self._is_stopped = True

    def _resolve_existing_file(self) -> Optional[Path]:
        """Find the active transcript file, checking primary and fallback paths."""
        if self.filepath.is_file():
            return self.filepath

        # Fallbacks
        candidates = [
            self.filepath,
            self.base_dir / self.conversation_id / ".system_generated" / "logs" / "transcript_full.jsonl",
            self.base_dir / self.conversation_id / "transcript.jsonl",
        ]
        for cand in candidates:
            if cand.is_file():
                return cand

        return None

    def tail_from_offset(self, from_offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        """
        Synchronously read all complete newline-terminated JSON lines starting from from_offset.
        Returns (steps_list, new_end_byte_offset).
        """
        target = self._resolve_existing_file()
        if not target or not target.is_file():
            return [], from_offset

        current_offset = max(0, from_offset)
        try:
            file_size = os.path.getsize(target)
        except OSError:
            return [], from_offset

        if current_offset > file_size:
            logger.warning(f"Offset {current_offset} exceeds file size {file_size}. Resetting to 0.")
            current_offset = 0

        steps: List[Dict[str, Any]] = []
        try:
            with open(target, "rb") as f:
                f.seek(current_offset)
                buffer = bytearray()
                bytes_consumed = 0

                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break
                    buffer.extend(chunk)

                    while True:
                        nl_idx = buffer.find(b"\n")
                        if nl_idx == -1:
                            # Incomplete trailing line at EOF: leave it unconsumed
                            break

                        line_bytes = bytes(buffer[:nl_idx])
                        line_len = nl_idx + 1
                        del buffer[:line_len]

                        line_start = current_offset + bytes_consumed
                        bytes_consumed += line_len
                        line_end = current_offset + bytes_consumed

                        entry = self._parse_line(line_bytes, line_start, line_end)
                        if entry:
                            steps.append(entry.to_step_dict())

                if not buffer:
                    buffer.clear()

            return steps, current_offset + bytes_consumed
        except Exception as e:
            logger.warning(f"Error reading transcript at {target}: {e}")
            return [], current_offset

    async def wait_for_file(self, timeout: float = 2.0) -> bool:
        """Wait until transcript file exists on disk."""
        elapsed = 0.0
        step = 0.05
        while elapsed < timeout:
            target = self._resolve_existing_file()
            if target and await asyncio.to_thread(target.is_file):
                self.filepath = target
                return True
            await asyncio.sleep(step)
            elapsed += step
        return False

    async def read_catchup(self, from_offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        """Asynchronously read all existing lines from from_offset to EOF."""
        return await asyncio.to_thread(self.tail_from_offset, from_offset)

    async def tail(
        self, from_offset: int = 0, follow: bool = True
    ) -> AsyncGenerator[TranscriptEntry, None]:
        """Async generator yielding TranscriptEntry items as they are written to disk."""
        self._emitted_offset = max(0, from_offset)
        self._buffer.clear()
        self._is_stopped = False
        self._poll_interval = self.min_poll_interval
        max_buffer_safety = 5 * 1024 * 1024  # 5MB safety threshold

        # Resolve file or poll asynchronously
        target = self._resolve_existing_file()
        while not target or not await asyncio.to_thread(target.is_file):
            if not follow or self._is_stopped:
                return
            await asyncio.sleep(self._poll_interval)
            target = self._resolve_existing_file()

        try:
            file_handle = await asyncio.to_thread(open, target, "rb")
        except Exception as e:
            logger.warning(f"Could not open transcript {target}: {e}")
            return

        try:
            file_size = await asyncio.to_thread(os.path.getsize, target)
            if self._emitted_offset > file_size:
                logger.warning(f"Seek offset {self._emitted_offset} > file size {file_size}. Resetting to 0.")
                self._emitted_offset = 0

            await asyncio.to_thread(file_handle.seek, self._emitted_offset)

            while not self._is_stopped:
                cur_size = await asyncio.to_thread(os.path.getsize, target)
                if cur_size < self._emitted_offset:
                    logger.warning(f"File truncated ({cur_size} < {self._emitted_offset}). Resetting offset to 0.")
                    self._emitted_offset = 0
                    self._buffer.clear()
                    await asyncio.to_thread(file_handle.seek, 0)

                chunk = await asyncio.to_thread(file_handle.read, self.chunk_size)

                if chunk:
                    self._buffer.extend(chunk)
                    self._poll_interval = self.min_poll_interval

                    # Safety check for memory bounding
                    if len(self._buffer) > max_buffer_safety:
                        nl_last = self._buffer.rfind(b"\n")
                        if nl_last != -1:
                            del self._buffer[: nl_last + 1]
                        else:
                            self._buffer.clear()

                    while True:
                        nl_pos = self._buffer.find(b"\n")
                        if nl_pos == -1:
                            break

                        line_bytes = bytes(self._buffer[:nl_pos])
                        line_len = nl_pos + 1
                        del self._buffer[:line_len]

                        start_offset = self._emitted_offset
                        self._emitted_offset += line_len

                        entry = self._parse_line(line_bytes, start_offset, self._emitted_offset)
                        if entry:
                            yield entry

                    if not self._buffer:
                        self._buffer.clear()
                else:
                    if not follow:
                        break

                    await asyncio.sleep(self._poll_interval)
                    self._poll_interval = min(self._poll_interval * 1.5, self.max_poll_interval)

        finally:
            await asyncio.to_thread(file_handle.close)

    async def stream_websocket_events(
        self, from_offset: int = 0, follow: bool = True
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Convert live transcript items into typed WebSocket events."""
        async for entry in self.tail(from_offset=from_offset, follow=follow):
            # 1. Stream thoughts if present
            if entry.thinking:
                yield {
                    "type": "token_stream",
                    "conversation_id": self.conversation_id,
                    "step_index": entry.step_index,
                    "kind": "thinking",
                    "delta": entry.thinking,
                    "byte_offset": entry.byte_offset_end,
                }

            # 2. Stream content tokens if present
            if entry.content and entry.type in ("PLANNER_RESPONSE", "USER_INPUT"):
                yield {
                    "type": "token_stream",
                    "conversation_id": self.conversation_id,
                    "step_index": entry.step_index,
                    "kind": "content",
                    "delta": entry.content,
                    "byte_offset": entry.byte_offset_end,
                }

            # 3. Stream tool call requests
            for tc in entry.tool_calls:
                yield {
                    "type": "tool_call_request",
                    "conversation_id": self.conversation_id,
                    "step_index": entry.step_index,
                    "tool_call": tc.to_dict(),
                    "status": "PENDING_APPROVAL" if entry.status == "PENDING_APPROVAL" else "DONE",
                    "byte_offset": entry.byte_offset_end,
                }

            # 4. Stream general step update
            yield {
                "type": "step_update",
                "conversation_id": self.conversation_id,
                "step": entry.to_step_dict(),
                "byte_offset": entry.byte_offset_end,
            }

    def _parse_line(
        self, line_bytes: bytes, start_offset: int, end_offset: int
    ) -> Optional[TranscriptEntry]:
        """Decode and validate a single line of JSON transcript."""
        line_str = line_bytes.decode("utf-8", errors="replace").strip()
        if not line_str:
            return None

        try:
            data = json.loads(line_str)
        except json.JSONDecodeError as err:
            logger.debug(f"Skipping incomplete JSON line at {start_offset}-{end_offset}: {err}")
            return None

        tool_calls: List[ToolCallInfo] = []
        for raw_tc in data.get("tool_calls", []):
            if isinstance(raw_tc, dict):
                args = self._normalize_args(raw_tc.get("args", {}))
                call_id = raw_tc.get("id") or raw_tc.get("call_id")
                tool_calls.append(
                    ToolCallInfo(
                        name=raw_tc.get("name", ""),
                        args=args,
                        id=call_id,
                        call_id=call_id,
                        tool_action=raw_tc.get("toolAction"),
                        tool_summary=raw_tc.get("toolSummary"),
                    )
                )

        return TranscriptEntry(
            step_index=data.get("step_index", 0),
            source=data.get("source", "UNKNOWN"),
            type=data.get("type", "GENERIC"),
            status=data.get("status", "DONE"),
            created_at=data.get("created_at", ""),
            byte_offset_start=start_offset,
            byte_offset_end=end_offset,
            content=data.get("content"),
            thinking=data.get("thinking"),
            tool_calls=tool_calls,
            exit_code=data.get("exit_code"),
            error=data.get("error"),
            error_code=data.get("error_code"),
            truncated_fields=data.get("truncated_fields"),
            raw_dict=data,
        )

    def _normalize_args(self, args: Any) -> Dict[str, Any]:
        """Normalize stringified JSON objects in tool call argument dictionaries."""
        if not isinstance(args, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, str):
                v_trimmed = v.strip()
                if (v_trimmed.startswith("{") and v_trimmed.endswith("}")) or (
                    v_trimmed.startswith("[") and v_trimmed.endswith("]")
                ):
                    try:
                        normalized[k] = json.loads(v_trimmed)
                        continue
                    except Exception:
                        pass
                if v_trimmed.startswith('"') and v_trimmed.endswith('"') and len(v_trimmed) >= 2:
                    try:
                        normalized[k] = json.loads(v_trimmed)
                        continue
                    except Exception:
                        pass
            normalized[k] = v
        return normalized


class TranscriptTailer(AsyncTranscriptTailer):
    """Alias for AsyncTranscriptTailer providing direct compatibility."""
    pass
