"""
backend/session_hub/routes.py

REST API and WebSocket endpoints for Antigravity 2.0 Session Hub.
Provides conversation enumeration, transcripts, prompt dispatch, tool decisions,
markdown artifacts, and realtime WebSocket event multiplexing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.security import get_current_user, verify_ws_token
from backend.session_hub.artifacts import ArtifactsManager
from backend.session_hub.cli_dispatcher import CLIDispatcher
from backend.session_hub.sqlite_reader import SQLiteReader
from backend.session_hub.tailer import TranscriptTailer
from backend.session_hub.tool_manager import ToolManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["Session Hub"])
ws_router = APIRouter(tags=["WebSocket Session Hub"])


# =====================================================================
# Request / Response Models
# =====================================================================

class PromptRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Prompt text or slash command to execute")
    mode: Optional[str] = Field("accept-edits", description="Execution mode: accept-edits, default, etc.")


class ToolApprovalRequest(BaseModel):
    tool_call_id: str = Field(..., min_length=1, description="ID of pending tool call")
    action: Literal["approve", "reject"] = Field(..., description="Decision action: approve or reject")
    pattern: Optional[str] = Field(None, description="Optional permission pattern (e.g. command(pytest*))")


# =====================================================================
# REST Endpoints
# =====================================================================

@router.get("", response_model=List[Dict[str, Any]])
async def list_conversations(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    parent_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Enumerate Antigravity conversations ordered by last modification time."""
    reader = SQLiteReader()
    return reader.list_conversations(
        limit=limit,
        offset=offset,
        search=search,
        status=status,
        parent_id=parent_id,
    )


@router.get("/{conversation_id}/transcript")
async def get_conversation_transcript(
    conversation_id: str,
    from_offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Retrieve full transcript steps, markdown artifacts, and pending tool calls."""
    reader = SQLiteReader()
    conv = reader.get_conversation(conversation_id)

    tailer = TranscriptTailer(conversation_id)
    steps, end_offset = tailer.tail_from_offset(from_offset)

    artifacts_mgr = ArtifactsManager()
    artifacts = artifacts_mgr.list_artifacts(conversation_id)

    tool_mgr = ToolManager()
    pending_tools = tool_mgr.get_pending_tool_calls(conversation_id)

    # If conversation is neither found in DB nor has transcript/artifacts
    if not conv and not steps and not artifacts:
        # Check if brain directory exists
        brain_dir = artifacts_mgr._resolve_brain_dir(conversation_id)
        if not brain_dir.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation '{conversation_id}' not found.",
            )

    return {
        "conversation_id": conversation_id,
        "title": conv.get("title", f"Session {conversation_id[:8]}") if conv else f"Session {conversation_id[:8]}",
        "steps": steps,
        "artifacts": artifacts,
        "pending_tool_calls": pending_tools,
        "byte_offset": end_offset,
    }


@router.post("/{conversation_id}/prompt")
async def dispatch_prompt_to_conversation(
    conversation_id: str,
    body: PromptRequest,
    current_user: dict = Depends(get_current_user),
):
    """Dispatch prompt or slash command to Antigravity CLI subprocess."""
    dispatcher = CLIDispatcher()
    result = await dispatcher._async_dispatch_prompt(
        conversation_id=conversation_id,
        prompt=body.prompt,
        mode=body.mode or "accept-edits",
    )

    # If transcript file / brain exists, append user input step asynchronously so state updates immediately
    def _append_user_input_step():
        try:
            tailer = TranscriptTailer(conversation_id)
            if tailer.filepath.parent.is_dir():
                now_iso = datetime.now(timezone.utc).isoformat()
                existing_steps, _ = tailer.tail_from_offset(0)
                next_idx = len(existing_steps)
                step_record = {
                    "step_index": next_idx,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": now_iso,
                    "content": body.prompt,
                }
                with open(tailer.filepath, "a", encoding="utf-8") as f:
                    f.write(json.dumps(step_record) + "\n")
        except Exception as e:
            logger.debug(f"Could not append USER_INPUT to transcript: {e}")

    await asyncio.to_thread(_append_user_input_step)
    return result


@router.post("/{conversation_id}/tool-approval")
async def process_tool_approval(
    conversation_id: str,
    body: ToolApprovalRequest,
    current_user: dict = Depends(get_current_user),
):
    """Approve or reject pending tool call and update globalPermissionGrants."""
    tool_mgr = ToolManager()
    try:
        res = await asyncio.to_thread(
            tool_mgr.handle_decision,
            conversation_id=conversation_id,
            tool_call_id=body.tool_call_id,
            action=body.action,
            pattern=body.pattern,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing tool approval: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/{conversation_id}/artifacts")
async def list_conversation_artifacts(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all markdown artifacts (task.md, implementation_plan.md, etc.)."""
    artifacts_mgr = ArtifactsManager()
    return await asyncio.to_thread(artifacts_mgr.list_artifacts, conversation_id)


@router.get("/{conversation_id}/artifacts/{filename}")
async def get_conversation_artifact_content(
    conversation_id: str,
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    """Retrieve full content and checklist progress of a specific markdown artifact."""
    artifacts_mgr = ArtifactsManager()
    artifact = await asyncio.to_thread(artifacts_mgr.get_artifact_content, conversation_id, filename)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact '{filename}' not found for conversation '{conversation_id}'.",
        )
    return artifact


@router.get("/{conversation_id}/subagents")
async def get_conversation_subagent_tree(
    conversation_id: str,
    max_depth: int = Query(5, ge=1, le=10),
    current_user: dict = Depends(get_current_user),
):
    """Retrieve subagent hierarchy tree for conversation."""
    reader = SQLiteReader()
    tree = await asyncio.to_thread(reader.get_subagent_tree, conversation_id, max_depth=max_depth)
    if not tree:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )
    return tree


# =====================================================================
# WebSocket Session Multiplexer (/ws/session)
# =====================================================================

@ws_router.websocket("/ws/session")
async def session_websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    conversation_id: Optional[str] = Query(None),
):
    """
    Realtime WebSocket session channel for live transcript streaming,
    token streaming (thinking & content), tool call requests, and subagent trees.
    """
    # 1. Authenticate Token
    auth_token = token or websocket.query_params.get("token")
    user_payload = verify_ws_token(auth_token)
    if not user_payload:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    active_tailer_tasks: Dict[str, asyncio.Task] = {}
    active_tailers: Dict[str, TranscriptTailer] = {}

    async def _forward_stream(cid: str, start_offset: int = 0):
        tailer = TranscriptTailer(cid)
        active_tailers[cid] = tailer
        try:
            # Emit subagent hierarchy if present
            reader = SQLiteReader()
            tree = await asyncio.to_thread(reader.get_subagent_tree, cid)
            if tree:
                try:
                    await websocket.send_json({
                        "type": "subagent_status",
                        "conversation_id": cid,
                        "subagent_id": cid,
                        "status": tree.get("status", "active"),
                        "hierarchy": tree.get("children", []),
                        "byte_offset": start_offset,
                    })
                except Exception:
                    return

            async for event in tailer.stream_websocket_events(from_offset=start_offset, follow=True):
                try:
                    await websocket.send_json(event)
                except Exception:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Tailer stream ended for {cid}: {e}")
        finally:
            tailer.stop()
            active_tailers.pop(cid, None)

    # If conversation_id was passed in URL query, subscribe immediately
    if conversation_id:
        try:
            await websocket.send_json({
                "type": "subscribed",
                "conversation_id": conversation_id,
            })
            task = asyncio.create_task(_forward_stream(conversation_id, 0))
            active_tailer_tasks[conversation_id] = task
        except Exception:
            return

    try:
        while True:
            # Receive incoming frame from client
            message_text = await websocket.receive_text()
            try:
                msg = json.loads(message_text)
            except Exception:
                # Malformed JSON frame
                logger.debug("Received non-JSON frame on /ws/session.")
                continue

            if not isinstance(msg, dict):
                continue

            action = msg.get("action")
            cid = msg.get("conversation_id") or conversation_id

            if action == "subscribe":
                target_cid = msg.get("conversation_id")
                from_offset = msg.get("from_offset", 0)
                if target_cid:
                    # Cancel existing task for this conversation if any
                    old_task = active_tailer_tasks.pop(target_cid, None)
                    if old_task:
                        old_task.cancel()
                    old_tailer = active_tailers.pop(target_cid, None)
                    if old_tailer:
                        old_tailer.stop()

                    try:
                        await websocket.send_json({
                            "type": "subscribed",
                            "conversation_id": target_cid,
                        })
                    except Exception:
                        break

                    task = asyncio.create_task(_forward_stream(target_cid, from_offset))
                    active_tailer_tasks[target_cid] = task

            elif action == "unsubscribe":
                target_cid = msg.get("conversation_id")
                if target_cid in active_tailer_tasks:
                    active_tailer_tasks[target_cid].cancel()
                    active_tailer_tasks.pop(target_cid, None)
                if target_cid in active_tailers:
                    active_tailers[target_cid].stop()
                    active_tailers.pop(target_cid, None)
                try:
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "conversation_id": target_cid,
                    })
                except Exception:
                    break

            elif action == "send_prompt":
                target_cid = msg.get("conversation_id")
                prompt_text = msg.get("prompt", "")
                mode = msg.get("mode", "accept-edits")
                if target_cid and prompt_text:
                    dispatcher = CLIDispatcher()
                    res = await dispatcher._async_dispatch_prompt(
                        conversation_id=target_cid,
                        prompt=prompt_text,
                        mode=mode,
                    )
                    try:
                        await websocket.send_json({
                            "type": "prompt_dispatched",
                            "conversation_id": target_cid,
                            "result": res,
                        })
                    except Exception:
                        break

            elif action == "tool_decision":
                target_cid = msg.get("conversation_id")
                tool_call_id = msg.get("tool_call_id")
                decision = msg.get("decision") or msg.get("action")
                pattern = msg.get("pattern")
                if target_cid and tool_call_id and decision:
                    tool_mgr = ToolManager()
                    try:
                        res = await asyncio.to_thread(
                            tool_mgr.handle_decision,
                            conversation_id=target_cid,
                            tool_call_id=tool_call_id,
                            action=decision,
                            pattern=pattern,
                        )
                        await websocket.send_json({
                            "type": "tool_decision_ack",
                            "conversation_id": target_cid,
                            "result": res,
                        })
                    except Exception as e:
                        try:
                            await websocket.send_json({
                                "type": "error",
                                "message": str(e),
                            })
                        except Exception:
                            break

            elif action == "cancel_prompt":
                target_cid = msg.get("conversation_id")
                if target_cid:
                    dispatcher = CLIDispatcher()
                    cancelled = await dispatcher.cancel_prompt(target_cid)
                    try:
                        await websocket.send_json({
                            "type": "prompt_cancelled",
                            "conversation_id": target_cid,
                            "success": cancelled,
                        })
                    except Exception:
                        break

            else:
                # Unrecognized action
                logger.debug(f"Unrecognized action received on /ws/session: {action}")
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Unrecognized action '{action}'",
                    })
                except Exception:
                    break

    except WebSocketDisconnect:
        logger.debug("Client disconnected from /ws/session.")
    except Exception as e:
        logger.debug(f"Exception on /ws/session: {e}")
    finally:
        for t in list(active_tailer_tasks.values()):
            t.cancel()
        for tailer in list(active_tailers.values()):
            tailer.stop()
        active_tailer_tasks.clear()
        active_tailers.clear()
