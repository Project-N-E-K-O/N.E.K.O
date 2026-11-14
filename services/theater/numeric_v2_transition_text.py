"""Recognize author instructions that cannot serve as a player confirmation."""

import re


_ACCEPT_INSTRUCTION = re.compile(
    r"(?:^|[，,；;]\s*或(?:者)?\s*)(?:[（(]\s*)?(?:请(?:玩家|你)?\s*)?"
    r"(?:明确\s*(?:选择|同意|接受|确认|表态)|同意(?:现在)?去准备对应去向所需)"
)
_PRESSURE = re.compile(r"逼迫|迫使|迫不得已|不得不|必须立刻|只能立即")
_CHOICE = re.compile(r"[？?]|愿不愿|愿意|要不要|请你|我们一起|可以选择|还是|你来决定")
_LEADING_ACTIONS = re.compile(r"^\s*(?:(?:（[^）]*）|\([^)]*\))\s*)+")
_DEFERRAL = re.compile(r"^(?:(?:现在)?先别|暂缓|稍等|等一下|再等等|现在不去|现在不开始)")


def is_placeholder_accept_input(text: str) -> bool:
    """Recognize explicit author instructions, without interpreting dialogue or free input."""

    text = text.strip().strip("“”‘’\"'")
    return bool(_ACCEPT_INSTRUCTION.search(text))


def is_pressure_only_offer(text: str) -> bool:
    """Recognize explicit compulsion without a player-facing invitation or choice."""
    return bool(_PRESSURE.search(text) and not _CHOICE.search(text))


def is_explicit_transition_deferral(text: str) -> bool:
    """Recognize a direct request to pause after optional player actions."""
    return bool(_DEFERRAL.match(_LEADING_ACTIONS.sub("", text).strip()))
