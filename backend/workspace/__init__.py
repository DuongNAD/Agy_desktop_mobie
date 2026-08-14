"""
AgyMobile Workspace Package
Provides workspace filesystem management, tree enumeration, safe reading/writing,
and visual unified diff generation.
"""

from backend.workspace.file_manager import (
    DEV_MIME_MAP,
    KNOWN_BINARY_EXTENSIONS,
    WorkspaceFileManager,
    router as workspace_router,
    workspace_file_manager,
)

__all__ = [
    "WorkspaceFileManager",
    "workspace_file_manager",
    "workspace_router",
    "DEV_MIME_MAP",
    "KNOWN_BINARY_EXTENSIONS",
]
