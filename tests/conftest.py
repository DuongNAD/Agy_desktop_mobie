import asyncio
import json
import os
import sqlite3
import time
from collections import namedtuple
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional
from unittest.mock import MagicMock

import httpx
import jwt
import pytest
from starlette.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from backend.config import Settings, get_settings
from backend.main import create_app
from backend.security import create_access_token, rate_limiter
from backend.telemetry.monitor import telemetry_poller

TEST_PIN = "849201"
TEST_JWT_SECRET = "test-secret-key-32-bytes-agymobile-12345"

DEFAULT_TEST_TELEMETRY_SNAPSHOT = {
    "type": "telemetry",
    "cpu": 14.5,
    "cpu_per_core": [12.0, 16.0],
    "ram": 38.9,
    "ram_details": {
        "total": 34359738368,
        "used": 13388218368,
        "free": 20971520000,
        "available": 20971520000,
        "percent": 38.9,
    },
    "disk": 45.0,
    "disk_details": {
        "total": 1000204886016,
        "used": 450000000000,
        "free": 550204886016,
        "percent": 45.0,
        "path": "C:\\",
    },
    "network": {
        "bytes_sent": 102400,
        "bytes_recv": 204800,
        "packets_sent": 1500,
        "packets_recv": 3000,
        "errin": 0,
        "errout": 0,
        "dropin": 0,
        "dropout": 0,
        "bytes_sent_per_sec": 0,
        "bytes_recv_per_sec": 0,
    },
    "uptime": 3600.0,
    "uptime_details": {
        "uptime_seconds": 3600.0,
        "boot_time": time.time() - 3600,
        "boot_time_iso": "2026-08-14T07:00:00Z",
    },
    "tasks": [
        {"pid": 1001, "name": "language_server.exe", "cpu": 4.2, "memory_mb": 500.0, "status": "running", "created": 100.0},
        {"pid": 1002, "name": "python.exe", "cpu": 2.1, "memory_mb": 150.0, "status": "running", "created": 105.0},
        {"pid": 1003, "name": "powershell.exe", "cpu": 0.8, "memory_mb": 90.0, "status": "running", "created": 110.0},
    ],
    "lan_ip": "127.0.0.1",
    "port": 8765,
    "os": "windows",
    "server_time": "2026-08-14T08:00:00Z",
    "hostname": "test-workstation",
}


# ---------------------------------------------------------------------------
# Autouse fixture to reset rate limiter and clear settings cache between tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def reset_rate_limiter(monkeypatch):
    get_settings.cache_clear()
    rate_limiter.clear()
    telemetry_poller._cached_snapshot = dict(DEFAULT_TEST_TELEMETRY_SNAPSHOT)
    monkeypatch.setenv("MOCK_SUBPROCESS", "1")
    yield
    get_settings.cache_clear()
    rate_limiter.clear()
    telemetry_poller._cached_snapshot = dict(DEFAULT_TEST_TELEMETRY_SNAPSHOT)


# ---------------------------------------------------------------------------
# 1. Hermetic Antigravity Configuration Environment Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_antigravity_env(tmp_path, monkeypatch):
    """
    Creates a hermetic ~/.gemini/antigravity filesystem layout with:
    - Master config.json with globalPermissionGrants
    - conversation_summaries.db SQLite database
    - Individual conversation SQLite databases (conversations/<id>.db)
    - Per-conversation brain directories with transcript.jsonl & markdown artifacts
    """
    gemini_home = tmp_path / "mock_gemini"
    antigravity_dir = gemini_home / "antigravity"
    config_dir = gemini_home / "config"
    brain_dir = antigravity_dir / "brain"
    convs_dir = antigravity_dir / "conversations"

    for d in [gemini_home, antigravity_dir, config_dir, brain_dir, convs_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # 1. Master config.json
    config_file = config_dir / "config.json"
    initial_config = {
        "userSettings": {
            "autoExecutionPolicy": "CASCADE_COMMANDS_AUTO_EXECUTION_EAGER",
            "artifactReviewMode": "ARTIFACT_REVIEW_MODE_TURBO",
            "globalPermissionGrants": {
                "allow": ["command(pytest tests/)", "read_file(*)"]
            }
        }
    }
    config_file.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

    # 2. SQLite conversation_summaries.db
    summaries_db_path = antigravity_dir / "conversation_summaries.db"
    conn = sqlite3.connect(str(summaries_db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_summaries (
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

    # Seed Conversations: Root (orchestrator) + Subagent (explorer)
    root_id = "1506d4df-0000-4000-8000-000000000001"
    sub_id = "1506d4df-0000-4000-8000-000000000002"

    cursor.execute("""
        INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        root_id,
        "Implement AgyMobile Controller",
        "Building FastAPI backend...",
        12,
        "2026-08-14T08:00:00Z",
        json.dumps(["e:/Project/Agy_desktop_mobie"]),
        "active",
        "orchestrator_1",
        None,
        0,
        0,
        "2026-08-14T07:55:00Z"
    ))

    cursor.execute("""
        INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sub_id,
        "Explore Subagent Hierarchy",
        "Analyzing tree structure...",
        6,
        "2026-08-14T08:05:00Z",
        json.dumps(["e:/Project/Agy_desktop_mobie"]),
        "running",
        "explorer_survey_1",
        root_id,
        1,
        0,
        "2026-08-14T08:02:00Z"
    ))
    conn.commit()
    conn.close()

    # 3. Individual Conversation DB (conversations/<id>.db)
    for cid in [root_id, sub_id]:
        cdb_path = convs_dir / f"{cid}.db"
        cconn = sqlite3.connect(str(cdb_path))
        ccur = cconn.cursor()
        ccur.execute("""
            CREATE TABLE IF NOT EXISTS trajectory_meta (
                trajectory_id TEXT,
                cascade_id TEXT,
                trajectory_type INTEGER,
                source INTEGER
            )
        """)
        ccur.execute("""
            CREATE TABLE IF NOT EXISTS steps (
                idx INTEGER PRIMARY KEY,
                step_type INTEGER,
                status INTEGER,
                has_subtrajectory NUMERIC,
                metadata BLOB,
                step_payload BLOB
            )
        """)
        ccur.execute("INSERT INTO trajectory_meta VALUES (?, ?, 1, 1)", (cid, f"cascade_{cid}"))
        cconn.commit()
        cconn.close()

    # 4. Brain Directory Layout & Initial transcript.jsonl
    root_brain = brain_dir / root_id
    root_logs = root_brain / ".system_generated" / "logs"
    root_logs.mkdir(parents=True, exist_ok=True)

    root_transcript = root_logs / "transcript.jsonl"
    initial_steps = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-08-14T07:55:00Z",
            "content": "Create AgyMobile test architecture."
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-08-14T07:55:10Z",
            "thinking": "Analyzing requirements and creating initial plan...",
            "tool_calls": [
                {
                    "id": "call_write_001",
                    "name": "write_to_file",
                    "args": {"TargetFile": "TEST_INFRA.md", "CodeContent": "..."}
                }
            ],
            "content": "I am designing the test infrastructure."
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "PENDING_APPROVAL",
            "created_at": "2026-08-14T08:00:00Z",
            "thinking": "Need permission to run command...",
            "tool_calls": [
                {
                    "id": "call_exec_001",
                    "name": "run_command",
                    "args": {"CommandLine": "powershell -c Get-Process"},
                    "toolAction": "Checking processes",
                    "toolSummary": "Run Get-Process"
                }
            ],
            "content": "Please approve running Get-Process"
        }
    ]
    with open(root_transcript, "w", encoding="utf-8") as f:
        for s in initial_steps:
            f.write(json.dumps(s) + "\n")

    # 5. Artifacts in brain
    plan_file = root_brain / "task.md"
    plan_file.write_text("# Project Plan Checklist\n- [x] Step 1\n- [ ] Step 2", encoding="utf-8")
    plan_meta = root_brain / "task.md.metadata.json"
    plan_meta.write_text(json.dumps({
        "summary": "Task checklist for test suite",
        "updatedAt": "2026-08-14T08:00:00Z",
        "requestFeedback": True,
        "userFacing": True
    }), encoding="utf-8")

    # Subagent brain
    sub_brain = brain_dir / sub_id
    sub_logs = sub_brain / ".system_generated" / "logs"
    sub_logs.mkdir(parents=True, exist_ok=True)
    sub_transcript = sub_logs / "transcript.jsonl"
    with open(sub_transcript, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "step_index": 0,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "thinking": "Surveying environment...",
            "content": "Environment surveyed."
        }) + "\n")

    # Helper Container
    class AntigravityEnvHelper:
        def __init__(self):
            self.gemini_home = gemini_home
            self.antigravity_dir = antigravity_dir
            self.config_dir = config_dir
            self.config_file = config_file
            self.summaries_db = summaries_db_path
            self.brain_dir = brain_dir
            self.convs_dir = convs_dir
            self.root_id = root_id
            self.sub_id = sub_id

        def append_step(self, conversation_id: str, step_dict: dict):
            tpath = self.brain_dir / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
            tpath.parent.mkdir(parents=True, exist_ok=True)
            with open(tpath, "a", encoding="utf-8") as f:
                f.write(json.dumps(step_dict) + "\n")

        def add_artifact(self, conversation_id: str, filename: str, content: str, meta: Optional[dict] = None):
            bdir = self.brain_dir / conversation_id
            bdir.mkdir(parents=True, exist_ok=True)
            (bdir / filename).write_text(content, encoding="utf-8")
            if meta is not None:
                (bdir / f"{filename}.metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        def get_grants(self) -> list:
            if not self.config_file.exists():
                return []
            cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
            return cfg.get("userSettings", {}).get("globalPermissionGrants", {}).get("allow", [])

    helper = AntigravityEnvHelper()

    # Apply monkeypatch / environment variables
    monkeypatch.setenv("GEMINI_HOME", str(gemini_home))
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(antigravity_dir))

    return helper


# ---------------------------------------------------------------------------
# 2. Temporary Workspace Directory Sandbox Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_workspace(tmp_path, monkeypatch):
    """
    Creates a sandbox workspace directory simulating a real project with:
    - Source files, nested subdirectories, documentation
    - Modified files for diff testing
    - Simulated .git repository metadata
    """
    ws = tmp_path / "mock_workspace"
    ws.mkdir(parents=True, exist_ok=True)

    # File structure
    (ws / "README.md").write_text("# Mock Workspace Project\nWelcome to AgyMobile.", encoding="utf-8")
    (ws / "pyproject.toml").write_text("[project]\nname = 'sample-app'\nversion = '0.1.0'", encoding="utf-8")

    src_dir = ws / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "main.py").write_text("def run():\n    print('Hello World')\n", encoding="utf-8")
    (src_dir / "utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    docs_dir = ws / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "guide.md").write_text("# User Guide\nHow to use this app.", encoding="utf-8")

    # Git metadata simulation
    git_dir = ws / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    class WorkspaceHelper:
        def __init__(self):
            self.root = ws
            self.src = src_dir
            self.docs = docs_dir

        def create_file(self, rel_path: str, content: str, encoding="utf-8"):
            p = self.root / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding=encoding)
            return p

        def create_binary(self, rel_path: str, data: bytes):
            p = self.root / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            return p

    helper = WorkspaceHelper()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    return helper


# ---------------------------------------------------------------------------
# 3. Settings & Authentication Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_config(monkeypatch):
    """Sets standard testing PIN and JWT Secret in environment."""
    monkeypatch.setenv("AGY_AUTH_PIN", TEST_PIN)
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
    return {"pin": TEST_PIN, "secret": TEST_JWT_SECRET}


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    antigravity_dir = tmp_path / "antigravity"
    antigravity_dir.mkdir(parents=True, exist_ok=True)

    (workspace / "main.py").write_text("print('hello world')", encoding="utf-8")
    (workspace / "src").mkdir(exist_ok=True)
    (workspace / "src" / "index.js").write_text("console.log('test')", encoding="utf-8")

    return Settings(
        HOST="127.0.0.1",
        PORT=8765,
        DEBUG=True,
        AUTH_PIN=TEST_PIN,
        JWT_SECRET=TEST_JWT_SECRET,
        JWT_ALGORITHM="HS256",
        TOKEN_EXPIRY_SECONDS=86400,
        WORKSPACE_ROOT=workspace,
        ANTIGRAVITY_DIR=antigravity_dir,
        RATE_LIMIT_MAX_ATTEMPTS=5,
        RATE_LIMIT_WINDOW_SECONDS=60,
        RATE_LIMIT_LOCKOUT_SECONDS=300,
    )


@pytest.fixture
def valid_jwt_token(auth_config):
    """Generates a valid HS256 JWT access token with 24-hour expiration."""
    payload = {
        "sub": "admin",
        "role": "mobile_controller",
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400,
        "iss": "agymobile-auth"
    }
    return jwt.encode(payload, auth_config["secret"], algorithm="HS256")


@pytest.fixture
def expired_jwt_token(auth_config):
    """Generates an expired HS256 JWT access token."""
    payload = {
        "sub": "admin",
        "role": "mobile_controller",
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600,
        "iss": "agymobile-auth"
    }
    return jwt.encode(payload, auth_config["secret"], algorithm="HS256")


@pytest.fixture
def invalid_jwt_token():
    """Generates a JWT token signed with an invalid/untrusted secret key."""
    payload = {
        "sub": "admin",
        "role": "mobile_controller",
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400,
        "iss": "agymobile-auth"
    }
    return jwt.encode(payload, "untrusted-wrong-secret-key-99999", algorithm="HS256")


@pytest.fixture
def valid_token(mock_settings: Settings) -> str:
    return create_access_token({"sub": "admin", "role": "admin"}, settings=mock_settings)


@pytest.fixture
def expired_token(mock_settings: Settings) -> str:
    return create_access_token({"sub": "admin"}, expires_delta=-10, settings=mock_settings)


@pytest.fixture
def auth_headers(valid_jwt_token):
    """Standard HTTP Authorization Bearer header dictionary."""
    return {"Authorization": f"Bearer {valid_jwt_token}"}


# ---------------------------------------------------------------------------
# 4. FastAPI TestClient & AsyncClient Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app(mock_settings: Settings):
    app_instance = create_app(settings=mock_settings)
    return app_instance


@pytest.fixture
def test_app(mock_antigravity_env, mock_workspace, auth_config):
    """Returns application configured with mock environment."""
    custom_settings = Settings(
        AUTH_PIN=auth_config["pin"],
        JWT_SECRET=auth_config["secret"],
        WORKSPACE_ROOT=mock_workspace.root,
        ANTIGRAVITY_DIR=mock_antigravity_env.antigravity_dir,
    )
    return create_app(settings=custom_settings)


@pytest.fixture
def client(test_app) -> Generator[TestClient, None, None]:
    """Synchronous Starlette TestClient with mocked dependencies."""
    with TestClient(test_app) as test_client:
        yield test_client


@pytest.fixture
async def async_client(test_app) -> Generator[AsyncClient, None, None]:
    """Asynchronous HTTPX client with ASGI transport."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def auth_client(client, auth_headers):
    """Pre-authenticated synchronous client."""
    client.headers.update(auth_headers)
    return client


@pytest.fixture
async def async_auth_client(async_client, auth_headers):
    """Pre-authenticated asynchronous client."""
    async_client.headers.update(auth_headers)
    return async_client


# ---------------------------------------------------------------------------
# 5. Mock WebSocket Event Collector & Helpers
# ---------------------------------------------------------------------------

class MockWebSocketCollector:
    """
    Test helper for WebSocket connections that provides asynchronous queueing,
    event filtering, and timeout-guarded assertions.
    """
    def __init__(self, ws_context):
        self.ws = ws_context
        self.events: List[Dict[str, Any]] = []

    def send_json(self, data: dict):
        self.ws.send_json(data)

    def receive_json(self) -> dict:
        data = self.ws.receive_json()
        self.events.append(data)
        return data

    def receive_until(self, predicate: Callable[[dict], bool], max_frames: int = 20) -> dict:
        """Reads frames until a frame satisfies the given predicate."""
        for _ in range(max_frames):
            frame = self.receive_json()
            if predicate(frame):
                return frame
        raise TimeoutError(f"Predicate not satisfied within {max_frames} frames limit.")


@pytest.fixture
def ws_session_client(client, valid_jwt_token):
    """Helper fixture to open /ws/session WebSocket connection."""
    def _connect(conversation_id: Optional[str] = None):
        url = f"/ws/session?token={valid_jwt_token}"
        if conversation_id:
            url += f"&conversation_id={conversation_id}"
        return client.websocket_connect(url)
    return _connect


@pytest.fixture
def ws_terminal_client(client, valid_jwt_token):
    """Helper fixture to open /ws/terminal WebSocket connection."""
    def _connect():
        return client.websocket_connect(f"/ws/terminal?token={valid_jwt_token}")
    return _connect


@pytest.fixture
def ws_system_client(client, valid_jwt_token):
    """Helper fixture to open /ws/system WebSocket connection."""
    def _connect():
        return client.websocket_connect(f"/ws/system?token={valid_jwt_token}")
    return _connect


# ---------------------------------------------------------------------------
# 6. Mock Process Streamer & Telemetry Fixtures
# ---------------------------------------------------------------------------

class MockProcessStream:
    """Simulates async subprocess execution and streaming I/O."""
    def __init__(self, output_chunks: Optional[List[bytes]] = None, exit_code: int = 0):
        self.output_chunks = output_chunks or [
            b"Windows PowerShell\r\n",
            b"PS E:\\Project> ",
            b"Get-ChildItem\r\n",
            b"Mode                Length Name\r\n",
            b"----                ------ ----\r\n",
            b"d-----                     src\r\n",
            b"-a----                 120 README.md\r\n",
            b"PS E:\\Project> "
        ]
        self.exit_code = exit_code
        self.stdin_received: List[bytes] = []
        self.returncode = exit_code
        self.pid = 49201

    async def communicate(self, input_bytes: Optional[bytes] = None):
        if input_bytes:
            self.stdin_received.append(input_bytes)
        return (b"".join(self.output_chunks), b"")

    class MockStreamReader:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        async def read(self, n=-1):
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

        async def readline(self):
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    class MockStreamWriter:
        def __init__(self, parent):
            self.parent = parent

        def write(self, data: bytes):
            self.parent.stdin_received.append(data)

        async def drain(self):
            pass

        def close(self):
            pass

    @property
    def stdout(self):
        return self.MockStreamReader(self.output_chunks)

    @property
    def stderr(self):
        return self.MockStreamReader([])

    @property
    def stdin(self):
        return self.MockStreamWriter(self)

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def mock_subprocess_runner(monkeypatch):
    """Mock subprocess execution for terminal tests."""
    def _mock_exec(*args, **kwargs):
        return asyncio.sleep(0, result=MockProcessStream())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _mock_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _mock_exec)
    return MockProcessStream


@pytest.fixture
def mock_psutil(monkeypatch):
    """Mocks psutil hardware and process APIs with deterministic metrics."""
    import psutil

    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 14.5)
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 16)

    svmem = namedtuple("svmem", ["total", "available", "percent", "used", "free"])
    mock_mem = svmem(
        total=34359738368,
        available=20971520000,
        percent=38.9,
        used=13388218368,
        free=20971520000
    )
    monkeypatch.setattr(psutil, "virtual_memory", lambda: mock_mem)

    sdiskusage = namedtuple("sdiskusage", ["total", "used", "free", "percent"])
    mock_disk = sdiskusage(
        total=1000204886016,
        used=450000000000,
        free=550204886016,
        percent=45.0
    )
    monkeypatch.setattr(psutil, "disk_usage", lambda path: mock_disk)
    monkeypatch.setattr(psutil, "boot_time", lambda: time.time() - 3600)

    MemInfo = namedtuple("MemInfo", ["rss", "vms"])

    def _mock_process_iter(attrs=None):
        processes = [
            MagicMock(info={"pid": 1001, "name": "language_server.exe", "cpu_percent": 4.2, "memory_info": MemInfo(rss=524288000, vms=0)}),
            MagicMock(info={"pid": 1002, "name": "python.exe", "cpu_percent": 2.1, "memory_info": MemInfo(rss=157286400, vms=0)}),
            MagicMock(info={"pid": 1003, "name": "powershell.exe", "cpu_percent": 0.8, "memory_info": MemInfo(rss=94371840, vms=0)})
        ]
        return iter(processes)

    monkeypatch.setattr(psutil, "process_iter", _mock_process_iter)
    return psutil
