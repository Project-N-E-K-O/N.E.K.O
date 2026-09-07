"""Display-only HTML routing. Keep updates on the original character."""
from collections import OrderedDict
import logging
from typing import Any

from starlette.websockets import WebSocketState

from plugin.sdk.shared.core.cards import card_fields

logger = logging.getLogger(__name__)

# Routing hints only, not stored card contents or an authorization registry.
_targets: OrderedDict[tuple[str, str], str] = OrderedDict()
# AgentHUD has one active page per plugin/character, independent of chat history.
# Both indexes contain only these active pages; replacement/close are O(1).
_view_targets: dict[tuple[str, str], str] = {}
_active_views: dict[tuple[str, str], str] = {}


async def deliver_plugin_card(event: dict, managers: dict[str, Any], default_target: str | None) -> bool:
    plugin_id = event.get("plugin_id")
    part = event.get("card")
    if not isinstance(plugin_id, str) or not plugin_id or not isinstance(part, dict):
        return False
    card_id = part.get("card_id")
    operation = part.get("operation")
    presentation = part.get("presentation", "chat")
    if presentation not in ("chat", "agent"):
        return False
    allowed_operations = ("create", "update", "close") if presentation == "agent" else ("create", "update")
    if not isinstance(card_id, str) or not card_id or operation not in allowed_operations:
        return False
    field_names = ("html", "css", "summary", "actions")
    if presentation == "agent":
        field_names += ("title",)
    try:
        fields = card_fields({key: part[key] for key in field_names if key in part}) if operation != "close" else {}
    except (TypeError, ValueError):
        return False
    if operation == "create":
        required = {"html", "title"} if presentation == "agent" else {"html", "summary"}
        if not required.issubset(fields):
            return False
        if presentation == "agent":
            fields.setdefault("summary", fields["title"])
    key = (plugin_id, card_id)
    targets = _view_targets if presentation == "agent" else _targets
    target = targets.get(key) or event.get("lanlan_name")
    if not target and operation == "create":
        target = default_target
    if not isinstance(target, str) or not target:
        return False
    mgr = managers.get(target)
    if mgr is None:
        return False
    if presentation != "agent":
        _targets[key] = target
        _targets.move_to_end(key)
        while len(_targets) > 512:
            _targets.popitem(last=False)
    block = {"type": "html_card", "cardId": card_id, "pluginId": plugin_id,
             "targetLanlan": target, "operation": operation, **fields}
    if presentation == "agent":
        websocket = getattr(mgr, "websocket", None)
        if getattr(websocket, "client_state", None) != WebSocketState.CONNECTED:
            return False
        try:
            await websocket.send_json({"type": "plugin_view", "view": {
                **block, "presentation": "agent",
            }})
        except Exception as error:
            logger.warning("Plugin view WebSocket send failed for %s: %s", target, error)
            return False
        # A failed replacement/close must leave the previous page routable.
        slot = (plugin_id, target)
        if operation == "create":
            previous = _active_views.get(slot)
            if previous is not None:
                _view_targets.pop((plugin_id, previous), None)
            _active_views[slot] = card_id
            _view_targets[key] = target
        elif operation == "close" and _active_views.get(slot) == card_id:
            _active_views.pop(slot)
            _view_targets.pop(key, None)
        # Do not repopulate either index from stale/recovered update handles.
        return True
    return await mgr.render_chat_blocks(
        [block],
        request_id=card_id, source="plugin", source_name=plugin_id,
    )
