from __future__ import annotations

from typing import Any, Dict, List


def normalize_actions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    normalized: List[Dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_type = item.get("type") or item.get("action") or item.get("name") or "unknown"
        normalized.append({
            "type": str(action_type),
            "label": str(item.get("label") or item.get("description") or item.get("name") or action_type),
            "raw": item,
        })
    return normalized


def normalize_snapshot(state_payload: Dict[str, Any], actions_payload: Dict[str, Any]) -> Dict[str, Any]:
    actions = normalize_actions(actions_payload)
    return {
        "screen": state_payload.get("screen") or state_payload.get("screen_type") or "unknown",
        "floor": state_payload.get("floor") or state_payload.get("act_floor") or 0,
        "act": state_payload.get("act") or 0,
        "in_combat": bool(state_payload.get("in_combat", False)),
        "run_id": state_payload.get("run_id"),
        "character": state_payload.get("character") or state_payload.get("character_id") or "",
        "available_actions": actions,
        "available_action_count": len(actions),
        "raw_state": state_payload,
        "raw_actions": actions_payload,
    }
