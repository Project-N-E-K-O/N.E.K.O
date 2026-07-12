"""Jukebox controller plugin."""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, neko_plugin, plugin_entry


_ACTION_ALIASES = {
    "play": "play",
    "next": "next",
    "skip": "next",
    "stop": "stop",
}


@neko_plugin
class JukeboxControllerPlugin(NekoPluginBase):
    name = "jukebox_controller"

    @plugin_entry(
        id="control_jukebox",
        name="控制点歌台",
        description=(
            "控制本地 N.E.K.O 点歌台。用户要求播放指定曲目时使用 play 并传 query；"
            "用户要求切歌、下一首时使用 next；用户要求停止点歌台播放时使用 stop。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "next", "skip", "stop"],
                    "description": "控制动作：play 播放指定曲目，next/skip 切到下一首，stop 停止播放。",
                },
                "query": {
                    "type": "string",
                    "description": "要播放的曲目名。action=play 时使用；支持不完整歌名，前端播放第一匹配项。",
                },
            },
            "required": ["action"],
        },
        llm_result_fields=["action", "query", "message"],
    )
    async def control_jukebox(self, action: str, query: str = "", **kwargs: Any):
        normalized = _ACTION_ALIASES.get(str(action or "").strip().lower())
        if not normalized:
            return Err(SdkError("INVALID_ARGUMENT: unsupported jukebox action"))

        clean_query = str(query or "").strip()
        target_lanlan = kwargs.get("target_lanlan")
        self.ctx.push_message(
            source="jukebox_controller",
            description=f"Jukebox control: {normalized}",
            priority=8,
            parts=[
                {
                    "type": "ui_action",
                    "action": "jukebox_control",
                    "jukebox_action": normalized,
                    "query": clean_query,
                }
            ],
            visibility=["chat"],
            ai_behavior="blind",
            metadata={
                "action": normalized,
                "query": clean_query,
            },
            target_lanlan=target_lanlan if isinstance(target_lanlan, str) and target_lanlan else None,
        )

        if normalized == "play":
            message = f"已发送点歌台播放指令: {clean_query or '第一首'}"
        elif normalized == "next":
            message = "已发送点歌台切歌指令"
        else:
            message = "已发送点歌台停止指令"

        return Ok({
            "action": normalized,
            "query": clean_query,
            "message": message,
        })
