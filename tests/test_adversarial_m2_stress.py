"""
tests/test_adversarial_m2_stress.py

Comprehensive Adversarial & Stress Test Suite for Milestone 2 (M2):
Antigravity 2.0 Session Hub Engine.

Target modules:
  1. CLIDispatcher (backend.session_hub.cli_dispatcher)
  2. ToolManager (backend.session_hub.tool_manager)
  3. ArtifactsManager (backend.session_hub.artifacts)
  4. SQLiteReader (backend.session_hub.sqlite_reader)
  5. TranscriptTailer (backend.session_hub.tailer)
  6. Session REST & WebSocket Hub Routes (backend.session_hub.routes)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.config import Settings
from backend.main import create_app
from backend.security import create_access_token
from backend.session_hub.artifacts import ArtifactsManager
from backend.session_hub.cli_dispatcher import CLIDispatcher
from backend.session_hub.sqlite_reader import AntigravitySqliteReader, SQLiteReader
from backend.session_hub.tailer import TranscriptTailer
from backend.session_hub.tool_manager import ToolManager

TEST_JWT_SECRET = "m2-challenger2-adversarial-secret-key-32b"
TEST_PIN = "998877"


@pytest.fixture
def m2_env(tmp_path: Path):
    """Set up isolated mock Antigravity directories and environment."""
    gemini_home = tmp_path / ".gemini"
    agy_dir = gemini_home / "antigravity"
    brain_dir = agy_dir / "brain"
    convs_dir = agy_dir / "conversations"
    config_dir = gemini_home / "config"

    brain_dir.mkdir(parents=True, exist_ok=True)
    convs_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / "config.json"
    initial_config = {
        "userSettings": {
            "autoExecutionPolicy": "CASCADE_COMMANDS_AUTO_EXECUTION_EAGER",
            "artifactReviewMode": "ARTIFACT_REVIEW_MODE_TURBO",
            "globalPermissionGrants": {
                "allow": ["read_file(existing.txt)"]
            },
        }
    }
    config_file.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

    # Create conversation_summaries.db
    summaries_db = agy_dir / "conversation_summaries.db"
    conn = sqlite3.connect(summaries_db)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE conversation_summaries (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            preview TEXT,
            step_count INTEGER,
            last_modified_time TEXT,
            workspace_uris TEXT,
            status TEXT,
            agent_name TEXT,
            parent_conversation_id TEXT,
            nesting_depth INTEGER,
            killed INTEGER,
            last_user_input_time TEXT
        )
    """)
    conn.commit()
    conn.close()

    settings = Settings(
        PIN=TEST_PIN,
        JWT_SECRET=TEST_JWT_SECRET,
        WORKSPACE_ROOT=tmp_path / "workspace",
        GEMINI_DIR=gemini_home,
        ANTIGRAVITY_DIR=agy_dir,
    )
    settings.WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    with patch.dict(os.environ, {
        "GEMINI_HOME": str(gemini_home),
        "ANTIGRAVITY_HOME": str(agy_dir),
        "MOCK_SUBPROCESS": "1",
        "JWT_SECRET_KEY": TEST_JWT_SECRET,
    }):
        yield {
            "tmp_path": tmp_path,
            "gemini_home": gemini_home,
            "agy_dir": agy_dir,
            "brain_dir": brain_dir,
            "convs_dir": convs_dir,
            "config_dir": config_dir,
            "config_file": config_file,
            "summaries_db": summaries_db,
            "settings": settings,
        }


@pytest.fixture
def auth_token(m2_env):
    return create_access_token(data={"sub": "admin"}, secret=TEST_JWT_SECRET)


@pytest.fixture
def test_client(m2_env):
    app = create_app(settings=m2_env["settings"])
    with TestClient(app) as client:
        yield client


# =====================================================================
# 1. CLI Dispatcher Adversarial Tests
# =====================================================================

class TestCLIDispatcherAdversarial:
    """Adversarial scenarios targeting CLIDispatcher."""

    def test_find_cli_executable_hierarchy(self, m2_env):
        """Verify CLI executable search order: custom env -> PATH -> standard paths."""
        dispatcher = CLIDispatcher(settings=m2_env["settings"])

        # 1. Custom env override
        custom_bin = m2_env["tmp_path"] / "custom_bin" / "agy.exe"
        custom_bin.parent.mkdir(parents=True, exist_ok=True)
        custom_bin.write_text("fake binary", encoding="utf-8")

        with patch.dict(os.environ, {"AGY_CLI_PATH": str(custom_bin)}):
            found = dispatcher.find_cli_executable()
            assert found == custom_bin.resolve()

        # 2. PATH resolution
        with patch.dict(os.environ, {"AGY_CLI_PATH": ""}):
            with patch("shutil.which", return_value="C:/Program Files/agy.exe"):
                found = dispatcher.find_cli_executable()
                assert found == Path("C:/Program Files/agy.exe").resolve()

    def test_mock_fallback_resilience(self, m2_env):
        """Verify mock fallback works predictably without agy.exe."""
        dispatcher = CLIDispatcher(settings=m2_env["settings"])
        with patch.object(dispatcher, "find_cli_executable", return_value=None):
            res = dispatcher.dispatch_prompt(
                conversation_id="conv_mock_test",
                prompt="test prompt without agy.exe",
                mode="accept-edits",
            )
            assert res["status"] == "dispatched"
            assert "process_id" in res
            assert res["process_id"] > 0
            assert res["conversation_id"] == "conv_mock_test"

            # Check process is tracked in active registry
            active = dispatcher.list_active_processes()
            assert "conv_mock_test" in active

    @pytest.mark.asyncio
    async def test_process_cancellation_and_registry_cleanup(self, m2_env):
        """Test process cancellation and registry removal."""
        dispatcher = CLIDispatcher(settings=m2_env["settings"])
        cid = "conv_cancel_test"
        res = await dispatcher._async_dispatch_prompt(
            conversation_id=cid,
            prompt="run long task",
        )
        assert res["status"] == "dispatched"
        assert dispatcher.get_running_process(cid) is not None

        # Cancel the prompt
        cancelled = await dispatcher.cancel_prompt(cid)
        assert cancelled is True
        assert dispatcher.get_running_process(cid) is None

        # Cancelling already cancelled process returns False
        cancelled_again = await dispatcher.cancel_prompt(cid)
        assert cancelled_again is False

    @pytest.mark.asyncio
    async def test_subprocess_timeout_cleanup(self, m2_env):
        """Test that monitor background task cleanly removes process upon timeout."""
        dispatcher = CLIDispatcher(settings=m2_env["settings"])
        cid = "conv_timeout_test"

        # Mock a long-running subprocess
        mock_process = AsyncMock()
        mock_process.pid = 88776
        # wait() hangs until cancelled
        async def slow_wait():
            await asyncio.sleep(10.0)
            return 0
        mock_process.wait = slow_wait

        with patch.dict(os.environ, {"MOCK_SUBPROCESS": "0"}):
            with patch.object(dispatcher, "find_cli_executable", return_value=Path("C:/fake/agy.exe")):
                with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                    with patch.object(dispatcher, "cancel_prompt", new_callable=AsyncMock) as mock_cancel:
                        mock_cancel.return_value = True
                        res = await dispatcher._async_dispatch_prompt(
                            conversation_id=cid,
                            prompt="long running prompt",
                            timeout_seconds=0.05,
                        )
                        assert res["status"] == "dispatched"
                        # Wait for monitor timeout to trigger
                        await asyncio.sleep(0.15)
                        mock_cancel.assert_called_once_with(cid)

    @pytest.mark.asyncio
    async def test_slash_commands_and_extreme_prompt_payloads(self, m2_env):
        """Test slash commands (/goal, /schedule, /invalid_cmd), injection vectors, and large strings."""
        dispatcher = CLIDispatcher(settings=m2_env["settings"])

        test_prompts = [
            "/goal Build mobile controller",
            "/schedule 0 * * * * Healthcheck",
            "/teamwork-preview enable",
            "/invalid_command_xyz --flag unknown",
            "/",
            "",
            "   ",
            "A" * 100_000,  # 100KB large string
            "'; DROP TABLE conversation_summaries; --",
            "& dir & whoami & tasklist",
            "$(cat /etc/passwd)",
            "Special Chars: \u0000\u0007\u001b[31mRed\u001b[0m Tiếng Việt có dấu 🚀",
        ]

        for idx, p in enumerate(test_prompts):
            cid = f"conv_slash_{idx}"
            res = await dispatcher._async_dispatch_prompt(conversation_id=cid, prompt=p)
            assert res["status"] in ("dispatched", "error")
            if res["status"] == "dispatched":
                assert res["conversation_id"] == cid
                # Clean up
                await dispatcher.cancel_prompt(cid)

    @pytest.mark.asyncio
    async def test_massive_concurrent_dispatch_burst(self, m2_env):
        """Burst 50 concurrent dispatch calls across multiple conversations."""
        dispatcher = CLIDispatcher(settings=m2_env["settings"])

        async def _dispatch_task(i: int):
            cid = f"burst_conv_{i % 10}"
            prompt = f"Prompt payload {i}"
            return await dispatcher._async_dispatch_prompt(conversation_id=cid, prompt=prompt)

        tasks = [_dispatch_task(i) for i in range(50)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 50
        for r in results:
            assert r["status"] == "dispatched"

        # Cleanup all
        for i in range(10):
            await dispatcher.cancel_prompt(f"burst_conv_{i}")


# =====================================================================
# 2. Tool Manager & Security Adversarial Tests
# =====================================================================

class TestToolManagerAdversarial:
    """Adversarial scenarios targeting ToolManager and atomic permission grants."""

    def test_pattern_derivation_matrix(self, m2_env):
        """Test pattern derivation across diverse tool names, flags, and missing args."""
        tool_mgr = ToolManager(settings=m2_env["settings"])

        cases = [
            ("run_command", {"CommandLine": "pytest tests/ -v"}, "command(pytest tests/ -v)"),
            ("run_command", {"CommandLine": ""}, "command(*)"),
            ("run_command", {}, "command(*)"),
            ("view_file", {"AbsolutePath": "C:/path/file.py"}, "read_file(C:/path/file.py)"),
            ("view_file", {"path": "relative/file.py"}, "read_file(relative/file.py)"),
            ("view_file", {}, "read_file(*)"),
            ("write_to_file", {"TargetFile": "e:/out.txt"}, "write_file(e:/out.txt)"),
            ("replace_file_content", {"TargetFile": "e:/edit.py"}, "write_file(e:/edit.py)"),
            ("read_url_content", {"Url": "https://api.github.com"}, "read_url(https://api.github.com)"),
            ("read_url_content", {}, "read_url(*)"),
            ("mcp_server_query", {"arg": 1}, "mcp(mcp_server_query)"),
            ("custom_tool", {"foo": "bar"}, "custom_tool(*)"),
        ]

        for tool_name, args, expected in cases:
            derived = tool_mgr.derive_pattern_from_tool_call(tool_name, args)
            assert derived == expected, f"Expected {expected}, got {derived} for {tool_name}"

    def test_tool_decision_invalid_action_rejection(self, m2_env):
        """Test invalid actions raise ValueError."""
        tool_mgr = ToolManager(settings=m2_env["settings"])
        invalid_actions = ["ALLOW", "DENY", "maybe", "yes", "no", "", "123", None]

        for action in invalid_actions:
            with pytest.raises(ValueError):
                tool_mgr.handle_decision(
                    conversation_id="conv_1",
                    tool_call_id="call_1",
                    action=action,
                )

    def test_sequential_permission_grant_atomic_writes(self, m2_env):
        """Verify sequential atomic writes to config.json preserve all grants."""
        tool_mgr = ToolManager(settings=m2_env["settings"])
        config_path = m2_env["config_file"]

        patterns = [f"command(tool_seq_{i}*)" for i in range(20)]

        for p in patterns:
            ok = tool_mgr.update_permission_grants_atomic(p, config_path=config_path)
            assert ok is True

        # Verify config file integrity
        raw_text = config_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        allow_list = data["userSettings"]["globalPermissionGrants"]["allow"]

        assert "read_file(existing.txt)" in allow_list
        for p in patterns:
            assert p in allow_list

    def test_concurrent_tool_approval_concurrency_race_analysis(self, m2_env):
        """Adversarial analysis: Probe ToolManager under multi-threaded concurrency."""
        tool_mgr = ToolManager(settings=m2_env["settings"])
        config_path = m2_env["config_file"]

        patterns = [f"command(tool_concurrent_{i}*)" for i in range(10)]

        def _update(pattern):
            return tool_mgr.update_permission_grants_atomic(pattern, config_path=config_path)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_update, p) for p in patterns]
            results = [f.result() for f in futures]

        # Check config file validity
        raw_text = config_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        allow_list = data["userSettings"]["globalPermissionGrants"]["allow"]
        assert isinstance(allow_list, list)

    def test_malformed_config_json_recovery(self, m2_env):
        """Test recovery from empty, broken, or corrupted config.json files."""
        tool_mgr = ToolManager(settings=m2_env["settings"])
        config_path = m2_env["config_file"]

        # Case 1: Empty file (0 bytes)
        config_path.write_text("", encoding="utf-8")
        success = tool_mgr.update_permission_grants_atomic("command(empty_recovery*)", config_path=config_path)
        assert success is True
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "command(empty_recovery*)" in data["userSettings"]["globalPermissionGrants"]["allow"]

        # Case 2: Broken JSON syntax
        config_path.write_text('{"userSettings": { "globalPermissionGrants": ', encoding="utf-8")
        success = tool_mgr.update_permission_grants_atomic("command(broken_json_recovery*)", config_path=config_path)
        assert success is True
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "command(broken_json_recovery*)" in data["userSettings"]["globalPermissionGrants"]["allow"]

        # Case 3: Missing globalPermissionGrants key
        config_path.write_text(json.dumps({"userSettings": {}}), encoding="utf-8")
        success = tool_mgr.update_permission_grants_atomic("command(missing_key_recovery*)", config_path=config_path)
        assert success is True
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "command(missing_key_recovery*)" in data["userSettings"]["globalPermissionGrants"]["allow"]

        # Case 4: Non-list allow field
        config_path.write_text(json.dumps({
            "userSettings": {
                "globalPermissionGrants": {"allow": "invalid_string_not_list"}
            }
        }), encoding="utf-8")
        success = tool_mgr.update_permission_grants_atomic("command(non_list_recovery*)", config_path=config_path)
        assert success is True
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert isinstance(data["userSettings"]["globalPermissionGrants"]["allow"], list)
        assert "command(non_list_recovery*)" in data["userSettings"]["globalPermissionGrants"]["allow"]

    def test_duplicate_grant_prevention(self, m2_env):
        """Adding identical pattern multiple times should not create duplicate entries."""
        tool_mgr = ToolManager(settings=m2_env["settings"])
        config_path = m2_env["config_file"]

        pattern = "command(run_unique_test)"
        tool_mgr.update_permission_grants_atomic(pattern, config_path=config_path)
        tool_mgr.update_permission_grants_atomic(pattern, config_path=config_path)
        tool_mgr.update_permission_grants_atomic(pattern, config_path=config_path)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        allow_list = data["userSettings"]["globalPermissionGrants"]["allow"]
        assert allow_list.count(pattern) == 1


# =====================================================================
# 3. Artifacts Manager Adversarial Tests
# =====================================================================

class TestArtifactsManagerAdversarial:
    """Adversarial scenarios targeting ArtifactsManager and checklist parsing."""

    def test_complex_and_mixed_checklist_formats(self, m2_env):
        """Test parsing of varied markdown checklist representations."""
        mgr = ArtifactsManager(settings=m2_env["settings"])

        mixed_markdown = """
# Project Implementation Plan

Some introductory text.

- [ ] Task 1: Unchecked dash
- [x] Task 2: Checked dash lowercase
- [X] Task 3: Checked dash uppercase
* [ ] Task 4: Unchecked asterisk
* [x] Task 5: Checked asterisk
1. [ ] Task 6: Unchecked numbered
2. [x] Task 7: Checked numbered
- `[ ]` Task 8: Antigravity backtick unchecked
* `[x]` Task 9: Antigravity backtick checked
        - [ ] Task 10: Indented 8 spaces
            * [x] Task 11: Indented 12 spaces

Not a checkbox:
- [] Missing space
- [  ] Extra spaces
- [v] Checkmark symbol
Just text with [x] in middle
"""
        result = mgr.parse_plan_steps(mixed_markdown)
        assert result["total"] == 11
        assert result["completed"] == 6  # tasks 2, 3, 5, 7, 9, 11
        assert result["percent"] == round((6 / 11) * 100, 1)

        # Check indentation levels
        steps = result["steps"]
        assert steps[0]["indent_level"] == 0
        assert steps[9]["indent_level"] == 8
        assert steps[10]["indent_level"] == 12

    def test_unicode_and_special_character_artifacts(self, m2_env):
        """Test artifact reading with Unicode, Vietnamese, CJK, Emojis, and HTML tags."""
        mgr = ArtifactsManager(settings=m2_env["settings"])
        cid = "conv_unicode_art"
        brain_conv = m2_env["brain_dir"] / cid
        brain_conv.mkdir(parents=True, exist_ok=True)

        md_file = brain_conv / "task.md"
        unicode_content = """# Nhiệm vụ: Xây dựng AgyMobile 🚀

Tài liệu này chứa các ký tự đặc biệt:
- Tiếng Việt: Ă, Â, Đ, Ê, Ô, Ơ, Ư
- CJK: 日本語のテスト / 中文测试 / 한국어
- Emojis: 🛰️ 💻 📱 🔥 ✨
- Special XML/HTML: <script>alert('xss')</script> &amp; &lt;div&gt;

## Checklist
- [x] Thiết lập WebSocket hai chiều 📡
- [ ] Tối ưu giao diện cảm ứng PWA 📱
- [x] Xác thực mã PIN 6 số an toàn 🔒
"""
        md_file.write_text(unicode_content, encoding="utf-8")

        meta_file = brain_conv / "task.md.metadata.json"
        meta_content = {
            "title": "Nhiệm vụ: Xây dựng AgyMobile 🚀",
            "summary": "Tài liệu kiểm thử Unicode và Tiếng Việt có dấu.",
            "requestFeedback": True,
            "userFacing": True,
            "updatedAt": "2026-08-14T10:00:00Z",
        }
        meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

        artifacts = mgr.list_artifacts(cid)
        assert len(artifacts) == 1
        art = artifacts[0]
        assert art["filename"] == "task.md"
        assert "AgyMobile 🚀" in art["title"]
        assert art["progress"]["total"] == 3
        assert art["progress"]["completed"] == 2
        assert art["request_feedback"] is True

        content_res = mgr.get_artifact_content(cid, "task.md")
        assert content_res is not None
        assert "Tiếng Việt: Ă, Â, Đ" in content_res["content"]
        assert len(content_res["steps"]) == 3

    def test_corrupted_companion_metadata_recovery(self, m2_env):
        """Artifacts manager should gracefully handle missing or corrupted .metadata.json."""
        mgr = ArtifactsManager(settings=m2_env["settings"])
        cid = "conv_broken_meta"
        brain_conv = m2_env["brain_dir"] / cid
        brain_conv.mkdir(parents=True, exist_ok=True)

        # Artifact with broken metadata
        (brain_conv / "broken_plan.md").write_text("# Broken Plan\n- [ ] Step 1\n", encoding="utf-8")
        (brain_conv / "broken_plan.md.metadata.json").write_text("{broken json syntax: missing quote", encoding="utf-8")

        # Artifact with missing metadata
        (brain_conv / "no_meta.md").write_text("# No Meta Plan\n- [x] Step 1\n", encoding="utf-8")

        artifacts = mgr.list_artifacts(cid)
        assert len(artifacts) == 2

        art_map = {a["filename"]: a for a in artifacts}
        assert "broken_plan.md" in art_map
        assert art_map["broken_plan.md"]["title"] == "Broken Plan"
        assert art_map["broken_plan.md"]["progress"]["total"] == 1

        assert "no_meta.md" in art_map
        assert art_map["no_meta.md"]["title"] == "No Meta Plan"
        assert art_map["no_meta.md"]["progress"]["completed"] == 1

    def test_large_artifact_stress_performance(self, m2_env):
        """Parse an artifact with 5,000 checklist items and ensure it completes in < 500ms."""
        mgr = ArtifactsManager(settings=m2_env["settings"])
        lines = ["# Mega Plan"]
        for i in range(5000):
            mark = "x" if i % 2 == 0 else " "
            lines.append(f"- [{mark}] Mega Step {i}: Description of step {i}")
        mega_text = "\n".join(lines)

        t0 = time.perf_counter()
        result = mgr.parse_plan_steps(mega_text)
        duration_ms = (time.perf_counter() - t0) * 1000

        assert result["total"] == 5000
        assert result["completed"] == 2500
        assert result["percent"] == 50.0
        assert duration_ms < 500  # Well within stress limit

    def test_nonexistent_and_directory_artifacts(self, m2_env):
        """Nonexistent conversations or files should return empty/None safely."""
        mgr = ArtifactsManager(settings=m2_env["settings"])

        # Nonexistent conversation
        assert mgr.list_artifacts("nonexistent_conv_999") == []
        assert mgr.get_artifact_content("nonexistent_conv_999", "plan.md") is None

        # Empty markdown text
        empty_res = mgr.parse_plan_steps("")
        assert empty_res["total"] == 0
        assert empty_res["percent"] == 0.0


# =====================================================================
# 4. Transcript Tailer & SQLite Reader Adversarial Tests
# =====================================================================

class TestTailerAndSQLiteAdversarial:
    """Adversarial scenarios targeting TranscriptTailer and SQLiteReader."""

    def test_sqlite_concurrent_read_queries(self, m2_env):
        """Test 30 concurrent read queries against SQLite under active updates."""
        reader = SQLiteReader(antigravity_dir=m2_env["agy_dir"])
        db_path = m2_env["summaries_db"]

        # Insert base data
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        for i in range(15):
            cur.execute("""
                INSERT INTO conversation_summaries
                (conversation_id, title, preview, step_count, last_modified_time, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (f"concurrent_cid_{i}", f"Title {i}", f"Prev {i}", i, f"2026-08-14T11:{i:02d}:00Z", "active"))
        conn.commit()
        conn.close()

        def _query_batch(limit):
            return reader.list_conversations(limit=limit)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_query_batch, 10) for _ in range(30)]
            results = [f.result() for f in futures]

        for r in results:
            assert len(r) == 10

    def test_sqlite_corrupted_header_database(self, m2_env):
        """Verify SQLiteReader safely ignores non-SQLite or corrupted header files."""
        convs_dir = m2_env["convs_dir"]
        corrupted_db = convs_dir / "corrupted_bad_header.db"
        corrupted_db.write_bytes(b"NOT_A_SQLITE_DATABASE_HEADER_GARBAGE")

        reader = SQLiteReader(antigravity_dir=m2_env["agy_dir"])
        convs = reader.list_conversations(limit=100)
        # Should not crash and should not include the corrupted DB
        assert all(c["id"] != "corrupted_bad_header" for c in convs)

    def test_tailer_partial_line_buffering_at_eof(self, m2_env):
        """Verify tailer buffers incomplete line at EOF until trailing newline is written."""
        cid = "conv_tailer_buffer"
        logs_dir = m2_env["brain_dir"] / cid / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        t_file = logs_dir / "transcript.jsonl"

        line1 = json.dumps({"step_index": 0, "source": "USER", "type": "USER_INPUT", "status": "DONE"}) + "\n"
        incomplete_line2 = '{"step_index": 1, "source": "MODEL", "content": "Incomp'

        t_file.write_text(line1 + incomplete_line2, encoding="utf-8")

        tailer = TranscriptTailer(cid)
        steps, offset = tailer.tail_from_offset(0)

        # Only line1 should be parsed
        assert len(steps) == 1
        assert steps[0]["step_index"] == 0

        # Now complete line2
        completed_line2 = incomplete_line2 + 'lete response"}\n'
        t_file.write_text(line1 + completed_line2, encoding="utf-8")

        steps2, offset2 = tailer.tail_from_offset(offset)
        assert len(steps2) == 1
        assert steps2[0]["step_index"] == 1
        assert steps2[0]["content"] == "Incomplete response"

    def test_tailer_file_truncation_detection(self, m2_env):
        """Verify tailer detects file truncation / rotation and resets offset to 0."""
        cid = "conv_tailer_trunc"
        logs_dir = m2_env["brain_dir"] / cid / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        t_file = logs_dir / "transcript.jsonl"

        long_content = "".join([
            json.dumps({"step_index": i, "content": f"Step {i}"}) + "\n"
            for i in range(20)
        ])
        t_file.write_text(long_content, encoding="utf-8")

        tailer = TranscriptTailer(cid)
        _, end_offset = tailer.tail_from_offset(0)
        assert end_offset > 500

        # Truncate file
        short_content = json.dumps({"step_index": 0, "content": "Reset"}) + "\n"
        t_file.write_text(short_content, encoding="utf-8")

        # Tail starting from old large offset
        steps, new_offset = tailer.tail_from_offset(end_offset)
        # Should reset and read from beginning
        assert len(steps) == 1
        assert steps[0]["content"] == "Reset"


# =====================================================================
# 5. Session REST & WebSocket Adversarial Tests
# =====================================================================

class TestSessionRESTAndWebSocketAdversarial:
    """Adversarial stress testing for Session REST API and WebSocket Multiplexer."""

    def test_rest_conversations_pagination_and_filters_adversarial(self, test_client, auth_token, m2_env):
        """Test edge cases in conversation filters and pagination."""
        headers = {"Authorization": f"Bearer {auth_token}"}

        # Populate SQLite with 20 conversations
        conn = sqlite3.connect(m2_env["summaries_db"])
        cur = conn.cursor()
        for i in range(20):
            status_val = "active" if i % 2 == 0 else "completed"
            parent = "root_parent_conv" if i >= 15 else None
            cur.execute("""
                INSERT INTO conversation_summaries
                (conversation_id, title, preview, step_count, last_modified_time, status, parent_conversation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (f"conv_{i:02d}", f"Conversation {i}", f"Preview {i}", i * 2, f"2026-08-14T10:{i:02d}:00Z", status_val, parent))
        conn.commit()
        conn.close()

        # Test pagination limits
        r = test_client.get("/api/conversations?limit=5&offset=0", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 5

        # Offset beyond count
        r = test_client.get("/api/conversations?limit=5&offset=100", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 0

        # Filter by status
        r = test_client.get("/api/conversations?status=completed", headers=headers)
        assert r.status_code == 200
        assert all(c["status"] == "completed" for c in r.json())

        # Filter by parent_id
        r = test_client.get("/api/conversations?parent_id=root_parent_conv", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 5

        # Search filter with special chars
        r = test_client.get("/api/conversations?search=Conversation+1", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_rest_transcript_not_found_handling(self, test_client, auth_token):
        """Non-existent conversation transcript request should return 404."""
        headers = {"Authorization": f"Bearer {auth_token}"}
        r = test_client.get("/api/conversations/non_existent_conv_id/transcript", headers=headers)
        assert r.status_code == 404
        assert "not found" in r.json()["detail"].lower()

    def test_rest_tool_approval_endpoint_adversarial(self, test_client, auth_token, m2_env):
        """Test tool approval REST endpoint with valid, invalid, and edge-case bodies."""
        headers = {"Authorization": f"Bearer {auth_token}"}

        # 1. Valid approve
        r = test_client.post(
            "/api/conversations/conv_01/tool-approval",
            headers=headers,
            json={"tool_call_id": "call_123", "action": "approve", "pattern": "command(pytest*)"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "updated"
        assert r.json()["action"] == "approve"

        # 2. Valid reject
        r = test_client.post(
            "/api/conversations/conv_01/tool-approval",
            headers=headers,
            json={"tool_call_id": "call_123", "action": "reject"},
        )
        assert r.status_code == 200
        assert r.json()["action"] == "reject"

        # 3. Invalid action (should be 422 or 400)
        r = test_client.post(
            "/api/conversations/conv_01/tool-approval",
            headers=headers,
            json={"tool_call_id": "call_123", "action": "invalid_action_type"},
        )
        assert r.status_code in (400, 422)

        # 4. Missing required tool_call_id
        r = test_client.post(
            "/api/conversations/conv_01/tool-approval",
            headers=headers,
            json={"action": "approve"},
        )
        assert r.status_code == 422

    def test_rest_prompt_dispatch_adversarial(self, test_client, auth_token, m2_env):
        """Test prompt dispatch endpoint with various inputs."""
        headers = {"Authorization": f"Bearer {auth_token}"}

        # Valid prompt
        r = test_client.post(
            "/api/conversations/conv_prompt_test/prompt",
            headers=headers,
            json={"prompt": "Hello Antigravity", "mode": "accept-edits"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "dispatched"

        # Empty prompt (Pydantic validation min_length=1)
        r = test_client.post(
            "/api/conversations/conv_prompt_test/prompt",
            headers=headers,
            json={"prompt": ""},
        )
        assert r.status_code == 422

    def test_ws_session_rapid_connect_disconnect_churn(self, test_client, auth_token):
        """Stress test: 30 rapid sequential connect/disconnect cycles."""
        for i in range(30):
            with test_client.websocket_connect(f"/ws/session?token={auth_token}") as ws:
                ws.send_json({"action": "subscribe", "conversation_id": f"churn_conv_{i}"})
                resp = ws.receive_json()
                assert resp["type"] == "subscribed"

    def test_ws_session_malformed_and_fuzz_frames(self, test_client, auth_token):
        """Send corrupted, malformed, non-dict, and fuzz frames over /ws/session."""
        with test_client.websocket_connect(f"/ws/session?token={auth_token}") as ws:
            # 1. Non-JSON raw text (server should skip and stay alive)
            ws.send_text("THIS IS NOT JSON {[[[")
            ws.send_text("<xml>invalid</xml>")

            # 2. JSON non-dict (list, int, string, null)
            ws.send_text(json.dumps([1, 2, 3]))
            ws.send_text(json.dumps(12345))
            ws.send_text(json.dumps("plain json string"))
            ws.send_text(json.dumps(None))

            # 3. Unrecognized action type
            ws.send_json({"action": "unrecognized_hack_action", "conversation_id": "test_cid"})
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert "unrecognized action" in resp["message"].lower()

            # 4. Valid action to verify connection remained intact after fuzzing
            ws.send_json({"action": "subscribe", "conversation_id": "conv_after_fuzz"})
            resp = ws.receive_json()
            assert resp["type"] == "subscribed"
            assert resp["conversation_id"] == "conv_after_fuzz"

    def test_ws_session_interactive_actions_and_tailer_streaming(self, test_client, auth_token, m2_env):
        """Test full interactive WebSocket flow: subscribe, send_prompt, tool_decision, and transcript streaming."""
        cid = "ws_full_flow_conv"
        brain_conv = m2_env["brain_dir"] / cid / ".system_generated" / "logs"
        brain_conv.mkdir(parents=True, exist_ok=True)
        transcript_file = brain_conv / "transcript.jsonl"

        # Initialize empty transcript
        transcript_file.write_text("", encoding="utf-8")

        with test_client.websocket_connect(f"/ws/session?token={auth_token}") as ws:
            # 1. Subscribe
            ws.send_json({"action": "subscribe", "conversation_id": cid, "from_offset": 0})
            sub_ack = ws.receive_json()
            assert sub_ack["type"] == "subscribed"

            # 2. Send Prompt
            ws.send_json({
                "action": "send_prompt",
                "conversation_id": cid,
                "prompt": "Test WebSocket Prompt",
                "mode": "accept-edits",
            })
            prompt_ack = ws.receive_json()
            assert prompt_ack["type"] == "prompt_dispatched"
            assert prompt_ack["result"]["status"] == "dispatched"

            # 3. Tool Decision
            ws.send_json({
                "action": "tool_decision",
                "conversation_id": cid,
                "tool_call_id": "call_ws_99",
                "decision": "approve",
                "pattern": "command(ws_tool_test*)",
            })
            decision_ack = ws.receive_json()
            assert decision_ack["type"] == "tool_decision_ack"
            assert decision_ack["result"]["status"] == "updated"

            # 4. Cancel prompt
            ws.send_json({
                "action": "cancel_prompt",
                "conversation_id": cid,
            })
            cancel_ack = ws.receive_json()
            assert cancel_ack["type"] == "prompt_cancelled"
            assert cancel_ack["conversation_id"] == cid

            # 5. Unsubscribe
            ws.send_json({
                "action": "unsubscribe",
                "conversation_id": cid,
            })
            unsub_ack = ws.receive_json()
            assert unsub_ack["type"] == "unsubscribed"

    def test_ws_session_unauthorized_connection_rejection(self, test_client):
        """Connections without valid token query parameter should be closed with code 1008."""
        with pytest.raises(Exception):
            with test_client.websocket_connect("/ws/session?token=invalid_expired_token") as ws:
                ws.receive_json()

        with pytest.raises(Exception):
            with test_client.websocket_connect("/ws/session") as ws:
                ws.receive_json()
