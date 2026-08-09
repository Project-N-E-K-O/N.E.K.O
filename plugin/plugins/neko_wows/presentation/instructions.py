"""Instruction text handed to the model, and the revision that produced it.

Dual and single channel differ *only* here. Switching between them changes no
priority, no TTL and no preemption rule -- it changes which paragraphs get
concatenated, nothing else.

A `PromptBundle` is one immutable revision of the three sections. The pipeline
reads the active bundle once per frame, so a swap can only ever take effect
between frames, and the revision id travels with the call-out for traceability.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.contracts import CHANNEL_SINGLE, LANE_URGENT

# Injected once when the telemetry link comes up, so the character knows what
# situation she is in before any call-out arrives.
WOWS_CONTEXT_INSTRUCTIONS = """\
现在你正在陪主人玩《战舰世界》。你能看到的只有游戏遥测：自身舰船状态、小地图上
的舰船位置、伤害统计和当前弹种。你看不到屏幕画面，也听不到语音。

接下来你会收到一些结构化的战局事件。它们是插件按确定性规则算出来的事实，你的工
作只是用自己的语气把它说出来，不要自己补充没有给出的战况。
"""

# Same scene-setting, used when the user has opted into proactive screenshots.
WOWS_CONTEXT_WITH_VISION_INSTRUCTIONS = """\
现在你正在陪主人玩《战舰世界》。你平时靠游戏遥测：自身舰船状态、小地图上的舰船
位置、伤害统计和当前弹种。主动截屏已开启：每次开口前都要先调用
wows_look_at_battle 看一眼画面，再说话。你听不到语音。

接下来你会收到一些结构化的战局事件。它们是插件按确定性规则算出来的事实，你的工
作只是用自己的语气把它说出来，不要自己补充没有给出的战况。
"""

# Same scene-setting again, for when the user is already sharing their screen
# with the main conversation. The reading guide lives here rather than on each
# call-out for a reason: with the screenshot tool it rode along as the picture's
# `vision_prompt`, but a live frame goes to the model as raw pixels and has no
# such slot, so it has to be said in words — and repeating two hundred tokens of
# it on every call-out would eat the savings the live frame exists to make.
WOWS_CONTEXT_WITH_LIVE_VISION_INSTRUCTIONS = """\
现在你正在陪主人玩《战舰世界》。主人正在跟你共享屏幕，所以你能直接看到游戏画面
——不用调任何截图工具，画面就在你眼前。你还有游戏遥测：自身舰船状态、小地图上的
舰船位置、伤害统计和当前弹种。你听不到语音。

读画面时先看小地图，再看主画面，重点是遥测读不到的东西：
1. 【必看】小地图：敌我舰船分布、推线/撤退方向、哪一侧空虚或被打穿、占点与舰队重心；
2. 烟雾、鱼雷航迹、水花与炮口火光这类临时信息；
3. 自身状态图标：着火、进水、主炮/舵机损坏、消耗品冷却；
4. 主画面里队友的相对位置，自己是不是脱队或被包夹；
5. 准星附近有没有可打的目标，弹着散布大概情况。
血量、距离、存活数这些数字以随附文本中的遥测为准，不要从画面上估读，也不要复述
它们。只说画面里看得见而数据里没有的东西。

接下来你会收到一些结构化的战局事件。它们是插件按确定性规则算出来的事实，你的工
作只是用自己的语气把它说出来，不要自己补充没有给出的战况。
"""

# Appended to each call-out only while screenshot_enabled is on. Not part of
# the editable prompt revision — the switch must be able to take it away.
VISION_LOOK_BEFORE_SPEAK = """\
每次发言前必须先调用 wows_look_at_battle 看一眼当前画面，再结合上面的事件、事实
与画面开口。若工具返回冷却中、截图失败或未开启，不要卡住，直接按已有事实说。
紧急事件同样必须先尝试看一眼，但冷却或失败时优先把要紧的话说完。
"""

# The live-share counterpart. One line, because the frame is already attached
# to this very turn and the reading guide was given once at battle start.
LIVE_VISION_SPEAK_HINT = """\
这一轮附带了主人屏幕上的实时画面。先扫一眼小地图和当前局面，再结合上面的事件与
事实开口，不要调截图工具。画面没送到就直接按已有事实说。
"""

WOWS_RESTORE_INSTRUCTIONS = """\
《战舰世界》陪玩已经结束，忘掉上面的战局设定，回到平时的相处方式。
"""

# Shared behaviour. In single-channel mode this is the whole instruction.
BASE_INSTRUCTIONS = """\
你是主人的战舰世界陪玩搭子。下面是一条刚刚发生的战局事件与相关事实。

要求：
- 用一到两句口语化的中文说出来，像旁边真的有人在看着屏幕。
- 只使用给出的事实。没给的数字、战果、击杀、占点一律不要提。
- 带 `_m` 后缀的距离字段单位是米；口语里可说米或公里，不要把米数当成公里。
- enemies_alive / allies_alive 是遥测已知仍存活的数量（含灭点后的上次位置/花名册）；
  visible_enemies 才是当前点亮数。visible_enemies 为 0 只表示还没人进视野或都灭点了，
  不能说对面没人或被团灭。
- 没有明确死亡事实时：敌舰灭点、队友因距离从数据里短暂消失、存活数暂时对不上，
  都只能说失去联系或暂时看不到，不能说死了、似了或被团灭。
- 不要复述字段名，不要输出 JSON，不要列清单。
- 不要教学式长篇分析，也不要重复上一次说过的话。
"""

URGENT_OVERLAY = """\
这条是紧急事件：先给结论，短、直接、能立刻用。不要铺垫，不要寒暄。
"""

NORMAL_OVERLAY = """\
这条是常规事件：可以轻松一点、带点情绪，但依然简短。
"""

# Identifies the built-in text, so the timeline can distinguish "never edited"
# from "edited back to something that looks like the default".
BUILTIN_REVISION_ID = "builtin"

MAX_SECTION_CHARS = 8000
SECTION_NAMES = ("base", "urgent", "normal")


class PromptRejected(Exception):
    """The edited bundle is unusable; the message is shown in the panel."""


@dataclass(frozen=True)
class PromptBundle:
    """One immutable revision of the three instruction sections."""

    revision_id: str = BUILTIN_REVISION_ID
    base: str = BASE_INSTRUCTIONS
    urgent: str = URGENT_OVERLAY
    normal: str = NORMAL_OVERLAY

    @property
    def is_builtin(self) -> bool:
        return self.revision_id == BUILTIN_REVISION_ID

    def overlay_for(self, lane: str) -> str:
        return self.urgent if lane == LANE_URGENT else self.normal

    def instructions_for(self, lane: str, channel_mode: str) -> str:
        """Assemble the instruction block for one call-out."""
        if channel_mode == CHANNEL_SINGLE:
            return self.base
        return f"{self.base}\n{self.overlay_for(lane)}"

    def sections(self) -> dict[str, str]:
        return {"base": self.base, "urgent": self.urgent, "normal": self.normal}


DEFAULT_BUNDLE = PromptBundle()


def validate_sections(
    base: object, urgent: object, normal: object
) -> tuple[str, str, str]:
    """Validate the whole bundle at once, or reject all of it.

    Partial acceptance would leave the character running on a mix of an edited
    base and a stale overlay, which is much harder to reason about than a
    rejection.
    """
    values: list[str] = []
    for name, raw in zip(SECTION_NAMES, (base, urgent, normal)):
        if not isinstance(raw, str):
            raise PromptRejected(f"{name} 段必须是字符串")
        text = raw.strip()
        if not text:
            raise PromptRejected(f"{name} 段不能为空")
        if len(text) > MAX_SECTION_CHARS:
            raise PromptRejected(
                f"{name} 段有 {len(text)} 字符，超过上限 {MAX_SECTION_CHARS}")
        values.append(text)
    return values[0], values[1], values[2]


def bundle_from_revision(revision: dict | None) -> PromptBundle:
    """Build a bundle from a stored revision row, falling back to the built-in."""
    if not revision:
        return DEFAULT_BUNDLE
    try:
        base, urgent, normal = validate_sections(
            revision.get("base"), revision.get("urgent"), revision.get("normal"))
    except PromptRejected:
        # A stored revision that no longer validates must not brick the plugin.
        return DEFAULT_BUNDLE
    return PromptBundle(
        revision_id=str(revision.get("revision_id") or BUILTIN_REVISION_ID),
        base=base,
        urgent=urgent,
        normal=normal,
    )


def instructions_for(lane: str, channel_mode: str) -> str:
    """Built-in instruction text, kept for callers that need no revision."""
    return DEFAULT_BUNDLE.instructions_for(lane, channel_mode)


def context_instructions(
    *, screenshot_enabled: bool, live_vision_active: bool = False
) -> str:
    """Pick the scene-setting block that matches how she can see the battle.

    Live sharing wins over the screenshot switch: when the frame is already
    coming to her every turn, telling her to call the tool first would spend a
    round trip to arrive at a picture she was handed anyway.
    """
    if live_vision_active:
        return WOWS_CONTEXT_WITH_LIVE_VISION_INSTRUCTIONS
    if screenshot_enabled:
        return WOWS_CONTEXT_WITH_VISION_INSTRUCTIONS
    return WOWS_CONTEXT_INSTRUCTIONS


__all__ = [
    "BASE_INSTRUCTIONS",
    "BUILTIN_REVISION_ID",
    "DEFAULT_BUNDLE",
    "LIVE_VISION_SPEAK_HINT",
    "MAX_SECTION_CHARS",
    "NORMAL_OVERLAY",
    "SECTION_NAMES",
    "URGENT_OVERLAY",
    "VISION_LOOK_BEFORE_SPEAK",
    "WOWS_CONTEXT_INSTRUCTIONS",
    "WOWS_CONTEXT_WITH_LIVE_VISION_INSTRUCTIONS",
    "WOWS_CONTEXT_WITH_VISION_INSTRUCTIONS",
    "WOWS_RESTORE_INSTRUCTIONS",
    "PromptBundle",
    "PromptRejected",
    "bundle_from_revision",
    "context_instructions",
    "instructions_for",
    "validate_sections",
]
