from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameAgentCommandMixin:
    @plugin_entry(
        id="galgame_agent_command",
        name=tr("entries.galgame_agent_command.name", default='向 Game LLM Agent 发送指令'),
        description=tr("entries.galgame_agent_command.description", default='查询 Agent 状态、上下文、发送消息或控制待机。'),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "query_status",
                        "query_context",
                        "send_message",
                        "set_standby",
                        "list_messages",
                        "ack_message",
                    ],
                },
                "message": {"type": "string", "default": ""},
                "context_query": {"type": "string", "default": ""},
                "message_id": {"type": "string", "default": ""},
                "reply_to_message_id": {"type": "string", "default": ""},
                "sender_role": {"type": "string", "default": ""},
                "consultation_reply": {"type": "boolean", "default": False},
                "direction": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 50},
                "standby": {"type": "boolean"},
            },
            "required": ["action"],
        },
        timeout=45.0,
        llm_result_fields=["result", "status"],
    )
    async def galgame_agent_command(
        self,
        action: str,
        message: str = "",
        context_query: str = "",
        message_id: str = "",
        reply_to_message_id: str = "",
        sender_role: str = "",
        consultation_reply: bool = False,
        direction: str = "",
        limit: int = 50,
        standby: bool | None = None,
        **_,
    ):
        if self._game_agent is None:
            return Err(SdkError("galgame_plugin game agent is not initialized"))
        local = self._snapshot_state(include_private_context=True)
        if action == "query_status":
            return Ok(await self._game_agent.query_status(local))
        if action == "query_context":
            if not context_query.strip():
                return Err(SdkError("context_query is required for query_context"))
            return Ok(
                await self._game_agent.query_context(
                    local,
                    context_query=context_query.strip(),
                )
            )
        if action == "send_message":
            if not message.strip():
                return Err(SdkError("message is required for send_message"))
            return Ok(
                await self._game_agent.send_message(
                    local,
                    message=message.strip(),
                    reply_to_message_id=reply_to_message_id.strip(),
                    sender_role=sender_role.strip(),
                    consultation_reply=bool(consultation_reply),
                )
            )
        if action == "set_standby":
            if standby is None:
                return Err(SdkError("standby is required for set_standby"))
            return Ok(await self._game_agent.set_standby(local, standby=bool(standby)))
        if action == "list_messages":
            sanitized_limit = _coerce_int_range(
                limit,
                default=50,
                minimum=1,
                maximum=500,
            )
            return Ok(
                await self._game_agent.list_messages(
                    local,
                    direction=direction,
                    limit=sanitized_limit,
                )
            )
        if action == "ack_message":
            if not message_id.strip():
                return Err(SdkError("message_id is required for ack_message"))
            return Ok(
                await self._game_agent.ack_message(
                    local,
                    message_id=message_id.strip(),
                )
            )
        return Err(SdkError(f"unsupported agent action: {action!r}"))
