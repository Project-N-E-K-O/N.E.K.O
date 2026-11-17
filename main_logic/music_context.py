"""Authoritative music evidence shared by user replies and proactive openers.

Snapshots are read on demand, never injected into unrelated conversation.
"""
import json

from config.prompts.prompts_music_context import MUSIC_RUNTIME_CONTEXT


def music_runtime_snapshot(manager):
    playback = getattr(manager, "_music_confirmed_state", None)
    if not playback:
        return None
    # Upstream receipts establish the current player, not fulfillment of a
    # particular command. Never invent false request-status claims here.
    return {
        "playback_state": playback.get("state") or "unknown",
        "track": {k: str(playback.get(k) or "")[:120] for k in ("name", "artist")},
    }


def music_runtime_context(manager):
    snapshot = music_runtime_snapshot(manager)
    if snapshot is None:
        return ""
    # Fixed keys and individually bounded names: no full library/history payload.
    return MUSIC_RUNTIME_CONTEXT.format(snapshot=json.dumps(snapshot, ensure_ascii=False))


def music_context_tools(manager):
    """Keep volatile player data behind a read-only tool on all offline turns."""
    from main_logic.omni_offline_client import ContextTool
    from main_logic.tool_calling import ToolDefinition
    from config.prompts.prompts_music_context import MUSIC_STATE_TOOL_DESCRIPTION

    def read():
        context = music_runtime_context(manager)
        return {"observation": context} if context else {"status": "unavailable"}

    return (ContextTool(ToolDefinition(
        name="read_music_state",
        description=MUSIC_STATE_TOOL_DESCRIPTION,
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    ), read),)
