from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameContinueAutoAdvanceMixin:
    @plugin_entry(
        id="galgame_continue_auto_advance",
        name=tr("entries.galgame_continue_auto_advance.name", default='继续自动推进 galgame 剧情'),
        description=tr("entries.galgame_continue_auto_advance.description", default='切换到自动推进模式，并向 Game LLM Agent 发送继续推进消息。'),
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "default": "继续推进剧情"},
            },
        },
        timeout=45.0,
        llm_result_fields=[
            "result",
            "status",
            "mode",
            "mode_result",
            "agent_result",
            "diagnostic",
        ],
    )
    async def galgame_continue_auto_advance(
        self,
        message: str = "继续推进剧情",
        **_,
    ):
        normalized_message = str(message or "").strip() or "继续推进剧情"
        mode_res = await self.galgame_set_mode(
            mode="choice_advisor",
            push_notifications=True,
        )
        if isinstance(mode_res, Err):
            return mode_res
        mode_payload = json_copy(mode_res.value or {})

        agent_res = await self.galgame_agent_command(
            action="send_message",
            message=normalized_message,
        )
        if isinstance(agent_res, Err):
            return Err(
                SdkError(
                    f"continue auto advance send_message failed: {agent_res.error}",
                    details={
                        "mode_result": mode_payload,
                        "message": normalized_message,
                    },
                    mode_result=mode_payload,
                )
            )
        agent_payload = json_copy(agent_res.value or {})
        status = (
            json_copy(agent_payload.get("status"))
            if isinstance(agent_payload, dict)
            else {}
        )
        result_text = (
            str(agent_payload.get("result") or "")
            if isinstance(agent_payload, dict)
            else ""
        )
        diagnostic = (
            str(agent_payload.get("diagnostic") or "")
            if isinstance(agent_payload, dict)
            else ""
        )
        degraded = bool(agent_payload.get("degraded", False)) if isinstance(agent_payload, dict) else False
        return Ok(
            {
                "action": "continue_auto_advance",
                "message": normalized_message,
                "mode": "choice_advisor",
                "mode_result": {
                    "success": True,
                    "mode": "choice_advisor",
                    "push_notifications": True,
                    "result": mode_payload,
                },
                "agent_result": agent_payload,
                "status": status,
                "result": result_text,
                "degraded": degraded,
                "diagnostic": diagnostic,
            }
        )
