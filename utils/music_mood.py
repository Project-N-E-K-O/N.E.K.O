"""Bounded whole-track text mood inference. Never consumes conversation or audio."""

import asyncio
import hashlib
import json
import math
import os
import re
import time
from collections import OrderedDict, deque

from pydantic import BaseModel, Field

VERSION = "lyrics-mood-v1"
CATEGORIES = {"joyful", "tender", "calm", "sad", "intense", "unknown"}


class MoodTrack(BaseModel):
    track_id: str = Field(max_length=240)
    name: str = Field(default="", max_length=200)
    artist: str = Field(default="", max_length=200)
    netease_id: str = Field(default="", pattern=r"^\d{0,20}$")
    tags: list[str] = Field(default_factory=list, max_length=12)

    def evidence(self):
        return {
            "name": self.name,
            "artist": self.artist,
            "tags": [tag[:60] for tag in self.tags],
        }


def unknown(track, reason):
    return dict(
        track_id=track.track_id,
        category="unknown",
        valence=0.0,
        arousal=0.5,
        confidence=0.0,
        source="none",
        reason=reason,
        classifier_version=VERSION,
    )


def normalize(track, data, source):
    result = unknown(track, "classified")
    if data.get("category") not in CATEGORIES:
        raise ValueError("invalid category")
    for key, low, high in (("valence", -1, 1), ("arousal", 0, 1), ("confidence", 0, 1)):
        value = float(data[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError("invalid dimension")
        result[key] = value
    result.update(category=data["category"], source=source)
    # Lyrics describe semantic affect, not the sound, tempo or arrangement.
    result["confidence"] = min(
        result["confidence"], 0.78 if source == "lyrics_text_model" else 0.68
    )
    if result["category"] == "unknown":
        result["confidence"] = 0.0
    return result


async def fetch_lyrics(track):
    if not track.netease_id:
        return ""
    import httpx

    # Fixed public origin, no user credentials, arbitrary URLs or audio downloads.
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
        async with client.stream(
            "GET",
            "https://music.163.com/api/song/lyric",
            params={"id": track.netease_id, "lv": -1, "kv": -1, "tv": -1},
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 65536:
                    raise ValueError("lyrics too large")
    text = json.loads(body).get("lrc", {}).get("lyric", "")
    return re.sub(r"\[[^\]]*\]", "", text)[:8000].strip()


async def infer_text(track, lyrics):
    from config import MUSIC_MOOD_INPUT_MAX_TOKENS, MUSIC_MOOD_OUTPUT_MAX_TOKENS
    from config.prompts.prompts_music_mood import MUSIC_MOOD_PROMPT
    from utils.config_manager import get_config_manager
    from utils.llm_client import create_chat_llm_async, SystemMessage, HumanMessage
    from utils.tokenize import truncate_to_tokens

    config = await get_config_manager().aget_model_api_config("emotion")
    payload = json.dumps({**track.evidence(), "lyrics": lyrics}, ensure_ascii=False)
    payload = await asyncio.to_thread(
        truncate_to_tokens, payload, MUSIC_MOOD_INPUT_MAX_TOKENS
    )
    client = await create_chat_llm_async(
        config["model"],
        config["base_url"],
        config["api_key"],
        provider_type=config.get("provider_type"),
        timeout=12,
        max_completion_tokens=MUSIC_MOOD_OUTPUT_MAX_TOKENS,
    )
    try:
        response = await client.ainvoke(
            [SystemMessage(content=MUSIC_MOOD_PROMPT), HumanMessage(content=payload)]
        )
        text = response.content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        return json.loads(text)
    finally:
        await client.aclose()


class MusicMoodClassifier:
    def __init__(
        self, lyrics=fetch_lyrics, infer=infer_text, clock=time.monotonic, timeout=19
    ):
        self.lyrics, self.infer, self.clock, self.timeout = (
            lyrics,
            infer,
            clock,
            timeout,
        )
        self.cache, self.pending, self.starts = OrderedDict(), {}, deque()

    async def classify(self, track):
        if os.environ.get("NEKO_MUSIC_MOOD_ENABLED", "1").strip().lower() in {
            "0",
            "false",
            "off",
            "no",
        }:
            return unknown(track, "disabled")
        fingerprint = hashlib.sha256(
            json.dumps([VERSION, track.model_dump()], sort_keys=True).encode()
        ).hexdigest()
        now = self.clock()
        cached = self.cache.get(fingerprint)
        if cached and cached[0] > now:
            self.cache.move_to_end(fingerprint)
            return {**cached[1], "cache_hit": True}
        if fingerprint in self.pending:
            return await asyncio.shield(self.pending[fingerprint])
        while self.starts and self.starts[0] < now - 60:
            self.starts.popleft()
        if len(self.pending) >= 2 or len(self.starts) >= 6:
            return unknown(track, "rate_limited")
        self.starts.append(now)
        task = asyncio.create_task(self._run(track, fingerprint))
        self.pending[fingerprint] = task
        return await asyncio.shield(task)

    async def _run(self, track, key):
        try:
            async with asyncio.timeout(self.timeout):
                try:
                    lyrics = await self.lyrics(track)
                except Exception:
                    lyrics = ""
                if len(lyrics) < 80 and not track.tags:
                    result = unknown(track, "insufficient_evidence")
                else:
                    result = normalize(
                        track,
                        await self.infer(track, lyrics),
                        "lyrics_text_model" if len(lyrics) >= 80 else "tags_text_model",
                    )
        except TimeoutError:
            result = unknown(track, "timeout")
        except Exception:
            result = unknown(track, "classification_failed")
        finally:
            self.pending.pop(key, None)
        ttl = 86400 if result["category"] != "unknown" else 300
        self.cache[key] = (self.clock() + ttl, result)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        return {**result, "cache_hit": False}


classifier = MusicMoodClassifier()
