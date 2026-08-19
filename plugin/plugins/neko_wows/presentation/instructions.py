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

# How to read the fact fields. This is injected with the scene, once per battle,
# rather than repeated on every call-out.
#
# It used to live in the per-call-out instructions, where it was several times
# longer than the event it accompanied. Given "主事件：开局" under a glossary
# explaining what a numerical disadvantage looks like, the model answered with
# the glossary -- "确认存活的友军比敌方少了些" -- for a battle that had just
# begun and for which no counts had been supplied at all.
WOWS_TELEMETRY_READING_RULES = """\
随附事实怎么读（整局有效，之后每条播报不再重复）：
- 带 `_m` 后缀的距离字段单位是米；口语里可说米或公里，不要把米数当成公里。
- allies_not_confirmed_sunk / enemies_not_confirmed_sunk 是花名册与最后已知记录构成
  的“未确认沉没”上限，不是确认存活数，也不是当前点亮数。
- confirmed_visible_allies / confirmed_visible_enemies 才是当前点亮且明确存活的数量。
  visible_enemies 为 0 只表示还没人进视野或都灭点了，不能说对面没人、团灭或全灭。
- 这些数字只有在某条播报把它们当成主事件时才值得说出口。其余时候它们只是背景：不要
  主动报数，也不要自己数船来覆盖它们。
- ship_name / target_name / own_ship 已经是口语可用的称呼；不要念内部舰船 index。
- 消耗品实时状态（雷达、水听、烟幕、损伤控制等是否开启或持续）遥测里没有，不要从数据里编。
  只有自己界面上看得见的冷却可以提，且绝不要据此声称他人开了雷达、水听或其他消耗品。
  小地图上敌舰图标亮起只表示被点亮/被发现，绝不等于对方开了雷达。
- bearing_deg 是罗盘方位：正北=0，顺时针到 360，与船头朝向无关。不要用它换算成
  正前方、右前方、正右、右后方、正后方、左后方、正左、左前方。
- 口语方向只用 relative_sector（正前方、右前方、正右、右后方、正后方、左后方、正左、
  左前方）。有这个字段就照说；没有给出相对方位字段时，不要说正前方、右前方、正右、
  右后方、正后方、左后方、正左、左前方。
- relative_bearing_deg：0=正前，正数偏右，负数偏左（-180~180）。
- 没有明确死亡事实时：敌舰灭点、队友因距离从数据里短暂消失、存活数暂时对不上，都只
  能说失去联系或暂时看不到，不能说死了、似了或被团灭。
"""

# Injected once when the telemetry link comes up, so the character knows what
# situation she is in before any call-out arrives.
_SCENE_TELEMETRY_ONLY = """\
现在你正在陪主人玩《战舰世界》。你能看到的只有游戏遥测：自身舰船状态、小地图上
的舰船位置、伤害统计和当前弹种。你看不到屏幕画面，也听不到语音。

消耗品实时状态当前不可用：不要说任何人开了或正在开雷达、水听、烟幕、损伤控制等。
舰船参考里的消耗品只是离线顶配，不是战场实况。

接下来你会收到一些结构化的战局事件。它们是插件按确定性规则算出来的事实，你的工
作只是用自己的语气把它说出来。主事件是要说的事；不要自己补充没有给出的舰船、
方位、距离或点亮关系，也不要把舰船参考里的数据讲成当前视野。
"""

# Same scene-setting, used when the user has opted into proactive screenshots.
_SCENE_WITH_VISION = """\
现在你正在陪主人玩《战舰世界》。你平时靠游戏遥测：自身舰船状态、小地图上的舰船
位置、伤害统计和当前弹种。主动截屏已开启：每次开口前都要先调用
wows_look_at_battle 看一眼画面，再按主事件说话。你听不到语音。

消耗品实时状态当前不可用：不要说任何人开了或正在开雷达、水听、烟幕、损伤控制等。
小地图上敌舰图标亮起只表示被点亮/被发现，绝不等于对方开了雷达。
舰船参考里的消耗品只是离线顶配，不是战场实况。

接下来你会收到一些结构化的战局事件。它们是插件按确定性规则算出来的事实，你的工
作只是用自己的语气把它说出来。主事件是要说的事；不要自己补充没有给出的舰船、
方位、距离或点亮关系，也不要把舰船参考里的数据讲成当前视野。
"""

# Same scene-setting again, for when the user is already sharing their screen
# with the main conversation. The reading guide lives here rather than on each
# call-out for a reason: with the screenshot tool it rode along as the picture's
# `vision_prompt`, but a live frame goes to the model as raw pixels and has no
# such slot, so it has to be said in words — and repeating two hundred tokens of
# it on every call-out would eat the savings the live frame exists to make.
_SCENE_WITH_LIVE_VISION = """\
现在你正在陪主人玩《战舰世界》。主人正在跟你共享屏幕，所以你能直接看到游戏画面
——不用调任何截图工具，画面就在你眼前。你还有游戏遥测：自身舰船状态、小地图上的
舰船位置、伤害统计和当前弹种。你听不到语音。

每次开口先说这条主事件。画面只用来确认主事件、以及补遥测读不到的东西：
烟雾、鱼雷航迹、水花与炮口火光、自身状态图标（着火、进水、主炮/舵机损坏）。
自己界面上看得见的消耗品冷却可以提，但绝不要据此声称敌方开了雷达、水听或其他消耗品。
不要把小地图解说当成发言内容：不要数船、不要编方位、不要把点亮数或推线讲成这条事件。
血量、距离与当前点亮数以随附文本中的遥测为准；未确认沉没数量只是花名册与最后已
知记录的上限，不代表确认存活。不要从画面上估读或复述这些数字，只说画面里看得见
而数据里没有的东西。
小地图上敌舰图标亮起只表示被点亮/被发现，绝不等于对方开了雷达。
他人（含友军与敌方）的消耗品实时状态（雷达、水听、烟幕等是否开启）当前不可用，不要提。

接下来你会收到一些结构化的战局事件。它们是插件按确定性规则算出来的事实，你的工
作只是用自己的语气把它说出来，不要自己补充没有给出的战况。
"""

# What actually goes to the host: the scene, then the standing reading rules.
WOWS_CONTEXT_INSTRUCTIONS = f"{_SCENE_TELEMETRY_ONLY}\n{WOWS_TELEMETRY_READING_RULES}"
WOWS_CONTEXT_WITH_VISION_INSTRUCTIONS = (
    f"{_SCENE_WITH_VISION}\n{WOWS_TELEMETRY_READING_RULES}")
WOWS_CONTEXT_WITH_LIVE_VISION_INSTRUCTIONS = (
    f"{_SCENE_WITH_LIVE_VISION}\n{WOWS_TELEMETRY_READING_RULES}")

# Appended to each call-out only while screenshot_enabled is on. Not part of
# the editable prompt revision — the switch must be able to take it away.
VISION_LOOK_BEFORE_SPEAK = """\
每次发言前必须先调用 wows_look_at_battle 看一眼当前画面，再按主事件开口。
画面只用来确认这件事、以及补事实里没有的东西。不要把小地图解说当成这条要说的话。
若工具返回冷却中、截图失败或未开启，不要卡住，直接按已有事实说。
紧急事件同样必须先尝试看一眼，但冷却或失败时优先把要紧的话说完。
"""

# The live-share counterpart. One line, because the frame is already attached
# to this very turn and the reading guide was given once at battle start.
LIVE_VISION_SPEAK_HINT = """\
这一轮附带了主人屏幕上的实时画面。先按主事件说；画面只用来确认这件事、以及补
事实里没有的东西（烟、鱼雷航迹、着火图标）。不要把小地图解说或当前战况里的点亮
数、距离当成这条要说的话。画面里没有的舰船、方位、点亮关系不要编。不要调截图工具。
画面没送到就直接按已有事实说。
"""

WOWS_RESTORE_INSTRUCTIONS = """\
《战舰世界》陪玩已经结束，忘掉上面的战局设定，回到平时的相处方式。
"""

# Shared behaviour. In single-channel mode this is the whole instruction.
#
# Deliberately short: it follows the event in the message, and everything that
# is true for the whole battle rather than for this one call-out belongs in
# `WOWS_TELEMETRY_READING_RULES` instead.
BASE_INSTRUCTIONS = """\
上面是刚刚发生的一条战局事件。你是主人的战舰世界陪玩搭子，用你自己的语气把它说出来。

要求：
- 只说主事件那件事，一到两句口语化的中文，像旁边真的有人在看着屏幕。
- 只使用上面给出的事实。没给的数字、战果、击杀、占点一律不要提，也不要编造上面没有
  写的舰船、距离、方位或点亮关系。
- 当前战况只是背景。除非主事件或附加事件本身就在说点亮数、最近敌舰或方位，否则不要
  把背景讲成这条要说的话。
- 不要复述字段名，不要输出 JSON，不要列清单，不要教学式长篇分析。
- 不要原样复述上一次说过的话；上一条如果对不上这条主事件，必须重说。
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
    "WOWS_TELEMETRY_READING_RULES",
    "WOWS_RESTORE_INSTRUCTIONS",
    "PromptBundle",
    "PromptRejected",
    "bundle_from_revision",
    "context_instructions",
    "instructions_for",
    "validate_sections",
]
