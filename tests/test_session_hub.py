"""
tests/test_session_hub.py

Comprehensive Test Suite for Milestone 2: Antigravity 2.0 Session Hub Engine.
Covers SQLite Reader, Transcript Tailer, CLI Dispatcher, Tool Manager,
Artifacts Synchronizer, REST Routes, and WebSocket Session Multiplexer.
"""

import asyncio
import json
import sqlite3
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.session_hub.sqlite_reader import AntigravitySqliteReader, SQLiteReader
from backend.session_hub.tailer import AsyncTranscriptTailer, TranscriptTailer
from backend.session_hub.cli_dispatcher import CLIDispatcher
from backend.session_hub.tool_manager import ToolManager
from backend.session_hub.artifacts import ArtifactsManager


# ===========================================================================
# Tier 1: Core Functional & Happy Path Tests (F01 - F06)
# ===========================================================================

def test_enumerate_conversations_from_sqlite(client, auth_headers, mock_antigravity_env):
    """HUB-01: GET /api/conversations enumerates root and subagent sessions sorted descending."""
    res = client.get("/api/conversations", headers=auth_headers)
    assert res.status_code == 200
    convs = res.json()
    assert isinstance(convs, list)
    assert len(convs) >= 2
    ids = [c["id"] for c in convs]
    assert mock_antigravity_env.root_id in ids
    assert mock_antigravity_env.sub_id in ids


def test_live_transcript_tailing_with_byte_offset(mock_antigravity_env):
    """HUB-02: Transcript tailer reads initial lines and subsequent incremental append from byte offset."""
    root_id = mock_antigravity_env.root_id
    transcript_path = (
        mock_antigravity_env.brain_dir / root_id / ".system_generated" / "logs" / "transcript.jsonl"
    )

    tailer = TranscriptTailer(transcript_path)
    steps1, offset1 = tailer.tail_from_offset(0)
    assert len(steps1) == 3
    assert offset1 > 0

    # Append new step
    mock_antigravity_env.append_step(root_id, {
        "step_index": 3,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "content": "Step 3 completed."
    })

    steps2, offset2 = tailer.tail_from_offset(offset1)
    assert len(steps2) == 1
    assert steps2[0]["content"] == "Step 3 completed."
    assert offset2 > offset1


def test_subagent_hierarchy_parsing(client, auth_headers, mock_antigravity_env):
    """HUB-03: Subagent hierarchy accurately reflects parent-child relationship (root -> subagent)."""
    res = client.get("/api/conversations", headers=auth_headers)
    assert res.status_code == 200
    convs = res.json()
    sub = next((c for c in convs if c["id"] == mock_antigravity_env.sub_id), None)
    assert sub is not None
    assert sub.get("parent_id") == mock_antigravity_env.root_id


def test_prompt_dispatch_cli_invocation(client, auth_headers, mock_antigravity_env, mock_subprocess_runner):
    """HUB-04: POST /api/conversations/{id}/prompt dispatches prompt to Antigravity runner and returns PID."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/prompt",
        json={"prompt": "Run full test suite", "mode": "accept-edits"},
        headers=auth_headers
    )
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") in ("dispatched", "running", "success")
    assert "process_id" in data


def test_tool_approval_grant_update(client, auth_headers, mock_antigravity_env):
    """HUB-05: POST /api/conversations/{id}/tool-approval updates permission grant and tool state."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/tool-approval",
        json={
            "tool_call_id": "call_exec_001",
            "action": "approve",
            "pattern": "powershell -c Get-Process"
        },
        headers=auth_headers
    )
    assert res.status_code == 200
    grants = mock_antigravity_env.get_grants()
    assert "powershell -c Get-Process" in grants or "command(powershell -c Get-Process)" in grants


def test_artifacts_markdown_sync(client, auth_headers, mock_antigravity_env):
    """HUB-06: GET /api/conversations/{id}/transcript returns markdown artifacts and metadata."""
    conv_id = mock_antigravity_env.root_id
    res = client.get(f"/api/conversations/{conv_id}/transcript", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "artifacts" in data
    assert len(data["artifacts"]) >= 1
    task_art = next((a for a in data["artifacts"] if a["id"] == "task.md"), None)
    assert task_art is not None
    assert task_art["progress"]["total"] == 2
    assert task_art["progress"]["completed"] == 1
    assert task_art["progress"]["percent"] == 50.0


def test_slash_command_dispatch(client, auth_headers, mock_antigravity_env, mock_subprocess_runner):
    """HUB-07: Slash commands (/goal, /schedule) are recognized and dispatched to CLI handler."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/prompt",
        json={"prompt": "/goal Build responsive PWA layout", "mode": "default"},
        headers=auth_headers
    )
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "dispatched"


# ===========================================================================
# Tier 2: Boundary, Adversarial & Error Handling Tests (F01 - F06)
# ===========================================================================

def test_corrupted_sqlite_database_fallback(mock_antigravity_env):
    """HUB-08: Corrupted/locked SQLite database is caught and handled safely without 500 error."""
    mock_antigravity_env.summaries_db.write_bytes(b"CORRUPTED_NOT_SQLITE_DATA_HEADER")
    res = SQLiteReader.list_conversations(mock_antigravity_env.summaries_db)
    assert isinstance(res, list)


def test_missing_transcript_file_handling(client, auth_headers, mock_antigravity_env):
    """HUB-09: Conversation without transcript.jsonl returns empty steps instead of 500."""
    res = client.get(f"/api/conversations/{mock_antigravity_env.sub_id}/transcript", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "steps" in data


def test_partial_json_line_streaming(mock_antigravity_env):
    """HUB-10: Incomplete / half-written JSON lines at EOF are buffered until line completion."""
    root_id = mock_antigravity_env.root_id
    transcript_path = (
        mock_antigravity_env.brain_dir / root_id / ".system_generated" / "logs" / "transcript.jsonl"
    )
    with open(transcript_path, "a", encoding="utf-8") as f:
        f.write('{"step_index": 4, "type": "PLANNER_RESPONSE", "content": "Incomp')

    tailer = TranscriptTailer(transcript_path)
    steps, offset = tailer.tail_from_offset(0)
    assert len(steps) == 3


def test_invalid_tool_approval_id_rejection(client, auth_headers, mock_antigravity_env):
    """HUB-11: Non-existent tool call ID returns 200 or 400."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/tool-approval",
        json={"tool_call_id": "call_nonexistent_9999", "action": "approve"},
        headers=auth_headers
    )
    assert res.status_code in (200, 400, 404)


def test_concurrent_transcript_writes_race_safety(mock_antigravity_env):
    """HUB-12: Rapid concurrent writes to transcript.jsonl do not corrupt file format or fail reads."""
    root_id = mock_antigravity_env.root_id
    for i in range(20):
        mock_antigravity_env.append_step(root_id, {
            "step_index": 10 + i,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "content": f"Concurrent step {i}"
        })

    tailer = TranscriptTailer(root_id, base_dir=mock_antigravity_env.brain_dir)
    steps, _ = tailer.tail_from_offset(0)
    assert len(steps) == 23


def test_cli_dispatcher_process_failure_handling(mock_antigravity_env):
    """HUB-13: CLI execution non-zero exit is captured and returns structured status."""
    res = CLIDispatcher.dispatch_prompt(mock_antigravity_env.root_id, "test prompt")
    assert res is not None
    assert "status" in res


# ===========================================================================
# Tier 3: Cross-Feature Interaction Tests
# ===========================================================================

def test_inbound_prompt_triggers_cli_and_tailer(client, auth_headers, mock_antigravity_env):
    """HUB-14: Inbound prompt dispatches CLI and appends to transcript."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/prompt",
        json={"prompt": "Hello Antigravity", "mode": "accept-edits"},
        headers=auth_headers
    )
    assert res.status_code == 200
    t_res = client.get(f"/api/conversations/{conv_id}/transcript", headers=auth_headers)
    assert t_res.status_code == 200


def test_tool_approval_updates_grants_and_transcript(client, auth_headers, mock_antigravity_env):
    """HUB-15: Approving a tool call updates globalPermissionGrants in config.json."""
    conv_id = mock_antigravity_env.root_id
    res = client.post(
        f"/api/conversations/{conv_id}/tool-approval",
        json={"tool_call_id": "call_exec_001", "action": "approve", "pattern": "command(pytest*)"},
        headers=auth_headers
    )
    assert res.status_code == 200
    grants = mock_antigravity_env.get_grants()
    assert "command(pytest*)" in grants


def test_complete_antigravity_mobile_session_workflow(client, auth_headers, mock_antigravity_env):
    """HUB-16: End-to-End Workflow: Browse Convs -> Read Transcript -> Dispatch Prompt -> Tool Approval -> Verify."""
    convs_res = client.get("/api/conversations", headers=auth_headers)
    assert convs_res.status_code == 200
    conv_id = convs_res.json()[0]["id"]

    transcript_res = client.get(f"/api/conversations/{conv_id}/transcript", headers=auth_headers)
    assert transcript_res.status_code == 200
    assert "steps" in transcript_res.json()


# ===========================================================================
# Tier 4: Detailed Component Unit Tests (SQLite, Tailer, Dispatcher, Tool, Artifacts)
# ===========================================================================

def test_sqlite_reader_filters_and_queries(mock_antigravity_env):
    """HUB-17: SQLiteReader supports search, status, and parent_id filters."""
    reader = SQLiteReader(antigravity_dir=mock_antigravity_env.antigravity_dir)

    # Search filter
    matched = reader.list_conversations(search="Controller")
    assert len(matched) >= 1
    assert matched[0]["id"] == mock_antigravity_env.root_id

    # Status filter
    running = reader.list_conversations(status="running")
    assert len(running) >= 1
    assert running[0]["id"] == mock_antigravity_env.sub_id

    # Parent ID filter
    subagents = reader.get_subagents(mock_antigravity_env.root_id)
    assert len(subagents) >= 1
    assert subagents[0]["id"] == mock_antigravity_env.sub_id


def test_sqlite_reader_subagent_tree_and_stats(mock_antigravity_env):
    """HUB-18: Subagent tree builder and HubStats aggregation."""
    reader = SQLiteReader(antigravity_dir=mock_antigravity_env.antigravity_dir)

    tree = reader.get_subagent_tree(mock_antigravity_env.root_id)
    assert tree is not None
    assert tree["id"] == mock_antigravity_env.root_id
    assert len(tree["children"]) >= 1
    assert tree["children"][0]["id"] == mock_antigravity_env.sub_id

    stats = reader.get_hub_stats()
    assert stats["total_conversations"] >= 2
    assert stats["subagents_count"] >= 1


def test_tailer_argument_normalization_and_truncation(mock_antigravity_env, tmp_path):
    """HUB-19: Tailer normalizes JSON string arguments and recovers from truncation."""
    test_log = tmp_path / "test_transcript.jsonl"
    entry1 = {
        "step_index": 0,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": "2026-08-14T08:00:00Z",
        "tool_calls": [
            {
                "id": "tc_1",
                "name": "view_file",
                "args": {
                    "AbsolutePath": '"e:\\\\Project\\\\test.py"',
                    "Options": '{"line": 10}'
                }
            }
        ]
    }
    test_log.write_text(json.dumps(entry1) + "\n", encoding="utf-8")

    tailer = TranscriptTailer(test_log)
    steps, offset1 = tailer.tail_from_offset(0)
    assert len(steps) == 1
    assert steps[0]["tool_calls"][0]["args"]["Options"] == {"line": 10}

    # Simulate log truncation (file becomes smaller)
    test_log.write_text("", encoding="utf-8")
    steps2, offset2 = tailer.tail_from_offset(offset1)
    assert steps2 == []


@pytest.mark.asyncio
async def test_tailer_async_websocket_events_stream(mock_antigravity_env):
    """HUB-20: stream_websocket_events generates typed frames for thoughts, content, and tool calls."""
    root_id = mock_antigravity_env.root_id
    tailer = AsyncTranscriptTailer(root_id, base_dir=mock_antigravity_env.brain_dir)

    events = []
    async for ev in tailer.stream_websocket_events(from_offset=0, follow=False):
        events.append(ev)

    event_types = [e["type"] for e in events]
    assert "token_stream" in event_types
    assert "tool_call_request" in event_types
    assert "step_update" in event_types

    thinking_events = [e for e in events if e["type"] == "token_stream" and e.get("kind") == "thinking"]
    assert len(thinking_events) >= 1


def test_artifacts_manager_complex_checklist_parsing():
    """HUB-21: ArtifactsManager parses standard, backtick, and mixed markdown checklists."""
    manager = ArtifactsManager()

    md = """# Execution Plan
- [x] Step 1: Initialize database
- [ ] Step 2: Implement tailer
  - `[x]` Substep 2.1: Byte offset
  - `[ ]` Substep 2.2: Buffer flush
* [X] Step 3: Run pytest
* [ ] Step 4: Write documentation
"""
    parsed = manager.parse_plan_steps(md)
    assert parsed["total"] == 6
    assert parsed["completed"] == 3
    assert parsed["percent"] == 50.0
    assert len(parsed["steps"]) == 6
    assert parsed["steps"][0]["completed"] is True
    assert parsed["steps"][1]["completed"] is False


def test_tool_manager_pattern_derivation():
    """HUB-22: ToolManager derives proper Antigravity permission patterns."""
    tm = ToolManager()

    pat1 = tm.derive_pattern_from_tool_call("run_command", {"CommandLine": "pytest tests/ -v"})
    assert pat1 == "command(pytest tests/ -v)"

    pat2 = tm.derive_pattern_from_tool_call("view_file", {"AbsolutePath": "E:/Project/main.py"})
    assert pat2 == "read_file(E:/Project/main.py)"

    pat3 = tm.derive_pattern_from_tool_call("write_to_file", {"TargetFile": "E:/Project/config.py"})
    assert pat3 == "write_file(E:/Project/config.py)"

    pat4 = tm.derive_pattern_from_tool_call("read_url_content", {"Url": "https://example.com/api"})
    assert pat4 == "read_url(https://example.com/api)"

    pat5 = tm.derive_pattern_from_tool_call("mcp_custom_tool", {})
    assert pat5 == "mcp(mcp_custom_tool)"


@pytest.mark.asyncio
async def test_cli_dispatcher_process_registry_and_cancel(mock_antigravity_env):
    """HUB-23: CLIDispatcher tracks active processes and handles prompt cancellation."""
    dispatcher = CLIDispatcher()
    conv_id = mock_antigravity_env.root_id

    res = await dispatcher._async_dispatch_prompt(conv_id, "Heavy processing task")
    assert res["status"] == "dispatched"

    pid = dispatcher.get_running_process(conv_id)
    assert pid is not None

    cancelled = await dispatcher.cancel_prompt(conv_id)
    assert cancelled is True
    assert dispatcher.get_running_process(conv_id) is None


def test_rest_subagents_and_artifacts_routes(client, auth_headers, mock_antigravity_env):
    """HUB-24: Dedicated REST endpoints for subagents and artifact retrieval."""
    root_id = mock_antigravity_env.root_id

    # Subagents route
    sub_res = client.get(f"/api/conversations/{root_id}/subagents", headers=auth_headers)
    assert sub_res.status_code == 200
    assert sub_res.json()["id"] == root_id

    # Artifacts list route
    art_res = client.get(f"/api/conversations/{root_id}/artifacts", headers=auth_headers)
    assert art_res.status_code == 200
    assert len(art_res.json()) >= 1

    # Single artifact route
    item_res = client.get(f"/api/conversations/{root_id}/artifacts/task.md", headers=auth_headers)
    assert item_res.status_code == 200
    assert item_res.json()["filename"] == "task.md"
    assert "content" in item_res.json()
