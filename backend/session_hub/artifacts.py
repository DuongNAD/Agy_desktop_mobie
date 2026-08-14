"""
backend/session_hub/artifacts.py

Markdown artifacts synchronizer and checklist progress parser for Antigravity 2.0.
Reads plan artifacts (task.md, implementation_plan.md, walkthrough.md) from
~/.gemini/antigravity/brain/<id>/*.md and parses companion metadata and checkboxes.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from backend.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Regular expressions for checklist checkboxes
CHECKBOX_REGEX = re.compile(
    r"^[ \t]*(?:[-*]|\d+\.)[ \t]+(?:`\[([ xX])\]`|\[([ xX])\])[ \t]+(.*)$",
    re.MULTILINE,
)
H1_REGEX = re.compile(r"^[ \t]*#[ \t]+(.+)$", re.MULTILINE)


@dataclass
class PlanStep:
    index: int
    title: str
    completed: bool
    indent_level: int = 0


@dataclass
class ArtifactProgress:
    total: int = 0
    completed: int = 0
    percent: float = 0.0


@dataclass
class ArtifactInfo:
    id: str
    filename: str
    title: str
    summary: str
    updated_at: str
    request_feedback: bool = False
    user_facing: bool = True
    progress: ArtifactProgress = field(default_factory=ArtifactProgress)
    size: int = 0
    content: Optional[str] = None


class ArtifactsManager:
    """Manager for scanning, parsing, and synchronizing conversation markdown artifacts."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def _resolve_brain_dir(self, conversation_id: str) -> Path:
        """Resolve the brain directory for the given conversation ID."""
        gemini_env = os.environ.get("GEMINI_HOME")
        if gemini_env:
            return (Path(gemini_env) / "antigravity" / "brain" / conversation_id).resolve()

        antigravity_env = os.environ.get("ANTIGRAVITY_HOME")
        if antigravity_env:
            return (Path(antigravity_env) / "brain" / conversation_id).resolve()

        try:
            return (self.settings.ANTIGRAVITY_DIR / "brain" / conversation_id).resolve()
        except Exception:
            return (Path.home() / ".gemini" / "antigravity" / "brain" / conversation_id).resolve()

    def parse_plan_steps(self, markdown_text: str) -> Dict[str, Any]:
        """
        Extract all markdown checklist items (- [ ] / - [x] / `[ ]` / `[x]`)
        and calculate total, completed, and completion percentage.
        """
        if not markdown_text:
            return {"total": 0, "completed": 0, "percent": 0.0, "steps": []}

        steps: List[Dict[str, Any]] = []
        total = 0
        completed = 0

        lines = markdown_text.splitlines()
        for idx, line in enumerate(lines):
            match = CHECKBOX_REGEX.match(line)
            if match:
                # Group 1 is backtick `[ ]`, Group 2 is standard [ ]
                mark = match.group(1) or match.group(2)
                title = (match.group(3) or "").strip()
                is_done = mark.lower() == "x"

                # Calculate indentation
                indent = len(line) - len(line.lstrip())

                total += 1
                if is_done:
                    completed += 1

                steps.append({
                    "index": idx,
                    "title": title,
                    "completed": is_done,
                    "indent_level": indent,
                })

        percent = round((completed / total) * 100.0, 1) if total > 0 else 0.0

        return {
            "total": total,
            "completed": completed,
            "percent": percent,
            "steps": steps,
        }

    def list_artifacts(self, conversation_id: str) -> List[Dict[str, Any]]:
        """List all markdown artifacts within a conversation brain directory."""
        brain_dir = self._resolve_brain_dir(conversation_id)
        if not brain_dir.is_dir():
            return []

        artifacts: List[Dict[str, Any]] = []
        try:
            for item in brain_dir.iterdir():
                if item.is_file() and item.suffix.lower() == ".md":
                    # Exclude internal or temporary filenames if needed
                    filename = item.name
                    meta_file = brain_dir / f"{filename}.metadata.json"

                    meta: Dict[str, Any] = {}
                    if meta_file.is_file():
                        try:
                            meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        except Exception:
                            pass

                    content = ""
                    try:
                        content = item.read_text(encoding="utf-8")
                    except Exception:
                        pass

                    # Determine title
                    title = meta.get("title")
                    if not title:
                        h1_match = H1_REGEX.search(content)
                        if h1_match:
                            title = h1_match.group(1).strip()
                        else:
                            title = filename

                    # Determine summary
                    summary = meta.get("summary")
                    if not summary:
                        # Extract first non-header line
                        for line in content.splitlines():
                            clean_line = line.strip()
                            if clean_line and not clean_line.startswith("#") and not clean_line.startswith("-"):
                                summary = clean_line[:150]
                                break
                    if not summary:
                        summary = f"Markdown artifact: {filename}"

                    # Progress parsing
                    progress_data = self.parse_plan_steps(content)
                    mtime = item.stat().st_mtime
                    iso_mtime = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

                    artifacts.append({
                        "id": filename,
                        "filename": filename,
                        "title": title,
                        "summary": summary,
                        "updated_at": meta.get("updatedAt") or iso_mtime,
                        "request_feedback": bool(meta.get("requestFeedback", False)),
                        "user_facing": bool(meta.get("userFacing", True)),
                        "progress": {
                            "total": progress_data["total"],
                            "completed": progress_data["completed"],
                            "percent": progress_data["percent"],
                        },
                        "size": item.stat().st_size,
                    })
        except Exception as e:
            logger.warning(f"Error listing artifacts in {brain_dir}: {e}")

        # Sort by updated_at / filename
        artifacts.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return artifacts

    def get_artifact_content(self, conversation_id: str, filename: str) -> Optional[Dict[str, Any]]:
        """Retrieve full artifact content and structured steps."""
        brain_dir = self._resolve_brain_dir(conversation_id)
        target = brain_dir / filename
        if not target.is_file() or not filename.endswith(".md"):
            return None

        meta_file = brain_dir / f"{filename}.metadata.json"
        meta: Dict[str, Any] = {}
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not read artifact file {target}: {e}")
            return None

        h1_match = H1_REGEX.search(content)
        title = meta.get("title") or (h1_match.group(1).strip() if h1_match else filename)
        summary = meta.get("summary") or f"Markdown artifact: {filename}"
        progress_data = self.parse_plan_steps(content)

        mtime = target.stat().st_mtime
        iso_mtime = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

        return {
            "id": filename,
            "filename": filename,
            "title": title,
            "summary": summary,
            "content": content,
            "updated_at": meta.get("updatedAt") or iso_mtime,
            "request_feedback": bool(meta.get("requestFeedback", False)),
            "user_facing": bool(meta.get("userFacing", True)),
            "progress": {
                "total": progress_data["total"],
                "completed": progress_data["completed"],
                "percent": progress_data["percent"],
            },
            "steps": progress_data["steps"],
            "size": target.stat().st_size,
        }
