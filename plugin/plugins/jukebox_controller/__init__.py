"""Jukebox controller plugin."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, neko_plugin, plugin_entry


_VALID_ACTIONS = {"play", "next", "previous", "stop", "set_volume", "adjust_volume", "set_mode"}
_VALID_MODES = {"none", "sequence", "single", "random"}
_VOLUME_ACTIONS = {"set_volume", "adjust_volume"}


def _submission_rejection(receipt: object) -> str | None:
    """Return the rejection reason when the SDK refused the submission.

    Mirrors ``galgame_plugin.agent_sync._require_submitted``: only an explicit
    ``submitted is False`` counts. Older supported SDKs return ``None`` after a
    successful synchronous submission, so treating a missing receipt as failure
    would report a failure for a command that was actually sent.
    """
    if not isinstance(receipt, Mapping):
        return None
    if receipt.get("submitted") is not False:
        return None
    reason = receipt.get("reason")
    return str(reason) if isinstance(reason, str) and reason else "rejected"


def _volume_argument_error(action: str, value: Any) -> str | None:
    """Return an error message when a volume action's ``value`` is unusable.

    The browser rejects these as ``invalid_volume`` / ``invalid_volume_delta``,
    but that verdict arrives asynchronously and never reaches the caller, so
    the model would otherwise report a change it never made.
    """
    if value is None or value == "":
        return f"INVALID_ARGUMENT: {action} requires a numeric value"
    # bool 是 int 的子类，float(True) == 1.0 会一路放行，前端的 Number(true) 又把它
    # 读成 1，于是 set_volume=true 悄悄变成 1%。显式挡掉。
    if isinstance(value, bool):
        return f"INVALID_ARGUMENT: {action} value must be a number, not a boolean"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"INVALID_ARGUMENT: {action} value must be a number"
    if number != number or number in (float("inf"), float("-inf")):
        return f"INVALID_ARGUMENT: {action} value must be a finite number"
    low = 0.0 if action == "set_volume" else -100.0
    if not low <= number <= 100.0:
        return f"INVALID_ARGUMENT: {action} value must be within {low:g}..100"
    return None


@neko_plugin
class JukeboxControllerPlugin(NekoPluginBase):
    name = "jukebox_controller"

    def _resolve_target_lanlan(self, kwargs: dict[str, Any]) -> str | None:
        """Resolve which character this command belongs to, invocation-locally.

        Deliberately no ``ctx._current_lanlan`` fallback. That attribute has
        exactly two writers (``plugin/core/host.py``), both of which set it
        from some invocation's ``_ctx["lanlan_name"]``. So whenever *this*
        invocation carries no ``lanlan_name``, whatever sits there was left by
        a different one -- reading it can only ever scope the command to
        somebody else's character, and the event bus would then faithfully
        deliver it to that character's websocket. A jukebox command routed to
        the wrong session is worse than one that fails.

        (``music_pusher._resolve_target_lanlan`` still keeps that tier plus env
        and config fallbacks; it is a different delivery contract and is not
        changed here.)
        """
        explicit = kwargs.get("target_lanlan")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        ctx_obj = kwargs.get("_ctx")
        if isinstance(ctx_obj, dict):
            lanlan_name = ctx_obj.get("lanlan_name")
            if isinstance(lanlan_name, str) and lanlan_name.strip():
                return lanlan_name.strip()

        return None

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
                    "description": (
                        "音量数值。0 到 1 之间的小数按比例算（0.5 = 50%），"
                        "1 以上按百分点算（30 = 30%，1 = 1%）。"
                        "action=set_volume 时是目标音量，取值 0-100；"
                        "action=adjust_volume 时是相对增减量，取值 -100 到 100。"
                    ),
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

        # 动作专属参数在推给前端之前就要判掉。前端的 invalid_volume /
        # invalid_playback_mode 是异步结果，回不到这里，模型会照样跟用户说已发送。
        if normalized in _VOLUME_ACTIONS:
            volume_error = _volume_argument_error(normalized, value)
            if volume_error:
                return Err(SdkError(volume_error))
        if normalized == "set_mode" and clean_mode not in _VALID_MODES:
            return Err(SdkError(
                "INVALID_ARGUMENT: set_mode requires one of "
                + ", ".join(sorted(_VALID_MODES))
            ))
        clean_target_lanlan = self._resolve_target_lanlan(kwargs) or ""
        if not clean_target_lanlan:
            # 无归属的指令后端本来就会丢。返回 Ok 会让模型跟用户说「已发送」，
            # 所以这里必须明确失败。
            return Err(SdkError(
                "FAILED_PRECONDITION: jukebox control needs an invocation-local target character"
            ))
        receipt = self.ctx.push_message(
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
            target_lanlan=clean_target_lanlan or None,
        )
        rejection = _submission_rejection(receipt)
        if rejection:
            # 同步就被拒了（背压 / 传输不可用 / 载荷过大），前端根本收不到这条指令。
            # 不看回执就返回 Ok，模型会跟用户说「已发送」。
            return Err(SdkError(f"UNAVAILABLE: jukebox control was not submitted: {rejection}"))

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
