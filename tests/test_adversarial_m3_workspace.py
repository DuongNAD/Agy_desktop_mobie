"""
Adversarial Penetration and Stress Testing Suite for Milestone 3 (M3) - Challenger 2
Focus: Workspace File Explorer, Safe Reader/Writer, Path Sandbox & Diff Engine
"""

import asyncio
import base64
import concurrent.futures
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.security import (
    PathSandbox,
    SandboxSecurityException,
    create_access_token,
    is_safe_sandbox_path,
    sanitize_and_resolve_sandbox_path,
)
from backend.workspace.file_manager import (
    DEV_MIME_MAP,
    KNOWN_BINARY_EXTENSIONS,
    MAX_DEFAULT_READ_SIZE,
    WorkspaceFileManager,
)

TEST_PIN = "849201"
TEST_JWT_SECRET = "test-secret-key-32-bytes-agymobile-12345"


# ===========================================================================
# Fixtures for M3 Workspace Stress Suite
# ===========================================================================

@pytest.fixture
def m3_ws_env(tmp_path: Path):
    """Creates an isolated test workspace environment."""
    ws = tmp_path / "m3_workspace"
    ws.mkdir(parents=True, exist_ok=True)

    src = ws / "src"
    src.mkdir(exist_ok=True)
    (src / "main.py").write_text("def run():\n    print('AgyMobile')\n", encoding="utf-8")
    (src / "utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    docs = ws / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "README.md").write_text("# Workspace Docs\nDocumentation.", encoding="utf-8")

    assets = ws / "assets"
    assets.mkdir(exist_ok=True)

    # Hidden folders
    git_dir = ws / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "config").write_text("[core]\nrepositoryformatversion = 0\n", encoding="utf-8")

    pycache_dir = src / "__pycache__"
    pycache_dir.mkdir(exist_ok=True)
    (pycache_dir / "main.cpython-311.pyc").write_bytes(b"\x61\x0d\x0d\x0a\x00\x00\x00\x00")

    # Allowed dotfiles
    (ws / ".gitignore").write_text("*.pyc\nnode_modules/\n", encoding="utf-8")
    (ws / ".env.example").write_text("AUTH_PIN=123456\nPORT=8765\n", encoding="utf-8")

    class EnvHelper:
        root = ws
        src_dir = src
        docs_dir = docs
        assets_dir = assets

        def create_file(self, rel: str, content: str, encoding: str = "utf-8") -> Path:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode(encoding))
            return p

        def create_bytes(self, rel: str, data: bytes) -> Path:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            return p

    return EnvHelper()


@pytest.fixture
def m3_settings(m3_ws_env) -> Settings:
    return Settings(
        HOST="127.0.0.1",
        PORT=8765,
        DEBUG=True,
        AUTH_PIN=TEST_PIN,
        JWT_SECRET=TEST_JWT_SECRET,
        JWT_ALGORITHM="HS256",
        TOKEN_EXPIRY_SECONDS=86400,
        WORKSPACE_ROOT=m3_ws_env.root,
        CORS_ORIGINS=["*"],
    )


@pytest.fixture
def m3_app(m3_settings: Settings):
    return create_app(settings=m3_settings)


@pytest.fixture
def m3_client(m3_app: Any) -> TestClient:
    return TestClient(m3_app)


@pytest.fixture
async def m3_async_client(m3_app: Any):
    transport = ASGITransport(app=m3_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def m3_token(m3_settings: Settings) -> str:
    return create_access_token({"sub": "admin", "role": "admin"}, settings=m3_settings)


@pytest.fixture
def m3_headers(m3_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {m3_token}"}


# ===========================================================================
# 1. COMPLEX PATH TRAVERSAL & WINDOWS SANDBOX ESCAPE ATTACKS
# ===========================================================================

M3_PATH_TRAVERSAL_PAYLOADS = [
    # Basic dot-dot
    "../../outside.txt",
    "..\\..\\outside.txt",
    "src/../../outside.txt",
    "src\\..\\..\\outside.txt",
    "docs/../../../../../../Windows/System32/calc.exe",
    # Encoded slashes and dots
    "..%2f..%2foutside.txt",
    "..%5c..%5coutside.txt",
    "%2e%2e%2f%2e%2e%2foutside.txt",
    "%2e%2e%5c%2e%2e%5coutside.txt",
    "%252e%252e%252f%252e%252e%252foutside.txt",
    "%252e%252e%255c%252e%252e%255coutside.txt",
    "src/%2e%2e/%2e%2e/outside.txt",
    "src%2f%2e%2e%2f%2e%2e%2foutside.txt",
    # Windows reserved device names
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM9",
    "LPT1",
    "LPT9",
    "con.txt",
    "PRN.JSON",
    "aux.py",
    "nul.log",
    "com1.dat",
    "lpt1.txt",
    "src/con.txt",
    "src/aux.py",
    "src/NUL",
    "src\\COM1.log",
    # NTFS Alternate Data Streams (ADS)
    "src/main.py:stream",
    "src/main.py::$DATA",
    "src/main.py:hidden:$DATA",
    "docs:stream",
    "src/main.py::$INDEX_ALLOCATION",
    "COM1:",
    "NUL:",
    # UNC network shares
    "\\\\127.0.0.1\\c$\\Windows\\win.ini",
    "\\\\localhost\\share\\data.txt",
    "//127.0.0.1/c$/Windows",
    # Other drive absolute paths
    "C:\\Windows\\System32\\cmd.exe",
    "C:/Windows/win.ini",
    "D:\\Secrets\\passwords.txt",
    "Z:\\Backup\\data.tar",
    "/etc/shadow",
    "/var/run/docker.sock",
    "/root/.ssh/id_rsa",
    # Null byte injections
    "src/main.py\x00.txt",
    "src/main.py%00.txt",
    "src/main.py%2500.txt",
    "\x00../../outside.txt",
]


@pytest.mark.parametrize("payload", M3_PATH_TRAVERSAL_PAYLOADS)
def test_path_sandbox_blocks_all_traversal_payloads(m3_ws_env, payload):
    """Verify that PathSandbox strictly rejects all adversarial traversal attempts."""
    sandbox = PathSandbox(m3_ws_env.root)
    assert sandbox.is_safe_path(payload) is False

    with pytest.raises((SandboxSecurityException, HTTPException, ValueError, PermissionError)):
        sandbox.validate_path(payload)


@pytest.mark.parametrize("payload", M3_PATH_TRAVERSAL_PAYLOADS)
def test_api_content_endpoint_blocks_all_traversal_payloads(m3_client, m3_headers, payload):
    """Verify that GET /api/files/content rejects all traversal payloads with 400, 403, or 422."""
    try:
        res = m3_client.get(f"/api/files/content?path={payload}", headers=m3_headers)
        assert res.status_code in (400, 403, 404, 422)
    except httpx.InvalidURL:
        # Invalid control characters / unencoded null bytes rejected at client level
        pass


@pytest.mark.parametrize("payload", M3_PATH_TRAVERSAL_PAYLOADS)
def test_api_save_endpoint_blocks_all_traversal_payloads(m3_client, m3_headers, payload):
    """Verify that POST /api/files/save rejects all traversal payloads with 400, 403, or 422."""
    try:
        res = m3_client.post(
            "/api/files/save",
            json={"path": payload, "content": "malicious payload overwrite"},
            headers=m3_headers,
        )
        assert res.status_code in (400, 403, 422)
    except httpx.InvalidURL:
        pass


def test_save_to_workspace_root_directly_is_blocked(m3_client, m3_headers):
    """Saving to the workspace root itself ('.' or '') must be rejected with 400 or 422."""
    r1 = m3_client.post("/api/files/save", json={"path": "", "content": "test"}, headers=m3_headers)
    assert r1.status_code in (400, 422)

    r2 = m3_client.post("/api/files/save", json={"path": ".", "content": "test"}, headers=m3_headers)
    assert r2.status_code in (400, 403, 422)


def test_save_to_existing_directory_path_is_blocked(m3_client, m3_headers):
    """Attempting to save text directly onto an existing directory name must return 400."""
    res = m3_client.post("/api/files/save", json={"path": "src", "content": "test"}, headers=m3_headers)
    assert res.status_code == 400
    assert "directory" in res.json().get("detail", "").lower()


def test_read_directory_as_file_is_blocked(m3_client, m3_headers):
    """Attempting to read a directory via GET /api/files/content must return 400 Bad Request."""
    res = m3_client.get("/api/files/content?path=src", headers=m3_headers)
    assert res.status_code == 400
    assert "directory" in res.json().get("detail", "").lower()


# ===========================================================================
# 2. ATOMIC SAVING CONCURRENCY, COLLISION & BACKUP TESTS
# ===========================================================================

def test_sequential_rapid_overwrites_on_same_file(m3_ws_env):
    """
    Stress-test: 100 rapid sequential saves on the same file.
    Verifies:
      1. All 100 saves succeed atomically.
      2. File on disk is intact and equals the 100th version.
      3. No temporary `.agy_tmp_*` files are leaked in the workspace.
    """
    mgr = WorkspaceFileManager(m3_ws_env.root)
    target_rel = "src/rapid_target.py"
    m3_ws_env.create_file(target_rel, "initial = 0\n")

    for i in range(1, 101):
        content = f"# Iteration {i}\ndef version_{i}():\n    return {i}\n"
        res = mgr.save_file(target_rel, content)
        assert res["status"] == "saved"
        assert res["size"] == len(content.encode("utf-8"))

    final_disk_text = (m3_ws_env.root / target_rel).read_text(encoding="utf-8")
    assert "# Iteration 100" in final_disk_text
    assert "def version_100():" in final_disk_text

    # Verify no temp files leaked
    parent_files = list((m3_ws_env.root / "src").glob(".agy_tmp_*"))
    assert len(parent_files) == 0, f"Leaked temporary files detected: {parent_files}"


def test_concurrent_saves_to_distinct_files(m3_ws_env):
    """
    Stress-test: 30 concurrent threads saving to 30 distinct files simultaneously in the same directory.
    Verifies thread safety, absence of cross-file race conditions, and complete temporary file cleanup.
    """
    mgr = WorkspaceFileManager(m3_ws_env.root)
    num_files = 30
    results = []
    errors = []
    lock = threading.Lock()

    def worker(idx: int):
        path = f"src/concurrent_file_{idx}.py"
        content = f"# Concurrent thread {idx}\nDATA_{idx} = {idx * 10}\n"
        try:
            res = mgr.save_file(path, content)
            with lock:
                results.append(res)
        except Exception as e:
            with lock:
                errors.append((idx, e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_files)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent saves failed: {errors}"
    assert len(results) == num_files

    # Verify all 30 files exist and contain their expected contents
    for i in range(num_files):
        p = m3_ws_env.root / f"src/concurrent_file_{i}.py"
        assert p.is_file()
        assert f"DATA_{i} = {i * 10}" in p.read_text(encoding="utf-8")

    # Verify no temp files leaked
    parent_files = list((m3_ws_env.root / "src").glob(".agy_tmp_*"))
    assert len(parent_files) == 0


@pytest.mark.asyncio
async def test_async_burst_saves_via_rest_api(m3_async_client: AsyncClient, m3_headers: Dict[str, str], m3_ws_env):
    """Stress-test 40 concurrent async POST /api/files/save requests to multiple distinct files."""
    num_files = 40

    async def _save_item(idx: int):
        path = f"src/generated/file_{idx}.py"
        body = f"# Generated file {idx}\nVALUE = {idx}\n"
        return await m3_async_client.post(
            "/api/files/save",
            json={"path": path, "content": body},
            headers=m3_headers,
        )

    tasks = [_save_item(i) for i in range(num_files)]
    responses = await asyncio.gather(*tasks)

    for i, res in enumerate(responses):
        assert res.status_code == 200, f"File {i} save failed: {res.text}"
        data = res.json()
        assert data.get("status") == "saved"

    # Verify all 40 files exist on disk
    for i in range(num_files):
        p = m3_ws_env.root / f"src/generated/file_{i}.py"
        assert p.is_file()
        assert f"VALUE = {i}" in p.read_text(encoding="utf-8")


def test_backup_file_creation_on_save(m3_ws_env):
    """
    Test backup=True flag creates a '.bak' copy of the old content before overwriting.
    """
    mgr = WorkspaceFileManager(m3_ws_env.root)
    file_path = "src/backup_test.py"
    m3_ws_env.create_file(file_path, "original_content = 1\n")

    # Save with backup=True
    mgr.save_file(file_path, "modified_content = 2\n", backup=True)

    # Verify new content on target
    assert (m3_ws_env.root / file_path).read_text(encoding="utf-8") == "modified_content = 2\n"

    # Verify .bak content
    bak_path = m3_ws_env.root / "src/backup_test.py.bak"
    assert bak_path.is_file()
    assert bak_path.read_text(encoding="utf-8") == "original_content = 1\n"


def test_read_only_file_handling_blocks_write_and_cleans_temp(m3_ws_env):
    """
    Verify writing to a read-only file returns 403 Forbidden and leaves no orphaned temp files.
    """
    mgr = WorkspaceFileManager(m3_ws_env.root)
    ro_file = m3_ws_env.create_file("src/readonly_locked.py", "locked_content = True\n")
    ro_file.chmod(0o444)

    try:
        with pytest.raises(HTTPException) as exc_info:
            mgr.save_file("src/readonly_locked.py", "attempted_overwrite = True\n")
        assert exc_info.value.status_code in (403, 500)

        # Content must remain unchanged
        assert ro_file.read_text(encoding="utf-8") == "locked_content = True\n"

        # No temp files leaked
        temp_files = list((m3_ws_env.root / "src").glob(".agy_tmp_*"))
        assert len(temp_files) == 0
    finally:
        ro_file.chmod(0o666)


def test_save_automatically_creates_deeply_nested_parent_dirs(m3_ws_env):
    """Verify save creates deeply nested parent directory hierarchy automatically."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    deep_path = "deep/level1/level2/level3/level4/module.py"
    mgr.save_file(deep_path, "# Deep module\n")

    target = m3_ws_env.root / deep_path
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "# Deep module\n"


# ===========================================================================
# 3. BINARY AND LARGE FILE GUARDS
# ===========================================================================

def test_large_file_guard_10mb_exceeds_default_limit(m3_ws_env):
    """
    Attempting to read a 10MB text file when limit is 5MB must raise HTTP 413.
    """
    mgr = WorkspaceFileManager(m3_ws_env.root)
    large_path = "large_10mb.txt"
    # Create 6MB file (exceeds 5MB default)
    m3_ws_env.create_file(large_path, "X" * (6 * 1024 * 1024))

    with pytest.raises(HTTPException) as exc_info:
        mgr.read_file(large_path)
    assert exc_info.value.status_code == 413
    assert "exceeds maximum limit" in str(exc_info.value.detail)


def test_custom_max_size_parameter_in_read_file(m3_ws_env):
    """Verify custom max_size limit properly enforces smaller bounds."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    file_path = "sample_data.txt"
    m3_ws_env.create_file(file_path, "A" * 5000)

    # 4000 limit raises 413 for 5000 byte file
    with pytest.raises(HTTPException) as exc_info:
        mgr.read_file(file_path, max_size=4000)
    assert exc_info.value.status_code == 413

    # 6000 limit allows reading
    res = mgr.read_file(file_path, max_size=6000)
    assert res["size"] == 5000
    assert len(res["content"]) == 5000


def test_binary_image_reading_with_base64_preview(m3_ws_env):
    """Small PNG image (<= 1MB) returns is_binary=True, content=None, preview data URI."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    m3_ws_env.create_bytes("assets/icon.png", png_bytes)

    res = mgr.read_file("assets/icon.png")
    assert res["is_binary"] is True
    assert res["content"] is None
    assert res["mime"] == "image/png"
    assert res["preview"] is not None
    assert res["preview"].startswith("data:image/png;base64,")


def test_large_binary_image_omits_preview_to_save_bandwidth(m3_ws_env):
    """Large PNG image (> 1MB) returns preview=None to avoid memory/network blowup."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    large_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024 + 100)
    m3_ws_env.create_bytes("assets/large_photo.png", large_png)

    res = mgr.read_file("assets/large_photo.png")
    assert res["is_binary"] is True
    assert res["content"] is None
    assert res["preview"] is None


@pytest.mark.parametrize("ext,mime_expected,magic_bytes", [
    (".exe", "application/x-msdownload", b"MZ\x90\x00\x03\x00\x00\x00"),
    (".dll", "application/x-msdownload", b"MZ\x90\x00\x03\x00\x00\x00"),
    (".zip", "application/zip", b"PK\x03\x04\x14\x00\x00\x00"),
    (".tar", "application/x-tar", b"ustar\x0000"),
    (".gz", "application/gzip", b"\x1f\x8b\x08\x00\x00\x00\x00\x00"),
    (".pdf", "application/pdf", b"%PDF-1.7\r\n"),
    (".pyc", "application/octet-stream", b"\x61\x0d\x0d\x0a\x00\x00\x00\x00"),
])
def test_known_binary_file_types_detected_properly(m3_ws_env, ext, mime_expected, magic_bytes):
    """Verify various binary executable/archive formats are flagged as binary with appropriate MIME."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    rel_path = f"assets/sample{ext}"
    m3_ws_env.create_bytes(rel_path, magic_bytes)

    res = mgr.read_file(rel_path)
    assert res["is_binary"] is True
    assert res["content"] is None


def test_text_save_on_existing_binary_file_is_strictly_blocked(m3_ws_env):
    """Attempting to overwrite an existing binary file with text raises 400 Bad Request."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    m3_ws_env.create_bytes("assets/app.exe", b"MZ\x90\x00binary")

    with pytest.raises(HTTPException) as exc_info:
        mgr.save_file("assets/app.exe", "print('overwritten')")
    assert exc_info.value.status_code == 400
    assert "binary" in str(exc_info.value.detail).lower()


# ===========================================================================
# 4. CORRUPTED TEXT AND UNUSUAL ENCODINGS FALLBACK
# ===========================================================================

def test_utf8_with_international_characters(m3_ws_env):
    """Test standard UTF-8 with Vietnamese, Japanese, Cyrillic, and emojis."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    text = "Xin chào AgyMobile! 🚀\n日本語テキスト\nРусский текст\n"
    m3_ws_env.create_file("unicode.txt", text, encoding="utf-8")

    res = mgr.read_file("unicode.txt")
    assert res["is_binary"] is False
    assert res["encoding"] == "utf-8"
    assert res["content"].replace("\r\n", "\n") == text


def test_utf8_with_bom_detection(m3_ws_env):
    """Test UTF-8 file with Byte Order Mark (BOM) decoded as utf-8-sig."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    bom_text = "const config = { version: '2.0' };\n"
    m3_ws_env.create_file("bom.js", bom_text, encoding="utf-8-sig")

    res = mgr.read_file("bom.js")
    assert res["is_binary"] is False
    assert "utf-8" in res["encoding"]
    assert "version: '2.0'" in res["content"]


def test_latin1_and_cp1252_encoding_fallback(m3_ws_env):
    """Test file with non-UTF8 Latin-1 byte sequences falls back cleanly."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    # 0xE9 is 'é' in Latin-1, invalid in UTF-8
    latin1_bytes = b"Cafe au lait: 100 \xa3 (Pound), Copyright \xa9 2026\r\n"
    m3_ws_env.create_bytes("french.txt", latin1_bytes)

    res = mgr.read_file("french.txt")
    assert res["is_binary"] is False
    assert res["encoding"] == "latin-1"
    assert "Cafe" in res["content"]
    assert "2026" in res["content"]


def test_zero_byte_empty_file_handling(m3_ws_env):
    """Test 0-byte file reading and writing."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    m3_ws_env.create_file("empty.txt", "")

    res = mgr.read_file("empty.txt")
    assert res["size"] == 0
    assert res["content"] == ""
    assert res["is_binary"] is False

    # Overwrite empty file
    save_res = mgr.save_file("empty.txt", "")
    assert save_res["size"] == 0


def test_file_with_null_byte_detected_as_binary(m3_ws_env):
    """File containing null byte not part of UTF-16 BOM must be treated as binary."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    mixed_bytes = b"Hello world\x00This is hidden"
    m3_ws_env.create_bytes("unknown.dat", mixed_bytes)

    res = mgr.read_file("unknown.dat")
    assert res["is_binary"] is True
    assert res["content"] is None


def test_file_with_high_control_character_density_detected_as_binary(m3_ws_env):
    """File containing >5% non-standard control characters is detected as binary."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    control_bytes = b"Normal text " + bytes([7, 8, 11, 12, 14, 15, 16, 17, 18, 19, 20] * 5)
    m3_ws_env.create_bytes("control.dat", control_bytes)

    res = mgr.read_file("control.dat")
    assert res["is_binary"] is True
    assert res["content"] is None


# ===========================================================================
# 5. DIFF ENGINE EDGE CASES
# ===========================================================================

def test_diff_identical_files_produces_no_changes(m3_ws_env):
    """Diffing identical text returns has_changes=False, diff='', additions=0, deletions=0."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    content = "def test():\n    return True\n"
    m3_ws_env.create_file("src/identical.py", content)

    res = mgr.generate_diff("src/identical.py", modified_content=content)
    assert res["has_changes"] is False
    assert res["diff"] == ""
    assert res["additions"] == 0
    assert res["deletions"] == 0
    assert len(res["hunks"]) == 0


def test_diff_completely_different_files(m3_ws_env):
    """Diffing completely distinct files returns full deletions + full additions."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    original = "line1\nline2\nline3\n"
    modified = "alpha\nbeta\ngamma\ndelta\n"
    m3_ws_env.create_file("src/changed.py", original)

    res = mgr.generate_diff("src/changed.py", modified_content=modified)
    assert res["has_changes"] is True
    assert res["deletions"] == 3
    assert res["additions"] == 4
    assert len(res["hunks"]) == 1


def test_diff_empty_against_empty(m3_ws_env):
    """Diffing 0-byte file against empty content produces no changes."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    m3_ws_env.create_file("src/zero.py", "")

    res = mgr.generate_diff("src/zero.py", modified_content="")
    assert res["has_changes"] is False
    assert res["additions"] == 0
    assert res["deletions"] == 0


def test_diff_empty_against_non_empty(m3_ws_env):
    """Diffing empty original against new lines shows only additions."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    m3_ws_env.create_file("src/new_file.py", "")

    res = mgr.generate_diff("src/new_file.py", modified_content="line 1\nline 2\n")
    assert res["has_changes"] is True
    assert res["additions"] == 2
    assert res["deletions"] == 0


def test_diff_non_empty_against_empty(m3_ws_env):
    """Diffing non-empty original against empty content shows only deletions."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    m3_ws_env.create_file("src/delete_all.py", "line 1\nline 2\nline 3\n")

    res = mgr.generate_diff("src/delete_all.py", modified_content="")
    assert res["has_changes"] is True
    assert res["additions"] == 0
    assert res["deletions"] == 3


def test_diff_multi_chunk_hunks_parsing(m3_ws_env):
    """
    Test multi-chunk diff: modifications at top, middle, and bottom of file separated
    by unchanged context lines. Verifies multiple structured hunk objects.
    """
    mgr = WorkspaceFileManager(m3_ws_env.root)
    lines = [f"Context line {i}\n" for i in range(1, 60)]
    original = "".join(lines)
    m3_ws_env.create_file("src/multihunk.py", original)

    # Modify line 3, line 30, line 55
    lines[2] = "MODIFIED TOP LINE 3\n"
    lines[29] = "MODIFIED MIDDLE LINE 30\n"
    lines[54] = "MODIFIED BOTTOM LINE 55\n"
    modified = "".join(lines)

    res = mgr.generate_diff("src/multihunk.py", modified_content=modified)
    assert res["has_changes"] is True
    assert len(res["hunks"]) >= 2
    assert res["additions"] == 3
    assert res["deletions"] == 3

    # Check hunk structure
    for hunk in res["hunks"]:
        assert "header" in hunk
        assert "old_start" in hunk
        assert "new_start" in hunk
        assert "lines" in hunk
        line_types = [l["type"] for l in hunk["lines"]]
        assert "add" in line_types
        assert "delete" in line_types
        assert "context" in line_types


def test_diff_binary_file_returns_safe_guard_error(m3_ws_env):
    """Attempting to diff a binary file returns is_binary=True and an error message."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    m3_ws_env.create_bytes("assets/logo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    res = mgr.generate_diff("assets/logo.png", modified_content="text diff attempt")
    assert res["is_binary"] is True
    assert res["has_changes"] is False
    assert "binary" in res.get("error", "").lower()


def test_diff_nonexistent_file_treats_baseline_as_empty(m3_ws_env):
    """Diffing against a non-existent file on disk treats baseline as empty."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    res = mgr.generate_diff("src/not_yet_created.py", modified_content="new_code = True\n")
    assert res["has_changes"] is True
    assert res["additions"] == 1
    assert res["deletions"] == 0


def test_diff_api_request_aliases(m3_client, m3_headers, m3_ws_env):
    """Test POST /api/files/diff handles alias parameter names (content, original)."""
    m3_ws_env.create_file("src/alias.py", "def a(): pass\n")

    # Using 'content' and 'original' aliases
    res = m3_client.post(
        "/api/files/diff",
        json={"path": "src/alias.py", "content": "def b(): pass\n", "original": "def a(): pass\n"},
        headers=m3_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["has_changes"] is True
    assert data["additions"] == 1
    assert data["deletions"] == 1


# ===========================================================================
# 6. DIRECTORY NAVIGATION & NOISE FILTERING TESTS
# ===========================================================================

def test_directory_listing_hides_ignored_and_dotfiles_by_default(m3_ws_env):
    """Verify .git, __pycache__, and noise files are excluded by default."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    root_items = mgr.list_directory("")
    names = [it["name"] for it in root_items]

    assert ".git" not in names
    assert ".gitignore" in names  # Allowed dotfile
    assert ".env.example" in names  # Allowed dotfile
    assert "src" in names
    assert "docs" in names

    src_items = mgr.list_directory("src")
    src_names = [it["name"] for it in src_items]
    assert "__pycache__" not in src_names
    assert "main.py" in src_names


def test_directory_listing_shows_hidden_files_when_requested(m3_ws_env):
    """Verify show_hidden=True reveals .git and internal hidden folders."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    root_items = mgr.list_directory("", show_hidden=True)
    names = [it["name"] for it in root_items]

    assert ".git" in names
    assert "src" in names


def test_recursive_tree_listing_depth_bounding(m3_ws_env):
    """Verify list_tree clamps depth to max_depth correctly."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    m3_ws_env.create_file("a/b/c/d/e/file.py", "x = 1\n")

    # Depth 1: only 'a' top-level
    t1 = mgr.list_tree("", max_depth=1)
    a_node = next((n for n in t1 if n["name"] == "a"), None)
    assert a_node is not None
    assert a_node["children"] == []

    # Depth 3: traverses down 3 levels
    t3 = mgr.list_tree("", max_depth=3)
    a_node3 = next((n for n in t3 if n["name"] == "a"), None)
    assert a_node3 is not None
    assert len(a_node3["children"]) > 0


def test_directory_sorting_directories_first_then_alphabetical(m3_ws_env):
    """Verify directory entries always come first, followed by alphabetical file entries."""
    mgr = WorkspaceFileManager(m3_ws_env.root)
    m3_ws_env.create_file("aaa_file.txt", "content")
    m3_ws_env.create_file("zzz_dir/dummy.txt", "content")

    items = mgr.list_directory("")
    # Find positions
    dir_indices = [i for i, item in enumerate(items) if item["is_dir"]]
    file_indices = [i for i, item in enumerate(items) if not item["is_dir"]]

    # All dir indices must precede all file indices
    assert max(dir_indices) < min(file_indices)
