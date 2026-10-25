# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
This module forwards lanlan's messages to all related servers, including:
1. Bullet Server: listens to real-time content and interacts with live-stream chat (danmaku).
2. Monitor Server: forwards real-time content to all secondary terminals. Secondary terminals play exactly the same content as the primary terminal but are non-interactive. Only one primary terminal can interact at a time.
3. Memory Server: summarizes and analyzes conversation history and converts it into persistent memory.
Note that the cross server is a one-way forwarder and never sends anything back to the main process. If a backchannel is needed, a dedicated bidirectional connection still has to be established.
"""

import ssl
import uuid
import inspect
from urllib.parse import quote

import asyncio
import time
import pickle
import aiohttp
from config import (
    MONITOR_SYNC_URL,
    MONITOR_TOKEN,
    MEMORY_SERVER_PORT,
    COMMENTER_SERVER_PORT,
    AVATAR_INTERACTION_DEDUPE_WINDOW_MS,
    PENDING_USER_IMAGES_MAX,
)
from datetime import datetime
import json
import re
import httpx
from utils.frontend_utils import replace_blank, is_only_punctuation
from utils.internal_http_client import get_internal_http_client
from utils.language_utils import is_supported_language_code
from utils.llm_tool_leak_filter import strip_tool_call_leaks
from utils.logger_config import get_module_logger
from main_logic.agent_event_bus import publish_analyze_request_reliably
from services.chat_sync.source import LATE_TEXT_GRACE_SECONDS, AssistantOriginalBuffer

# Setup logger for this module
logger = get_module_logger(__name__, "Main")
AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS = AVATAR_INTERACTION_DEDUPE_WINDOW_MS
MEMORY_CACHE_SCOPE_AVATAR = "avatar interaction cache"
MEMORY_CACHE_SCOPE_TURN_END = "turn end cache"
emoji_pattern = re.compile(r'[^\w\u4e00-\u9fff\s>][^\w\u4e00-\u9fff\s]{2,}[^\w\u4e00-\u9fff\s<]', flags=re.UNICODE)
emoji_pattern2 = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                           "]+", flags=re.UNICODE)
emotion_pattern = re.compile('<(.*?)>')


async def _publish_analyze_request_with_fallback(lanlan_name: str, trigger: str, messages: list[dict], *, conversation_id: str | None = None, had_user_input: bool = True, language: str | None = None) -> bool:
    """Publish analyze request via EventBus with ack/retry.

    ``had_user_input`` is False for a proactive turn (lanlan spoke with no fresh
    user input). Such turns carry NO user-text gate hint (master-emotion never
    re-ran, and the "latest user message" here is a stale prior turn) and are
    marked ``proactive=True`` so the agent routes them through its throttled
    proactive path instead of the user-turn dedupe (which would drop them).

    ``language`` is the live session locale, taken from the same
    ``_current_user_language()`` provider that already feeds the memory-server
    calls and the agent task-result rendering. The agent process cannot read the
    session's ``user_language`` on its own, so its analyzer prompts would
    otherwise follow the machine's Steam/system locale instead of the UI language.
    """
    try:
        # Optional optimization hint: the cheap pre-gate signal the master-emotion
        # call produced at input-time, matched to THIS turn's latest user message.
        # It must NEVER block or abort the publish — wrapped in its own try so any
        # lazy-import/read failure degrades to None (the agent gate then fails open
        # and runs its assessment). turn_end also never blocks on the emotion call,
        # so this only reads an already-cached value. Cache miss / disabled / stale
        # (different turn) / no-signal / proactive turn → None.
        external_intent = None
        if had_user_input:
            try:
                from main_logic.activity.master_emotion import gate_signal_for
                _latest_user_msg = next(
                    (m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"),
                    None,
                )
                # Skip the gate hint when the latest user turn carries attachments:
                # the actionable intent may live in the image, which the text-only
                # signal never saw → leave external_intent None so the agent fails
                # open and assesses the turn (never drop an image-driven task).
                if _latest_user_msg is not None and not _latest_user_msg.get("attachments"):
                    _latest_user_text = str(_latest_user_msg.get("content") or _latest_user_msg.get("text") or "")
                    external_intent = gate_signal_for(lanlan_name, _latest_user_text)
            except Exception:
                external_intent = None
        sent = await publish_analyze_request_reliably(
            lanlan_name=lanlan_name,
            trigger=trigger,
            messages=messages,
            ack_timeout_s=0.8,
            retries=1,
            conversation_id=conversation_id,
            external_intent=external_intent,
            proactive=not had_user_input,
            language=language,
        )
        if sent:
            logger.debug(
                "[%s] analyze_request forwarded with ack: trigger=%s messages=%d",
                lanlan_name,
                trigger,
                len(messages) if isinstance(messages, list) else 0,
            )
            return True
    except Exception as e:
        logger.info(
            "[%s] analyze_request forwarding exception: trigger=%s error=%s",
            lanlan_name,
            trigger,
            e,
        )
        return False
    return False


def normalize_text(text):  # Perform basic text preprocessing.
    text = text.strip()
    text = replace_blank(text)

    text = emoji_pattern2.sub('', text)
    text = emoji_pattern.sub('', text)
    text = emotion_pattern.sub("", text)
    if is_only_punctuation(text):
        return ""
    return text


# The time stamp ``run_sync_connector`` starts every reply buffer with
# (``strftime('[%Y%m%d %a %H:%M] ')``; the weekday may be localized).
_REPLY_STAMP_RE = re.compile(r"\[\d{8} [^\]\s]+ \d{2}:\d{2}\] ")


def normalize_assistant_text(text, tool_names=None):
    """``normalize_text`` for a reply, with any tool call it spoke as text cut.

    A model that stops calling a tool natively writes the call into its reply
    (``asynccall:name{...}``, ``name(param=...)``). Stored, that line is shown
    to every later session as an example to copy, so it is cut before memory
    sees it. ``tool_names`` are the registered tools, which also lets a bare
    ``name(param=...)`` count. A reply that was nothing but such a call comes
    back empty rather than as its bare time stamp.
    """
    stamp = _REPLY_STAMP_RE.match(text)
    head = stamp.group(0) if stamp else ""
    body = text[len(head):]
    stripped = strip_tool_call_leaks(body, tool_names=tool_names)
    if stripped != body and not normalize_text(stripped):
        return ""
    return normalize_text(head + stripped)


# Mirror schema + detection lives in utils.mirror_meta (re-exported by main_logic.mirror_meta);
# cross_server only consumes those helpers — does not own the schema.
from main_logic.mirror_meta import (
    MIRROR_USER_INPUT_TYPES,
    is_mirror_assistant_message,
    is_mirror_turn_end_meta,
)

_USER_IMAGE_INPUT_TYPES = frozenset({"screen", "camera", "avatar_drop_image", "user_image"})
AVATAR_DROP_SOURCE = "avatar-drop"


def merge_unsynced_tail_assistants(chat_history, last_synced_index):
    """Merge the trailing run of consecutive assistant messages after last_synced_index into one.

    Only touches proactive-chat messages not yet synced to memory; synced normal replies
    are unaffected. Returns the number of messages eliminated (0 means nothing to merge).
    """
    tail = chat_history[last_synced_index:]
    if len(tail) < 2:
        return 0

    consecutive = 0
    for msg in reversed(tail):
        if msg.get('role') == 'assistant':
            consecutive += 1
        else:
            break

    if consecutive < 2:
        return 0

    first_idx = len(chat_history) - consecutive
    parts = []
    for msg in chat_history[first_idx:]:
        try:
            text = msg['content'][0]['text']
            if text:
                parts.append(text)
        except (KeyError, IndexError, TypeError):
            pass

    if not parts:
        return 0

    # Keep only the latest proactive message, discarding older redundancy to avoid persistent-memory growth.
    merged = {**chat_history[-1], 'content': [{'type': 'text', 'text': parts[-1]}]}
    removed = consecutive - 1
    chat_history[first_idx:] = [merged]
    logger.info(f"[cleanup] 精简了 {consecutive} 条未同步的连续主动搭话消息，仅保留最后一条")
    return removed


def _extract_chat_item_text(item: dict) -> str:
    try:
        content = item.get('content') or []
        if not content:
            return ''
        first = content[0]
        if isinstance(first, dict):
            return str(first.get('text', '') or '')
        return str(first or '')
    except Exception:
        return ''


def _should_persist_avatar_interaction_memory(
    cache: dict[str, dict[str, int | str]],
    memory_note: str,
    dedupe_key: str = '',
    dedupe_rank: int = 1,
) -> bool:
    note = str(memory_note or '').strip()
    if not note:
        return False

    key = str(dedupe_key or note).strip() or note
    try:
        rank = max(1, int(dedupe_rank))
    except (TypeError, ValueError):
        rank = 1

    now_ms = int(time.time() * 1000)
    expired_keys = [
        cache_key
        for cache_key, entry in cache.items()
        if now_ms - int((entry or {}).get('ts', 0) or 0) >= AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS
    ]
    for cache_key in expired_keys:
        cache.pop(cache_key, None)

    previous = cache.get(key)
    if previous:
        previous_ts = int(previous.get('ts', 0) or 0)
        previous_rank = int(previous.get('rank', 1) or 1)
        if now_ms - previous_ts < AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS and rank <= previous_rank:
            return False

    cache[key] = {
        'ts': now_ms,
        'rank': rank,
        'note': note,
    }
    return True


def _iter_source_values(value: object) -> list[str]:
    if isinstance(value, str):
        source = value.strip()
        return [source] if source else []
    if isinstance(value, (list, tuple, set, frozenset)):
        values: list[str] = []
        for item in value:
            values.extend(_iter_source_values(item))
        return values
    return []


def _message_has_source(message: dict, source: str) -> bool:
    if not isinstance(message, dict):
        return False
    expected = str(source or "").strip()
    if not expected:
        return False

    if expected in _iter_source_values(message.get("source")):
        return True

    for metadata_key in ("metadata", "meta"):
        metadata = message.get(metadata_key)
        if not isinstance(metadata, dict):
            continue
        if expected in _iter_source_values(metadata.get("source")):
            return True
        if expected in _iter_source_values(metadata.get("sources")):
            return True

    attachments = message.get("attachments")
    if isinstance(attachments, list):
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if expected in _iter_source_values(attachment.get("source")):
                return True
            if expected == AVATAR_DROP_SOURCE and str(attachment.get("input_type") or "") == "avatar_drop_image":
                return True

    return False


def _latest_user_message_has_source(messages: list[dict], source: str) -> bool:
    for message in reversed(messages or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message_has_source(message, source)
    return False


def _append_user_input_cache_to_history(chat_history: list, text: object, sources: set[str], proof=None) -> bool:
    user_text = str(text or "")
    if not user_text:
        sources.clear()
        return False

    item = {'role': 'user', 'content': [{"type": "text", "text": user_text}]}
    normalized_sources = sorted({source for source in sources if source})
    if normalized_sources:
        item["metadata"] = {"sources": normalized_sources}
        if len(normalized_sources) == 1:
            item["source"] = normalized_sources[0]
    if proof:
        item.setdefault("metadata", {})["chat_original_source"] = dict(proof)
    chat_history.append(item)
    sources.clear()
    return True


def _assistant_history_message(text, proof):
    """Preserve producer identity in the original archive, outside model context."""
    item = {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}
    if proof:
        item['metadata'] = {'chat_original_source': dict(proof)}
    return item


def _normalize_pending_user_attachments(pending_user_images: list) -> list[dict]:
    attachments = []
    for raw in pending_user_images or []:
        input_type = ""
        source = ""
        if isinstance(raw, dict):
            url = str(raw.get("data") or raw.get("url") or "").strip()
            input_type = str(raw.get("input_type") or "").strip()
            source = str(raw.get("source") or "").strip()
        else:
            url = str(raw or "").strip()
        if not url:
            continue
        attachment = {
            "type": "image_url",
            "url": url,
        }
        if input_type:
            attachment["input_type"] = input_type
        if source:
            attachment["source"] = source
        attachments.append(attachment)
    return attachments


def _append_pending_user_image(
    pending_user_images: list,
    data: object,
    request_id: object,
    input_type: object = None,
    *,
    source: object = None,
) -> bool:
    """Append a real user image entry and return whether one was queued.

    Empty data means the caller only sent metadata, so the placeholder is skipped.
    """
    image_data = str(data or "").strip()
    if not image_data:
        return False
    entry = {
        "data": image_data,
        "request_id": request_id or "",
    }
    if input_type:
        entry["input_type"] = input_type
    source_value = str(source or "").strip()
    if source_value:
        entry["source"] = source_value
    pending_user_images.append(entry)
    if len(pending_user_images) > PENDING_USER_IMAGES_MAX:
        del pending_user_images[:-PENDING_USER_IMAGES_MAX]
    return True


def _select_pending_user_images_for_turn(pending_user_images: list, request_id: object) -> list:
    turn_request_id = str(request_id or "")
    selected = []
    for raw in pending_user_images or []:
        if not isinstance(raw, dict):
            if turn_request_id:
                continue
            selected.append(raw)
            continue
        image_request_id = str(raw.get("request_id") or "")
        if image_request_id and image_request_id != turn_request_id:
            continue
        selected.append(raw)
    return selected


def _partition_pending_user_images_for_turn(
    pending_user_images: list,
    request_id: object,
    *,
    consume_untagged: bool = True,
) -> tuple[list, list]:
    turn_request_id = str(request_id or "")
    selected = []
    remaining = []
    for raw in pending_user_images or []:
        if not isinstance(raw, dict):
            if not consume_untagged:
                remaining.append(raw)
                continue
            if not turn_request_id:
                selected.append(raw)
            continue
        image_request_id = str(raw.get("request_id") or "")
        if image_request_id and image_request_id != turn_request_id:
            remaining.append(raw)
            continue
        if not image_request_id and not consume_untagged:
            remaining.append(raw)
            continue
        selected.append(raw)
    return selected, remaining


def _select_pending_user_images_for_session_end(pending_user_images: list, request_id: object) -> list:
    session_request_id = str(request_id or "")
    if not session_request_id:
        for raw in reversed(pending_user_images or []):
            if isinstance(raw, dict) and str(raw.get("request_id") or ""):
                session_request_id = str(raw.get("request_id") or "")
                break
    return _select_pending_user_images_for_turn(pending_user_images, session_request_id)


def _build_recent_analyze_messages(
    chat_history: list,
    pending_user_images: list,
    limit: int = 6,
    *,
    allow_attach_to_last_user: bool = False,
) -> list[dict]:
    recent: list[dict] = []
    last_user_idx: int | None = None
    last_user_source_idx: int | None = None
    slice_start = max(0, len(chat_history) - limit)

    for source_idx, item in enumerate(chat_history[-limit:], start=slice_start):
        if item.get('role') not in ['user', 'assistant']:
            continue
        try:
            txt = item['content'][0]['text'] if item.get('content') else ''
        except Exception:
            txt = ''
        txt = str(txt or '')
        if txt == '':
            continue
        recent_item = {'role': item.get('role'), 'content': txt}
        if item.get("source"):
            recent_item["source"] = item.get("source")
        if isinstance(item.get("metadata"), dict) and item.get("metadata"):
            recent_item["metadata"] = item.get("metadata").copy()
        recent.append(recent_item)
        if item.get('role') == 'user':
            last_user_idx = len(recent) - 1
            last_user_source_idx = source_idx

    attachments = _normalize_pending_user_attachments(pending_user_images)
    if attachments:
        if (
            not allow_attach_to_last_user
            or last_user_idx is None
            or last_user_source_idx is None
            or last_user_source_idx < slice_start
        ):
            recent.append({
                'role': 'user',
                'content': '',
                'attachments': attachments,
            })
        else:
            recent[last_user_idx]['attachments'] = attachments

    return [msg for msg in recent if msg.get('content') or msg.get('attachments')]


async def _safe_close(target) -> None:
    """Unified fallback for closing ws / session. We are already in the cleanup phase, so a failed close must not affect the rest of the flow."""
    if target is None:
        return
    try:
        await target.close()
    except Exception as e:
        logger.debug(f"_safe_close: ignored exception during close: {e}")


class _WSSlot:
    """Shared state for a single ws endpoint. Read/written by the main loop, reader, and maintainer.

    The main loop only reads ``ws`` / ``dead_event`` and marks them dead; it never calls
    ws_connect / close. The entire lifecycle is managed by :func:`_slot_maintainer` in a
    separate task.
    """
    __slots__ = ("name", "url", "lanlan_name", "ws_kwargs",
                 "ws", "session", "reader", "maintainer", "dead_event")

    def __init__(self, name: str, url: str, lanlan_name: str, *, ws_kwargs=None):
        self.name = name
        self.url = url
        self.lanlan_name = lanlan_name
        self.ws_kwargs = ws_kwargs or {}
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.session: aiohttp.ClientSession | None = None
        self.reader: asyncio.Task | None = None
        self.maintainer: asyncio.Task | None = None
        self.dead_event = asyncio.Event()
        self.dead_event.set()  # Initially dead -> maintainer's first wait returns immediately, triggering initial connection.


def _mark_dead(slot: _WSSlot) -> None:
    """Mark the slot as disconnected. Idempotent. Called both when the main loop's send fails and when the reader detects a close."""
    slot.ws = None
    slot.dead_event.set()


async def _slot_reader(slot: _WSSlot, ws: aiohttp.ClientWebSocketResponse) -> None:
    """Read loop bound to a specific ws instance. Detects close → mark_dead wakes the maintainer.

    The ws is passed as a parameter instead of read from the slot, so that an old reader
    cannot mistakenly mark a new ws dead after the maintainer has already replaced
    slot.ws. Does not call mark_dead when actively cancelled by the maintainer.
    """
    try:
        while True:
            try:
                msg = await ws.receive(timeout=30)
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                return
    except Exception:
        pass
    # Mark dead only if slot.ws still references this socket, avoiding overwriting the maintainer's replacement.
    if slot.ws is ws:
        _mark_dead(slot)


async def _slot_maintainer(
    slot: _WSSlot,
    *,
    backoff_min: float = 0.25,
    backoff_max: float = 1.5,
    auth_retry: float = 30.0,
) -> None:
    """Per-slot reconnect loop, event-driven + exponential backoff.

    The monitor is an optional add-on; when absent, this task silently retries at the
    backoff_max cadence and the main sync loop is completely unaware.

    Cycle guarantee: each attempt = ``wait_for(ws_connect, timeout=backoff)``, so the
    cycle duration is clamped by backoff on both sides —
    - upper bound: connect hangs until timeout before failing, cycle ≈ backoff
    - lower bound: connect fails instantly (refused), sleep pads up to backoff, cycle = backoff

    backoff_max=1.5s means "worst case 1.5s polling". On Windows a bare ws_connect
    failure can take ~4s (TCP SYN timeout); without wait_for this guarantee would break.
    """
    backoff = backoff_min
    auth_warned = False
    while True:
        await slot.dead_event.wait()

        # Clean old readers/sessions asynchronously without blocking reconnection.
        old_reader = slot.reader
        old_session = slot.session
        slot.reader = None
        slot.session = None
        if old_reader is not None:
            old_reader.cancel()
        if old_session is not None:
            asyncio.create_task(_safe_close(old_session))

        new_session = aiohttp.ClientSession()
        slot.session = new_session
        cycle_start = time.monotonic()
        try:
            new_ws = await asyncio.wait_for(
                new_session.ws_connect(slot.url, **slot.ws_kwargs),
                timeout=backoff,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Reap failed sessions in the background immediately to prevent lingering.
            asyncio.create_task(_safe_close(new_session))
            slot.session = None
            if isinstance(e, aiohttp.WSServerHandshakeError) and e.status in (401, 403):
                # Token mismatch cannot recover through fast retries; notify once and slow retries rather than flooding Monitor every 1.5 s.
                if not auth_warned:
                    logger.warning(
                        f"[{slot.lanlan_name}] {slot.name} Monitor 拒绝认证 (HTTP {e.status})，"
                        f"请检查主服务与 Monitor 的 NEKO_MONITOR_TOKEN 是否一致；{auth_retry:.0f}s 后重试"
                    )
                    auth_warned = True
                await asyncio.sleep(auth_retry)
                continue
            logger.debug(
                f"[{slot.lanlan_name}] {slot.name} ws_connect 失败: "
                f"{type(e).__name__}: {e} (backoff {backoff:.2f}s)"
            )
            # Maintain the backoff period: add sleep after millisecond-scale refused fast failures;
            # deadline timeouts need little/no sleep before retry.
            elapsed = time.monotonic() - cycle_start
            if elapsed < backoff:
                await asyncio.sleep(backoff - elapsed)
            backoff = min(backoff * 2, backoff_max)
            continue  # dead_event remains set, triggering immediate retry next round.

        # Ordering matters: install ws and clear dead_event before creating its reader;
        # otherwise reader mark_dead may occur before clear(), which erases the signal.
        slot.ws = new_ws
        auth_warned = False
        slot.dead_event.clear()
        slot.reader = asyncio.create_task(
            _slot_reader(slot, new_ws),
            name=f"WSReader-{slot.name}-{slot.lanlan_name}",
        )
        backoff = backoff_min


async def _try_send_json(slot: _WSSlot | None, payload: dict) -> bool:
    """Fail-soft send. Returns False when the slot is missing / ws is dead / send raises; never propagates upward."""
    if slot is None:
        return False
    ws = slot.ws
    if ws is None:
        return False
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        _mark_dead(slot)
        return False


async def _try_send_bytes(slot: _WSSlot | None, payload: bytes) -> bool:
    if slot is None:
        return False
    ws = slot.ws
    if ws is None:
        return False
    try:
        await ws.send_bytes(payload)
        return True
    except Exception:
        _mark_dead(slot)
        return False


async def _post_memory_server(
    endpoint: str,
    lanlan_name: str,
    payload: list[dict],
    *,
    timeout_s: float,
    language: str | None = None,
    render_language: str | None = None,
) -> tuple[bool, str, dict]:
    """Post history payload to memory_server and treat only 2xx+valid JSON as success."""
    encoded_name = quote(lanlan_name, safe="")
    url = f"http://127.0.0.1:{MEMORY_SERVER_PORT}/{endpoint}/{encoded_name}"

    client = get_internal_http_client()
    request_payload = {
        "input_history": json.dumps(payload, indent=2, ensure_ascii=False),
    }
    # Only a live explicit frontend/session locale is durable provenance.
    # A renderer locale is a request-local fallback, never a substitute for the
    # durable ``language`` field.  Keep the two wire fields mutually exclusive
    # so downstream persistence cannot mistake UI evidence for a preference.
    if is_supported_language_code(language):
        request_payload["language"] = language
    elif is_supported_language_code(render_language):
        request_payload["render_language"] = render_language
    response = await client.post(
        url,
        json=request_payload,
        timeout=timeout_s,
    )
    raw_body = response.text
    status_code = response.status_code

    if status_code < 200 or status_code >= 300:
        return False, f"HTTP {status_code} (body_len={len(raw_body)})", {}

    try:
        result = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return False, f"non-JSON response (body_len={len(raw_body)})", {}

    if not isinstance(result, dict):
        return False, f"unexpected response type: {type(result).__name__}", {}

    if result.get("status") == "error":
        return False, str(result.get("message", "unknown_error")), result

    return True, "", result


def _is_expected_memory_write_exception(exc: Exception) -> bool:
    return isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError, httpx.HTTPError, ConnectionError, OSError))


def _mark_memory_cache_success(lanlan_name: str, scope: str, health_state: dict[str, bool]) -> None:
    if health_state.get(scope, False):
        logger.info(f"[{lanlan_name}] {scope} 已恢复")
    health_state[scope] = False


def _mark_memory_cache_business_failure(
    lanlan_name: str,
    scope: str,
    detail: str,
    health_state: dict[str, bool],
) -> None:
    was_unhealthy = health_state.get(scope, False)
    health_state[scope] = True
    if was_unhealthy:
        logger.debug(f"[{lanlan_name}] {scope} 失败（持续）: {detail}")
    else:
        logger.debug(f"[{lanlan_name}] {scope} 失败（进入失败状态）: {detail}")


def _mark_memory_cache_exception(
    lanlan_name: str,
    scope: str,
    exc: Exception,
    health_state: dict[str, bool],
) -> None:
    was_unhealthy = health_state.get(scope, False)
    health_state[scope] = True
    reason = "网络层" if _is_expected_memory_write_exception(exc) else "未知类型"
    msg = f"[{lanlan_name}] {scope} 异常（{reason}{'，持续' if was_unhealthy else '，进入异常状态'}）: {type(exc).__name__}: {exc}"
    if was_unhealthy:
        logger.debug(msg)
    elif reason == "未知类型":
        logger.warning(msg, exc_info=True)
    else:
        logger.warning(msg)


async def _complete_session_end_memory_barrier(message: dict, lanlan_name: str) -> None:
    """Run an optional post-settlement callback and resolve its in-process ack.

    Language-preference changes attach this private control data to the ordinary
    ``session end`` queue item.  Running the callback here keeps it ordered after
    every older chat message and after the terminal memory-server write, while the
    future lets the caller avoid returning before the isolation step completes.
    """
    callback = message.get("_after_memory_settlement")
    completion = message.get("_memory_settlement_done")
    if not callable(callback):
        if isinstance(completion, asyncio.Future) and not completion.done():
            completion.set_result(None)
        return

    try:
        callback_result = callback()
        if inspect.isawaitable(callback_result):
            await callback_result
    except Exception as exc:
        logger.warning(
            "[%s] session-end post-settlement callback failed: %s: %s",
            lanlan_name,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        if isinstance(completion, asyncio.Future) and not completion.done():
            completion.set_exception(exc)
    else:
        if isinstance(completion, asyncio.Future) and not completion.done():
            completion.set_result(None)


async def run_sync_connector(
    message_queue: asyncio.Queue,
    lanlan_name,
    sync_server_url=MONITOR_SYNC_URL,
    config=None,
    status_callback=None,
    user_language_provider=None,
    render_language_provider=None,
    monitor_auth_token: str | None = MONITOR_TOKEN or None,
    tool_names_provider=None,
):
    """Async-native sync connector, running on the caller's main event loop.

    Architecture:
    - Main loop: blocks on ``await message_queue.get()`` for messages, then pushes data
      into the ws via ``_try_send_*``; fail-soft skips when the ws is dead/missing.
      **The main loop never calls ws_connect / session.close / ws.close**, so a monitor
      that hasn't started or is disconnected never blocks other await paths like memory.
    - One :func:`_slot_maintainer` background task per ws: woken by ``dead_event`` +
      exponential-backoff reconnect (0.25s → capped at 2s). Retries silently on failure
      without disturbing the main loop.
    - One :func:`_slot_reader` background task per live ws: detects server close →
      ``_mark_dead`` → wakes the maintainer.
    - The application-level heartbeat has been removed: aiohttp's underlying
      ``heartbeat=10`` ping/pong turns a dead connection into a ``CLOSED`` the reader
      can see within ~10s.

    Args:
        status_callback: optional ``Callable[[str], None]``. Runs on the main loop, so it
            may call ``asyncio.create_task(...)`` directly without
            ``run_coroutine_threadsafe``.
        user_language_provider: optional callable returning the live session locale.
        render_language_provider: optional callable returning the current renderer
            locale when the session has no explicit language preference.
        monitor_auth_token: Monitor bearer token, defaulting to the configured
            ``MONITOR_TOKEN``. When set it is sent as an Authorization header on
            Monitor sync WebSocket connections.
        tool_names_provider: optional callable returning the session's registered
            tool names, used to cut tool calls a reply spoke as text
            (``normalize_assistant_text``) before it is stored.
    """
    chat_history: list = []
    default_config = {'bullet': True, 'monitor': True}
    if config is None:
        config = {}
    config = default_config | config

    def _current_user_language() -> str | None:
        if not callable(user_language_provider):
            return None
        try:
            selected = user_language_provider()
        except Exception as exc:
            logger.debug("[%s] session language provider failed: %s", lanlan_name, exc)
            return None
        return selected if is_supported_language_code(selected) else None

    def _current_render_language() -> str | None:
        if not callable(render_language_provider):
            return None
        try:
            selected = render_language_provider()
        except Exception as exc:
            logger.debug("[%s] render language provider failed: %s", lanlan_name, exc)
            return None
        return selected if is_supported_language_code(selected) else None

    def _normalized_reply(text: str) -> str:
        tool_names = None
        if callable(tool_names_provider):
            try:
                tool_names = tool_names_provider()
            except Exception as exc:
                logger.debug("[%s] tool names provider failed: %s", lanlan_name, exc)
        return normalize_assistant_text(text, tool_names)

    def _current_memory_languages() -> tuple[str | None, str | None]:
        """Snapshot mutually exclusive durable and render-only memory hints."""
        language = _current_user_language()
        if language is not None:
            return language, None
        return None, _current_render_language()

    def _current_analyze_language() -> str | None:
        """Use the visible locale for one analyzer request without persisting it."""
        language, render_language = _current_memory_languages()
        return language or render_language

    # Historical preservation: threaded code used ``if shutdown_event.is_set(): break`` to skip HTTP calls
    # to a shutting-down memory_server in subprocess mode. Async cancellation now raises
    # CancelledError naturally at awaits, making guards redundant. Replace uniformly with
    # always-False stubs to avoid mass reindentation. They are now dead code but retain correct
    # semantics by allowing normal paths; a later cleanup PR may remove them together.
    class _NeverShutdown:
        @staticmethod
        def is_set() -> bool:
            return False
    shutdown_event = _NeverShutdown()

    # WS slots: disabled config endpoints create no maintainer and cost nothing.
    sync_slot: _WSSlot | None = None
    binary_slot: _WSSlot | None = None
    bullet_slot: _WSSlot | None = None
    if config['monitor']:
        monitor_ws_kwargs = {'heartbeat': 10}
        if monitor_auth_token:
            monitor_ws_kwargs['headers'] = {
                'Authorization': f'Bearer {monitor_auth_token}',
            }
        sync_slot = _WSSlot(
            'sync',
            f"{sync_server_url}/sync/{lanlan_name}",
            lanlan_name,
            ws_kwargs=monitor_ws_kwargs,
        )
        binary_slot = _WSSlot(
            'binary',
            f"{sync_server_url}/sync_binary/{lanlan_name}",
            lanlan_name,
            ws_kwargs=monitor_ws_kwargs.copy(),
        )
    if config['bullet']:
        bullet_slot = _WSSlot(
            'bullet',
            f"wss://127.0.0.1:{COMMENTER_SERVER_PORT}/sync/{lanlan_name}",
            lanlan_name,
            ws_kwargs={'ssl': ssl._create_unverified_context()},
        )
    slots: list[_WSSlot] = [s for s in (sync_slot, binary_slot, bullet_slot) if s is not None]
    for s in slots:
        s.maintainer = asyncio.create_task(
            _slot_maintainer(s),
            name=f"WSMaint-{s.name}-{lanlan_name}",
        )

    user_input_cache = ''
    user_input_proof = None
    user_input_sources: set[str] = set()
    text_output_cache = ''  # lanlan's current message.
    text_output_request_id = None
    text_output_proof = None
    # Raw originals use server-created identities and never normalized memory text.
    original_output = AssistantOriginalBuffer(tail_grace=LATE_TEXT_GRACE_SECONDS)
    current_turn = 'user'
    had_user_input_this_turn = False  # Whether the current turn has user input; False means proactive chat.
    current_turn_start_index = 0
    last_screen = None
    pending_user_images: list = []
    last_synced_index = 0  # Sync only new messages to memory at turn end so memory_browser updates correctly.
    avatar_interaction_memory_cache: dict[str, dict[str, int | str]] = {}
    memory_cache_health_state = {
        MEMORY_CACHE_SCOPE_AVATAR: False,
        MEMORY_CACHE_SCOPE_TURN_END: False,
    }

    try:
        while True:
            # Block awaiting messages. With none, the main loop stays silent because WS maintenance lives in _slot_maintainer,
            # eliminating periodic reconnect/heartbeat inspection wakeups.
            message = await message_queue.get()  # noqa: ASYNC_BLOCK — asyncio.Queue, not queue.Queue

            if message is not None:
                try:
                    if message["type"] == "json":
                        # Forward to monitor if enabled, failing softly by skipping absent monitors.
                        await _try_send_json(sync_slot, message["data"])

                        # Only treat assistant turn when it's a gemini_response
                        if message["data"].get("type") == "gemini_response":
                            if is_mirror_assistant_message(message["data"]):
                                logger.debug(
                                    "[%s] mirror assistant line skipped for ordinary memory: source=%s",
                                    lanlan_name,
                                    (message["data"].get("metadata") or {}).get("source"),
                                )
                                continue
                            original_output.append(message["data"])
                            if current_turn == 'user':  # assistant new message starts
                                had_user_input_this_turn = bool(user_input_cache)
                                if user_input_cache:
                                    _append_user_input_cache_to_history(chat_history, user_input_cache, user_input_sources, user_input_proof)
                                    user_input_cache = ''
                                current_turn = 'assistant'
                                text_output_request_id = message["data"].get("request_id")
                                current_turn_start_index = len(chat_history)
                                text_output_cache = datetime.now().strftime('[%Y%m%d %a %H:%M] ')
                                text_output_proof = message['data'].get('chat_original_source')
                                text_output_proof = dict(text_output_proof) if isinstance(text_output_proof, dict) else None

                                if bullet_slot is not None and bullet_slot.ws is not None:
                                    try:
                                        last_user = last_ai = None
                                        for i in chat_history[::-1]:
                                            if i["role"] == "user":
                                                last_user = i['content'][0]['text']
                                                break
                                        for i in chat_history[::-1]:
                                            if i["role"] == "assistant":
                                                last_ai = i['content'][0]['text']
                                                break

                                        message_data = {
                                            "user": last_user,
                                            "ai": last_ai,
                                            "screen": last_screen
                                        }
                                        binary_message = pickle.dumps(message_data)
                                        await _try_send_bytes(bullet_slot, binary_message)
                                    except Exception as e:
                                        logger.error(f"[{lanlan_name}] Error when sending to commenter: {e}")

                            # Append assistant streaming text
                            try:
                                text_output_cache += message["data"].get("text", "")
                            except Exception:
                                pass

                    elif message["type"] == "binary":
                        await _try_send_bytes(binary_slot, message["data"])

                    elif message["type"] == "user":  # Prepare transcription.
                        data = message["data"].get("data")
                        input_type = message["data"].get("input_type")
                        if input_type == "transcript": # Handle only audio for now; images still need recording later.
                            for source_value in _iter_source_values(message["data"].get("source")):
                                user_input_sources.add(source_value)
                            transcript_metadata = message["data"].get("metadata")
                            if isinstance(transcript_metadata, dict):
                                for source_value in _iter_source_values(transcript_metadata.get("source")):
                                    user_input_sources.add(source_value)
                                for source_value in _iter_source_values(transcript_metadata.get("sources")):
                                    user_input_sources.add(source_value)
                            if user_input_cache == '':
                                user_input_proof = message['data'].get('chat_original_source')
                                await _try_send_json(sync_slot, {'type': 'user_activity'})  # Interrupt frontend audio playback.
                            user_input_cache += data
                            # Send user transcripts to monitor for secondary-terminal display.
                            if data:
                                await _try_send_json(sync_slot, {'type': 'user_transcript', 'text': data})
                        elif input_type in MIRROR_USER_INPUT_TYPES:
                            # Mirror channel user inputs (e.g. text/voice that
                            # was hijacked into an external controller) are
                            # logged for monitor display but **never** flushed
                            # into chat_history as a UserMessage — the chat
                            # LLM did not "hear" them; they belong to the
                            # external controller's transcript log.
                            if data:
                                await _try_send_json(sync_slot, {'type': 'user_transcript', 'text': data})
                            await _try_send_json(sync_slot, {'type': 'user_activity'})
                        elif input_type in _USER_IMAGE_INPUT_TYPES:
                            if input_type in {"screen", "camera"}:
                                last_screen = data
                            appended_image = _append_pending_user_image(
                                pending_user_images,
                                data,
                                message["data"].get("request_id") or "",
                                input_type,
                                source=message["data"].get("source"),
                            )
                            if not appended_image and message["data"].get("has_image"):
                                await _try_send_json(sync_slot, {'type': 'user_activity'})

                    elif message["type"] == "system":
                        try:
                            if message["data"] == "chat original tool":
                                # Provenance only: keep the whole reply local,
                                # without changing memory or forwarding a UI event.
                                original_output.mark_tool(message.get("chat_original_id"))
                                if text_output_proof and text_output_proof.get('recordId') == message.get('chat_original_id'):
                                    text_output_proof['origin'] = 'tool'
                                continue
                            if message["data"] == "google disconnected":
                                original_output.discard()
                                if len(text_output_cache) > 0:
                                    chat_history.append({'role': 'system', 'content': [
                                        {'type': 'text', 'text': "网络错误，您已断开连接！"}]})
                                text_output_cache = ''
                                text_output_request_id = None
                                text_output_proof = None
                            
                            elif message["data"] == "response_discarded_clear":
                                original_output.discard()
                                logger.debug(f"[{lanlan_name}] 收到 response_discarded_clear，清空当前输出缓存")
                                text_output_cache = ''
                                text_output_request_id = None
                                text_output_proof = None
                            
                            if message["data"] == "renew session":
                                # Check shutdown state.
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过renew session处理")
                                    break
                                # The existing reset persists completed/partial output at this boundary.
                                await original_output.finish(lanlan_name)
                                
                                # First process unfinished user-input buffers, if any.
                                if user_input_cache:
                                    _append_user_input_cache_to_history(chat_history, user_input_cache, user_input_sources, user_input_proof)
                                    user_input_cache = ''
                                
                                # Then process unfinished output buffers, if any.
                                current_turn = 'user'
                                text_output_cache = _normalized_reply(text_output_cache)
                                if len(text_output_cache) > 0:
                                    chat_history.append(
                                            _assistant_history_message(text_output_cache, text_output_proof))
                                text_output_cache = ''
                                text_output_request_id = None
                                text_output_proof = None
                                current_turn_start_index = len(chat_history)
                                # Merge unsynced consecutive proactive-chat messages.
                                merge_unsynced_tail_assistants(chat_history, last_synced_index)

                                # Recheck shutdown state.
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过memory_server请求")
                                    chat_history.clear()
                                    break
                                
                                # Send only remaining messages not covered by /cache, triggering LLM settlement.
                                remaining = chat_history[last_synced_index:]
                                logger.info(f"[{lanlan_name}] 热重置：聊天历史 {len(chat_history)} 条，增量 {len(remaining)} 条")
                                # Select /renew for new messages, otherwise /settle to complete summaries/timestamps.
                                _renew_endpoint = "renew" if remaining else "settle"
                                _renew_payload = remaining if remaining else []
                                _memory_language, _memory_render_language = (
                                    _current_memory_languages()
                                )
                                try:
                                    ok, err_detail, _ = await _post_memory_server(
                                        _renew_endpoint,
                                        lanlan_name,
                                        _renew_payload,
                                        timeout_s=30.0,
                                        language=_memory_language,
                                        render_language=_memory_render_language,
                                    )
                                    if not ok:
                                        logger.error(f"[{lanlan_name}] 热重置记忆处理失败 ({_renew_endpoint}): {err_detail}")
                                        if status_callback:
                                            try:
                                                status_callback(f"⚠️ 热重置记忆失败: {err_detail}")
                                            except Exception:
                                                pass
                                    else:
                                        logger.info(f"[{lanlan_name}] 热重置记忆已成功上传到 memory_server ({_renew_endpoint})")
                                except RuntimeError as e:
                                    if "shutdown" in str(e).lower() or "closed" in str(e).lower():
                                        logger.info(f"[{lanlan_name}] 进程正在关闭，{_renew_endpoint}请求已取消")
                                    else:
                                        logger.exception(f"[{lanlan_name}] 调用 /{_renew_endpoint} API 失败: {type(e).__name__}: {e}")
                                except Exception as e:
                                    logger.exception(f"[{lanlan_name}] 调用 /{_renew_endpoint} API 失败: {type(e).__name__}: {e}")
                                chat_history.clear()
                                last_synced_index = 0

                            if message["data"] in ('turn end', 'turn end agent_callback'): # lanlan's message ended.
                                is_agent_callback_turn_end = (message["data"] == 'turn end agent_callback')
                                # Backend labeling atomically binds meta to turn-end events, removing reliance on separate-channel
                                # pending_* state. Truncate game-only soccer dialogue here before
                                # unlabeled compatibility mirror text can enter ordinary chat_history.
                                turn_end_meta = message.get("meta") if isinstance(message, dict) else None
                                if not isinstance(turn_end_meta, dict):
                                    turn_end_meta = None
                                turn_request_id = message.get("request_id") if isinstance(message, dict) else None
                                was_assistant_turn = current_turn == 'assistant'
                                if is_mirror_turn_end_meta(turn_end_meta):
                                    # Mirror turn-end: mirror assistant messages
                                    # never enter ``text_output_cache`` (they
                                    # ``continue`` at line ~595), and mirror user
                                    # inputs never enter ``user_input_cache``
                                    # (policy A at line ~660), so we must NOT
                                    # touch user-side state — a pre-takeover real
                                    # user utterance is still legitimate and must
                                    # be flushed when the post-takeover assistant
                                    # turn arrives.
                                    #
                                    # BUT if takeover started mid-ordinary-
                                    # assistant turn, that turn becomes orphaned:
                                    # ``current_turn`` stays ``'assistant'`` and
                                    # ``text_output_cache`` holds partial text
                                    # from a turn that never completes.  Without
                                    # cleanup the next post-takeover real
                                    # ``gemini_response`` is treated as a
                                    # continuation, skipping the ``current_turn
                                    # == 'user'`` block that flushes
                                    # ``user_input_cache`` into chat_history → the
                                    # user's input is silently dropped.
                                    # So reset assistant-side state when the
                                    # current ordinary turn was in flight, leave
                                    # user-side alone.
                                    if was_assistant_turn:
                                        original_output.discard()
                                        text_output_cache = ''
                                        text_output_request_id = None
                                        text_output_proof = None
                                        current_turn = 'user'
                                        current_turn_start_index = len(chat_history)
                                        had_user_input_this_turn = False
                                    await _try_send_json(sync_slot, {'type': 'turn end'})
                                    logger.debug("[%s] mirror turn end skipped for ordinary memory/analyzer", lanlan_name)
                                    continue
                                # Terminal metadata excludes avatar/tool output before raw persistence.
                                await original_output.finish(
                                    lanlan_name, message.get('chat_original_id'),
                                    excluded=bool(turn_end_meta and turn_end_meta.get('kind') == 'avatar_interaction'),
                                )
                                current_turn = 'user'
                                text_output_cache = _normalized_reply(text_output_cache)
                                if len(text_output_cache) > 0:
                                    chat_history.append(
                                        _assistant_history_message(text_output_cache, text_output_proof))
                                text_output_cache = ''
                                text_output_request_id = None
                                text_output_proof = None
                                # Only kind == 'avatar_interaction' enters isolation; otherwise process as
                                # proactive / normal.
                                # Core labels turn-end meta atomically and clears it if user input takes over avatar
                                # interactions. No had_user_input fallback is needed here, avoiding ordinary-path
                                # misclassification when user audio buffers happen to enqueue first.
                                is_avatar_interaction_turn = (
                                    turn_end_meta is not None
                                    and turn_end_meta.get("kind") == "avatar_interaction"
                                )
                                avatar_turn_start_index = min(current_turn_start_index, len(chat_history))
                                avatar_turn_slice = chat_history[avatar_turn_start_index:] if is_avatar_interaction_turn else []
                                avatar_turn_assistant_text = ''
                                if is_avatar_interaction_turn and avatar_turn_slice:
                                    avatar_turn_assistant_text = _extract_chat_item_text(avatar_turn_slice[-1]).strip()
                                # For proactive turns without user input, merge unsynced consecutive assistant messages without /cache writes.
                                if not had_user_input_this_turn and not is_avatar_interaction_turn:
                                    merge_unsynced_tail_assistants(chat_history, last_synced_index)
                                await _try_send_json(sync_slot, {'type': 'turn end'})
                                if is_avatar_interaction_turn:
                                    # Avatar tool turns are handled in an isolated
                                    # memory path so they never leak into analyzer
                                    # requests or later session-end bulk syncs.
                                    memory_note = str(turn_end_meta.get('memory_note') or '').strip()
                                    memory_dedupe_key = str(turn_end_meta.get('memory_dedupe_key') or '').strip()
                                    memory_dedupe_rank = turn_end_meta.get('memory_dedupe_rank', 1)
                                    # Snapshot dedupe slots first and roll back on /cache failure, preventing real interactions
                                    # in the next 8 s from being dropped as already recorded.
                                    dedupe_rollback_key = (memory_dedupe_key or memory_note).strip()
                                    dedupe_prior_entry = avatar_interaction_memory_cache.get(dedupe_rollback_key)
                                    should_persist_avatar_turn = (
                                        bool(avatar_turn_assistant_text)
                                        and _should_persist_avatar_interaction_memory(
                                            avatar_interaction_memory_cache,
                                            memory_note,
                                            memory_dedupe_key,
                                            memory_dedupe_rank,
                                        )
                                    )
                                    if should_persist_avatar_turn:
                                        avatar_memory_messages = [
                                            {'role': 'user', 'content': [{'type': 'text', 'text': memory_note}]},
                                            {'role': 'assistant', 'content': [{'type': 'text', 'text': avatar_turn_assistant_text}]},
                                        ]
                                        cache_persist_failed = False
                                        _memory_language, _memory_render_language = (
                                            _current_memory_languages()
                                        )
                                        try:
                                            ok, err_detail, _ = await _post_memory_server(
                                                "cache",
                                                lanlan_name,
                                                avatar_memory_messages,
                                                timeout_s=10.0,
                                                language=_memory_language,
                                                render_language=_memory_render_language,
                                            )
                                            if ok:
                                                _mark_memory_cache_success(
                                                    lanlan_name,
                                                    MEMORY_CACHE_SCOPE_AVATAR,
                                                    memory_cache_health_state,
                                                )
                                            else:
                                                cache_persist_failed = True
                                                _mark_memory_cache_business_failure(
                                                    lanlan_name,
                                                    MEMORY_CACHE_SCOPE_AVATAR,
                                                    err_detail,
                                                    memory_cache_health_state,
                                                )
                                        except Exception as e:
                                            cache_persist_failed = True
                                            _mark_memory_cache_exception(
                                                lanlan_name,
                                                MEMORY_CACHE_SCOPE_AVATAR,
                                                e,
                                                memory_cache_health_state,
                                            )
                                        if cache_persist_failed and dedupe_rollback_key:
                                            if dedupe_prior_entry is not None:
                                                avatar_interaction_memory_cache[dedupe_rollback_key] = dedupe_prior_entry
                                            else:
                                                avatar_interaction_memory_cache.pop(dedupe_rollback_key, None)

                                    if avatar_turn_slice:
                                        del chat_history[avatar_turn_start_index:]
                                        if last_synced_index > len(chat_history):
                                            last_synced_index = len(chat_history)

                                    current_turn_start_index = len(chat_history)
                                    # The avatar branch's continue skips ordinary finally cleanup of pending_user_images;
                                    # leftover screenshots/camera frames would contaminate the next analyzer turn.
                                    pending_user_images = []
                                    continue
                                # Send recent conversation to tool_server nonblockingly for potential-task analysis.
                                # Skip only the dedicated agent-callback channel to avoid reanalyzing task-result callbacks.
                                if not shutdown_event.is_set():
                                    selected_pending_user_images, remaining_pending_user_images = (
                                        _partition_pending_user_images_for_turn(
                                            pending_user_images,
                                            turn_request_id,
                                            consume_untagged=had_user_input_this_turn,
                                        )
                                    )
                                    try:
                                        # Build recent-message summaries and retain this turn's latest image attachments.
                                        recent = _build_recent_analyze_messages(
                                            chat_history,
                                            selected_pending_user_images,
                                            allow_attach_to_last_user=had_user_input_this_turn,
                                        )
                                        has_user = any(m.get('role') == 'user' for m in recent)
                                        latest_user_is_avatar_drop = _latest_user_message_has_source(
                                            recent,
                                            AVATAR_DROP_SOURCE,
                                        )
                                        logger.info(
                                            f"[{lanlan_name}] turn_end analyze check: "
                                            f"history={len(chat_history)} recent={len(recent)} "
                                            f"has_user={has_user} had_input={had_user_input_this_turn} "
                                            f"agent_callback_turn={is_agent_callback_turn_end} "
                                            f"avatar_drop_turn={latest_user_is_avatar_drop}"
                                        )
                                        if recent and has_user and latest_user_is_avatar_drop:
                                            logger.info(f"[{lanlan_name}] analyze_request skipped (avatar_drop turn_end), messages={len(recent)}")
                                        elif recent and not is_agent_callback_turn_end:
                                            # had_user_input gates the proactive marking: a turn with no
                                            # user text but WITH a fresh user image (screenshot/camera) is
                                            # still a user turn — count its attachments as input so it is
                                            # NOT mis-marked proactive (which, with the feature off, would
                                            # drop the image task). Only a genuinely self-initiated turn
                                            # (no text, no image) is proactive.
                                            _turn_had_user_input = had_user_input_this_turn or bool(selected_pending_user_images)
                                            sent = await _publish_analyze_request_with_fallback(
                                                lanlan_name=lanlan_name,
                                                trigger="turn_end",
                                                messages=recent,
                                                conversation_id=uuid.uuid4().hex,
                                                had_user_input=_turn_had_user_input,
                                                language=_current_analyze_language(),
                                            )
                                            if sent:
                                                logger.debug(f"[{lanlan_name}] analyze_request dispatch success (turn_end), messages={len(recent)}")
                                            else:
                                                logger.info(f"[{lanlan_name}] analyze_request dispatch failed (turn_end), messages={len(recent)}")
                                    except asyncio.TimeoutError:
                                        logger.debug(f"[{lanlan_name}] 发送到analyzer超时")
                                    except RuntimeError as e:
                                        if "shutdown" in str(e).lower() or "closed" in str(e).lower():
                                            logger.info(f"[{lanlan_name}] 进程正在关闭，跳过analyzer请求")
                                        else:
                                            logger.debug(f"[{lanlan_name}] 发送到analyzer失败: {e}")
                                    except Exception as e:
                                        logger.debug(f"[{lanlan_name}] 发送到analyzer失败: {e}")
                                    finally:
                                        pending_user_images = remaining_pending_user_images

                                # Lightweight turn-end cache: write recent history only, without LLM summarization/organization.
                                # Do not cache proactive chat; persist with the next ordinary turn after the user's response.
                                if had_user_input_this_turn and not shutdown_event.is_set() and last_synced_index < len(chat_history):
                                    new_messages = chat_history[last_synced_index:]
                                    _memory_language, _memory_render_language = (
                                        _current_memory_languages()
                                    )
                                    try:
                                        ok, err_detail, _ = await _post_memory_server(
                                            "cache",
                                            lanlan_name,
                                            new_messages,
                                            timeout_s=10.0,
                                            language=_memory_language,
                                            render_language=_memory_render_language,
                                        )
                                        if ok:
                                            _mark_memory_cache_success(
                                                lanlan_name,
                                                MEMORY_CACHE_SCOPE_TURN_END,
                                                memory_cache_health_state,
                                            )
                                            last_synced_index = len(chat_history)
                                        else:
                                            _mark_memory_cache_business_failure(
                                                lanlan_name,
                                                MEMORY_CACHE_SCOPE_TURN_END,
                                                err_detail,
                                                memory_cache_health_state,
                                            )
                                    except Exception as e:
                                        _mark_memory_cache_exception(
                                            lanlan_name,
                                            MEMORY_CACHE_SCOPE_TURN_END,
                                            e,
                                            memory_cache_health_state,
                                        )

                            elif message["data"] == 'session end': # Current session ended.
                                is_context_reset_barrier = callable(
                                    message.get("_after_memory_settlement")
                                )
                                # Skip network operations during shutdown.
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过session end处理")
                                    break
                                await original_output.finish(lanlan_name)
                                
                                # First process any unfinished user-input buffers.
                                if user_input_cache:
                                    _append_user_input_cache_to_history(chat_history, user_input_cache, user_input_sources, user_input_proof)
                                    user_input_cache = ''
                                
                                # Then process any unfinished output buffers.
                                current_turn = 'user'
                                text_output_cache = _normalized_reply(text_output_cache)
                                if len(text_output_cache) > 0:
                                    chat_history.append(
                                        _assistant_history_message(text_output_cache, text_output_proof))
                                text_output_cache = ''
                                text_output_request_id = None
                                text_output_proof = None
                                current_turn_start_index = len(chat_history)
                                # Merge unsynced consecutive proactive-chat messages.
                                merge_unsynced_tail_assistants(chat_history, last_synced_index)

                                # Send recent conversation to tool_server for potential-task analysis, matching turn-end handling.
                                # Recheck shutdown state.
                                if not shutdown_event.is_set():
                                    try:
                                        # Build recent-message summaries and retain this turn's latest image attachments.
                                        recent = _build_recent_analyze_messages(
                                            chat_history,
                                            _select_pending_user_images_for_session_end(
                                                pending_user_images,
                                                message.get("request_id"),
                                            ),
                                            allow_attach_to_last_user=had_user_input_this_turn,
                                        )
                                        has_user = any(m.get('role') == 'user' for m in recent)
                                        latest_user_is_avatar_drop = _latest_user_message_has_source(
                                            recent,
                                            AVATAR_DROP_SOURCE,
                                        )
                                        if recent and has_user and latest_user_is_avatar_drop:
                                            logger.info(f"[{lanlan_name}] analyze_request skipped (avatar_drop session_end), messages={len(recent)}")
                                        elif recent and has_user:
                                            sent = await _publish_analyze_request_with_fallback(
                                                lanlan_name=lanlan_name,
                                                trigger="session_end",
                                                messages=recent,
                                                conversation_id=uuid.uuid4().hex,
                                                language=_current_analyze_language(),
                                                # session_end is terminal — never treated as proactive
                                                # (had_user_input defaults True), so it always takes the
                                                # ordinary user-turn path.
                                            )
                                            if sent:
                                                logger.info(f"[{lanlan_name}] analyze_request dispatch success (session_end), messages={len(recent)}")
                                            else:
                                                logger.info(f"[{lanlan_name}] analyze_request dispatch failed (session_end), messages={len(recent)}")
                                    except asyncio.TimeoutError:
                                        logger.debug(f"[{lanlan_name}] 发送到analyzer超时 (session end)")
                                    except RuntimeError as e:
                                        if "shutdown" in str(e).lower() or "closed" in str(e).lower():
                                            logger.info(f"[{lanlan_name}] 进程正在关闭，跳过analyzer请求")
                                        else:
                                            logger.debug(f"[{lanlan_name}] 发送到analyzer失败: {e} (session end)")
                                    except Exception as e:
                                        logger.debug(f"[{lanlan_name}] 发送到analyzer失败: {e} (session end)")
                                    finally:
                                        pending_user_images = []
                                
                                # Recheck shutdown state.
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过 session end 收尾")
                                    chat_history.clear()
                                    last_synced_index = 0
                                    break
                                
                                # Ordinary-session settlement: /process with new messages, otherwise /settle.
                                # The language-switch barrier authoritatively clears recent.json afterward, so skip
                                # summaries destined for deletion; write only unsynced tails through lightweight /cache into
                                # the time index/outbox. Without a tail, proceed directly to cleanup callbacks.
                                remaining = chat_history[last_synced_index:]
                                logger.info(f"[{lanlan_name}] 会话结束：聊天历史 {len(chat_history)} 条，增量 {len(remaining)} 条")
                                if is_context_reset_barrier:
                                    _settle_endpoint = "cache" if remaining else None
                                else:
                                    _settle_endpoint = "process" if remaining else "settle"
                                _settle_payload = remaining if remaining else []
                                if _settle_endpoint is not None and not shutdown_event.is_set():
                                    _memory_language, _memory_render_language = (
                                        _current_memory_languages()
                                    )
                                    try:
                                        ok, err_detail, _ = await _post_memory_server(
                                            _settle_endpoint,
                                            lanlan_name,
                                            _settle_payload,
                                            timeout_s=(
                                                10.0
                                                if is_context_reset_barrier
                                                else 30.0
                                            ),
                                            language=_memory_language,
                                            render_language=_memory_render_language,
                                        )
                                        if not ok:
                                            logger.warning(f"[{lanlan_name}] session end 记忆结算失败 ({_settle_endpoint}): {err_detail}")
                                            if status_callback:
                                                try:
                                                    status_callback(f"⚠️ 记忆摘要失败: {err_detail}")
                                                except Exception:
                                                    pass
                                        else:
                                            logger.info(f"[{lanlan_name}] session end 记忆结算完成（{_settle_endpoint}），{len(_settle_payload)} 条消息")
                                    except Exception as e:
                                        logger.warning(f"[{lanlan_name}] session end 记忆结算失败 ({_settle_endpoint}): {e}")
                                        if status_callback:
                                            try:
                                                status_callback(f"⚠️ 记忆结算异常: {type(e).__name__}")
                                            except Exception:
                                                pass
                                chat_history.clear()
                                last_synced_index = 0
                        except Exception as e:
                            logger.error(f"[{lanlan_name}] System message error: {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"[{lanlan_name}] Message processing error: {e}", exc_info=True)
                finally:
                    is_memory_barrier = (
                        isinstance(message, dict)
                        and message.get("type") == "system"
                        and message.get("data") == "session end"
                        and (
                            callable(message.get("_after_memory_settlement"))
                            or isinstance(
                                message.get("_memory_settlement_done"),
                                asyncio.Future,
                            )
                        )
                    )
                    if is_memory_barrier:
                        # Even when terminal persistence failed unexpectedly, the
                        # old connector-local tail must not survive the isolation
                        # callback and leak into a later session.  Normal session
                        # ends already clear these fields in their success path;
                        # repeating the reset here makes the barrier fail closed.
                        chat_history.clear()
                        last_synced_index = 0
                        user_input_cache = ''
                        user_input_sources.clear()
                        text_output_cache = ''
                        text_output_request_id = None
                        text_output_proof = None
                        current_turn = 'user'
                        had_user_input_this_turn = False
                        current_turn_start_index = 0
                        pending_user_images = []
                        await _complete_session_end_memory_barrier(
                            message,
                            lanlan_name,
                        )

            # WS maintenance lives in independent _slot_maintainer tasks; the main loop no longer inspects it.

    except asyncio.CancelledError:
        raise
    finally:
        # Completed replies may still wait out their late-transcript grace.
        try:
            await original_output.flush_terminated()
        except Exception:
            print("[chat-sync] original_save_failed")
        # Cleanup ordering: cancel maintainers/readers first to stop new connections/read loops,
        # then close existing sockets/sessions. Maintainers own session references; after cancellation,
        # retrieve them from slots and close together.
        for s in slots:
            if s.maintainer is not None:
                s.maintainer.cancel()
            if s.reader is not None:
                s.reader.cancel()

        bg_tasks = [t for s in slots for t in (s.maintainer, s.reader) if t is not None]
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)

        await asyncio.gather(
            *(_safe_close(s.ws) for s in slots),
            *(_safe_close(s.session) for s in slots),
            return_exceptions=True,
        )
        # Do not call aclose_internal_http_client_current_loop() here.
        # Old subprocesses/dedicated threads owned their loops and per-loop cached HTTP clients,
        # requiring closure on exit. Now merged into the main loop, clients are shared with main code;
        # we do not own them and must not close them.
