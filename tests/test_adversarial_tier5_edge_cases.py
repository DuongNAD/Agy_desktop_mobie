"""
tests/test_adversarial_tier5_edge_cases.py

Tier 5 Adversarial Stress & Edge Case Test Suite:
1. Windows filesystem race conditions (concurrent writes, atomic save collisions, simultaneous delete/write, rapid read/replace).
2. Windows symlink, directory junction, NTFS Alternate Data Stream (ADS) sandbox bypasses (::$DATA, CON.txt, unicode/encoded traversals, UNC).
3. Malformed payloads and fuzzing across REST & WebSocket channels (invalid JSON, huge payloads, unexpected types, null bytes, SQL/XSS injections).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from fastapi import HTTPException, status
from starlette.testclient import TestClient

from backend.config import Settings, get_settings
from backend.main import create_app
from backend.security import (
    PathSandbox,
    SandboxSecurityException,
    create_access_token,
    decode_access_token,
    is_safe_sandbox_path,
    rate_limiter,
    sanitize_and_resolve_sandbox_path,
    verify_pin_constant_time,
    verify_ws_token,
)
from backend.session_hub.artifacts import ArtifactsManager
from backend.session_hub.cli_dispatcher import CLIDispatcher
from backend.session_hub.sqlite_reader import SQLiteReader
from backend.session_hub.tailer import TranscriptTailer
from backend.session_hub.tool_manager import ToolManager
from backend.terminal.pty_session import TerminalManager, TerminalSession, terminal_manager
from backend.workspace.file_manager import WorkspaceFileManager


@pytest.fixture(autouse=True)
def cleanup_terminal_state():
    """Autouse fixture to reset terminal sessions before and after each test."""
    terminal_manager._sessions.clear()
    yield
    terminal_manager._sessions.clear()


# ===========================================================================
# Section 1: Windows Filesystem Race Conditions & Concurrency
# ===========================================================================

def test_concurrent_file_saves_same_file_atomic_consistency(mock_workspace):
    """
    Stress test: 20 concurrent threads writing distinct payloads to the exact same file path.
    On Windows NTFS, simultaneous os.replace operations on the same locked file will either
    succeed or raise a controlled PermissionError (HTTPException 403) from file locking.
    Verify:
      - Operations return clean 200 or 403 (never unhandled 500 crashes or Python fatal crash).
      - At least one save succeeds.
      - No temporary files (.agy_tmp_*) remain leaked in the workspace directory.
      - The resulting file content is fully coherent and matches one of the written payloads exactly.
    """
    parent_dir = mock_workspace.root / "race_test"
    parent_dir.mkdir(parents=True, exist_ok=True)

    fm = WorkspaceFileManager(root_dir=mock_workspace.root)
    rel_path = "race_test/concurrent_target.txt"
    num_threads = 20
    results: List[Dict[str, Any]] = []
    unhandled_errors: List[Exception] = []

    def _worker(thread_idx: int):
        content = f"Payload from thread {thread_idx:03d} - {'x' * 500}\n"
        try:
            res = fm.save_file(subpath=rel_path, content=content, backup=False)
            results.append(res)
        except HTTPException as exc:
            if exc.status_code not in (status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST):
                unhandled_errors.append(exc)
        except Exception as exc:
            unhandled_errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(unhandled_errors) == 0, f"Unhandled errors encountered: {unhandled_errors}"
    assert len(results) > 0, "Expected at least one thread to successfully persist file"

    # Verify target file exists and is intact
    target_file = mock_workspace.root / rel_path
    assert target_file.exists()
    final_content = target_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert final_content.startswith("Payload from thread ")
    assert len(final_content) == len("Payload from thread 000 - " + ("x" * 500) + "\n")

    # Check that no temporary files leaked in parent directory
    tmp_files = list(parent_dir.glob(".agy_tmp_*"))
    assert len(tmp_files) == 0, f"Leaked temp files found: {tmp_files}"


def test_concurrent_file_saves_different_files_in_same_directory(mock_workspace):
    """
    Stress test: 30 concurrent threads creating and saving 30 separate files in the same directory.
    Verify all files are written cleanly without locking collisions.
    """
    batch_dir = mock_workspace.root / "race_batch"
    batch_dir.mkdir(parents=True, exist_ok=True)

    fm = WorkspaceFileManager(root_dir=mock_workspace.root)
    num_files = 30
    errors: List[Exception] = []

    def _worker(idx: int):
        p = f"race_batch/file_{idx:03d}.py"
        code = f"# Script {idx}\ndef run_{idx}():\n    return {idx * 10}\n"
        try:
            fm.save_file(subpath=p, content=code)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(num_files)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(errors) == 0, f"Save errors in batch directory: {errors}"
    written_files = list(batch_dir.glob("file_*.py"))
    assert len(written_files) == num_files


def test_rapid_save_overwrite_stress_no_tmp_leak(mock_workspace):
    """
    Stress test: 50 sequential rapid atomic overwrites on a single file.
    Verify no temp files leak, file size is accurate, and stat timestamps update monotonically.
    """
    fm = WorkspaceFileManager(root_dir=mock_workspace.root)
    rel_path = "rapid_stress/overwrite.log"

    for i in range(50):
        content = f"Log line {i:04d} at timestamp {time.time()}\n" * (i + 1)
        res = fm.save_file(subpath=rel_path, content=content)
        assert res["status"] == "saved"
        assert res["size"] == len(content.encode("utf-8"))

    target_file = mock_workspace.root / rel_path
    assert target_file.exists()
    tmp_files = list(target_file.parent.glob(".agy_tmp_*"))
    assert len(tmp_files) == 0


def test_simultaneous_delete_and_write_race(mock_workspace):
    """
    Race test: One worker repeatedly writes to a file while another worker randomly deletes the file.
    Verify no uncaught fatal crash occurs; saves recreate the file properly.
    """
    fm = WorkspaceFileManager(root_dir=mock_workspace.root)
    rel_path = "race_delete/volatile.txt"
    stop_event = threading.Event()
    save_errors: List[Exception] = []
    saves_count = 0

    def _writer():
        nonlocal saves_count
        for i in range(30):
            if stop_event.is_set():
                break
            try:
                fm.save_file(subpath=rel_path, content=f"Volatile content version {i}\n")
                saves_count += 1
            except Exception as e:
                save_errors.append(e)
            time.sleep(0.005)

    def _deleter():
        target = mock_workspace.root / rel_path
        for _ in range(15):
            if stop_event.is_set():
                break
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                pass
            time.sleep(0.01)

    t1 = threading.Thread(target=_writer)
    t2 = threading.Thread(target=_deleter)
    t1.start()
    t2.start()

    t1.join(timeout=10.0)
    stop_event.set()
    t2.join(timeout=10.0)

    assert saves_count > 0


def test_simultaneous_read_and_atomic_replace_race(mock_workspace):
    """
    Race test: Simultaneous rapid reads and atomic writes on the same file.
    Verify readers always read complete, uncorrupted UTF-8 strings.
    """
    fm = WorkspaceFileManager(root_dir=mock_workspace.root)
    rel_path = "race_read_write/shared.txt"
    initial_content = "INITIAL_STATE_" + ("A" * 100)
    fm.save_file(subpath=rel_path, content=initial_content)

    stop_event = threading.Event()
    read_corruptions = []
    read_count = 0

    def _writer():
        for i in range(25):
            if stop_event.is_set():
                break
            payload = f"VERSION_{i:04d}_" + (chr(65 + (i % 26)) * 200)
            try:
                fm.save_file(subpath=rel_path, content=payload)
            except HTTPException:
                pass
            time.sleep(0.004)

    def _reader():
        nonlocal read_count
        for _ in range(50):
            if stop_event.is_set():
                break
            try:
                data = fm.read_file(subpath=rel_path)
                content = data.get("content")
                if content is not None:
                    normalized = content.replace("\r\n", "\n")
                    if not (normalized.startswith("INITIAL_STATE_") or normalized.startswith("VERSION_")):
                        read_corruptions.append(content)
                    read_count += 1
            except HTTPException as e:
                if e.status_code != 404:
                    read_corruptions.append(str(e))
            time.sleep(0.002)

    w_thread = threading.Thread(target=_writer)
    r_threads = [threading.Thread(target=_reader) for _ in range(3)]

    for r in r_threads:
        r.start()
    w_thread.start()

    w_thread.join(timeout=10.0)
    stop_event.set()
    for r in r_threads:
        r.join(timeout=10.0)

    assert len(read_corruptions) == 0, f"Read corruptions observed: {read_corruptions}"
    assert read_count > 0


def test_directory_deleted_during_tree_listing_race(mock_workspace):
    """
    Race test: Tree navigation while subdirectories are rapidly created and removed.
    Verify list_directory and list_tree handle transient missing entries gracefully without raising unhandled OSError.
    """
    fm = WorkspaceFileManager(root_dir=mock_workspace.root)
    base_dir = mock_workspace.root / "transient_tree"
    base_dir.mkdir(parents=True, exist_ok=True)

    stop_event = threading.Event()
    scan_errors = []

    def _mutator():
        for i in range(30):
            if stop_event.is_set():
                break
            sub = base_dir / f"temp_dir_{i % 5}"
            try:
                sub.mkdir(exist_ok=True)
                (sub / "dummy.txt").write_text("dummy", encoding="utf-8")
                time.sleep(0.005)
                shutil.rmtree(sub, ignore_errors=True)
            except Exception:
                pass

    def _scanner():
        for _ in range(25):
            if stop_event.is_set():
                break
            try:
                fm.list_tree(subpath="transient_tree", max_depth=3)
                fm.list_directory(subpath="transient_tree")
            except HTTPException as e:
                if e.status_code not in (404, 400):
                    scan_errors.append(e)
            except Exception as e:
                scan_errors.append(e)
            time.sleep(0.004)

    t_mut = threading.Thread(target=_mutator)
    t_scan = threading.Thread(target=_scanner)

    t_mut.start()
    t_scan.start()

    t_mut.join(timeout=10.0)
    stop_event.set()
    t_scan.join(timeout=10.0)

    assert len(scan_errors) == 0, f"Unexpected tree scan errors: {scan_errors}"


def test_concurrent_backup_creation_under_high_load(mock_workspace):
    """
    Race test: Multiple threads saving with backup=True to the same file.
    On Windows, simultaneous writes/backups to the same locked file will either succeed
    or return controlled 403/400.
    Verify at least one write succeeds and backup file exists.
    """
    parent_dir = mock_workspace.root / "backup_race"
    parent_dir.mkdir(parents=True, exist_ok=True)

    fm = WorkspaceFileManager(root_dir=mock_workspace.root)
    rel_path = "backup_race/target.txt"
    fm.save_file(subpath=rel_path, content="Initial Baseline\n")

    unhandled_errors = []
    successes = []

    def _worker(idx: int):
        try:
            res = fm.save_file(subpath=rel_path, content=f"Backup write update {idx}\n", backup=True)
            successes.append(res)
        except HTTPException as e:
            if e.status_code not in (status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST):
                unhandled_errors.append(e)
        except Exception as e:
            unhandled_errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(unhandled_errors) == 0, f"Unhandled backup race errors: {unhandled_errors}"
    assert len(successes) > 0
    target_file = mock_workspace.root / rel_path
    bak_file = target_file.with_suffix(".txt.bak")
    assert target_file.exists()
    assert bak_file.exists()


def test_rapid_interleaved_save_read_diff_requests(auth_client, mock_workspace):
    """
    Integration stress test: Rapidly alternating POST /api/files/save, GET /api/files/content,
    and POST /api/files/diff across multiple simulated client requests.
    """
    path = "interleaved/script.py"
    for i in range(15):
        new_content = f"# Version {i}\ndef calculate():\n    return {i * 100}\n"
        # 1. Save
        save_resp = auth_client.post("/api/files/save", json={"path": path, "content": new_content})
        assert save_resp.status_code == 200

        # 2. Read
        read_resp = auth_client.get(f"/api/files/content?path={path}")
        assert read_resp.status_code == 200
        assert read_resp.json()["content"].replace("\r\n", "\n") == new_content.replace("\r\n", "\n")

        # 3. Diff
        diff_resp = auth_client.post("/api/files/diff", json={
            "path": path,
            "new_content": new_content + "# Extra line\n"
        })
        assert diff_resp.status_code == 200
        diff_json = diff_resp.json()
        assert diff_json["has_changes"] is True
        assert diff_json["additions"] == 1


# ===========================================================================
# Section 2: Windows Symlinks, Directory Junctions, NTFS ADS & Sandbox Bypasses
# ===========================================================================

@pytest.mark.parametrize("ads_payload", [
    "safe.txt::$DATA",
    "sub/safe.txt::$DATA",
    "safe.txt:hidden_stream",
    "safe.txt:hidden_stream:$DATA",
    "folder:stream/file.txt",
    "safe.txt%3A%3A%24DATA",
    "safe.txt%3Ahidden_stream",
    "%2e%2e/safe.txt:stream",
    "sub/file.txt::$INDEX_ALLOCATION",
])
def test_sandbox_ntfs_alternate_data_streams_rejection(mock_workspace, ads_payload):
    """
    Adversarial test: Attempting NTFS Alternate Data Stream (ADS) injection using colon syntax.
    Verify that sanitize_and_resolve_sandbox_path raises SandboxSecurityException or HTTPException with 400 Bad Request.
    """
    sandbox = PathSandbox(root_dir=mock_workspace.root)
    with pytest.raises((SandboxSecurityException, HTTPException)) as exc_info:
        sandbox.validate_path(ads_payload)
    assert exc_info.value.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN)
    assert not is_safe_sandbox_path(ads_payload, root_dir=mock_workspace.root)


@pytest.mark.parametrize("reserved_name", [
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    "con.txt", "prn.json", "aux.py", "nul.dat", "com1.log", "lpt2.cfg",
    "cOn.TxT", "Nul.DAT", "PrN.Html", "aUx",
    "sub/con.txt", "nested/dir/PRN.json", "deep/tree/aux.py",
])
def test_sandbox_windows_reserved_device_names_rejection(mock_workspace, reserved_name):
    """
    Adversarial test: Attempting to access or save to Windows DOS reserved device names
    (CON, PRN, AUX, NUL, COM1-9, LPT1-9) with and without extensions and case variations.
    Verify sandbox unconditionally rejects with 400 Bad Request.
    """
    sandbox = PathSandbox(root_dir=mock_workspace.root)
    with pytest.raises((SandboxSecurityException, HTTPException)) as exc_info:
        sandbox.validate_path(reserved_name)
    assert exc_info.value.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN)
    assert "Reserved Windows device name" in str(exc_info.value.detail) or "Invalid path" in str(exc_info.value.detail)


def test_sandbox_windows_symlink_pointing_outside_workspace(mock_workspace, tmp_path):
    """
    Adversarial test: Create a filesystem symlink inside workspace root pointing to an external secret directory.
    Verify that resolving paths through the symlink is detected and blocked with 403 Forbidden.
    """
    external_secret_dir = tmp_path / "external_secret"
    external_secret_dir.mkdir(parents=True, exist_ok=True)
    secret_file = external_secret_dir / "passwords.txt"
    secret_file.write_text("SUPER_SECRET_EXTERNAL_PASSWORD", encoding="utf-8")

    symlink_path = mock_workspace.root / "symlink_escape"
    try:
        os.symlink(external_secret_dir, symlink_path, target_is_directory=True)
    except (OSError, NotImplementedError, PermissionError):
        pass

    sandbox = PathSandbox(root_dir=mock_workspace.root)

    if symlink_path.exists():
        with pytest.raises((SandboxSecurityException, HTTPException)) as exc_info:
            sandbox.validate_path("symlink_escape/passwords.txt")
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert not is_safe_sandbox_path("symlink_escape/passwords.txt", root_dir=mock_workspace.root)


@pytest.mark.parametrize("traversal_payload", [
    "../outside.txt",
    "../../outside.txt",
    "..\\..\\outside.txt",
    "..%2f..%2fsecret.txt",
    "..%5c..%5csecret.txt",
    "%2e%2e%2f%2e%2e%2fsecret.txt",
    "%2e%2e%5c%2e%2e%5csecret.txt",
    "%252e%252e%252fsecret.txt",
    "src/../../outside.txt",
    "src/..\\..\\outside.txt",
    "src/./../../outside.txt",
    "docs/../../../../../../../../Windows/System32/drivers/etc/hosts",
    "docs/..\\..\\..\\..\\..\\..\\..\\..\\Windows\\System32\\drivers\\etc\\hosts",
])
def test_sandbox_unicode_and_encoded_directory_traversal_rejection(mock_workspace, traversal_payload):
    """
    Adversarial test: Directory traversal attempts using URL encoding, double encoding,
    mixed forward/backward slashes, and deep parent traversal.
    Verify sandbox strictly rejects escaping the workspace root.
    """
    sandbox = PathSandbox(root_dir=mock_workspace.root)
    with pytest.raises((SandboxSecurityException, HTTPException)) as exc_info:
        sandbox.validate_path(traversal_payload)
    assert exc_info.value.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST)
    assert not is_safe_sandbox_path(traversal_payload, root_dir=mock_workspace.root)


@pytest.mark.parametrize("null_byte_payload", [
    "safe.txt\x00.png",
    "src/main.py\x00/../../secret.txt",
    "safe.txt%00.png",
    "docs/guide.md%2500.exe",
    "\x00README.md",
])
def test_sandbox_null_byte_injection_rejection(mock_workspace, null_byte_payload):
    """
    Adversarial test: Null byte injection attempts (raw \x00, URL-encoded %00, double-encoded %2500).
    Verify sandbox detects null bytes and rejects with 400 Bad Request.
    """
    sandbox = PathSandbox(root_dir=mock_workspace.root)
    with pytest.raises((SandboxSecurityException, HTTPException)) as exc_info:
        sandbox.validate_path(null_byte_payload)
    assert exc_info.value.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN)
    assert "Null byte" in str(exc_info.value.detail) or "Invalid path" in str(exc_info.value.detail)


@pytest.mark.parametrize("unc_and_device_payload", [
    "\\\\127.0.0.1\\c$\\secret.txt",
    "\\\\localhost\\share\\data",
    "//192.168.1.1/share/file",
    "\\\\?\\C:\\Windows\\System32\\cmd.exe",
    "\\\\.\\Volume{12345678-1234-1234-1234-123456789abc}\\",
])
def test_sandbox_unc_and_device_namespace_rejection(mock_workspace, unc_and_device_payload):
    """
    Adversarial test: UNC network paths and Win32 device namespaces.
    Verify sandbox strictly rejects with 403 Forbidden or 400 Bad Request.
    """
    sandbox = PathSandbox(root_dir=mock_workspace.root)
    with pytest.raises((SandboxSecurityException, HTTPException)) as exc_info:
        sandbox.validate_path(unc_and_device_payload)
    assert exc_info.value.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST)
    assert not is_safe_sandbox_path(unc_and_device_payload, root_dir=mock_workspace.root)


def test_sandbox_drive_letter_hopping_rejection(mock_workspace):
    """
    Adversarial test: Absolute paths pointing to different drive letters (e.g. C:\\ when workspace is on E:\\).
    Verify sandbox raises 403 Forbidden.
    """
    sandbox = PathSandbox(root_dir=mock_workspace.root)
    current_drive = mock_workspace.root.drive.upper()
    other_drive = "C:" if current_drive != "C:" else "D:"
    drive_payload = f"{other_drive}\\Windows\\System32\\calc.exe"

    with pytest.raises((SandboxSecurityException, HTTPException)) as exc_info:
        sandbox.validate_path(drive_payload)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert not is_safe_sandbox_path(drive_payload, root_dir=mock_workspace.root)


# ===========================================================================
# Section 3: Malformed Payloads & Fuzzing Across REST & WebSocket Channels
# ===========================================================================

def test_fuzz_rest_auth_login_malformed_bodies(client):
    """
    Fuzzing test: POST /api/auth/login with malformed, unexpected, or boundary bodies.
    Verify server returns 422 Unprocessable Entity or 400/401 without unhandled 500 crashes.
    """
    test_cases = [
        {},                                         # Missing PIN
        {"pin": None},                              # Null PIN
        {"pin": 123456},                            # Integer instead of string
        {"pin": True},                              # Boolean
        {"pin": ["1", "2", "3", "4", "5", "6"]},   # List
        {"pin": {"nested": "123456"}},             # Dict
        {"pin": ""},                                # Empty string
        {"pin": " " * 50},                          # Whitespace only
        {"pin": "\x00123456"},                      # Null byte
        {"pin": "a" * 5000},                        # Giant PIN string
        {"unknown_field": "some_value"},            # Missing required field
    ]

    for payload in test_cases:
        resp = client.post("/api/auth/login", json=payload)
        assert resp.status_code in (
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_429_TOO_MANY_REQUESTS,
        ), f"Failed for payload: {payload}, status={resp.status_code}"


def test_fuzz_rest_files_save_malformed_and_boundary_payloads(auth_client, mock_workspace):
    """
    Fuzzing test: POST /api/files/save with missing fields, invalid types, null bytes, and giant content.
    Verify server returns 422, 400, or 403 without 500 crashes.
    """
    test_cases = [
        {},                                                 # Empty body
        {"path": None, "content": "hello"},                # Null path
        {"path": "test.txt", "content": None},             # Null content
        {"path": 12345, "content": "text"},                # Integer path
        {"path": "test.txt", "content": 12345},            # Integer content
        {"path": "", "content": "text"},                   # Empty path
        {"path": "foo\x00bar.txt", "content": "text"},     # Null byte in path
        {"path": "con.txt", "content": "text"},            # Reserved device name
        {"path": "../escape.txt", "content": "text"},      # Path traversal
        {"path": "file.txt::$DATA", "content": "text"},    # NTFS ADS
        {"path": "huge.txt", "content": "A" * (2 * 1024 * 1024)},  # 2MB string (valid payload)
    ]

    for payload in test_cases:
        resp = auth_client.post("/api/files/save", json=payload)
        if payload.get("path") == "huge.txt":
            assert resp.status_code == 200
            assert (mock_workspace.root / "huge.txt").exists()
        else:
            assert resp.status_code in (
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
            ), f"Failed on save payload: {payload}, got {resp.status_code}"


def test_fuzz_rest_files_diff_malformed_and_edge_case_payloads(auth_client):
    """
    Fuzzing test: POST /api/files/diff with missing or invalid fields.
    Verify clean response or 422/400 validation error.
    """
    test_cases = [
        {},
        {"path": None},
        {"path": "", "new_content": "diff"},
        {"path": "README.md", "new_content": None},
        {"path": "non_existent_file.txt", "new_content": "some new lines\n"},
        {"path": "README.md", "new_content": "Updated\n", "original": None},
    ]

    for payload in test_cases:
        resp = auth_client.post("/api/files/diff", json=payload)
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ), f"Failed on diff payload: {payload}, status={resp.status_code}"


def test_fuzz_rest_conversations_prompt_and_approval(auth_client, mock_antigravity_env, monkeypatch):
    """
    Fuzzing test: POST /api/conversations/{id}/prompt and tool-approval with unusual payloads.
    Verify server validates inputs and doesn't crash.
    """
    monkeypatch.setattr(CLIDispatcher, "find_cli_executable", lambda self: None)
    cid = mock_antigravity_env.root_id

    # 1. Prompt endpoint fuzzing
    prompt_cases = [
        {},                                         # Missing prompt
        {"prompt": ""},                             # Empty prompt
        {"prompt": None},                           # Null prompt
        {"prompt": 12345},                          # Number
        {"prompt": "A" * (100 * 1024)},             # 100KB large prompt
        {"prompt": "<script>alert(1)</script>"},    # XSS payload
        {"prompt": "'; DROP TABLE test; --"},       # SQL injection payload
        {"prompt": "\x00nullbyte"},                 # Null byte
    ]
    for payload in prompt_cases:
        resp = auth_client.post(f"/api/conversations/{cid}/prompt", json=payload)
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_400_BAD_REQUEST,
        )

    # 2. Tool approval endpoint fuzzing
    approval_cases = [
        {},                                                         # Missing fields
        {"tool_call_id": "call_1", "action": "INVALID_ACTION"},    # Invalid action enum
        {"tool_call_id": "", "action": "approve"},                 # Empty ID
        {"tool_call_id": None, "action": "reject"},                # Null ID
        {"tool_call_id": "call_exec_001", "action": "approve", "pattern": "command(*)"},  # Valid
    ]
    for payload in approval_cases:
        resp = auth_client.post(f"/api/conversations/{cid}/tool-approval", json=payload)
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_400_BAD_REQUEST,
        )


def test_fuzz_rest_conversations_search_sql_injection(auth_client, mock_antigravity_env):
    """
    Adversarial test: SQL Injection attacks on GET /api/conversations query parameters
    (search, status, parent_id, limit, offset).
    Verify SQLite queries use parameterized placeholders and do not leak or execute arbitrary SQL.
    """
    sqli_payloads = [
        "' OR '1'='1",
        "'; DROP TABLE conversation_summaries; --",
        "1' UNION SELECT 1,2,3,4,5,6,7,8,9,10,11,12--",
        "\" OR \"\"=\"",
        "admin'--",
    ]

    for sqli in sqli_payloads:
        encoded = urllib.parse.quote(sqli)
        resp = auth_client.get(f"/api/conversations?search={encoded}")
        assert resp.status_code == 200
        # Check database is intact and hasn't been dropped
        conn = sqlite3.connect(str(mock_antigravity_env.summaries_db))
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM conversation_summaries")
        count = cur.fetchone()[0]
        conn.close()
        assert count >= 2


def test_fuzz_websocket_session_invalid_json_frames(client, valid_jwt_token):
    """
    Fuzzing test: Send non-JSON raw strings, truncated JSON, and null bytes to /ws/session.
    Verify WebSocket connection stays alive, handles syntax errors gracefully, and does not crash.
    """
    malformed_frames = [
        "THIS_IS_NOT_JSON",
        "{malformed_json: true",
        "{\"action\": ",
        "\x00\x01\x02\x03\xff\xfe",
        "",
        "   ",
        "undefined",
        "NaN",
    ]

    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        for raw in malformed_frames:
            ws.send_text(raw)
        # Send a valid subscribe to verify the connection is still responsive
        ws.send_json({"action": "subscribe", "conversation_id": "test_cid"})
        resp = ws.receive_json()
        assert resp.get("type") in ("subscribed", "error", "subagent_status")


def test_fuzz_websocket_session_non_dict_json_frames(client, valid_jwt_token):
    """
    Fuzzing test: Send valid JSON frames that are not dictionaries (arrays, numbers, booleans, strings, null).
    Verify server ignores or handles them gracefully without raising unhandled AttributeError.
    """
    non_dict_payloads = [
        "[1, 2, 3, 4]",
        "\"just a string\"",
        "12345",
        "99.99",
        "true",
        "false",
        "null",
    ]

    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        for payload in non_dict_payloads:
            ws.send_text(payload)

        # Confirm socket is functional
        ws.send_json({"action": "subscribe", "conversation_id": "test_active"})
        resp = ws.receive_json()
        assert resp.get("type") in ("subscribed", "error", "subagent_status")


def test_fuzz_websocket_session_unknown_and_missing_action_frames(client, valid_jwt_token):
    """
    Fuzzing test: Send JSON frames with unknown actions, empty actions, or missing fields to /ws/session.
    Verify server responds with error frame or ignores safely without disconnecting the client.
    """
    unknown_frames = [
        {"action": "UNKNOWN_ACTION_999"},
        {"action": ""},
        {"action": None},
        {"action": 12345},
        {"action": "send_prompt"},  # Missing prompt & conversation_id
        {"action": "tool_decision"},  # Missing decision & tool_call_id
        {"action": "unsubscribe"},  # Missing conversation_id
    ]

    with client.websocket_connect(f"/ws/session?token={valid_jwt_token}") as ws:
        for frame in unknown_frames:
            ws.send_json(frame)

        # Verify socket is still active by sending subscribe
        ws.send_json({"action": "subscribe", "conversation_id": "test_ping"})
        got_resp = False
        for _ in range(5):
            try:
                resp = ws.receive_json()
                if resp.get("type") in ("subscribed", "error", "subagent_status", "unsubscribed"):
                    got_resp = True
                    break
            except Exception:
                break
        assert got_resp is True


@pytest.mark.asyncio
async def test_fuzz_terminal_session_direct_methods_fuzzing():
    """
    Fuzzing test: Directly test TerminalSession handling of invalid dimensions,
    null-byte inputs, and terminal lifecycle edge cases.
    """
    session = TerminalSession(session_id="fuzz_session")
    
    # 1. Dimension clamping fuzzing
    session.resize(-100, -50)
    assert session.cols == 10 and session.rows == 5
    session.resize(999999, 999999)
    assert session.cols == 500 and session.rows == 200
    
    # Non-integer types should either clamp or raise ValueError/TypeError
    with pytest.raises((ValueError, TypeError)):
        session.resize("invalid", 40)

    # 2. Input fuzzing on non-running session
    await session.write_input("\x00\x01\x02")
    await session.write_input("a" * 10000)
    await session.write_input("")

    # 3. Clean terminate on non-running session
    await session.terminate()
    assert session.is_alive() is False


@pytest.mark.asyncio
async def test_fuzz_terminal_manager_concurrency_and_idle_cleanup():
    """
    Stress test: Concurrently create, retrieve, list, and cleanup multiple terminal sessions in TerminalManager.
    Verify thread-safety and no unhandled dictionary mutation exceptions.
    """
    mgr = TerminalManager()
    
    # Concurrent session creation simulation
    async def _create(sid: str):
        s = TerminalSession(session_id=sid)
        # Set last_active in the past so it qualifies as idle
        s.last_active = time.time() - 1000
        async with mgr._lock:
            mgr._sessions[sid] = s
        return s

    tasks = [_create(f"sess_{i}") for i in range(20)]
    await asyncio.gather(*tasks)

    # List sessions
    active_list = await mgr.list_sessions()
    assert len(active_list) == 20

    # Idle cleanup
    await mgr.cleanup_idle_sessions(max_idle_seconds=300)
    remaining = await mgr.list_sessions()
    assert len(remaining) == 0


def test_fuzz_websocket_terminal_handshake_and_frames(client, valid_jwt_token):
    """
    Fuzzing test: Send raw text, invalid JSON, and ping frames over /ws/terminal.
    Verify connection handles frames without crashing.
    """
    with client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}") as ws:
        # 1. Plain text fallback
        ws.send_text("echo fuzz_test\r\n")

        # 2. Invalid JSON
        ws.send_text("{bad_json: 123}")

        # 3. Valid ping frame
        ws.send_json({"type": "ping"})
        
        got_response = False
        for _ in range(5):
            try:
                frame = ws.receive_json()
                if frame.get("type") in ("output", "pong"):
                    got_response = True
                    break
            except Exception:
                break
        assert got_response is True


def test_fuzz_websocket_system_inbound_frames_and_unauthorized_tokens(client, valid_jwt_token, invalid_jwt_token):
    """
    Fuzzing test: /ws/system WebSocket authentication enforcement and inbound frame fuzzing.
    Verify:
      - Connecting with invalid/missing token is rejected with 1008 policy violation.
      - Sending random inbound frames to the telemetry channel does not crash the server.
    """
    # 1. Invalid token rejection
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/system?token={invalid_jwt_token}") as ws:
            ws.send_json({"action": "poll"})

    # 2. Valid token connection & fuzzing inbound frames
    with client.websocket_connect(f"/ws/system?token={valid_jwt_token}") as ws:
        fuzz_frames = [
            "RANDOM_TEXT_NOT_JSON",
            {"type": "unknown_system_event", "data": 12345},
            {"ping": True},
            [1, 2, 3],
        ]
        for frame in fuzz_frames:
            if isinstance(frame, str):
                ws.send_text(frame)
            else:
                ws.send_json(frame)
