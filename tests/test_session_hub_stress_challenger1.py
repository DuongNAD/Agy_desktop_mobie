"""
tests/test_session_hub_stress_challenger1.py

Adversarial Stress Test Suite for Milestone 2 (Session Hub Engine):
- SQLite Reader: Corrupted DBs, file locks, missing tables/files, deep subagent trees, write protection.
- Async Transcript Tailer: High-throughput bursts, partial JSON buffering, truncation, zero-byte/missing files,
  UTF-8 fuzzing/corrupt bytes, chunk boundary seek accuracy, WebSocket event streaming.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.session_hub.sqlite_reader import (
    AntigravitySqliteReader,
    ConversationSummary,
    SQLiteReader,
    _parse_iso_timestamp,
)
from backend.session_hub.tailer import (
    AsyncTranscriptTailer,
    ToolCallInfo,
    TranscriptEntry,
    TranscriptTailer,
)


# ===========================================================================
# 1. SQLite Reader Adversarial Scenarios
# ===========================================================================

def test_sqlite_corrupted_db_random_binary_garbage(tmp_path: Path):
    """
    Scenario 1.1: Database file contains random binary garbage (no valid SQLite header).
    The reader must reject the invalid header, log a warning, and safely return an empty list
    without raising an unhandled exception or crashing.
    """
    corrupt_db = tmp_path / "corrupt_garbage.db"
    corrupt_db.write_bytes(os.urandom(2048))

    reader = SQLiteReader(antigravity_dir=tmp_path)
    # Direct list_conversations on corrupt file
    res = reader.list_conversations(corrupt_db)
    assert isinstance(res, list)
    assert len(res) == 0

    # Test get_conversation on corrupt file
    detail = reader.get_conversation("corrupt_garbage")
    assert detail is None

    # Test get_steps_meta on corrupt file
    steps = reader.get_steps_meta("corrupt_garbage")
    assert steps == []

    # Test get_hub_stats with corrupt directory
    stats = reader.get_hub_stats()
    assert stats["total_conversations"] == 0
    assert stats["total_steps"] == 0


def test_sqlite_corrupted_page_data_with_valid_header(tmp_path: Path):
    """
    Scenario 1.2: Database file has a valid 16-byte SQLite header ("SQLite format 3\x00"),
    but the remaining page payload is corrupted binary garbage.
    The reader must catch sqlite3.DatabaseError / OperationalError during query execution
    and handle it safely without 500/crash.
    """
    corrupt_db = tmp_path / "corrupt_pages.db"
    # Valid 16-byte header + corrupt payload
    header = b"SQLite format 3\x00"
    payload = os.urandom(4096)
    corrupt_db.write_bytes(header + payload)

    reader = SQLiteReader(antigravity_dir=tmp_path)
    res = reader.list_conversations(corrupt_db)
    assert isinstance(res, list)
    assert len(res) == 0

    conn = reader._get_ro_connection(corrupt_db)
    if conn:
        try:
            # Querying corrupted pages raises DatabaseError
            cur = conn.cursor()
            with pytest.raises(sqlite3.DatabaseError):
                cur.execute("SELECT * FROM sqlite_master;")
        finally:
            conn.close()


def test_sqlite_locked_database_timeout_and_fallback(tmp_path: Path):
    """
    Scenario 1.3: Simulate database lock by opening an exclusive write lock in another connection.
    The reader must respect busy_timeout_ms, avoid indefinite hangs, and safely return without crashing.
    """
    db_path = tmp_path / "conversation_summaries.db"
    conn_writer = sqlite3.connect(str(db_path), timeout=5.0)
    conn_writer.execute("PRAGMA journal_mode = WAL;")
    conn_writer.execute("""
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
            killed NUMERIC,
            last_user_input_time TEXT
        )
    """)
    conn_writer.execute(
        "INSERT INTO conversation_summaries VALUES ('c1', 'Title 1', '', 5, '2026-08-14T08:00:00Z', '[]', 'active', 'agent1', NULL, 0, 0, '')"
    )
    conn_writer.commit()

    # Hold an exclusive transaction on writer connection
    conn_writer.execute("BEGIN EXCLUSIVE;")

    # Reader with small timeout (100ms)
    reader = SQLiteReader(antigravity_dir=tmp_path, busy_timeout_ms=100)
    try:
        # Should either read snapshot or safely handle busy state without hanging
        start_t = time.monotonic()
        res = reader.list_conversations(db_path)
        elapsed = time.monotonic() - start_t
        assert elapsed < 2.0  # Must not hang
        assert isinstance(res, list)
    finally:
        conn_writer.rollback()
        conn_writer.close()


def test_sqlite_missing_tables_and_empty_db(tmp_path: Path):
    """
    Scenario 1.4: Database exists and is valid SQLite, but lacks required tables
    (e.g., conversation_summaries or steps tables do not exist).
    The reader must handle missing table errors safely without raising OperationalError.
    """
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE unrelated_table (id INTEGER PRIMARY KEY, note TEXT);")
    conn.execute("INSERT INTO unrelated_table VALUES (1, 'no summaries here');")
    conn.commit()
    conn.close()

    reader = SQLiteReader(antigravity_dir=tmp_path)
    res = reader.list_conversations(db_path)
    assert isinstance(res, list)
    assert len(res) == 0

    # Also test conversation db without 'steps' table
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    conv_db = conv_dir / "conv_no_steps.db"
    c_conn = sqlite3.connect(str(conv_db))
    c_conn.execute("CREATE TABLE dummy (col TEXT);")
    c_conn.commit()
    c_conn.close()

    steps = reader.get_steps_meta("conv_no_steps")
    assert steps == []
    count = reader.get_conversation_steps_count("conv_no_steps")
    assert count == 0


def test_sqlite_missing_summary_db_layer2_orphan_fallback(tmp_path: Path):
    """
    Scenario 1.5: Missing conversation_summaries.db entirely.
    The reader must activate Layer 2 fallback and discover orphan <cid>.db files in conversations/.
    """
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)

    for i in range(5):
        cid = f"orphan_session_{i:03d}"
        cdb = conv_dir / f"{cid}.db"
        conn = sqlite3.connect(str(cdb))
        conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, has_subtrajectory NUMERIC);")
        for s in range(i + 2):
            conn.execute("INSERT INTO steps VALUES (?, 1, 1, 0)", (s,))
        conn.commit()
        conn.close()

    # Pass tmp_path as antigravity_dir (no conversation_summaries.db exists)
    reader = SQLiteReader(antigravity_dir=tmp_path)
    assert reader._find_summaries_db() is None

    convs = reader.list_conversations()
    assert len(convs) == 5
    ids = {c["id"] for c in convs}
    assert "orphan_session_000" in ids
    assert "orphan_session_004" in ids

    # Check that step counts were accurately resolved from individual steps tables
    c4 = next(c for c in convs if c["id"] == "orphan_session_004")
    assert c4["step_count"] == 6  # 0..5 = 6 steps


def test_sqlite_deeply_nested_subagent_hierarchy_15_levels(tmp_path: Path):
    """
    Scenario 1.6: Subagent hierarchy with 15 nested levels (root -> sub_1 -> sub_2 -> ... -> sub_14).
    The reader must reconstruct the hierarchy up to requested max_depth.
    """
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
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
            killed NUMERIC,
            last_user_input_time TEXT
        )
    """)

    num_levels = 15
    prev_id = None
    for lvl in range(num_levels):
        cid = f"subagent_lvl_{lvl:02d}"
        parent = prev_id
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, f"Agent Level {lvl}", f"Preview {lvl}", lvl + 1, "2026-08-14T08:00:00Z", "[]", "active", f"agent_{lvl}", parent, lvl, 0, "")
        )
        prev_id = cid
    conn.commit()
    conn.close()

    reader = SQLiteReader(antigravity_dir=tmp_path)

    # 1. Full depth tree query (max_depth=15)
    tree_full = reader.get_subagent_tree("subagent_lvl_00", max_depth=15)
    assert tree_full is not None
    assert tree_full["id"] == "subagent_lvl_00"

    # Walk down 15 levels
    curr = tree_full
    depth_count = 0
    while curr.get("children"):
        depth_count += 1
        curr = curr["children"][0]
    assert depth_count == 14  # 0 to 14 = 15 nodes total

    # 2. Bounded depth query (max_depth=4)
    tree_bounded = reader.get_subagent_tree("subagent_lvl_00", max_depth=4)
    assert tree_bounded is not None
    curr_b = tree_bounded
    depth_b = 0
    while curr_b.get("children"):
        depth_b += 1
        curr_b = curr_b["children"][0]
    assert depth_b == 4


def test_sqlite_cyclic_subagent_reference_safety(tmp_path: Path):
    """
    Scenario 1.7: Pathological cyclic hierarchy (A is parent of B, B is parent of A).
    The tree builder must terminate safely at max_depth without recursion overflow.
    """
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
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
            killed NUMERIC,
            last_user_input_time TEXT
        )
    """)
    conn.execute("INSERT INTO conversation_summaries VALUES ('cycle_a', 'Cycle A', '', 1, '', '[]', 'active', 'a', 'cycle_b', 0, 0, '')")
    conn.execute("INSERT INTO conversation_summaries VALUES ('cycle_b', 'Cycle B', '', 1, '', '[]', 'active', 'b', 'cycle_a', 1, 0, '')")
    conn.commit()
    conn.close()

    reader = SQLiteReader(antigravity_dir=tmp_path)
    # Must not raise RecursionError
    tree = reader.get_subagent_tree("cycle_a", max_depth=6)
    assert tree is not None
    assert tree["id"] == "cycle_a"


def test_sqlite_write_protection_read_only_enforcement(tmp_path: Path):
    """
    Scenario 1.8: Attempting any write operation (INSERT, UPDATE, DELETE, CREATE, DROP)
    via connections created by the reader MUST raise sqlite3.OperationalError.
    """
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, title TEXT);")
    conn.execute("INSERT INTO conversation_summaries VALUES ('sec_1', 'Secure Session');")
    conn.commit()
    conn.close()

    reader = SQLiteReader(antigravity_dir=tmp_path)
    ro_conn = reader._get_ro_connection(db_path)
    assert ro_conn is not None

    try:
        # INSERT attempt
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            ro_conn.execute("INSERT INTO conversation_summaries VALUES ('hack_1', 'Hacked');")
        assert "readonly" in str(exc_info.value).lower() or "query_only" in str(exc_info.value).lower() or "attempt to write" in str(exc_info.value).lower()

        # UPDATE attempt
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute("UPDATE conversation_summaries SET title = 'Altered' WHERE conversation_id = 'sec_1';")

        # DELETE attempt
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute("DELETE FROM conversation_summaries WHERE conversation_id = 'sec_1';")

        # DROP attempt
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute("DROP TABLE conversation_summaries;")

        # CREATE attempt
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute("CREATE TABLE malicious (id TEXT);")
    finally:
        ro_conn.close()


def test_sqlite_multithreaded_concurrent_read_stress(tmp_path: Path):
    """
    Scenario 1.9: 20 concurrent threads hammering list_conversations, get_conversation, get_hub_stats.
    Verify thread safety, zero data races, and consistent responses.
    """
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("""
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
            killed NUMERIC,
            last_user_input_time TEXT
        )
    """)
    for i in range(25):
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"thread_sess_{i:02d}", f"Thread Session {i}", f"Preview {i}", i * 3, f"2026-08-14T08:{i:02d}:00Z", "[]", "active", "agent", None, 0, 0, "")
        )
    conn.commit()
    conn.close()

    reader = SQLiteReader(antigravity_dir=tmp_path)
    errors: List[Exception] = []

    def worker_thread(thread_idx: int):
        try:
            for _ in range(15):
                convs = reader.list_conversations()
                assert len(convs) == 25
                target = f"thread_sess_{thread_idx % 25:02d}"
                c = reader.get_conversation(target)
                assert c is not None
                assert c["id"] == target
                stats = reader.get_hub_stats()
                assert stats["total_conversations"] == 25
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker_thread, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(errors) == 0, f"Encountered thread errors: {errors}"


# ===========================================================================
# 2. Transcript Tailer Adversarial Scenarios
# ===========================================================================

def test_tailer_high_throughput_burst_1000_lines(tmp_path: Path):
    """
    Scenario 2.1: Write 1,000 JSON lines in a single burst, then append 500 more.
    Verify that all lines are parsed, offsets are tracked precisely, and no lines are dropped or corrupted.
    """
    log_file = tmp_path / "transcript.jsonl"

    # Generate 1,000 valid JSON lines
    lines_batch1 = []
    for i in range(1000):
        entry = {
            "step_index": i,
            "source": "MODEL" if i % 2 == 0 else "USER_EXPLICIT",
            "type": "PLANNER_RESPONSE" if i % 2 == 0 else "USER_INPUT",
            "status": "DONE",
            "created_at": f"2026-08-14T08:00:{i%60:02d}Z",
            "content": f"High throughput step payload {i} - {'A' * 100}"
        }
        lines_batch1.append(json.dumps(entry) + "\n")

    log_file.write_text("".join(lines_batch1), encoding="utf-8")

    tailer = TranscriptTailer(log_file)
    steps1, offset1 = tailer.tail_from_offset(0)
    assert len(steps1) == 1000
    assert offset1 == log_file.stat().st_size
    assert steps1[0]["step_index"] == 0
    assert steps1[999]["step_index"] == 999

    # Append 500 more lines
    lines_batch2 = []
    for i in range(1000, 1500):
        entry = {
            "step_index": i,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": f"2026-08-14T08:01:{i%60:02d}Z",
            "content": f"Burst batch 2 payload {i}"
        }
        lines_batch2.append(json.dumps(entry) + "\n")

    with open(log_file, "a", encoding="utf-8") as f:
        f.write("".join(lines_batch2))

    steps2, offset2 = tailer.tail_from_offset(offset1)
    assert len(steps2) == 500
    assert offset2 == log_file.stat().st_size
    assert steps2[0]["step_index"] == 1000
    assert steps2[499]["step_index"] == 1499


def test_tailer_partial_json_split_at_arbitrary_byte_boundaries(tmp_path: Path):
    """
    Scenario 2.2: Partial JSON fragments split at arbitrary byte boundaries without newlines.
    The tailer must NOT consume or crash on partial trailing bytes until the newline arrives.
    """
    log_file = tmp_path / "transcript.jsonl"
    full_entry = {
        "step_index": 42,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": "2026-08-14T08:15:00Z",
        "content": "A complete response after partial streaming writes.",
        "thinking": "Partial thinking token stream simulation."
    }
    json_bytes = (json.dumps(full_entry) + "\n").encode("utf-8")

    tailer = TranscriptTailer(log_file)

    # Write in 7-byte slices
    written_bytes = 0
    slice_size = 7
    log_file.write_bytes(b"")

    with open(log_file, "wb") as f:
        while written_bytes < len(json_bytes):
            chunk = json_bytes[written_bytes : written_bytes + slice_size]
            f.write(chunk)
            f.flush()
            written_bytes += len(chunk)

            steps, offset = tailer.tail_from_offset(0)
            if written_bytes < len(json_bytes):
                # No complete newline yet
                assert len(steps) == 0
                assert offset == 0
            else:
                # Newline written
                assert len(steps) == 1
                assert steps[0]["step_index"] == 42
                assert steps[0]["content"] == "A complete response after partial streaming writes."
                assert offset == len(json_bytes)


def test_tailer_file_truncation_mid_stream_sync(tmp_path: Path):
    """
    Scenario 2.3: File truncation mid-stream (file size shrinks below recorded offset).
    The tailer must detect truncation (offset > file_size), reset offset to 0, and read accurately.
    """
    log_file = tmp_path / "transcript.jsonl"

    # Write 10 lines
    initial_lines = [
        json.dumps({"step_index": i, "content": f"Initial step {i}"}) + "\n"
        for i in range(10)
    ]
    log_file.write_text("".join(initial_lines), encoding="utf-8")

    tailer = TranscriptTailer(log_file)
    steps1, offset1 = tailer.tail_from_offset(0)
    assert len(steps1) == 10
    assert offset1 >= 400

    # Truncate file down to 2 lines
    new_lines = [
        json.dumps({"step_index": 100 + i, "content": f"New step {i}"}) + "\n"
        for i in range(2)
    ]
    log_file.write_text("".join(new_lines), encoding="utf-8")
    assert log_file.stat().st_size < offset1

    # Seek with stale offset1
    steps2, offset2 = tailer.tail_from_offset(offset1)
    assert len(steps2) == 2
    assert steps2[0]["step_index"] == 100
    assert steps2[1]["step_index"] == 101
    assert offset2 == log_file.stat().st_size


@pytest.mark.asyncio
async def test_tailer_async_truncation_live_stream_recovery(tmp_path: Path):
    """
    Scenario 2.4: Live streaming async generator tail(follow=True) handling abrupt file truncation.
    """
    log_file = tmp_path / "transcript.jsonl"
    log_file.write_text(json.dumps({"step_index": 1, "content": "Before truncation"}) + "\n", encoding="utf-8")

    tailer = AsyncTranscriptTailer(log_file, min_poll_interval=0.01, max_poll_interval=0.05)

    received = []
    tail_task = asyncio.create_task(
        _collect_entries(tailer, received, target_count=3, timeout=3.0)
    )

    await asyncio.sleep(0.05)
    # Truncate to empty, then write new step 2 and step 3
    log_file.write_text("", encoding="utf-8")
    await asyncio.sleep(0.05)

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps({"step_index": 2, "content": "After truncation 1"}) + "\n")
        f.write(json.dumps({"step_index": 3, "content": "After truncation 2"}) + "\n")

    await tail_task
    tailer.stop()

    assert len(received) >= 3
    indices = [r.step_index for r in received]
    assert 1 in indices
    assert 2 in indices
    assert 3 in indices


async def _collect_entries(tailer: AsyncTranscriptTailer, out_list: List[TranscriptEntry], target_count: int, timeout: float = 3.0):
    start = time.monotonic()
    async for entry in tailer.tail(from_offset=0, follow=True):
        out_list.append(entry)
        if len(out_list) >= target_count or (time.monotonic() - start) > timeout:
            break


def test_tailer_zero_byte_and_nonexistent_file(tmp_path: Path):
    """
    Scenario 2.5: Zero-byte transcript file and non-existent file path handling.
    Must return empty list and zero/preserved offset without crashing.
    """
    # 1. Zero-byte file
    zero_file = tmp_path / "empty_transcript.jsonl"
    zero_file.write_bytes(b"")

    tailer = TranscriptTailer(zero_file)
    steps, offset = tailer.tail_from_offset(0)
    assert steps == []
    assert offset == 0

    # 2. Non-existent file
    missing_file = tmp_path / "does_not_exist.jsonl"
    tailer_missing = TranscriptTailer(missing_file)
    steps_m, offset_m = tailer_missing.tail_from_offset(0)
    assert steps_m == []
    assert offset_m == 0


def test_tailer_utf8_invalid_multibyte_sequences_and_unicode_fuzz(tmp_path: Path):
    """
    Scenario 2.6: Invalid raw UTF-8 sequences (0xFF, 0xFE, unpaired surrogates)
    and complex multi-lingual Unicode (Vietnamese, Chinese, Japanese, Emoji).
    The tailer must not raise UnicodeDecodeError and must preserve valid multi-byte sequences.
    """
    log_file = tmp_path / "transcript.jsonl"

    # Valid Unicode Line with Vietnamese, Emojis, and Math Symbols
    valid_unicode_step = {
        "step_index": 1,
        "content": "Kiểm tra Tiếng Việt: Ă Â Đ Ê Ô Ơ Ư và Biểu tượng cảm xúc 🚀 🤖 ⚡ 🎯 ∫ f(x)dx = ∑ x_i",
        "thinking": "Phân tích suy nghĩ bằng ngôn ngữ tự nhiên..."
    }
    line1 = (json.dumps(valid_unicode_step, ensure_ascii=False) + "\n").encode("utf-8")

    # Corrupted UTF-8 line (invalid bytes injected into string payload)
    corrupted_raw_line = b'{"step_index": 2, "content": "Corrupted bytes: \xff\xfe\x80\x81 here", "status": "DONE"}\n'

    log_file.write_bytes(line1 + corrupted_raw_line)

    tailer = TranscriptTailer(log_file)
    steps, offset = tailer.tail_from_offset(0)
    assert len(steps) == 2
    assert "Tiếng Việt" in steps[0]["content"]
    assert "🚀" in steps[0]["content"]
    assert steps[1]["step_index"] == 2
    # Invalid bytes replaced with Unicode replacement character \ufffd
    assert "\ufffd" in steps[1]["content"] or "Corrupted bytes" in steps[1]["content"]


@pytest.mark.asyncio
async def test_tailer_byte_offset_seek_accuracy_across_chunk_boundaries(tmp_path: Path):
    """
    Scenario 2.7: Byte-offset seek accuracy across tiny chunk boundaries (chunk_size=19 bytes).
    Verify that entry.byte_offset_start and entry.byte_offset_end exactly match file slices.
    """
    log_file = tmp_path / "transcript.jsonl"

    entries_data = []
    for i in range(30):
        e = {
            "step_index": i,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "content": f"Step payload index {i} " + ("#" * (30 + (i * 7))),
        }
        entries_data.append(e)

    full_text = "".join(json.dumps(e) + "\n" for e in entries_data)
    log_file.write_text(full_text, encoding="utf-8")
    raw_file_bytes = log_file.read_bytes()

    # Small prime chunk size to force cross-chunk line accumulation
    tailer = AsyncTranscriptTailer(log_file, chunk_size=19)

    yielded_entries: List[TranscriptEntry] = []
    async for entry in tailer.tail(from_offset=0, follow=False):
        yielded_entries.append(entry)

    assert len(yielded_entries) == 30

    # Verify every entry's byte boundaries against raw disk bytes
    for entry in yielded_entries:
        start = entry.byte_offset_start
        end = entry.byte_offset_end
        assert start < end
        slice_bytes = raw_file_bytes[start:end]
        assert slice_bytes.endswith(b"\n")
        parsed_slice = json.loads(slice_bytes.decode("utf-8").strip())
        assert parsed_slice["step_index"] == entry.step_index


@pytest.mark.asyncio
async def test_tailer_websocket_events_streaming_and_nested_arg_normalization(tmp_path: Path):
    """
    Scenario 2.8: stream_websocket_events generates typed events:
    token_stream (thinking, content), tool_call_request (with normalized JSON string args), and step_update.
    """
    log_file = tmp_path / "transcript.jsonl"

    complex_step = {
        "step_index": 5,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "PENDING_APPROVAL",
        "created_at": "2026-08-14T08:20:00Z",
        "thinking": "Generating deep thought reasoning...",
        "content": "Here is the response proposal.",
        "tool_calls": [
            {
                "id": "tc_complex_99",
                "name": "run_command",
                "args": {
                    "CommandLine": "Get-Process",
                    "JsonConfig": '{"timeout": 30, "retries": 3}',
                    "NestedList": '["item1", "item2"]',
                    "QuotedString": '"simple_value"'
                },
                "toolAction": "Running PowerShell command",
                "toolSummary": "Get-Process tool call"
            }
        ]
    }
    log_file.write_text(json.dumps(complex_step) + "\n", encoding="utf-8")

    tailer = AsyncTranscriptTailer(log_file)
    events = []
    async for ev in tailer.stream_websocket_events(from_offset=0, follow=False):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "token_stream" in types
    assert "tool_call_request" in types
    assert "step_update" in types

    # Verify thinking token stream event
    th_ev = next(e for e in events if e["type"] == "token_stream" and e.get("kind") == "thinking")
    assert th_ev["delta"] == "Generating deep thought reasoning..."

    # Verify content token stream event
    cnt_ev = next(e for e in events if e["type"] == "token_stream" and e.get("kind") == "content")
    assert cnt_ev["delta"] == "Here is the response proposal."

    # Verify tool call event and arg normalization
    tc_ev = next(e for e in events if e["type"] == "tool_call_request")
    assert tc_ev["status"] == "PENDING_APPROVAL"
    args = tc_ev["tool_call"]["args"]
    assert args["JsonConfig"] == {"timeout": 30, "retries": 3}
    assert args["NestedList"] == ["item1", "item2"]
    assert args["QuotedString"] == "simple_value"


def test_tailer_malformed_json_lines_skipped_resiliently(tmp_path: Path):
    """
    Scenario 2.9: Interleaved valid lines, syntax-corrupted JSON, and empty whitespace lines.
    The tailer must skip corrupted lines and cleanly yield all valid steps.
    """
    log_file = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"step_index": 1, "content": "Valid line 1"}) + "\n",
        '{"step_index": 2, "corrupt_json: true\n',  # Syntax error (missing quote)
        "\n",  # Empty line
        "   \r\n",  # Whitespace line
        json.dumps({"step_index": 3, "content": "Valid line 3"}) + "\n",
        "NOT_EVEN_JSON_DATA_AT_ALL\n",  # Pure garbage
        json.dumps({"step_index": 4, "content": "Valid line 4"}) + "\n",
    ]
    log_file.write_text("".join(lines), encoding="utf-8")

    tailer = TranscriptTailer(log_file)
    steps, offset = tailer.tail_from_offset(0)
    assert len(steps) == 3
    indices = [s["step_index"] for s in steps]
    assert indices == [1, 3, 4]
    assert offset == log_file.stat().st_size


# ===========================================================================
# 3. Additional Hardcore Adversarial Scenarios
# ===========================================================================

def test_sqlite_sql_injection_and_complex_filters(tmp_path: Path):
    """
    Scenario 3.1: SQL injection attempts and complex filter combinations in SQLiteReader.
    Verify parameterized queries prevent SQL injection and filter combinations work correctly.
    """
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
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
            killed NUMERIC,
            last_user_input_time TEXT
        )
    """)
    conn.execute("INSERT INTO conversation_summaries VALUES ('c1', 'Normal Title', 'preview', 5, '2026-08-14T08:00:00Z', '[\"e:/ws\"]', 'active', 'agent1', NULL, 0, 0, '')")
    conn.execute("INSERT INTO conversation_summaries VALUES ('c2', 'Secret Project', 'classified', 10, '2026-08-14T08:10:00Z', '[\"e:/ws\"]', 'killed', 'agent2', 'c1', 1, 1, '')")
    conn.commit()
    conn.close()

    reader = SQLiteReader(antigravity_dir=tmp_path)

    # SQL Injection payload in search
    sql_inj = "Normal'; DROP TABLE conversation_summaries; --"
    res_inj = reader.list_conversations(search=sql_inj)
    assert isinstance(res_inj, list)
    assert len(res_inj) == 0

    # Ensure table was NOT dropped
    all_convs = reader.list_conversations()
    assert len(all_convs) == 2

    # Test root filter (parent_id="root" or "")
    root_convs = reader.list_conversations(parent_id="root")
    assert len(root_convs) == 1
    assert root_convs[0]["id"] == "c1"

    # Test subagent filter (parent_id="c1")
    child_convs = reader.list_conversations(parent_id="c1")
    assert len(child_convs) == 1
    assert child_convs[0]["id"] == "c2"

    # Test status filter
    killed_convs = reader.list_conversations(status="killed")
    assert len(killed_convs) == 1
    assert killed_convs[0]["id"] == "c2"


def test_sqlite_timestamp_parsing_fuzz():
    """
    Scenario 3.2: Fuzzing _parse_iso_timestamp with various edge-case timestamps.
    """
    assert _parse_iso_timestamp(None) is None
    assert _parse_iso_timestamp("") is None
    assert _parse_iso_timestamp("not-a-valid-date") is None

    # Epoch string
    assert _parse_iso_timestamp("1770000000.5") == 1770000000.5

    # Standard ISO
    ts1 = _parse_iso_timestamp("2026-08-14T08:00:00Z")
    assert ts1 is not None and ts1 > 0

    # ISO with microsecond and timezone
    ts2 = _parse_iso_timestamp("2026-08-14T08:00:00.123456+07:00")
    assert ts2 is not None and ts2 > 0

    # ISO with excessive fractional digits (nanoseconds)
    ts3 = _parse_iso_timestamp("2026-08-14T08:00:00.123456789+00:00")
    assert ts3 is not None and ts3 > 0


def test_tailer_large_payload_2mb_line(tmp_path: Path):
    """
    Scenario 3.3: Giant 2MB single JSON line (e.g. large file diff or image payload).
    Verify tailer buffers and parses the entire line correctly.
    """
    log_file = tmp_path / "transcript.jsonl"
    big_content = "X" * (2 * 1024 * 1024)  # 2MB string
    large_step = {
        "step_index": 1,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "content": big_content
    }
    log_file.write_text(json.dumps(large_step) + "\n", encoding="utf-8")

    tailer = TranscriptTailer(log_file)
    steps, offset = tailer.tail_from_offset(0)
    assert len(steps) == 1
    assert len(steps[0]["content"]) == len(big_content)
    assert offset == log_file.stat().st_size


@pytest.mark.asyncio
async def test_tailer_concurrent_multi_subscriber_stress(tmp_path: Path):
    """
    Scenario 3.4: 10 concurrent async tailers reading from the same transcript file simultaneously
    while new lines are actively appended in real-time.
    """
    log_file = tmp_path / "transcript.jsonl"
    log_file.write_text(json.dumps({"step_index": 0, "content": "Init"}) + "\n", encoding="utf-8")

    num_subscribers = 10
    total_steps_to_write = 15

    received_by_subscriber: Dict[int, List[int]] = {i: [] for i in range(num_subscribers)}

    async def subscriber_task(sub_id: int):
        tailer = AsyncTranscriptTailer(log_file, min_poll_interval=0.01, max_poll_interval=0.05)
        async for entry in tailer.tail(from_offset=0, follow=True):
            received_by_subscriber[sub_id].append(entry.step_index)
            if len(received_by_subscriber[sub_id]) >= total_steps_to_write:
                break

    # Start all 10 subscriber tasks
    tasks = [asyncio.create_task(subscriber_task(i)) for i in range(num_subscribers)]

    # Concurrently append steps to log_file
    for s in range(1, total_steps_to_write):
        await asyncio.sleep(0.01)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"step_index": s, "content": f"Live step {s}"}) + "\n")

    await asyncio.gather(*tasks)

    # Verify every subscriber received all 15 steps in order 0..14
    for sub_id, steps in received_by_subscriber.items():
        assert len(steps) == total_steps_to_write, f"Subscriber {sub_id} missed steps: {steps}"
        assert steps == list(range(total_steps_to_write))

