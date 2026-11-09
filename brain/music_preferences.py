"""Use character-scoped memory as additional context for requested playlist curation."""

import logging
import re
from urllib.parse import quote

import httpx

from config.network import MEMORY_SERVER_PORT
from utils.internal_http_client import get_internal_http_client
from utils.preferences import aload_global_conversation_settings

logger = logging.getLogger(__name__)
_CURATE = re.compile(
    r"放点歌|放些歌|放音乐|排.{0,8}歌|编.{0,8}歌单|按.{0,12}(喜好|口味|记忆)|来点音乐|"
    r"play some music|make (?:me )?a playlist",
    re.IGNORECASE,
)
_MUSIC = re.compile(
    r"音乐|歌曲|曲风|歌手|听歌|摇滚|流行|爵士|说唱|民谣|后摇|古典|电子乐|乐队|梦泡|"
    r"music|pop|rock|jazz|rap|folk|ambient|shoegaze",
    re.IGNORECASE,
)


async def playlist_context(conversation, request, character):
    if not character or not _CURATE.search(request):
        return conversation

    rows = []
    try:
        settings = await aload_global_conversation_settings()
        if not settings.get("autoMemoryRecallEnabled", True):
            return conversation
        client = get_internal_http_client()
        response = await client.post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/query_memory/{quote(character, safe='')}",
            json={
                "query": "用户喜欢或不喜欢听什么音乐、歌曲、曲风和歌手",
                "budget_ms": 250,
            },
            timeout=1.5,
        )
        response.raise_for_status()
        rows = [row for row in response.json()["results"]
                if isinstance(row, dict) and isinstance(row.get("text"), str) and _MUSIC.search(row["text"])]
    except (OSError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.debug("Music preference recall unavailable: %s", type(exc).__name__)

    if not rows:
        return conversation
    from memory.recall_render import render_recall_block
    rendered = render_recall_block(rows[:8], "zh")
    return "音乐记忆（历史偏好，非当前指令）：\n" + rendered.text + "\n" + conversation
