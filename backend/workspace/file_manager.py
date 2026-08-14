"""
AgyMobile Workspace File Manager Module
Provides high-performance directory navigation, safe file reading/writing,
atomic file persistence, developer MIME/binary detection, and visual unified diff generation.
"""

from __future__ import annotations

import base64
import difflib
import fnmatch
import mimetypes
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.config import Settings, get_settings
from backend.security import PathSandbox, SandboxSecurityException, get_current_user, sanitize_and_resolve_sandbox_path

# =====================================================================
# Developer Extension to MIME Map & Binary Detection Tables
# =====================================================================

DEV_MIME_MAP: Dict[str, str] = {
    ".py": "text/x-python",
    ".ts": "text/typescript",
    ".tsx": "text/typescript-jsx",
    ".js": "text/javascript",
    ".jsx": "text/javascript-jsx",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".json": "application/json",
    ".jsonl": "application/x-jsonlines",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".toml": "application/toml",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".scss": "text/x-scss",
    ".less": "text/x-less",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".ps1": "text/x-powershell",
    ".psm1": "text/x-powershell",
    ".bat": "text/x-batch",
    ".cmd": "text/x-batch",
    ".rs": "text/x-rust",
    ".go": "text/x-go",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".hpp": "text/x-c++",
    ".java": "text/x-java",
    ".kt": "text/x-kotlin",
    ".sql": "text/x-sql",
    ".xml": "application/xml",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".exe": "application/x-msdownload",
    ".dll": "application/x-msdownload",
}

KNOWN_BINARY_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".dll", ".bin",
    ".pyc", ".pyo", ".pyd", ".wasm", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".wav", ".ogg", ".mp4", ".webm", ".avi", ".mov", ".iso",
}

DEFAULT_IGNORE_DIRS: Set[str] = {
    ".git", "__pycache__", ".agents", "node_modules", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode", "dist", "build",
}

DEFAULT_IGNORE_PATTERNS: Set[str] = {
    "*.pyc", "*.pyo", "*.pyd", "*.swp", "*.swo", ".DS_Store", "Thumbs.db", "desktop.ini", "*.egg-info",
}

MAX_DEFAULT_READ_SIZE: int = 5 * 1024 * 1024  # 5 MB


# =====================================================================
# Pydantic Request / Response Schemas
# =====================================================================

class FileNode(BaseModel):
    name: str = Field(..., description="Filename or directory name")
    path: str = Field(..., description="Relative path from workspace root")
    is_dir: bool = Field(..., description="True if entry is a directory")
    size: int = Field(default=0, description="File size in bytes (0 for directories)")
    modified_at: float = Field(default=0.0, description="POSIX timestamp of last modification")
    modified: Optional[float] = Field(default=None, description="Alias for modified_at")
    read_only: bool = Field(default=False, description="True if file is protected or non-writable")
    is_readonly: Optional[bool] = Field(default=False, description="Alias for read_only")
    extension: str = Field(default="", description="File extension")
    mime_type: Optional[str] = Field(default=None, description="Detected MIME type")
    mime: Optional[str] = Field(default=None, description="Alias for mime_type")
    is_binary: bool = Field(default=False, description="True if binary file")
    children_count: int = Field(default=0, description="Number of direct children if directory")
    children: Optional[List[FileNode]] = Field(default=None, description="Child nodes for recursive tree")


class FileContentResponse(BaseModel):
    path: str = Field(..., description="Relative file path")
    name: Optional[str] = Field(None, description="File name")
    content: Optional[str] = Field(None, description="UTF-8 text content or null if binary")
    mime: str = Field(default="text/plain", description="MIME content type")
    size: int = Field(..., description="File size in bytes")
    encoding: str = Field(default="utf-8", description="Character encoding used")
    is_binary: bool = Field(default=False, description="True if binary content detected")
    truncated: bool = Field(default=False, description="True if content exceeded max read limit")
    read_only: bool = Field(default=False, description="True if file is read-only")
    modified_at: float = Field(default=0.0, description="Last modification timestamp")
    preview: Optional[str] = Field(default=None, description="Base64 preview data URI for images")


class FileSaveRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024, description="Relative file path")
    content: str = Field(..., description="Text content to save")
    backup: Optional[bool] = Field(default=False, description="Create .bak backup copy before write")
    create_parents: Optional[bool] = Field(default=True, description="Automatically create missing parent dirs")
    encoding: Optional[str] = Field(default="utf-8", description="Encoding for file write")


class FileSaveResponse(BaseModel):
    status: str = Field(default="saved", description="Operation status")
    path: str = Field(..., description="Relative file path saved")
    size: int = Field(..., description="Saved file size in bytes")
    modified_at: float = Field(default=0.0, description="New modification timestamp")
    timestamp: Optional[float] = Field(default=None, description="Operation timestamp")


class FileDiffRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Relative file path to compare against")
    new_content: Optional[str] = Field(default=None, description="Modified content to diff against disk")
    content: Optional[str] = Field(default=None, description="Alias for new_content")
    original: Optional[str] = Field(default=None, description="Optional baseline content")
    original_content: Optional[str] = Field(default=None, description="Alias for original")


class FileDiffResponse(BaseModel):
    path: str = Field(..., description="Relative file path")
    diff: str = Field(default="", description="Unified diff string")
    raw_diff: Optional[str] = Field(default=None, description="Raw diff string")
    additions: int = Field(default=0, description="Number of lines added")
    deletions: int = Field(default=0, description="Number of lines removed")
    is_binary: bool = Field(default=False, description="True if either file is binary")
    has_changes: bool = Field(default=False, description="True if differences exist")
    stats: Optional[Dict[str, int]] = Field(default=None, description="Diff statistics")
    hunks: Optional[List[Dict[str, Any]]] = Field(default=None, description="Structured diff hunks")


# =====================================================================
# Workspace File Manager Class
# =====================================================================

class WorkspaceFileManager:
    """Core workspace filesystem manager for AgyMobile."""

    def __init__(
        self,
        root_dir: Optional[Union[str, Path]] = None,
        max_read_size: int = MAX_DEFAULT_READ_SIZE,
    ) -> None:
        if root_dir is not None:
            self.root = Path(root_dir).resolve()
        else:
            s = get_settings()
            self.root = s.WORKSPACE_ROOT.resolve()
        self.sandbox = PathSandbox(self.root)
        self.max_read_size = max_read_size

    def _is_ignored(
        self,
        name: str,
        is_dir: bool,
        ignore_patterns: Optional[Set[str]] = None,
        show_hidden: bool = False,
    ) -> bool:
        """Determine if a file or directory matches noise or ignore patterns."""
        if not show_hidden and name.startswith(".") and name not in (".env.example", ".gitignore", ".gitattributes"):
            return True
        if is_dir and name in DEFAULT_IGNORE_DIRS and not show_hidden:
            return True
        patterns = ignore_patterns or DEFAULT_IGNORE_PATTERNS
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def detect_mime_and_binary(self, path: Path) -> Tuple[str, bool]:
        """Detect MIME type and binary status using developer map and heuristic sampling."""
        ext = path.suffix.lower()
        if ext in DEV_MIME_MAP:
            mime = DEV_MIME_MAP[ext]
            is_bin = ext in KNOWN_BINARY_EXTENSIONS
            return mime, is_bin

        guessed, _ = mimetypes.guess_type(str(path))
        if guessed:
            is_bin = not guessed.startswith("text/") and guessed not in (
                "application/json", "application/xml", "application/javascript", "application/x-yaml", "application/toml"
            )
            return guessed, is_bin

        # Sample inspection if physical file exists
        if path.is_file():
            try:
                with open(path, "rb") as f:
                    chunk = f.read(8192)
                if not chunk:
                    return "text/plain", False
                # Check for null byte not part of UTF-16 BOM
                if b"\x00" in chunk and not (chunk.startswith(b"\xff\xfe") or chunk.startswith(b"\xfe\xff")):
                    return "application/octet-stream", True
                # Control characters density check
                control_chars = bytearray({7, 8, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31})
                if sum(1 for b in chunk if b in control_chars) / len(chunk) > 0.05:
                    return "application/octet-stream", True
            except Exception:
                pass

        return "text/plain", False

    def list_directory(
        self,
        subpath: str = "",
        show_hidden: bool = False,
    ) -> List[Dict[str, Any]]:
        """List immediate contents of a subdirectory in the workspace sandbox."""
        target_dir = self.sandbox.validate_path(subpath)
        if not target_dir.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Directory '{subpath}' not found")
        if not target_dir.is_dir():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Path '{subpath}' is not a directory")

        results: List[Dict[str, Any]] = []
        try:
            with os.scandir(target_dir) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        name = entry.name
                        if self._is_ignored(name, is_dir, show_hidden=show_hidden):
                            continue

                        rel_path = os.path.relpath(entry.path, self.root).replace("\\", "/")
                        stat_info = entry.stat(follow_symlinks=False)
                        mime, is_bin = ("directory", False) if is_dir else self.detect_mime_and_binary(Path(entry.path))
                        ext = Path(name).suffix if not is_dir else ""

                        # Children count for directories
                        children_count = 0
                        if is_dir:
                            try:
                                with os.scandir(entry.path) as sub_it:
                                    children_count = sum(1 for _ in sub_it)
                            except Exception:
                                children_count = 0

                        is_writable = os.access(entry.path, os.W_OK)

                        results.append({
                            "name": name,
                            "path": rel_path,
                            "is_dir": is_dir,
                            "size": 0 if is_dir else stat_info.st_size,
                            "modified": stat_info.st_mtime,
                            "modified_at": stat_info.st_mtime,
                            "extension": ext,
                            "mime": mime,
                            "mime_type": mime,
                            "is_binary": is_bin,
                            "is_readonly": not is_writable,
                            "read_only": not is_writable,
                            "children_count": children_count,
                        })
                    except (OSError, PermissionError):
                        continue
        except PermissionError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied reading directory")

        # Sort: directories first, then alphabetical by lowercase name
        results.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
        return results

    def list_tree(
        self,
        subpath: str = "",
        max_depth: int = 3,
        show_hidden: bool = False,
    ) -> List[Dict[str, Any]]:
        """Recursive directory tree listing bounded by max_depth."""
        target_dir = self.sandbox.validate_path(subpath)
        if not target_dir.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Directory '{subpath}' not found")
        if not target_dir.is_dir():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Path '{subpath}' is not a directory")

        effective_max_depth = max(1, min(10, int(max_depth)))

        def _traverse(current_path: Path, current_depth: int) -> List[Dict[str, Any]]:
            if current_depth > effective_max_depth:
                return []

            nodes: List[Dict[str, Any]] = []
            try:
                with os.scandir(current_path) as it:
                    for entry in it:
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            name = entry.name
                            if self._is_ignored(name, is_dir, show_hidden=show_hidden):
                                continue

                            rel_path = os.path.relpath(entry.path, self.root).replace("\\", "/")
                            stat_info = entry.stat(follow_symlinks=False)
                            mime, is_bin = ("directory", False) if is_dir else self.detect_mime_and_binary(Path(entry.path))
                            ext = Path(name).suffix if not is_dir else ""
                            is_writable = os.access(entry.path, os.W_OK)

                            children = None
                            children_count = 0
                            if is_dir:
                                children = _traverse(Path(entry.path), current_depth + 1)
                                children_count = len(children)

                            nodes.append({
                                "name": name,
                                "path": rel_path,
                                "is_dir": is_dir,
                                "size": 0 if is_dir else stat_info.st_size,
                                "modified": stat_info.st_mtime,
                                "modified_at": stat_info.st_mtime,
                                "extension": ext,
                                "mime": mime,
                                "mime_type": mime,
                                "is_binary": is_bin,
                                "is_readonly": not is_writable,
                                "read_only": not is_writable,
                                "children_count": children_count,
                                "children": children,
                            })
                        except (OSError, PermissionError):
                            continue
            except PermissionError:
                pass

            nodes.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
            return nodes

        return _traverse(target_dir, 1)

    def read_file(
        self,
        subpath: str,
        max_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read workspace file content with MIME detection, encoding fallback, and memory limits."""
        target_file = self.sandbox.validate_path(subpath)
        if not target_file.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File '{subpath}' not found")
        if not target_file.is_file():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Path '{subpath}' is a directory, not a file")

        limit = max_size or self.max_read_size
        file_size = target_file.stat().st_size
        mime, is_bin = self.detect_mime_and_binary(target_file)
        rel_path = os.path.relpath(target_file, self.root).replace("\\", "/")
        is_writable = os.access(target_file, os.W_OK)
        mod_time = target_file.stat().st_mtime

        if is_bin:
            preview_b64 = None
            if mime.startswith("image/") and file_size <= 1024 * 1024:
                try:
                    raw = target_file.read_bytes()
                    preview_b64 = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
                except Exception:
                    pass
            return {
                "path": rel_path,
                "name": target_file.name,
                "is_binary": True,
                "mime": mime,
                "size": file_size,
                "content": None,
                "preview": preview_b64,
                "read_only": not is_writable,
                "is_readonly": not is_writable,
                "modified_at": mod_time,
                "truncated": False,
                "encoding": "binary",
            }

        # Text file read with fallback
        if file_size > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File size ({file_size} bytes) exceeds maximum limit ({limit} bytes)",
            )

        raw_bytes = target_file.read_bytes()
        detected_encoding = "utf-8"
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = raw_bytes.decode("utf-8-sig")
                detected_encoding = "utf-8-sig"
            except UnicodeDecodeError:
                try:
                    content = raw_bytes.decode("latin-1")
                    detected_encoding = "latin-1"
                except Exception:
                    content = raw_bytes.decode("utf-8", errors="replace")
                    detected_encoding = "utf-8-replace"

        return {
            "path": rel_path,
            "name": target_file.name,
            "is_binary": False,
            "mime": mime,
            "size": file_size,
            "content": content,
            "encoding": detected_encoding,
            "read_only": not is_writable,
            "is_readonly": not is_writable,
            "modified_at": mod_time,
            "truncated": False,
        }

    def save_file(
        self,
        subpath: str,
        content: str,
        backup: bool = False,
        allow_binary: bool = False,
    ) -> Dict[str, Any]:
        """Atomically persist text content to a file in the workspace sandbox."""
        if not subpath or not str(subpath).strip():
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File path cannot be empty")

        target_file = self.sandbox.validate_path(subpath)
        if target_file.resolve() == self.root.resolve():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot save to workspace root")
        if target_file.exists() and target_file.is_dir():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target path is a directory")

        if target_file.exists():
            if not allow_binary:
                _, is_bin = self.detect_mime_and_binary(target_file)
                if is_bin:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot overwrite binary file with text")
            if not os.access(target_file, os.W_OK):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="File is read-only on filesystem")

        target_file.parent.mkdir(parents=True, exist_ok=True)

        if backup and target_file.exists():
            bak_path = target_file.with_suffix(target_file.suffix + ".bak")
            try:
                bak_path.write_bytes(target_file.read_bytes())
            except Exception:
                pass

        # Windows NTFS safe atomic write:
        # Create temp file in the same parent directory, flush, fsync, close, and os.replace
        tmp_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_file.parent,
            delete=False,
            prefix=".agy_tmp_",
        )
        tmp_name = tmp_file.name
        try:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_file.close()

            # Retry loop for Windows transient file lock contention (antivirus, search indexers)
            max_retries = 5
            last_err = None
            for attempt in range(max_retries):
                try:
                    os.replace(tmp_name, target_file)
                    last_err = None
                    break
                except (PermissionError, OSError) as exc:
                    last_err = exc
                    if attempt < max_retries - 1:
                        time.sleep(0.02 * (2 ** attempt))

            if last_err is not None:
                if isinstance(last_err, PermissionError):
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied writing file")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to save file: {str(last_err)}",
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save file: {str(exc)}",
            )
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

        rel_path = os.path.relpath(target_file, self.root).replace("\\", "/")
        now = time.time()
        return {
            "status": "saved",
            "path": rel_path,
            "size": len(content.encode("utf-8")),
            "modified_at": now,
            "timestamp": now,
        }

    def generate_diff(
        self,
        subpath: str,
        modified_content: str,
        original_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate unified diff and structured hunks for mobile diff viewer."""
        target_file = self.sandbox.validate_path(subpath)
        rel_path = os.path.relpath(target_file, self.root).replace("\\", "/")

        if original_content is None:
            if target_file.exists() and target_file.is_file():
                _, is_bin = self.detect_mime_and_binary(target_file)
                if is_bin:
                    return {
                        "path": rel_path,
                        "diff": "",
                        "raw_diff": "",
                        "has_changes": False,
                        "is_binary": True,
                        "error": "Binary files cannot be diffed as text",
                        "additions": 0,
                        "deletions": 0,
                        "stats": {"additions": 0, "deletions": 0, "total_changes": 0},
                        "hunks": [],
                    }
                original_content = target_file.read_text(encoding="utf-8", errors="replace")
            else:
                original_content = ""

        old_lines = original_content.splitlines(keepends=True)
        new_lines = modified_content.splitlines(keepends=True)

        diff_lines = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            n=3,
        ))
        raw_diff = "".join(diff_lines)
        return self._parse_unified_diff(raw_diff, rel_path)

    @staticmethod
    def _parse_unified_diff(raw_diff: str, file_path: str) -> Dict[str, Any]:
        """Parse unified diff string into structured JSON hunks."""
        lines = raw_diff.splitlines()
        hunks: List[Dict[str, Any]] = []
        current_hunk: Optional[Dict[str, Any]] = None
        additions = 0
        deletions = 0
        hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(?: (.*))?")

        old_line_num = 0
        new_line_num = 0

        for line in lines:
            if line.startswith("---") or line.startswith("+++"):
                continue
            m = hunk_header_re.match(line)
            if m:
                if current_hunk is not None:
                    hunks.append(current_hunk)
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) is not None else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) is not None else 1
                old_line_num = old_start
                new_line_num = new_start
                current_hunk = {
                    "header": line,
                    "old_start": old_start,
                    "old_lines": old_count,
                    "new_start": new_start,
                    "new_lines": new_count,
                    "lines": [],
                }
            elif current_hunk is not None:
                if line.startswith("+"):
                    additions += 1
                    current_hunk["lines"].append({
                        "type": "add",
                        "content": line[1:],
                        "old_line": None,
                        "new_line": new_line_num,
                    })
                    new_line_num += 1
                elif line.startswith("-"):
                    deletions += 1
                    current_hunk["lines"].append({
                        "type": "delete",
                        "content": line[1:],
                        "old_line": old_line_num,
                        "new_line": None,
                    })
                    old_line_num += 1
                elif line.startswith(" "):
                    current_hunk["lines"].append({
                        "type": "context",
                        "content": line[1:],
                        "old_line": old_line_num,
                        "new_line": new_line_num,
                    })
                    old_line_num += 1
                    new_line_num += 1
                elif line.startswith("\\"):
                    current_hunk["lines"].append({
                        "type": "marker",
                        "content": line,
                        "old_line": None,
                        "new_line": None,
                    })

        if current_hunk is not None:
            hunks.append(current_hunk)

        return {
            "path": file_path,
            "diff": raw_diff,
            "raw_diff": raw_diff,
            "has_changes": (additions + deletions) > 0,
            "additions": additions,
            "deletions": deletions,
            "is_binary": False,
            "stats": {
                "additions": additions,
                "deletions": deletions,
                "total_changes": additions + deletions,
            },
            "hunks": hunks,
        }


# Singleton instance
workspace_file_manager = WorkspaceFileManager()


# =====================================================================
# FastAPI REST Router for Workspace Files (`/api/files`)
# =====================================================================

router = APIRouter(prefix="/api/files", tags=["Workspace"])


def get_file_manager(request: Request) -> WorkspaceFileManager:
    """Dependency injection helper to obtain WorkspaceFileManager with active settings."""
    app_state = getattr(getattr(request, "app", None), "state", None)
    active_settings: Settings = getattr(app_state, "settings", None) or get_settings()
    return WorkspaceFileManager(root_dir=active_settings.WORKSPACE_ROOT)


@router.get("/tree", response_model=List[FileNode])
async def get_file_tree(
    path: Optional[str] = Query("", description="Relative subdirectory path"),
    depth: Optional[int] = Query(1, description="Depth: 1 for shallow listing, >1 for recursive"),
    show_hidden: Optional[bool] = Query(False, description="Include hidden files"),
    include_hidden: Optional[bool] = Query(False, description="Alias for show_hidden"),
    current_user: dict = Depends(get_current_user),
    fm: WorkspaceFileManager = Depends(get_file_manager),
):
    """List directory files and folders within workspace sandbox."""
    allow_hidden = show_hidden or include_hidden
    if depth and depth > 1:
        return fm.list_tree(subpath=path or "", max_depth=depth, show_hidden=allow_hidden)
    return fm.list_directory(subpath=path or "", show_hidden=allow_hidden)


@router.get("/content", response_model=FileContentResponse)
async def get_file_content(
    path: str = Query(..., description="Relative file path"),
    max_bytes: Optional[int] = Query(None, description="Max read bytes limit"),
    current_user: dict = Depends(get_current_user),
    fm: WorkspaceFileManager = Depends(get_file_manager),
):
    """Read file content with MIME detection, encoding fallback, and size limits."""
    return fm.read_file(subpath=path, max_size=max_bytes)


@router.post("/save", response_model=FileSaveResponse)
async def save_file_content(
    body: FileSaveRequest,
    current_user: dict = Depends(get_current_user),
    fm: WorkspaceFileManager = Depends(get_file_manager),
):
    """Atomically save text content to a file in workspace sandbox."""
    return fm.save_file(subpath=body.path, content=body.content, backup=bool(body.backup))


@router.post("/diff", response_model=FileDiffResponse)
async def post_file_diff(
    body: FileDiffRequest,
    current_user: dict = Depends(get_current_user),
    fm: WorkspaceFileManager = Depends(get_file_manager),
):
    """Generate visual unified diff between updated content and disk state."""
    new_text = body.new_content if body.new_content is not None else (body.content or "")
    base_text = body.original_content if body.original_content is not None else body.original
    return fm.generate_diff(subpath=body.path, modified_content=new_text, original_content=base_text)


@router.get("/diff", response_model=FileDiffResponse)
async def get_file_diff(
    path: str = Query(..., description="Relative file path"),
    content: Optional[str] = Query(None, description="Modified content to diff against"),
    current_user: dict = Depends(get_current_user),
    fm: WorkspaceFileManager = Depends(get_file_manager),
):
    """Generate visual diff for a file path."""
    return fm.generate_diff(subpath=path, modified_content=content or "")
