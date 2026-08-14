"""
backend/session_hub/sqlite_reader.py

High-performance, read-only query engine for Antigravity 2.0 SQLite databases
(conversation_summaries.db + individual conversations/*.db).
Enforces zero-lock concurrency via PRAGMA query_only = ON, busy timeouts, and
read-only URI file handles.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from backend.config import get_settings

logger = logging.getLogger(__name__)


def _parse_iso_timestamp(ts_str: Optional[str]) -> Optional[float]:
    """Parse ISO or SQL datetime string into Unix epoch timestamp."""
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        if "." in clean:
            parts = clean.split(".")
            sec = parts[0]
            rest = parts[1]
            tz_part = ""
            if "+" in rest:
                rest, tz_part = rest.split("+", 1)
                tz_part = "+" + tz_part
            elif "-" in rest:
                rest, tz_part = rest.split("-", 1)
                tz_part = "-" + tz_part
            clean = f"{sec}.{rest[:6]}{tz_part}"
        dt = datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        try:
            return float(ts_str)
        except Exception:
            return None


@dataclass
class ConversationSummary:
    id: str
    title: str = ""
    preview: str = ""
    step_count: int = 0
    last_modified: Optional[float] = None
    last_modified_time: Optional[str] = None
    workspace_uris: List[str] = field(default_factory=list)
    status: str = "active"
    agent_name: Optional[str] = None
    parent_id: Optional[str] = None
    nesting_depth: int = 0
    killed: bool = False
    last_user_input_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationDetail(ConversationSummary):
    steps_meta: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    subagents: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SubagentNode:
    id: str
    title: str = ""
    agent_name: Optional[str] = None
    status: str = "active"
    step_count: int = 0
    nesting_depth: int = 0
    last_modified: Optional[float] = None
    parent_id: Optional[str] = None
    children: List[SubagentNode] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "agent_name": self.agent_name,
            "status": self.status,
            "step_count": self.step_count,
            "nesting_depth": self.nesting_depth,
            "last_modified": self.last_modified,
            "parent_id": self.parent_id,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class HubStats:
    total_conversations: int = 0
    active_conversations: int = 0
    total_steps: int = 0
    subagents_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AntigravitySqliteReader:
    """Read-only SQLite query engine for Antigravity 2.0 conversations."""

    def __init__(
        self,
        gemini_home: Optional[Union[str, Path]] = None,
        antigravity_dir: Optional[Union[str, Path]] = None,
        busy_timeout_ms: int = 3000,
    ) -> None:
        if antigravity_dir:
            self.antigravity_dir = Path(antigravity_dir).resolve()
        else:
            env_dir = os.environ.get("ANTIGRAVITY_HOME") or os.environ.get("AGY_ANTIGRAVITY_DIR")
            if env_dir:
                self.antigravity_dir = Path(env_dir).resolve()
            else:
                try:
                    self.antigravity_dir = get_settings().ANTIGRAVITY_DIR.resolve()
                except Exception:
                    self.antigravity_dir = (Path.home() / ".gemini" / "antigravity").resolve()

        if gemini_home:
            self.gemini_home = Path(gemini_home).resolve()
        else:
            env_gemini = os.environ.get("GEMINI_HOME")
            if env_gemini:
                self.gemini_home = Path(env_gemini).resolve()
            else:
                self.gemini_home = self.antigravity_dir.parent.resolve()

        self.busy_timeout_ms = busy_timeout_ms

    def _get_ro_connection(self, db_path: Union[str, Path]) -> Optional[sqlite3.Connection]:
        """Open a database file with read-only pragma settings."""
        p = Path(db_path).resolve()
        if not p.is_file():
            return None

        # Check if file has SQLite header
        try:
            with open(p, "rb") as f:
                header = f.read(16)
                if not header.startswith(b"SQLite format 3"):
                    logger.warning(f"File {p} is not a valid SQLite database (header mismatch).")
                    return None
        except Exception as e:
            logger.warning(f"Could not read database header for {p}: {e}")
            return None

        uri = f"file:{p.as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=self.busy_timeout_ms / 1000.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON;")
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA temp_store = MEMORY;")
            conn.execute("PRAGMA locking_mode = NORMAL;")
            return conn
        except sqlite3.Error as e:
            logger.warning(f"Failed to open read-only connection to {p}: {e}")
            return None

    def _find_summaries_db(self, explicit_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
        """Resolve the location of conversation_summaries.db."""
        if explicit_path:
            p = Path(explicit_path).resolve()
            if p.is_file():
                return p
            if p.is_dir() and (p / "conversation_summaries.db").is_file():
                return p / "conversation_summaries.db"

        candidates = [
            self.antigravity_dir / "conversation_summaries.db",
            self.gemini_home / "antigravity" / "conversation_summaries.db",
            self.gemini_home / "antigravity-cli" / "conversation_summaries.db",
            self.gemini_home / "antigravity-ide" / "conversation_summaries.db",
        ]

        for cand in candidates:
            if cand.is_file():
                return cand

        return None

    def _find_conversations_dir(self, explicit_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
        """Resolve the directory containing individual <conversation_id>.db files."""
        if explicit_path:
            p = Path(explicit_path).resolve()
            if p.is_dir() and (p / "conversations").is_dir():
                return p / "conversations"
            if p.is_dir() and p.name == "conversations":
                return p
            if p.is_file() and p.parent.name == "conversations":
                return p.parent

        candidates = [
            self.antigravity_dir / "conversations",
            self.gemini_home / "antigravity" / "conversations",
            self.gemini_home / "antigravity-cli" / "conversations",
            self.gemini_home / "antigravity-ide" / "conversations",
        ]

        for cand in candidates:
            if cand.is_dir():
                return cand

        return None

    def _query_conversations(
        self,
        db_or_dir: Optional[Union[str, Path]] = None,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        summaries_path = self._find_summaries_db(db_or_dir)
        results: List[Dict[str, Any]] = []
        known_summary_ids = set()

        if summaries_path:
            conn = self._get_ro_connection(summaries_path)
            if conn:
                try:
                    # Index all known conversation IDs from summaries to avoid Layer 2 duplicates
                    try:
                        cur_all = conn.cursor()
                        cur_all.execute("SELECT conversation_id FROM conversation_summaries")
                        for r in cur_all.fetchall():
                            if r[0]:
                                known_summary_ids.add(r[0])
                    except Exception:
                        pass

                    query = """
                        SELECT conversation_id, title, preview, step_count, last_modified_time,
                               workspace_uris, status, agent_name, parent_conversation_id,
                               nesting_depth, killed, last_user_input_time
                        FROM conversation_summaries
                        WHERE 1=1
                    """
                    params: List[Any] = []

                    if status:
                        query += " AND status = ?"
                        params.append(status)

                    if parent_id is not None:
                        if parent_id == "" or parent_id == "root":
                            query += " AND (parent_conversation_id IS NULL OR parent_conversation_id = '')"
                        else:
                            query += " AND parent_conversation_id = ?"
                            params.append(parent_id)

                    if search:
                        query += " AND (title LIKE ? OR preview LIKE ? OR agent_name LIKE ?)"
                        wildcard = f"%{search}%"
                        params.extend([wildcard, wildcard, wildcard])

                    query += " ORDER BY last_modified_time DESC"

                    cursor = conn.cursor()
                    cursor.execute(query, params)
                    rows = cursor.fetchall()

                    for r in rows:
                        cid = r["conversation_id"]

                        workspace_uris = []
                        if r["workspace_uris"]:
                            try:
                                workspace_uris = json.loads(r["workspace_uris"])
                            except Exception:
                                workspace_uris = [r["workspace_uris"]]

                        last_mod_str = r["last_modified_time"]
                        last_mod_epoch = _parse_iso_timestamp(last_mod_str)

                        item = {
                            "id": cid,
                            "title": r["title"] or "Untitled Conversation",
                            "preview": r["preview"] or "",
                            "step_count": r["step_count"] or 0,
                            "last_modified": last_mod_epoch,
                            "last_modified_time": last_mod_str or "",
                            "workspace_uris": workspace_uris,
                            "status": r["status"] or "active",
                            "agent_name": r["agent_name"] or "",
                            "parent_id": r["parent_conversation_id"] or None,
                            "nesting_depth": r["nesting_depth"] or 0,
                            "killed": bool(r["killed"]) if r["killed"] is not None else False,
                            "last_user_input_time": r["last_user_input_time"] or "",
                        }
                        results.append(item)
                except Exception as e:
                    logger.warning(f"Error querying conversation_summaries table: {e}")
                finally:
                    conn.close()

        # Secondary Layer: Scan filesystem for any standalone conversation DBs not in summaries
        convs_dir = self._find_conversations_dir(db_or_dir)
        if convs_dir and convs_dir.is_dir():
            try:
                for db_file in convs_dir.glob("*.db"):
                    cid = db_file.stem
                    # Skip if already tracked in summaries
                    if cid in known_summary_ids:
                        continue

                    # Apply filters for orphan DBs
                    if parent_id is not None and parent_id not in ("", "root"):
                        continue
                    if status and status != "active":
                        continue
                    if search:
                        title_candidate = f"Session {cid[:8]}".lower()
                        if search.lower() not in cid.lower() and search.lower() not in title_candidate:
                            continue

                    conn = self._get_ro_connection(db_file)
                    if conn:
                        try:
                            step_count = 0
                            cur = conn.cursor()
                            try:
                                cur.execute("SELECT COUNT(*) FROM steps")
                                row = cur.fetchone()
                                if row:
                                    step_count = row[0]
                            except Exception:
                                pass

                            mtime = db_file.stat().st_mtime
                            iso_mtime = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

                            known_summary_ids.add(cid)
                            results.append({
                                "id": cid,
                                "title": f"Session {cid[:8]}",
                                "preview": "",
                                "step_count": step_count,
                                "last_modified": mtime,
                                "last_modified_time": iso_mtime,
                                "workspace_uris": [],
                                "status": "active",
                                "agent_name": "",
                                "parent_id": None,
                                "nesting_depth": 0,
                                "killed": False,
                                "last_user_input_time": iso_mtime,
                            })
                        except Exception:
                            pass
                        finally:
                            conn.close()
            except Exception as e:
                logger.warning(f"Error scanning conversations directory {convs_dir}: {e}")

        # Sort results by last_modified descending
        results.sort(
            key=lambda x: (x.get("last_modified") or 0.0),
            reverse=True,
        )

        # Apply pagination if requested
        if offset > 0 or limit is not None:
            return results[offset : offset + limit if limit else None]

        return results

    def list_conversations(
        self_or_path: Optional[Union[AntigravitySqliteReader, str, Path]] = None,
        db_or_dir: Optional[Union[str, Path]] = None,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """
        List conversations ordered by last_modified descending.
        Works both as reader.list_conversations() and SQLiteReader.list_conversations(path).
        """
        if isinstance(self_or_path, AntigravitySqliteReader):
            instance = self_or_path
            target_path = db_or_dir
        else:
            instance = AntigravitySqliteReader()
            target_path = self_or_path if isinstance(self_or_path, (str, Path)) else db_or_dir

        return instance._query_conversations(
            db_or_dir=target_path,
            limit=limit,
            offset=offset,
            search=search,
            status=status,
            parent_id=parent_id,
        )

    def get_conversation(
        self_or_cid: Union[AntigravitySqliteReader, str],
        cid: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get complete details for a single conversation."""
        if isinstance(self_or_cid, AntigravitySqliteReader):
            instance = self_or_cid
            conversation_id = cid or ""
        else:
            instance = AntigravitySqliteReader()
            conversation_id = self_or_cid

        all_convs = instance.list_conversations(limit=10000)
        found = next((c for c in all_convs if c["id"] == conversation_id), None)

        if not found:
            convs_dir = instance._find_conversations_dir()
            if convs_dir:
                db_path = convs_dir / f"{conversation_id}.db"
                if db_path.is_file():
                    step_count = instance.get_conversation_steps_count(conversation_id)
                    mtime = db_path.stat().st_mtime
                    found = {
                        "id": conversation_id,
                        "title": f"Session {conversation_id[:8]}",
                        "preview": "",
                        "step_count": step_count,
                        "last_modified": mtime,
                        "last_modified_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                        "workspace_uris": [],
                        "status": "active",
                        "agent_name": "",
                        "parent_id": None,
                        "nesting_depth": 0,
                        "killed": False,
                        "last_user_input_time": "",
                    }

        if not found:
            return None

        steps_meta = instance.get_steps_meta(conversation_id)
        subagents = instance.get_subagents(conversation_id)

        detail = dict(found)
        detail["steps_meta"] = steps_meta
        detail["subagents"] = subagents
        return detail

    def get_subagents(
        self_or_pid: Union[AntigravitySqliteReader, str],
        pid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get direct child subagent conversations for a parent conversation."""
        if isinstance(self_or_pid, AntigravitySqliteReader):
            instance = self_or_pid
            parent_id = pid or ""
        else:
            instance = AntigravitySqliteReader()
            parent_id = self_or_pid

        all_convs = instance.list_conversations(limit=10000)
        return [c for c in all_convs if c.get("parent_id") == parent_id]

    def get_subagent_tree(
        self_or_cid: Union[AntigravitySqliteReader, str],
        root_id_or_depth: Optional[Union[str, int]] = None,
        max_depth: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """Build hierarchical subagent tree starting at root_conversation_id."""
        if isinstance(self_or_cid, AntigravitySqliteReader):
            instance = self_or_cid
            root_conversation_id = str(root_id_or_depth) if root_id_or_depth is not None else ""
            depth = max_depth
        else:
            instance = AntigravitySqliteReader()
            root_conversation_id = self_or_cid
            depth = int(root_id_or_depth) if isinstance(root_id_or_depth, int) else max_depth

        root_conv = instance.get_conversation(root_conversation_id)
        if not root_conv:
            return None

        node = {
            "id": root_conv["id"],
            "title": root_conv["title"],
            "agent_name": root_conv["agent_name"],
            "status": root_conv["status"],
            "step_count": root_conv["step_count"],
            "nesting_depth": root_conv["nesting_depth"],
            "last_modified": root_conv["last_modified"],
            "parent_id": root_conv["parent_id"],
            "children": [],
        }

        if depth <= 0:
            return node

        children = instance.get_subagents(root_conversation_id)
        for child in children:
            child_tree = instance.get_subagent_tree(child["id"], max_depth=depth - 1)
            if child_tree:
                node["children"].append(child_tree)

        return node

    def get_steps_meta(
        self_or_cid: Union[AntigravitySqliteReader, str],
        cid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve step metadata from <conversation_id>.db steps table."""
        if isinstance(self_or_cid, AntigravitySqliteReader):
            instance = self_or_cid
            conversation_id = cid or ""
        else:
            instance = AntigravitySqliteReader()
            conversation_id = self_or_cid

        convs_dir = instance._find_conversations_dir()
        if not convs_dir:
            return []

        db_path = convs_dir / f"{conversation_id}.db"
        conn = instance._get_ro_connection(db_path)
        if not conn:
            return []

        steps: List[Dict[str, Any]] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT idx, step_type, status, has_subtrajectory
                FROM steps
                ORDER BY idx ASC
            """)
            for row in cur.fetchall():
                steps.append({
                    "idx": row["idx"],
                    "step_type": row["step_type"],
                    "status": row["status"],
                    "has_subtrajectory": bool(row["has_subtrajectory"]),
                })
        except Exception as e:
            logger.warning(f"Error fetching steps for {conversation_id}: {e}")
        finally:
            conn.close()

        return steps

    def get_conversation_steps_count(
        self_or_cid: Union[AntigravitySqliteReader, str],
        cid: Optional[str] = None,
    ) -> int:
        """Count total steps recorded in conversation database."""
        if isinstance(self_or_cid, AntigravitySqliteReader):
            instance = self_or_cid
            conversation_id = cid or ""
        else:
            instance = AntigravitySqliteReader()
            conversation_id = self_or_cid

        steps = instance.get_steps_meta(conversation_id)
        return len(steps)

    def get_hub_stats(self_or_none: Optional[AntigravitySqliteReader] = None) -> Dict[str, Any]:
        """Aggregate statistical summary across all conversations."""
        instance = self_or_none if isinstance(self_or_none, AntigravitySqliteReader) else AntigravitySqliteReader()
        convs = instance.list_conversations(limit=10000)
        total = len(convs)
        active = sum(1 for c in convs if c.get("status") in ("active", "running"))
        total_steps = sum(c.get("step_count", 0) for c in convs)
        subagents = sum(1 for c in convs if c.get("parent_id"))

        return {
            "total_conversations": total,
            "active_conversations": active,
            "total_steps": total_steps,
            "subagents_count": subagents,
        }


SQLiteReader = AntigravitySqliteReader
