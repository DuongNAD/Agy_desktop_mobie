"""
backend/session_hub/__init__.py

Antigravity 2.0 Session Hub Engine package.
Exports SQLite reader, transcript tailer, CLI dispatcher, tool manager,
markdown artifacts manager, and FastAPI routers.
"""

from backend.session_hub.sqlite_reader import (
    AntigravitySqliteReader,
    ConversationDetail,
    ConversationSummary,
    HubStats,
    SQLiteReader,
    SubagentNode,
)
from backend.session_hub.tailer import (
    AsyncTranscriptTailer,
    ToolCallInfo,
    TranscriptEntry,
    TranscriptTailer,
)
from backend.session_hub.cli_dispatcher import CLIDispatcher
from backend.session_hub.tool_manager import ToolManager
from backend.session_hub.artifacts import (
    ArtifactInfo,
    ArtifactProgress,
    ArtifactsManager,
    PlanStep,
)
from backend.session_hub.routes import router as session_router, ws_router as session_ws_router

__all__ = [
    "AntigravitySqliteReader",
    "SQLiteReader",
    "ConversationSummary",
    "ConversationDetail",
    "SubagentNode",
    "HubStats",
    "AsyncTranscriptTailer",
    "TranscriptTailer",
    "TranscriptEntry",
    "ToolCallInfo",
    "CLIDispatcher",
    "ToolManager",
    "ArtifactsManager",
    "ArtifactInfo",
    "ArtifactProgress",
    "PlanStep",
    "session_router",
    "session_ws_router",
]
