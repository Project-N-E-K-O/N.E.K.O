# -*- coding: utf-8 -*-
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

"""In-repo client for memory_server's scoped ``/internal/memory/*`` endpoints.

Until the QQ auto-reply plugin left the repo (#2996) its ``memory_bridge`` was
the only caller of the scoped endpoints; nothing in the main repo could reach
them. This module is the shared replacement: one thin wire client that any
in-repo feature (the visit runtime first) can import.

Wire contract
-------------
The request models in ``app/memory_server/routes.py`` are the source of truth
(``ScopedContextRequest``, ``ScopedMentionsRequest``, ``ScopedForgetRequest``,
``ScopedHistoryRequest`` / ``ScopedHistorySegment``). The ``b0b283e34``
version of the QQ ``memory_bridge`` is the reference implementation, and its
omission rules are kept: ``language`` is sent only when it is a supported
locale code, and optional speaker fields are sent only when they carry a
value, so an absent field keeps its server default instead of being pinned to
an explicit null.

``idempotency_key`` / ``client_requested_at`` on ``scoped_history`` are sent
only when not ``None``. The server learns those two fields in a later change;
until then a ``None`` must leave the request body byte-identical to a caller
that never heard of them.

Bodies are serialized here, not by httpx (``json.dumps`` compact separators,
``ensure_ascii=False``, UTF-8), so the bytes on the wire do not depend on the
installed httpx version. ``tests/unit/test_scoped_client_wire.py`` pins them
byte-for-byte against fixtures.

Layering: this is an L2 ``memory`` module and imports only ``utils`` (L1),
the standard library and third-party packages. It must never import
``app`` / ``main_logic`` / ``main_routers``, not even inside a function.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from utils.http.internal_client import get_internal_http_client
from utils.language_utils import is_supported_language_code
from utils.logger_config import get_module_logger
from utils.tokenize import atruncate_to_tokens

logger = get_module_logger(__name__, "Memory")

#: Retry schedule for writes answered with HTTP 502. memory_server answers
#: ``/scoped_history`` with 502 when the LLM extraction fails ("retry later");
#: the call is re-sent after each delay in turn, so at most ``len()`` retries.
SCOPED_WRITE_RETRY_DELAYS_S: tuple[float, ...] = (5.0, 15.0, 45.0)
_RETRYABLE_STATUS = 502

# 各端点的单次请求超时。scoped_history 要等服务端一次 LLM 抽取，其余是本地
# 读写；数值与 b0b283e34 版 QQ memory_bridge 一致。
_READ_TIMEOUT_S = 5.0
_MENTIONS_TIMEOUT_S = 5.0
_FORGET_TIMEOUT_S = 30.0
_HISTORY_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class ScopedBatchResult:
    """Per-segment outcome of a batched ``/scoped_history`` call.

    ``segments_ok[i]`` tells whether request segment ``i`` was extracted
    (server status ``"ok"``); a transport failure, a non-2xx answer or a
    malformed response marks every segment False. The server commits the
    successful segments on its own, so a caller should retry only the
    failed positions. Truthiness is :attr:`ok` (every segment succeeded).
    """

    segments_ok: tuple[bool, ...]

    @property
    def ok(self) -> bool:
        """True when the batch is non-empty and every segment succeeded."""
        return bool(self.segments_ok) and all(self.segments_ok)

    @property
    def failed_positions(self) -> tuple[int, ...]:
        """Request positions that still need a retry."""
        return tuple(i for i, done in enumerate(self.segments_ok) if not done)

    def __bool__(self) -> bool:
        return self.ok


class ScopedMemoryError(RuntimeError):
    """A scoped memory read could not produce a result.

    Raised by the read methods (``fetch_bootstrap`` / ``list_scoped_subjects``)
    on transport errors, non-2xx answers and malformed payloads. The write
    methods report the same conditions as ``False`` instead.
    """


def encode_wire_body(payload: dict[str, Any]) -> bytes:
    """Serialize a request body exactly as it goes on the wire."""
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _encode_history(messages: Sequence[dict[str, Any]]) -> str:
    # 与 QQ 版逐字节一致：input_history 是 JSON 字符串而非嵌套对象，
    # 用默认分隔符 + ensure_ascii=False。
    return json.dumps(list(messages), ensure_ascii=False)


class ScopedMemoryClient:
    """Thin async client for memory_server's scoped memory endpoints.

    ``base_url`` is memory_server's origin, e.g. ``http://127.0.0.1:48912``.
    ``http`` defaults to the process-wide internal client, looked up per
    request because that client is bound to the running event loop. The
    client never closes ``http``: its lifetime belongs to whoever created it.

    ``retry_delays`` / ``sleep`` exist so tests can observe the 502 back-off
    without waiting for it.
    """

    def __init__(
        self,
        *,
        base_url: str,
        http: httpx.AsyncClient | None = None,
        retry_delays: Sequence[float] = SCOPED_WRITE_RETRY_DELAYS_S,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._http = http
        self._retry_delays = tuple(float(delay) for delay in retry_delays)
        self._sleep = sleep

    # ------------------------------------------------------------------ wire

    def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        return get_internal_http_client()

    def _url(self, lanlan: str, endpoint: str) -> str:
        # 角色名作路径段：quote(safe="") 让 "/" "?" "#" 之类无法改写路由或
        # 拼出查询串；非 ASCII 名按 UTF-8 百分号编码（与 cross_server 同法）。
        return (
            f"{self._base_url}/internal/memory/"
            f"{quote(str(lanlan), safe='')}/{endpoint}"
        )

    async def _send(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        timeout: float,
        retry: bool,
    ) -> httpx.Response:
        """Send one request, re-sending on 502 when ``retry`` is set.

        Transport errors propagate as ``httpx.HTTPError``; the final response
        (2xx or not) is returned to the caller to judge.
        """
        content = encode_wire_body(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else None
        delays = list(self._retry_delays) if retry else []
        while True:
            response = await self._client().request(
                method,
                url,
                content=content,
                headers=headers,
                params=params,
                timeout=timeout,
            )
            if response.status_code != _RETRYABLE_STATUS or not delays:
                return response
            delay = delays.pop(0)
            logger.info(
                "scoped memory %s answered 502; retrying in %.0f s (%d left)",
                url, delay, len(delays),
            )
            await self._sleep(delay)

    async def _post_write(
        self, url: str, body: dict[str, Any], *, timeout: float, what: str,
    ) -> httpx.Response | None:
        """POST a write with the 502 back-off; ``None`` means it failed."""
        try:
            response = await self._send(
                "POST", url, body=body, timeout=timeout, retry=True,
            )
        except httpx.HTTPError as exc:
            logger.warning("scoped memory %s failed: %s", what, exc)
            return None
        if not response.is_success:
            logger.warning(
                "scoped memory %s failed: HTTP %s", what, response.status_code,
            )
            return None
        return response

    # ------------------------------------------------------------------ reads

    async def fetch_bootstrap(
        self,
        lanlan: str,
        *,
        subjects: list[dict],
        lang: str | None,
        include_legacy_private: bool = False,
        max_tokens: int,
    ) -> str:
        """Render the scoped persona/reflection context for ``subjects``.

        ``subjects`` order is the server's budget priority (1..8 items).
        ``lang`` is sent only when it is a supported locale code; otherwise
        the server restores the durable per-subject locale.

        The scoped endpoint never serves the legacy private corpus (the
        server hard-codes ``include_legacy_private=False``), so asking for it
        raises ``ValueError`` instead of being silently ignored.

        ``max_tokens`` is applied here, to the rendered text: the endpoint
        has no budget field of its own. No 502 back-off: this sits on a
        session-start path that cannot wait a minute for optional context.
        """
        if include_legacy_private:
            raise ValueError(
                "scoped_context never serves the legacy private corpus; "
                "include_legacy_private must be False"
            )
        if not subjects:
            return ""
        body: dict[str, Any] = {"subjects": list(subjects)}
        if is_supported_language_code(lang):
            body["language"] = lang
        url = self._url(lanlan, "scoped_context")
        try:
            response = await self._send(
                "POST", url, body=body, timeout=_READ_TIMEOUT_S, retry=False,
            )
        except httpx.HTTPError as exc:
            raise ScopedMemoryError(f"scoped_context failed: {exc}") from exc
        if not response.is_success:
            raise ScopedMemoryError(
                f"scoped_context failed: HTTP {response.status_code}"
            )
        # 截断放在线程里：上游可能回一整段超长合并 reflection，tiktoken 对
        # 切不开的超长 chunk 是二次退化，同步跑会卡住事件循环
        # （b0b283e34 版 QQ memory_bridge.query_relevant_memory 同理）。
        return await atruncate_to_tokens(response.text.strip(), max_tokens)

    async def list_scoped_subjects(
        self, lanlan: str, *, platform: str,
    ) -> list[dict]:
        """List the stored scoped subjects of one platform.

        Read-only ``GET .../scoped_subjects?platform=`` answering
        ``{"subjects": [...]}``. Non-dict rows are dropped; anything else
        malformed raises ``ScopedMemoryError``.
        """
        url = self._url(lanlan, "scoped_subjects")
        try:
            response = await self._send(
                "GET", url, params={"platform": platform},
                timeout=_READ_TIMEOUT_S, retry=False,
            )
        except httpx.HTTPError as exc:
            raise ScopedMemoryError(f"scoped_subjects failed: {exc}") from exc
        if not response.is_success:
            raise ScopedMemoryError(
                f"scoped_subjects failed: HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ScopedMemoryError("scoped_subjects returned invalid JSON") from exc
        subjects = payload.get("subjects") if isinstance(payload, dict) else None
        if not isinstance(subjects, list):
            raise ScopedMemoryError("scoped_subjects returned no subjects list")
        return [row for row in subjects if isinstance(row, dict)]

    # ----------------------------------------------------------------- writes

    async def post_mentions(
        self,
        lanlan: str,
        *,
        subjects: list[dict],
        response_text: str,
    ) -> bool:
        """Bump mention counters of ``subjects``' entries echoed in a reply.

        Nothing to record (no subjects, blank text) is a successful no-op
        with no request, as on the server side.
        """
        if not subjects or not str(response_text or "").strip():
            return True
        body = {"response_text": response_text, "subjects": list(subjects)}
        response = await self._post_write(
            self._url(lanlan, "scoped_mentions"), body,
            timeout=_MENTIONS_TIMEOUT_S, what="scoped_mentions",
        )
        return response is not None

    async def post_forget(self, lanlan: str, *, subject: dict) -> bool:
        """Erase everything stored for one exact subject. Idempotent."""
        response = await self._post_write(
            self._url(lanlan, "scoped_forget"), {"subject": subject},
            timeout=_FORGET_TIMEOUT_S, what="scoped_forget",
        )
        return response is not None

    async def post_history(
        self,
        lanlan: str,
        *,
        subject: dict,
        messages: list[dict],
        idempotency_key: str | None = None,
        client_requested_at: float | None = None,
        speaker_label: str | None = None,
        speaker_tier: str | None = None,
        speaker_activity_events: list[dict] | None = None,
        speaker_channel: str | None = None,
        speaker_id: str | None = None,
        speaker_is_owner: bool = False,
        display_name: str | None = None,
    ) -> bool:
        """Extract scoped facts from one subject's history batch.

        The single-subject shape of ``/scoped_history``. The speaker fields
        follow the QQ reference: each is sent only when it carries a value.
        """
        body: dict[str, Any] = {
            "input_history": _encode_history(messages),
            "subject": subject,
        }
        if speaker_label:
            body["speaker_label"] = speaker_label
        if speaker_tier is not None:
            body["speaker_tier"] = speaker_tier
        if speaker_activity_events:
            body["speaker_activity_events"] = speaker_activity_events
        if speaker_channel:
            body["speaker_channel"] = speaker_channel
        if speaker_id:
            body["speaker_id"] = speaker_id
        if speaker_is_owner:
            body["speaker_is_owner"] = True
        if display_name:
            body["display_name"] = display_name
        _put_retry_identity(body, idempotency_key, client_requested_at)
        response = await self._post_write(
            self._url(lanlan, "scoped_history"), body,
            timeout=_HISTORY_TIMEOUT_S, what="scoped_history",
        )
        if response is None:
            return False
        try:
            payload = response.json()
        except ValueError:
            # 截断 / HTML 之类的 2xx 不能证明抽取与信赖写入已完成：按失败，调用方重试
            logger.warning("scoped_history returned a non-JSON body; keep and retry")
            return False
        if not isinstance(payload, dict) or payload.get("status") != "processed":
            # null / [] / {} 之类的 2xx 不能证明这批已处理：按失败，调用方重试
            logger.warning("scoped_history: response does not confirm processing")
            return False
        if not _trust_settled(payload):
            logger.warning("scoped_history: trust write not persisted, keep and retry")
            return False
        return True

    async def post_history_batch(
        self,
        lanlan: str,
        *,
        segments: list[dict],
        idempotency_key: str | None = None,
        client_requested_at: float | None = None,
    ) -> ScopedBatchResult:
        """Extract facts for several single-speaker segments in one call.

        Each segment dict is ``{"messages": [...], "subject": {...},
        "speaker_label": str}`` plus any of ``speaker_tier``,
        ``speaker_activity_events``, ``speaker_channel``, ``speaker_id``,
        ``speaker_is_owner``, ``trust_signal_excluded_fact_identities`` and
        ``display_name`` (each sent only when it carries a value).
        Returns a :class:`ScopedBatchResult` with one flag per segment (truthy
        only when every segment came back ``"ok"``). The server commits the
        successful segments and reports them in request order, so callers
        retry only ``failed_positions`` instead of re-extracting the whole
        batch.
        """
        wire_segments = [_wire_segment(segment) for segment in segments]
        body: dict[str, Any] = {"segments": wire_segments}
        _put_retry_identity(body, idempotency_key, client_requested_at)
        response = await self._post_write(
            self._url(lanlan, "scoped_history"), body,
            timeout=_HISTORY_TIMEOUT_S, what="scoped_history segments",
        )
        none_ok = ScopedBatchResult(tuple(False for _ in wire_segments))
        if response is None:
            return none_ok
        try:
            payload = response.json()
        except ValueError:
            logger.warning("scoped_history segments returned invalid JSON")
            return none_ok
        results = payload.get("segments") if isinstance(payload, dict) else None
        if not isinstance(results, list) or len(results) != len(wire_segments):
            logger.warning("scoped_history segments returned a mismatched result list")
            return none_ok
        outcome = ScopedBatchResult(tuple(
            isinstance(result, dict) and result.get("status") == "ok" and _trust_settled(result)
            for result in results
        ))
        if outcome.failed_positions:
            logger.warning(
                "scoped_history segments not extracted: positions %s",
                list(outcome.failed_positions),
            )
        return outcome


def _trust_settled(result: Any) -> bool:
    """False when the server reports ``trust.persisted is False`` (retain and retry).

    ``app/memory_server/routes.py::_trust_response_block``: ``ok`` with
    ``persisted`` true / null may be dropped, ``ok`` with ``persisted`` false
    must be kept and retried, otherwise an owner trust correction is lost.
    """
    trust = result.get("trust") if isinstance(result, dict) else None
    return not (isinstance(trust, dict) and trust.get("persisted") is False)


def _put_retry_identity(
    body: dict[str, Any],
    idempotency_key: str | None,
    client_requested_at: float | None,
) -> None:
    # None 时两个键都不出现：服务端还不认识它们，不带时请求体须与旧调用方
    # 逐字节一致；非 None 原样带上，不做任何改写。
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    if client_requested_at is not None:
        body["client_requested_at"] = client_requested_at


def _wire_segment(segment: dict[str, Any]) -> dict[str, Any]:
    # 字段顺序与省略规则照 b0b283e34 版 QQ post_scoped_memory_history_batch。
    wire: dict[str, Any] = {
        "input_history": _encode_history(segment.get("messages") or []),
        "subject": segment.get("subject"),
        "speaker_label": segment.get("speaker_label"),
    }
    tier = segment.get("speaker_tier")
    if tier is not None:
        wire["speaker_tier"] = tier
    activity_events = segment.get("speaker_activity_events")
    if activity_events:
        wire["speaker_activity_events"] = activity_events
    channel = segment.get("speaker_channel")
    if channel:
        wire["speaker_channel"] = channel
    speaker_id = segment.get("speaker_id")
    if speaker_id:
        wire["speaker_id"] = speaker_id
    if segment.get("speaker_is_owner"):
        wire["speaker_is_owner"] = True
    excluded = segment.get("trust_signal_excluded_fact_identities")
    if excluded:
        wire["trust_signal_excluded_fact_identities"] = [
            list(identity) for identity in excluded
        ]
    display_name = segment.get("display_name")
    if display_name:
        wire["display_name"] = display_name
    return wire
