"""Live lines spoken inside the watch-together scene while it owns speech.

The scene route holds the respond cues plugins address to the character. The
scene asks for one short line when its reaction timeline has a gap, and for an
intermission (a one-line summary plus replies) after a video in automatic mode.
Generated speech is kept in a small in-memory store that the scene fetches.
"""
from __future__ import annotations

import asyncio
import io
import itertools
import json
import math
import re
import secrets
import tempfile
import time
import wave
from collections import OrderedDict
from pathlib import Path

from main_logic.proactive_delivery import (
    CALLBACK_EXPIRES_AT_KEY,
    DELIVERY_RETRACTED_KEY,
    callback_is_expired,
    effective_priority,
    resolve_callback_delivery_ack,
    trim_images_to_turn_budget,
)

INBOX_LIMIT = 12
INBOX_MAX_AGE_SECONDS = 300.0
# Matches ProactiveDeliveryManager's default TTL: cues this recent go back to
# ordinary delivery when the scene closes; older ones would be dropped there anyway.
HANDOFF_MAX_AGE_SECONDS = 90.0
INTERJECT_TAKE = 3
INTERMISSION_TAKE = 8
MAX_REPLIES = 3
MAX_IMAGES = 2
LINE_TOKEN_LIMIT = 120
CONTEXT_TOKEN_BUDGET = 1500
DESCRIPTION_TOKEN_BUDGET = 400
PERSONA_TOKEN_BUDGET = 3000
TITLE_TOKEN_BUDGET = 200
AUDIO_ROUTE = "/api/watch-together/live-audio"
AUDIO_STORE_LIMIT = 16
AUDIO_STORE_MAX_BYTES = 48 * 1024 * 1024
AUDIO_TTL_SECONDS = 600.0
AUDIO_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class LiveInbox:
    """Bounded per-route holder for respond cues, ordered like proactive delivery."""

    def __init__(self, *, limit=INBOX_LIMIT, max_age=INBOX_MAX_AGE_SECONDS, clock=time.monotonic):
        self._limit = limit
        self._max_age = max_age
        self._clock = clock
        self._items: list[tuple[int, int, float, dict]] = []
        self._seq = itertools.count()
        self._closed = False

    def accept(self, callback) -> bool:
        # Plugin cues only. Topic hooks, computer-use/browser results and system
        # cues are ordinary proactive speech and stay behind the takeover gate.
        if self._closed or not isinstance(callback, dict) or callback.get("source_kind") != "plugin":
            return False
        # Expired cues must not occupy capacity that would shed a live one.
        self._prune()
        key = str(callback.get("coalesce_key") or "").strip()
        if key:
            self._discard([item for item in self._items
                           if str(item[3].get("coalesce_key") or "").strip() == key])
        self._items.append((effective_priority(callback.get("priority")), next(self._seq), self._clock(), callback))
        while len(self._items) > self._limit:
            # Shed the cue that would be delivered last, as the delivery manager does.
            self._discard([max(self._items, key=lambda item: (-item[0], item[1]))])
        return True

    @property
    def pending(self) -> int:
        self._prune()
        return len(self._items)

    def take(self, limit: int) -> list[dict]:
        self._prune()
        chosen = sorted(self._items, key=lambda item: (-item[0], item[1]))[:max(0, limit)]
        taken = {id(item) for item in chosen}
        self._items = [item for item in self._items if id(item) not in taken]
        return [item[3] for item in chosen]

    def close(self, *, handoff_age=HANDOFF_MAX_AGE_SECONDS) -> list[dict]:
        """Stop accepting; return recent cues for ordinary delivery and release the rest."""
        self._closed = True
        self._prune()
        now = self._clock()
        handoff = [item for item in self._items if now - item[2] <= handoff_age]
        kept = {id(item) for item in handoff}
        self._discard([item for item in self._items if id(item) not in kept])
        self._items = []
        for _priority, _seq, received, callback in handoff:
            # Carry the remaining window: resubmission must not restart the 90-second clock.
            deadline = time.monotonic() + max(0.0, handoff_age - (now - received))
            existing = callback.get(CALLBACK_EXPIRES_AT_KEY)
            if isinstance(existing, (int, float)) and not isinstance(existing, bool) and math.isfinite(existing):
                deadline = min(deadline, float(existing))
            callback[CALLBACK_EXPIRES_AT_KEY] = deadline
        return [item[3] for item in handoff]

    def _prune(self) -> None:
        now = self._clock()
        self._discard([item for item in self._items
                       if item[3].get(DELIVERY_RETRACTED_KEY) or callback_is_expired(item[3])
                       or now - item[2] > self._max_age])

    def _discard(self, items) -> None:
        if not items:
            return
        doomed = {id(item) for item in items}
        self._items = [item for item in self._items if id(item) not in doomed]
        for item in items:
            resolve_callback_delivery_ack(item[3], False)


def settle(callbacks, delivered: bool) -> None:
    for callback in callbacks:
        resolve_callback_delivery_ack(callback, delivered)


_audio_store: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()


def _prune_audio(now: float) -> None:
    for token in [token for token, (created, _data) in _audio_store.items() if now - created > AUDIO_TTL_SECONDS]:
        _audio_store.pop(token, None)
    while len(_audio_store) > AUDIO_STORE_LIMIT or sum(len(data) for _created, data in _audio_store.values()) > AUDIO_STORE_MAX_BYTES:
        _audio_store.popitem(last=False)


def store_audio(data: bytes, *, clock=time.monotonic) -> str:
    token = secrets.token_urlsafe(24)
    _audio_store[token] = (clock(), bytes(data))
    _prune_audio(clock())
    return token


def read_audio(token: str, *, clock=time.monotonic) -> bytes | None:
    if not isinstance(token, str) or not AUDIO_TOKEN_PATTERN.fullmatch(token):
        return None
    _prune_audio(clock())
    entry = _audio_store.get(token)
    return entry[1] if entry else None


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as stream:
        rate = stream.getframerate()
        return stream.getnframes() / rate if rate else 0.0


def video_context(library, job: str, version: str) -> dict:
    """Read the bounded prompt context of one prepared video (blocking)."""
    timeline = library.timeline(job, version)
    events = []
    for item in timeline.get("events") or []:
        if not isinstance(item, dict) or isinstance(item.get("at"), bool):
            continue
        try:
            at = float(item.get("at"))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(at):
            events.append({"at": at, "text": str(item.get("text") or "")[:160]})
    try:
        duration = float(timeline.get("duration") or 0)
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    description = ""
    try:
        path = library.resource(job, version, "evidence.json")
        if path.stat().st_size <= 4 * 1024 * 1024:
            evidence = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(evidence, dict):
                description = str(evidence.get("description") or "")[:4000]
    except (KeyError, OSError, ValueError):
        # Imported legacy jobs have no evidence.json; the description is optional context.
        description = ""
    return {"title": str(timeline.get("title") or "")[:500],
            "duration": duration if math.isfinite(duration) else 0.0,
            "events": events, "description": description}


def _fill(template: str, **values) -> str:
    # One pass over the template, so inserted untrusted text is never rescanned.
    pattern = re.compile(r"\{(" + "|".join(map(re.escape, values)) + r")\}")
    return pattern.sub(lambda match: str(values[match.group(1)]), template)


def _validator(mode: str):
    def validate(value):
        if mode == "interject":
            valid = isinstance(value, dict) and isinstance(value.get("line"), str)
            return value, [] if valid else [{"field": "line", "reason": "expected_string"}]
        replies = value.get("replies", []) if isinstance(value, dict) else None
        valid = (isinstance(value, dict) and isinstance(value.get("summary"), str)
                 and isinstance(replies, list) and all(isinstance(reply, str) for reply in replies))
        return value, [] if valid else [{"field": "summary", "reason": "expected_summary_and_replies"}]
    return validate


async def compose(manager, *, mode: str, callbacks, video: dict, language: str,
                  position: float = 0.0, seconds: float = 6.0, cm=None) -> list[str]:
    """Write the lines to speak: one gap response, or a summary plus replies."""
    from config.prompts.prompts_watch_together import (
        WATCH_LIVE_EMPTY_TEXT, WATCH_LIVE_INTERJECT_PROMPT, WATCH_LIVE_INTERMISSION_PROMPT,
        WATCH_LIVE_SYSTEM_PROMPT, watch_live_template,
    )
    from main_logic.core.callback_render import _build_callback_instruction
    from utils.tokenize import truncate_to_tokens
    from .engine import structured_json_completion, vision_model_config

    async def bounded(text, budget):
        text = str(text or "").strip()
        return await asyncio.to_thread(truncate_to_tokens, text, budget) if text else ""

    character = str(getattr(manager, "lanlan_name", "") or "")
    master = str(getattr(manager, "master_name", "") or "")
    empty = watch_live_template(WATCH_LIVE_EMPTY_TEXT, language)
    messages = _build_callback_instruction(
        callbacks, lang=language, lanlan_name=character, master_name=master,
    ) if callbacks else ""
    events = video.get("events") or []
    if mode == "interject":
        events = [event for event in events if event["at"] <= position]
    reactions = "\n".join(f"{event['at']:.0f}s {event['text']}" for event in events if event.get("text"))
    system = _fill(watch_live_template(WATCH_LIVE_SYSTEM_PROMPT, language), character=character, master=master,
                   persona=await bounded(getattr(manager, "lanlan_prompt", ""), PERSONA_TOKEN_BUDGET) or empty)
    values = {
        "character": character,
        "title": await bounded(video.get("title"), TITLE_TOKEN_BUDGET) or empty,
        "duration": f"{float(video.get('duration') or 0):.0f}",
        "reactions": await bounded(reactions, CONTEXT_TOKEN_BUDGET) or empty,
        "messages": await bounded(messages, CONTEXT_TOKEN_BUDGET) or empty,
    }
    if mode == "interject":
        template = WATCH_LIVE_INTERJECT_PROMPT
        values.update(position=f"{position:.0f}", seconds=f"{seconds:.0f}")
    else:
        template = WATCH_LIVE_INTERMISSION_PROMPT
        values["description"] = await bounded(video.get("description"), DESCRIPTION_TOKEN_BUDGET) or empty
    content = [{"type": "text", "text": _fill(watch_live_template(template, language), **values)}]
    images = [image for callback in callbacks for image in (callback.get("media_images") or [])
              if isinstance(image, str) and image]
    # Several callbacks may each carry a full image budget; apply the ordinary per-turn byte cap.
    images, _dropped = trim_images_to_turn_budget(images[:MAX_IMAGES])
    for image in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}})
    if cm is None:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
    cfg = await vision_model_config(cm)
    value = await structured_json_completion(
        cfg, system, content, {}, _validator(mode),
        stage=f"Live {mode}", label=f"live_{mode}", max_completion_tokens=1024)
    raw = [value["line"]] if mode == "interject" else [value["summary"], *value.get("replies", [])[:MAX_REPLIES]]
    lines = []
    for line in raw:
        clean = " ".join(line.split())
        if clean:
            lines.append(await bounded(clean, LINE_TOKEN_LIMIT))
    return lines


async def synthesize(manager, text: str, language: str) -> tuple[bytes, float]:
    """Synthesize one line with the character's official TTS into WAV bytes."""
    from main_logic.core.game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE
    from .audio import write_speech_wav_async
    identity = manager.game_speech_audio_cache_identity(text, render_language=language)
    key = identity[0]
    result = await manager.preload_game_speech_audio([text], render_language=language)
    # Same guard as preparation: a voice change during synthesis must not read the old entry.
    if manager.game_speech_audio_cache_identity(text, render_language=language) != identity:
        raise ValueError("Character voice changed during synthesis")
    if not result.get("ok"):
        raise ValueError("Character speech unavailable")
    chunks = GAME_SPEECH_AUDIO_CACHE.get(key)
    if not chunks:
        raise ValueError("Synthesized audio unavailable")
    with tempfile.TemporaryDirectory(prefix="neko-watch-live-") as directory:
        output = Path(directory) / "line.wav"
        await write_speech_wav_async(chunks, output)
        data = await asyncio.to_thread(output.read_bytes)
    return data, wav_duration(data)


async def speak(manager, lines, language: str) -> list[dict]:
    spoken = []
    for text in lines:
        try:
            data, duration = await synthesize(manager, text, language)
        except Exception as exc:
            print(f"Watch live line synthesis failed: {type(exc).__name__}")
            continue
        spoken.append({"text": text, "audio": f"{AUDIO_ROUTE}/{store_audio(data)}", "duration": round(duration, 3)})
    if lines and not spoken:
        raise ValueError("Character speech unavailable")
    return spoken
