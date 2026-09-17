"""Manual hosted-panel tests for ChatCard and AgentHUD PluginView."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any

from plugin.sdk.plugin import (
    CardSubmissionError,
    ChatCard,
    Err,
    NekoPluginBase,
    Ok,
    PluginView,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    tr,
    ui,
)


@dataclass
class _Target:
    chat: ChatCard | None = None
    agent: PluginView | None = None
    chat_updates: int = 0
    agent_updates: int = 0


@neko_plugin
class UiPushTestPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._targets: dict[str, _Target] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=30)
        self._lock = asyncio.Lock()
        self._last_target = ""
        self._sequence = 0

    @lifecycle(id="startup")
    async def startup(self):
        return Ok(None)

    @lifecycle(id="shutdown")
    async def shutdown(self):
        # No display is opened automatically, and diagnostics are session-local.
        self._targets.clear()
        self._events.clear()
        return Ok(None)

    def _text(self, key: str, locale: str, **params: object) -> str:
        return self.i18n.t(key, locale=locale, **params)

    def _record(self, operation: str, target: str, display_id: str, **details: Any) -> dict[str, Any]:
        self._sequence += 1
        event = {
            "sequence": self._sequence,
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "operation": operation,
            "target_lanlan": target,
            "display_id": display_id,
            **details,
        }
        self._events.append(event)
        return event

    @ui.context(id="dashboard", title=tr("panel.title", default="UI Push Test"))
    async def dashboard(self):
        async with self._lock:
            return {
                "last_target": self._last_target,
                "targets": [{
                    "target_lanlan": name,
                    "chat_id": slot.chat.id if slot.chat else "",
                    "agent_id": slot.agent.id if slot.agent else "",
                    "chat_updates": slot.chat_updates,
                    "agent_updates": slot.agent_updates,
                } for name, slot in self._targets.items()],
                "events": list(reversed(self._events)),
            }

    def _content(self, title: str, html: str, locale: str) -> str:
        return (
            '<section class="push-test">'
            f'<strong>{escape(title)}</strong><div class="push-test-body">{html}</div>'
            f'<button data-neko-action="echo">{escape(self._text("content.echo", locale))}</button>'
            '</section>'
        )

    async def _submit(self, surface: str, create: bool, target_lanlan: str,
                      title: str, html: str, css: str, summary: str, locale: str):
        if not isinstance(target_lanlan, str) or not target_lanlan.strip():
            return Err(SdkError(self._text("error.target", locale)))
        target = target_lanlan.strip()
        if not all(isinstance(value, str) for value in (title, html, css, summary)):
            return Err(SdkError(self._text("error.content", locale)))
        async with self._lock:
            slot = self._targets.setdefault(target, _Target())
            handle = slot.chat if surface == "chat" else slot.agent
            if not create and handle is None:
                return Err(SdkError(self._text("error.createFirst", locale)))
            resolved_title = title.strip() or self._text(f"section.{surface}", locale)
            body = self._text("content.created", locale) if create else html
            args = {
                "html": self._content(resolved_title, body, locale),
                "css": css,
                "summary": summary.strip() or resolved_title,
                "actions": {"echo": {"entry": "echo", "args": {"surface": surface, "locale": locale}}},
            }
            try:
                if create:
                    if surface == "chat":
                        handle = await self.ctx.create_card(target_lanlan=target, **args)
                        slot.chat, slot.chat_updates = handle, 0
                    else:
                        handle = await self.ctx.create_view(target_lanlan=target, title=resolved_title, **args)
                        slot.agent, slot.agent_updates = handle, 0
                else:
                    if surface == "agent":
                        await handle.update(title=resolved_title, **args)
                        slot.agent_updates += 1
                    else:
                        await handle.update(**args)
                        slot.chat_updates += 1
            except CardSubmissionError as error:
                self._record(f"{surface}_failed", target, handle.id if handle else "", error=str(error))
                return Err(SdkError(self._text("error.submit", locale, detail=str(error))))
            self._last_target = target
            event = self._record(f"{surface}_{'create' if create else 'push'}", target, handle.id)
            return Ok({"submitted": True, "message": self._text("result.submitted", locale), **event})

    @ui.action(id="chat_create", label=tr("action.chatCreate", default="Create chat card"))
    @plugin_entry(id="chat_create", name=tr("action.chatCreate", default="Create chat card"))
    async def chat_create(self, target_lanlan: str, title: str = "", css: str = "", locale: str = "zh-CN"):
        return await self._submit("chat", True, target_lanlan, title, "", css, "", locale)

    @ui.action(id="chat_push", label=tr("action.chatPush", default="Push chat content"))
    @plugin_entry(id="chat_push", name=tr("action.chatPush", default="Push chat content"))
    async def chat_push(self, target_lanlan: str, html: str, css: str = "", title: str = "",
                        summary: str = "", locale: str = "zh-CN"):
        return await self._submit("chat", False, target_lanlan, title, html, css, summary, locale)

    @ui.action(id="agent_create", label=tr("action.agentCreate", default="Create Agent content"))
    @plugin_entry(id="agent_create", name=tr("action.agentCreate", default="Create Agent content"))
    async def agent_create(self, target_lanlan: str, title: str = "", css: str = "", locale: str = "zh-CN"):
        return await self._submit("agent", True, target_lanlan, title, "", css, "", locale)

    @ui.action(id="agent_push", label=tr("action.agentPush", default="Push Agent content"))
    @plugin_entry(id="agent_push", name=tr("action.agentPush", default="Push Agent content"))
    async def agent_push(self, target_lanlan: str, html: str, css: str = "", title: str = "",
                         summary: str = "", locale: str = "zh-CN"):
        return await self._submit("agent", False, target_lanlan, title, html, css, summary, locale)

    @ui.action(id="agent_close", label=tr("action.agentClose", default="Close Agent content"))
    @plugin_entry(id="agent_close", name=tr("action.agentClose", default="Close Agent content"))
    async def agent_close(self, target_lanlan: str, locale: str = "zh-CN"):
        target = target_lanlan.strip() if isinstance(target_lanlan, str) else ""
        async with self._lock:
            slot = self._targets.get(target)
            if not slot or slot.agent is None:
                return Err(SdkError(self._text("error.createFirst", locale)))
            handle = slot.agent
            try:
                await handle.close()
            except CardSubmissionError as error:
                return Err(SdkError(self._text("error.submit", locale, detail=str(error))))
            slot.agent = None
            event = self._record("agent_close", target, handle.id)
            return Ok({"submitted": True, "message": self._text("result.submitted", locale), **event})

    @ui.action(id="echo", label=tr("content.echo", default="Test callback"), refresh_context=False)
    @plugin_entry(id="echo", name=tr("content.echo", default="Test callback"))
    async def echo(self, surface: str, locale: str = "zh-CN", _ctx: dict | None = None):
        context = _ctx or {}
        display_id = context.get("view_id" if surface == "agent" else "card_id")
        target = context.get("lanlan_name")
        if surface not in ("chat", "agent") or not display_id or not target:
            return Err(SdkError(self._text("error.callbackContext", locale)))
        async with self._lock:
            event = self._record(f"{surface}_callback", str(target), str(display_id), run_id=context.get("run_id", ""))
            return Ok({"message": self._text("result.callback", locale), **event})
