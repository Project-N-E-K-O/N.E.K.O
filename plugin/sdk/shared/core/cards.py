"""Small handles for online HTML chat cards and AgentHUD plugin content."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any


class CardSubmissionError(RuntimeError):
    """The local plugin uplink rejected a card submission."""


def card_fields(fields: dict[str, Any]) -> dict[str, Any]:
    for key in ("html", "css", "summary", "title"):
        if key in fields and not isinstance(fields[key], str):
            raise TypeError(f"{key} must be a string")
    if "actions" in fields:
        actions = fields["actions"]
        if not isinstance(actions, dict):
            raise TypeError("actions must be a dict")
        for name, action in actions.items():
            if not isinstance(name, str) or not name or not isinstance(action, dict):
                raise TypeError("actions must map non-empty button IDs to action objects")
            if not isinstance(action.get("entry"), str) or not action["entry"]:
                raise ValueError("each card action requires an entry ID")
            if not isinstance(action.get("args", {}), dict):
                raise TypeError("card action args must be a dict")
    # Snapshot mutable arguments before the asynchronous submission.
    return json.loads(json.dumps(fields, allow_nan=False))


class ChatCard:
    """A card ID and its original recipient. Awaiting only confirms submission."""

    _presentation = "chat"

    def __init__(self, context: Any, card_id: str, target_lanlan: str | None):
        if not isinstance(card_id, str) or not card_id:
            raise ValueError("card_id must be a non-empty string")
        self._context = context
        self._id = card_id
        self._target_lanlan = target_lanlan
        self._lock = asyncio.Lock()

    @property
    def id(self) -> str:
        return self._id

    async def _send(self, operation: str, fields: dict[str, Any]) -> None:
        async with self._lock:
            part = {"type": "html_card", "card_id": self.id,
                    "operation": operation, **fields}
            if self._presentation != "chat":
                part["presentation"] = self._presentation
            result = await asyncio.to_thread(
                self._context.push_message,
                visibility=["chat"], ai_behavior="blind",
                target_lanlan=self._target_lanlan,
                parts=[part],
            )
            if not isinstance(result, dict) or result.get("submitted") is not True:
                reason = result.get("reason", "transport_error") if isinstance(result, dict) else "transport_error"
                raise CardSubmissionError(f"Card submission failed: {reason}")

    async def update(self, *, html: str | None = None, css: str | None = None,
                     summary: str | None = None, actions: dict[str, Any] | None = None) -> None:
        """Replace supplied fields; omitted/None fields stay, actions={} clears buttons."""
        fields = {key: value for key, value in {
            "html": html, "css": css, "summary": summary, "actions": actions,
        }.items() if value is not None}
        if fields:
            await self._send("update", card_fields(fields))


class PluginView(ChatCard):
    """HTML content in AgentHUD. Closing the view only ends its display."""

    _presentation = "agent"

    async def update(self, *, title: str | None = None, html: str | None = None,
                     css: str | None = None, summary: str | None = None,
                     actions: dict[str, Any] | None = None) -> None:
        """Replace supplied fields without reopening a closed view."""
        fields = {key: value for key, value in {
            "title": title, "html": html, "css": css,
            "summary": summary, "actions": actions,
        }.items() if value is not None}
        if fields:
            await self._send("update", card_fields(fields))

    async def close(self) -> None:
        """End this display instance; repeated or stale closes are harmless."""
        await self._send("close", {})


async def create_card(context: Any, *, html: str, summary: str, css: str = "",
                      actions: dict[str, Any] | None = None,
                      target_lanlan: str | None = None) -> ChatCard:
    fields = card_fields({"html": html, "summary": summary, "css": css,
                          "actions": actions if actions is not None else {}})
    card = ChatCard(context, uuid.uuid4().hex, target_lanlan)
    await card._send("create", fields)
    return card


async def create_view(context: Any, *, title: str, html: str, css: str = "",
                      actions: dict[str, Any] | None = None,
                      summary: str | None = None,
                      target_lanlan: str | None = None) -> PluginView:
    fields = card_fields({"title": title, "html": html, "css": css,
                          "summary": title if summary is None else summary,
                          "actions": actions if actions is not None else {}})
    view = PluginView(context, uuid.uuid4().hex, target_lanlan)
    await view._send("create", fields)
    return view
