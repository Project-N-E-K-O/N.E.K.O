"""Explicit song offers in a delivered proactive turn, without an LLM gate.

This narrowly scoped route uses the normal plugin executor. Merely mentioning
a title, discussing music, or quoting a previous user request is not an offer.
"""

import re

_TITLE = re.compile(r'《([^《》\n]{1,120})》|“([^“”\n]{1,120})”|"([^"\n]{1,120})"')
_OFFER = re.compile(
    r"(?:推荐(?:你|给你)?(?:听听|听)?|(?:给你|陪你|一起|来|去|不如|可以|试着)"
    r"(?:放一首|放首|播放|听听|听一下|听)|放一首|放首|播放|听听)"
    r"[^。！？!?；;\n]{0,40}$|(?:recommend|let's listen to|play)\s+.{0,40}$",
    re.I,
)
_NOT_NOW = re.compile(
    r"别|不要|不用|不想|不放|不播|不听|不推荐|不建议|没打算|只.{0,5}推荐|只是|仅仅|"
    r"明天|下次|以后|回头|改天|等会|待会|稍后|听完|播完|要不要|想不想|"
    r"昨天|之前|刚才|上次|曾经|你说|你提|你让|你叫|你问|你要求|如果|假如|"
    r"don't|do not|later|tomorrow|yesterday|you said|would you|shall I",
    re.I,
)


def proactive_music_query(text: str) -> str | None:
    """Return the one concrete song offered now; ambiguous offers stay chat."""
    offers = []
    for match in _TITLE.finditer(text or ""):
        start = (
            max(
                (text.rfind(mark, 0, match.start()) for mark in "。！？!?；;\n"),
                default=-1,
            )
            + 1
        )
        end = re.search(r"[。！？!?；;\n]", text[match.end() :])
        clause = text[start : match.end() + end.end()] if end else text[start:]
        prefix = text[start : match.start()]
        if _NOT_NOW.search(clause) or clause.rstrip().endswith(("?", "？")):
            continue
        if not _OFFER.search(prefix):
            continue
        # A recommendation of a book/film also uses title brackets. Require
        # listening/music wording, not merely the word 'recommend'.
        if not re.search(
            r"听|播放|放首|放一首|首|歌曲|音乐|歌|曲|listen|song|track|music|play",
            clause,
            re.I,
        ):
            continue
        if re.search(r"电影|小说|这本书|有声书|audiobook|movie|novel", clause, re.I):
            continue
        title = next(group for group in match.groups() if group is not None).strip()
        if title:
            offers.append(title)
    unique = list(dict.fromkeys(offers))
    return unique[0] if len(unique) == 1 else None


def latest_proactive_music_query(messages) -> str | None:
    for message in reversed(messages):
        if str(message.get("role", "")).lower() == "assistant":
            return proactive_music_query(
                str(message.get("text") or message.get("content") or "")
            )
    return None


async def proactive_music_block_reason(lanlan_name: str | None) -> str | None:
    """Recheck the live player before planning AND immediately before dispatch."""
    if not lanlan_name:
        return "missing_character"
    from config.network import MAIN_SERVER_PORT
    from utils.internal_http_client import get_internal_http_client

    try:
        response = await get_internal_http_client().get(
            f"http://127.0.0.1:{MAIN_SERVER_PORT}/api/music/current/netease",
            params={"lanlan_name": lanlan_name},
            timeout=3,
        )
        response.raise_for_status()
        status = response.json()
        if status.get("proactive_allowed") is not True:
            return status.get("proactive_block_reason") or "player_unavailable"
    except Exception:
        return "player_status_unavailable"
    return None
