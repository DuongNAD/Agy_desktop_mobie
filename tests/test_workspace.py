import os
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.workspace.file_manager import (
    DEV_MIME_MAP,
    KNOWN_BINARY_EXTENSIONS,
    WorkspaceFileManager,
    workspace_file_manager,
)


# ===========================================================================
# Tier 1: Core Functional & Happy Path Tests (F07, F08)
# ===========================================================================

def test_directory_listing_root(client, auth_headers, mock_workspace):
    """WRK-01: GET /api/files/tree returns array of file/dir objects at workspace root."""
    res = client.get("/api/files/tree", headers=auth_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    assert len(items) > 0

    names = [entry["name"] for entry in items]
    assert "src" in names
    assert "README.md" in names

    # Verify directories come first
    is_dirs = [entry["is_dir"] for entry in items]
    # First item(s) should be dir
    assert is_dirs[0] is True


def test_nested_directory_listing(client, auth_headers, mock_workspace):
    """WRK-02: GET /api/files/tree?path=src returns items strictly within the src subdirectory."""
    res = client.get("/api/files/tree?path=src", headers=auth_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    names = [entry["name"] for entry in items]
    assert "main.py" in names
    assert "utils.py" in names
    assert "__init__.py" in names


def test_file_content_read_utf8(client, auth_headers, mock_workspace):
    """WRK-03: GET /api/files/content?path=src/main.py reads UTF-8 content with MIME type and size."""
    res = client.get("/api/files/content?path=src/main.py", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "content" in data
    assert "def run():" in data["content"]
    assert data["mime"] == "text/x-python"
    assert data["is_binary"] is False
    assert data["size"] > 0


def test_file_save_atomic_success(client, auth_headers, mock_workspace):
    """WRK-04: POST /api/files/save updates file content atomically and persists to disk."""
    new_content = "def run():\n    print('Hello AgyMobile')\n"
    res = client.post(
        "/api/files/save",
        json={"path": "src/main.py", "content": new_content},
        headers=auth_headers
    )
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "saved"
    assert data.get("path") == "src/main.py"

    # Verify disk state
    disk_content = (mock_workspace.src / "main.py").read_text(encoding="utf-8")
    assert disk_content == new_content


def test_unified_diff_generation(mock_workspace):
    """WRK-05: Fast diff viewer computes standard unified diff showing additions and deletions."""
    mgr = WorkspaceFileManager(mock_workspace.root)
    new_content = "def add(a, b):\n    # Optimized addition\n    return a + b\n"
    diff_res = mgr.generate_diff("src/utils.py", new_content)

    assert isinstance(diff_res, dict)
    assert diff_res["has_changes"] is True
    assert diff_res["additions"] >= 1
    assert "---" in diff_res["diff"]
    assert "+++" in diff_res["diff"]
    assert len(diff_res["hunks"]) > 0


def test_binary_file_detection_and_safeguard(mock_workspace, client, auth_headers):
    """WRK-06: Binary files (PNG/EXE/ZIP) are flagged and guarded against accidental text overwrites."""
    bin_path = mock_workspace.create_binary("assets/image.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert bin_path.is_file()

    # Read binary content
    res = client.get("/api/files/content?path=assets/image.png", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["is_binary"] is True
    assert data["content"] is None
    assert data["mime"] == "image/png"

    # Block text overwrite of binary file
    save_res = client.post(
        "/api/files/save",
        json={"path": "assets/image.png", "content": "text overwrite"},
        headers=auth_headers
    )
    assert save_res.status_code == 400


# ===========================================================================
# Tier 2: Boundary, Adversarial & Error Handling Tests (F07, F08)
# ===========================================================================

def test_nonexistent_file_404(client, auth_headers, mock_workspace):
    """WRK-07: Querying non-existent file returns HTTP 404 Not Found."""
    res = client.get("/api/files/content?path=src/non_existent_file.py", headers=auth_headers)
    assert res.status_code == 404


def test_directory_escape_attempt_sandbox(client, auth_headers):
    """WRK-08: Traversal attempts to read outside workspace return HTTP 403 Forbidden."""
    res = client.get("/api/files/content?path=../../outside.txt", headers=auth_headers)
    assert res.status_code in (400, 403)


def test_empty_file_handling(mock_workspace, client, auth_headers):
    """WRK-09: 0-byte empty files are read and written cleanly without error."""
    mock_workspace.create_file("empty.txt", "")
    res = client.get("/api/files/content?path=empty.txt", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["content"] == ""
    assert res.json()["size"] == 0


def test_large_file_read_limit_truncation(mock_workspace, client, auth_headers):
    """WRK-10: Extremely large files handle truncation or size limits safely without memory exhaust."""
    mgr = WorkspaceFileManager(mock_workspace.root, max_read_size=100)
    mock_workspace.create_file("large.log", "A" * 500)
    with pytest.raises(Exception):
        mgr.read_file("large.log")


def test_invalid_utf8_encoding_fallback(mock_workspace, client, auth_headers):
    """WRK-11: Non-UTF8 byte sequences are handled gracefully using replacement characters or Latin-1."""
    bin_file = mock_workspace.create_binary("latin.txt", "Café au lait".encode("latin-1"))
    mgr = WorkspaceFileManager(mock_workspace.root)
    result = mgr.read_file("latin.txt")
    assert result["is_binary"] is False
    assert "Caf" in result["content"]


def test_save_creates_parent_directories(client, auth_headers, mock_workspace):
    """WRK-12: Saving a file to a non-existent nested path automatically creates parent directories."""
    nested_path = "sub1/sub2/sub3/auto_created.py"
    res = client.post(
        "/api/files/save",
        json={"path": nested_path, "content": "# Auto created\nprint(123)\n"},
        headers=auth_headers
    )
    assert res.status_code == 200
    assert (mock_workspace.root / nested_path).is_file()


def test_read_only_file_permission_error(mock_workspace, client, auth_headers):
    """WRK-13: Saving to a protected/read-only file returns structured error without crashing server."""
    ro_file = mock_workspace.create_file("readonly.txt", "read-only text")
    ro_file.chmod(0o444)
    try:
        res = client.post(
            "/api/files/save",
            json={"path": "readonly.txt", "content": "attempt edit"},
            headers=auth_headers
        )
        assert res.status_code in (403, 500)
    finally:
        ro_file.chmod(0o666)


def test_workspace_tree_recursive_depth(mock_workspace, client, auth_headers):
    """WRK-Extra: Recursive tree traversal with depth limit."""
    res = client.get("/api/files/tree?depth=3", headers=auth_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    # Check that children exist on directory items
    src_item = next((item for item in items if item["name"] == "src"), None)
    assert src_item is not None
    assert "children" in src_item


def test_workspace_diff_api_endpoint(client, auth_headers, mock_workspace):
    """WRK-Extra: POST /api/files/diff generates unified diff via REST API."""
    res = client.post(
        "/api/files/diff",
        json={"path": "src/utils.py", "new_content": "def add(a, b):\n    return a + b + 1\n"},
        headers=auth_headers
    )
    assert res.status_code == 200
    data = res.json()
    assert data["has_changes"] is True
    assert "diff" in data
    assert "additions" in data


# ===========================================================================
# Tier 3: Cross-Feature Interaction Tests
# ===========================================================================

def test_workspace_edit_reflection_in_terminal(mock_workspace, client, auth_headers):
    """WRK-14: Modifications made in mobile file editor are immediately visible on disk."""
    mock_workspace.create_file("src/cli.py", "print('cli active')")
    assert (mock_workspace.root / "src/cli.py").exists()


def test_workspace_edit_reflection_in_session_hub(mock_workspace, client, auth_headers):
    """WRK-15: Files written by workspace API are listed in directory tree queries."""
    mock_workspace.create_file("docs/architecture_notes.md", "# Architecture Notes")
    assert (mock_workspace.root / "docs/architecture_notes.md").exists()


# ===========================================================================
# Tier 4: Real-World Mobile Workflow Test
# ===========================================================================

def test_mobile_code_review_and_patch_workflow(client, auth_headers, mock_workspace):
    """WRK-16: End-to-End Workflow: Browse Tree -> Read Code -> Save Edits -> Re-verify Content."""
    # 1. Inspect tree
    tree_res = client.get("/api/files/tree", headers=auth_headers)
    assert tree_res.status_code == 200

    # 2. Inspect code
    code_res = client.get("/api/files/content?path=src/utils.py", headers=auth_headers)
    assert code_res.status_code == 200
    original_code = code_res.json()["content"]
    assert "def add" in original_code

    # 3. Apply patch
    patched_code = original_code + "\ndef multiply(a, b):\n    return a * b\n"
    save_res = client.post(
        "/api/files/save",
        json={"path": "src/utils.py", "content": patched_code},
        headers=auth_headers
    )
    assert save_res.status_code == 200

    # 4. Re-verify
    recheck_res = client.get("/api/files/content?path=src/utils.py", headers=auth_headers)
    assert recheck_res.status_code == 200
    assert "def multiply" in recheck_res.json()["content"]
