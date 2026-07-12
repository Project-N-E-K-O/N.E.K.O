"""Jukebox controller plugin."""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, neko_plugin, plugin_entry


_VALID_ACTIONS = {"play", "next", "previous", "stop", "set_volume", "adjust_volume", "set_mode"}


@neko_plugin
class JukeboxControllerPlugin(NekoPluginBase):
    name = "jukebox_controller"

    @plugin_entry(
        id="control_jukebox",
        name="控制点歌台",
        description=(
            "控制本地 N.E.K.O 点歌台。用户要求播放指定曲目时使用 play 并传 query；"
            "用户要求切歌、下一首时使用 next；用户要求停止点歌台播放时使用 stop；"
            "用户要求上一首时使用 previous；"
            "用户要求设置音量时使用 set_volume 并传 value；用户要求调大/调小音量时使用 adjust_volume 并传 value；"
            "用户要求切换播放模式时使用 set_mode 并传 mode。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "next", "previous", "stop", "set_volume", "adjust_volume", "set_mode"],
                    "description": "控制动作：play 播放指定曲目，next 切到下一首，previous 切到上一首，stop 停止播放，set_volume 设置音量，adjust_volume 增减音量，set_mode 设置播放模式。",
                },
                "query": {
                    "type": "string",
                    "description": "要播放的曲目名。action=play 时使用；支持不完整歌名，前端播放第一匹配项。",
                },
                "value": {
                    "type": "number",
                    "description": "action=set_volume 时表示目标音量，可传 0-1 或 0-100；action=adjust_volume 时表示相对增减量，可传 -1 到 1 或 -100 到 100。",
                },
                "mode": {
                    "type": "string",
                    "enum": ["none", "sequence", "single", "random"],
                    "description": "action=set_mode 时使用。none 不自动下一首，sequence 顺序播放，single 单曲循环，random 随机播放。",
                },
            },
            "required": ["action"],
        },
        llm_result_fields=["action", "query", "value", "mode", "message"],
    )
    async def control_jukebox(
        self,
        action: str,
        query: str = "",
        value: Any = None,
        mode: str = "",
        **kwargs: Any,
    ):
        normalized = str(action or "").strip().lower()
        if normalized not in _VALID_ACTIONS:
            return Err(SdkError("INVALID_ARGUMENT: unsupported jukebox action"))

        clean_query = str(query or "").strip()
        clean_mode = str(mode or "").strip().lower()
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
                    "value": value,
                    "mode": clean_mode,
                }
            ],
            visibility=["chat"],
            ai_behavior="blind",
            metadata={
                "action": normalized,
                "query": clean_query,
                "value": value,
                "mode": clean_mode,
            },
            target_lanlan=target_lanlan if isinstance(target_lanlan, str) and target_lanlan else None,
        )

        if normalized == "play":
            message = f"已发送点歌台播放指令: {clean_query or '第一首'}"
        elif normalized == "next":
            message = "已发送点歌台切歌指令"
        elif normalized == "previous":
            message = "已发送点歌台上一首指令"
        elif normalized == "stop":
            message = "已发送点歌台停止指令"
        elif normalized == "set_volume":
            message = "已发送点歌台音量设置指令"
        elif normalized == "adjust_volume":
            message = "已发送点歌台音量调整指令"
        else:
            message = "已发送点歌台播放模式设置指令"

        return Ok({
            "action": normalized,
            "query": clean_query,
            "value": value,
            "mode": clean_mode,
            "message": message,
        })
