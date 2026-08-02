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

WOWS_RESTORE_INSTRUCTIONS = """\
《战舰世界》陪玩已经结束，忘掉上面的战局设定，回到平时的相处方式。
"""

# Shared behaviour. In single-channel mode this is the whole instruction.
BASE_INSTRUCTIONS = """\
你是主人的战舰世界陪玩搭子。下面是一条刚刚发生的战局事件与相关事实。

要求：
- 用一到两句口语化的中文说出来，像旁边真的有人在看着屏幕。
- 只使用给出的事实。没给的数字、战果、击杀、占点一律不要提。
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


__all__ = [
    "BASE_INSTRUCTIONS",
    "BUILTIN_REVISION_ID",
    "DEFAULT_BUNDLE",
    "MAX_SECTION_CHARS",
    "NORMAL_OVERLAY",
    "SECTION_NAMES",
    "URGENT_OVERLAY",
    "WOWS_CONTEXT_INSTRUCTIONS",
    "WOWS_RESTORE_INSTRUCTIONS",
    "PromptBundle",
    "PromptRejected",
    "bundle_from_revision",
    "instructions_for",
    "validate_sections",
]
