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

``idempotency_key`` / ``client_requested_at`` / ``subject_epochs`` on
``scoped_history`` and ``forget_epoch`` on ``scoped_forget`` are sent only
when not ``None``: a ``None`` leaves the request body byte-identical to a
caller that never heard of them. A keyed ``scoped_history`` retry the server
already completed answers ``duplicate: true`` in the same success shape, so
it reads as a success here.

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
from collections.abc import Awaitable, Callable, Iterable, Sequence
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

# Per-endpoint single-request timeouts. scoped_history waits for one server-side LLM extraction; the others are local
# reads/writes; values match the QQ memory_bridge at revision b0b283e34.
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


# Subject keys per GET /forget_epochs (the server rejects larger requests).
_FORGET_EPOCHS_BATCH = 64


class ScopedMemoryError(RuntimeError):
    """A scoped memory read could not produce a result.

    Raised by the read methods (``fetch_bootstrap`` / ``list_scoped_subjects``)
    on transport errors, non-2xx answers and malformed payloads. The write
    methods report the same conditions as ``False`` instead.
    """


class ForgetTargetEpochsRequired(ScopedMemoryError):
    """An opted-in ``scoped_forget`` was refused: some fan-out targets had no epoch.

    ``missing`` lists their subject keys. Nothing was erased; the caller
    bumps each missing key's own forget generation, persists it, and resends
    with those keys added to ``target_epochs``.
    """

    def __init__(self, missing: Sequence[str]) -> None:
        self.missing: tuple[str, ...] = tuple(missing)
        super().__init__(f"scoped_forget needs epochs for fan-out targets: {list(self.missing)}")


# Same bound the server applies to ScopedForgetRequest.target_epochs.
_FORGET_TARGET_EPOCHS_MAX = 64


def _check_target_epochs(subject: dict, forget_epoch: int | None, target_epochs: dict[str, int]) -> None:
    """Raise ``ValueError`` for a ``target_epochs`` the server always answers with 422."""
    if forget_epoch is None:
        raise ValueError("target_epochs requires forget_epoch")
    if len(target_epochs) > _FORGET_TARGET_EPOCHS_MAX:
        raise ValueError("too many target_epochs keys")
    for key, value in target_epochs.items():
        if not isinstance(key, str) or not key or len(key) > 512 or not key.isprintable():
            raise ValueError("invalid target_epochs key")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("target_epochs values must be non-negative integers")
    # Like the server's MemorySubject.create, strip leading/trailing whitespace; otherwise the key will not match and this check will miss it
    subject_key = f"{str(subject.get('subject_kind') or '').strip()}:{str(subject.get('subject_id') or '').strip()}"
    if subject_key in target_epochs and target_epochs[subject_key] != forget_epoch:
        raise ValueError("target_epochs disagrees with forget_epoch")


def _missing_target_epochs(response: httpx.Response) -> list[str] | None:
    """The fan-out keys a 409 ``scoped_forget`` answer names, or None for any other 409."""
    try:
        payload = _response_json(response)
    except ValueError:
        return None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if not isinstance(detail, dict) or detail.get("error") != "missing_target_epochs":
        return None
    missing = detail.get("missing_target_epochs")
    if not isinstance(missing, list) or not missing or not all(isinstance(k, str) and k for k in missing):
        return None
    return list(missing)


def encode_wire_body(payload: dict[str, Any]) -> bytes:
    """Serialize a request body exactly as it goes on the wire."""
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _encode_history(messages: Sequence[dict[str, Any]]) -> str:
    # Byte-for-byte identical to the QQ version: input_history is a JSON string, not a nested object,
    # Use the default separator and ensure_ascii=False.
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

    def with_retry_delays(self, retry_delays: Sequence[float]) -> "ScopedMemoryClient":
        """Return a client for the same server and HTTP client with another 502 back-off."""
        return ScopedMemoryClient(
            base_url=self._base_url, http=self._http,
            retry_delays=retry_delays, sleep=self._sleep,
        )

    # ------------------------------------------------------------------ wire

    def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        return get_internal_http_client()

    def _url(self, lanlan: str, endpoint: str) -> str:
        # The character name is a path segment: quote(safe="") prevents "/", "?", "#", and similar characters from rewriting the route or
        # forming a query string; non-ASCII names are percent-encoded as UTF-8 (same method as cross_server).
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
        params: dict[str, str] | list[tuple[str, str]] | None = None,
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
        # Truncate in a thread: the upstream may return one very long merged reflection, and tiktoken on
        # an unsplittable oversized chunk can trigger a second fallback; running synchronously would block the event loop
        # (the QQ memory_bridge.query_relevant_memory at revision b0b283e34 has the same behavior).
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
            payload = _response_json(response)
        except ValueError as exc:
            raise ScopedMemoryError("scoped_subjects returned invalid JSON") from exc
        subjects = payload.get("subjects") if isinstance(payload, dict) else None
        if not isinstance(subjects, list):
            raise ScopedMemoryError("scoped_subjects returned no subjects list")
        return [row for row in subjects if isinstance(row, dict)]

    async def get_forget_epochs(
        self, lanlan: str, subject_keys: Iterable[str],
    ) -> dict[str, int]:
        """Current server-side forget fence of each subject key.

        Read-only ``GET .../forget_epochs?subject=...`` answering
        ``{"epochs": {key: epoch}}``; keys without a tombstone are absent.
        Anything else (transport error, non-2xx, malformed body) raises
        ``ScopedMemoryError``: an unknown fence must not read as none.
        """
        keys = list(dict.fromkeys(subject_keys))
        url = self._url(lanlan, "forget_epochs")
        merged: dict[str, int] = {}
        # The server accepts at most _FORGET_EPOCHS_BATCH keys per request: query in batches and merge the results
        for start in range(0, len(keys), _FORGET_EPOCHS_BATCH):
            batch = keys[start:start + _FORGET_EPOCHS_BATCH]
            try:
                response = await self._send(
                    "GET", url, params=[("subject", key) for key in batch],
                    timeout=_READ_TIMEOUT_S, retry=False,
                )
            except httpx.HTTPError as exc:
                raise ScopedMemoryError(f"forget_epochs failed: {exc}") from exc
            if not response.is_success:
                raise ScopedMemoryError(f"forget_epochs failed: HTTP {response.status_code}")
            try:
                payload = _response_json(response)
            except ValueError as exc:
                raise ScopedMemoryError("forget_epochs returned invalid JSON") from exc
            epochs = payload.get("epochs") if isinstance(payload, dict) else None
            if not isinstance(epochs, dict) or not all(
                isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for key, value in epochs.items()
            ):
                raise ScopedMemoryError("forget_epochs returned a malformed epochs map")
            merged.update({key: value for key, value in epochs.items() if key in batch})
        return merged

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
        with no request, as on the server side. Otherwise ``True`` only when
        the server answers ``status: "recorded"`` (or ``"skipped"``); a
        truncated, non-JSON or wrong-shaped 2xx body is a failed write.
        """
        if not subjects or not str(response_text or "").strip():
            return True
        body = {"response_text": response_text, "subjects": list(subjects)}
        response = await self._post_write(
            self._url(lanlan, "scoped_mentions"), body,
            timeout=_MENTIONS_TIMEOUT_S, what="scoped_mentions",
        )
        if response is None:
            return False
        try:
            payload = _response_json(response)
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or payload.get("status") not in ("recorded", "skipped"):
            # Without explicit confirmation, do not count it as recorded: the caller will discard this update, but the repetition count would not increase
            logger.warning("scoped_mentions: response does not confirm the update")
            return False
        return True

    async def post_forget(
        self,
        lanlan: str,
        *,
        subject: dict,
        forget_epoch: int | None = None,
        target_epochs: dict[str, int] | None = None,
    ) -> bool:
        """Erase everything stored for one exact subject. Idempotent.

        ``forget_epoch`` is the client-side erase generation of this subject
        (sent only when not ``None``); the server keeps it as a tombstone and
        drops keyed history products stamped with a lower generation.

        ``target_epochs`` (sent only when not ``None``, requires
        ``forget_epoch``) is ``{subject_key: generation}`` for the other
        subjects identity expansion erases along with this one, each from
        its own counter, so each gets a tombstone in its own epoch domain.
        Opting in means the server refuses (HTTP 409, nothing erased) a map
        that misses a fan-out target; that answer raises
        :class:`ForgetTargetEpochsRequired` carrying the missing keys, so the
        caller can bump those counters and resend. A NEW forget must bump
        EVERY target's own counter (not only the missing ones): a target
        whose completion marker already reaches the epoch sent for it is
        treated as a replay and is not erased again. A ``target_epochs`` the
        server always refuses (no ``forget_epoch``, a value for this subject
        that differs from ``forget_epoch``, too many or malformed keys or
        epochs) raises ``ValueError`` before anything is sent.

        ``True`` only when the server confirms ``status: "forgotten"``; a
        truncated, non-JSON or wrong-shaped 2xx body is a failed erase.
        """
        if target_epochs is not None:
            # The server always returns 422 for these requests: treating it as an ordinary failure and returning False would make the revocation flow retry
            # a request that can never succeed. This is a caller programming error; raise directly
            _check_target_epochs(subject, forget_epoch, target_epochs)
        body: dict[str, Any] = {"subject": subject}
        if forget_epoch is not None:
            body["forget_epoch"] = forget_epoch
        url = self._url(lanlan, "scoped_forget")
        if target_epochs is None:
            response = await self._post_write(
                url, body, timeout=_FORGET_TIMEOUT_S, what="scoped_forget",
            )
        else:
            body["target_epochs"] = dict(target_epochs)
            response = await self._post_forget_with_targets(url, body)
        if response is None:
            return False
        try:
            payload = _response_json(response)
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or payload.get("status") != "forgotten":
            # Without explicit confirmation, it cannot be marked complete: the revocation flow would then delete the roster and logs, losing its retry path
            logger.warning("scoped_forget: response does not confirm the erase")
            return False
        return True

    async def _post_forget_with_targets(
        self, url: str, body: dict[str, Any],
    ) -> httpx.Response | None:
        """POST an opted-in forget; a 409 naming missing fan-out targets raises."""
        try:
            response = await self._send(
                "POST", url, body=body, timeout=_FORGET_TIMEOUT_S, retry=True,
            )
        except httpx.HTTPError as exc:
            logger.warning("scoped memory scoped_forget failed: %s", exc)
            return None
        if response.status_code == 409:
            missing = _missing_target_epochs(response)
            if missing is not None:
                raise ForgetTargetEpochsRequired(missing)
        if not response.is_success:
            logger.warning(
                "scoped memory scoped_forget failed: HTTP %s", response.status_code,
            )
            return None
        return response

    async def post_history(
        self,
        lanlan: str,
        *,
        subject: dict,
        messages: list[dict],
        idempotency_key: str | None = None,
        client_requested_at: float | None = None,
        subject_epochs: dict[str, int] | None = None,
        speaker_label: str | None = None,
        speaker_tier: str | None = None,
        speaker_activity_events: list[dict] | None = None,
        speaker_channel: str | None = None,
        speaker_id: str | None = None,
        speaker_is_owner: bool = False,
        display_name: str | None = None,
        language: str | None = None,
    ) -> bool:
        """Extract scoped facts from one subject's history batch.

        The single-subject shape of ``/scoped_history``. The speaker fields
        follow the QQ reference: each is sent only when it carries a value.
        ``language`` (the language the history was recorded in) is sent only
        when it is a supported code, like ``scoped_context``.
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
        if is_supported_language_code(language):
            body["language"] = language
        _put_retry_identity(body, idempotency_key, client_requested_at, subject_epochs)
        response = await self._post_write(
            self._url(lanlan, "scoped_history"), body,
            timeout=_HISTORY_TIMEOUT_S, what="scoped_history",
        )
        if response is None:
            return False
        try:
            payload = _response_json(response)
        except ValueError:
            # A truncated or HTML-like 2xx response does not prove extraction and trust data were written: treat as failure so the caller retries
            logger.warning("scoped_history returned a non-JSON body; keep and retry")
            return False
        if not isinstance(payload, dict) or payload.get("status") != "processed":
            # A 2xx response such as null / [] / {} does not prove this batch was processed: treat as failure so the caller retries
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
        subject_epochs: dict[str, int] | None = None,
        language: str | None = None,
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
        batch. A keyed batch (``idempotency_key``) succeeds or fails as a
        whole: any unsettled position marks every position failed, and the
        caller retries the identical batch under the same key. An empty ``segments`` raises ``ValueError`` without a request
        (the server rejects it and there would be nothing to retry).
        """
        if not segments:
            # The server always returns 422 for an empty batch, and the result has no retryable positions: the caller's "retry failed positions only"
            # loop would hang forever. Report this as a caller error
            raise ValueError("post_history_batch needs at least one segment")
        wire_segments = [_wire_segment(segment) for segment in segments]
        body: dict[str, Any] = {"segments": wire_segments}
        # Same shape as the single-item request: include only supported language codes (a batch shares one language)
        if is_supported_language_code(language):
            body["language"] = language
        _put_retry_identity(body, idempotency_key, client_requested_at, subject_epochs)
        response = await self._post_write(
            self._url(lanlan, "scoped_history"), body,
            timeout=_HISTORY_TIMEOUT_S, what="scoped_history segments",
        )
        none_ok = ScopedBatchResult(tuple(False for _ in wire_segments))
        if response is None:
            return none_ok
        try:
            payload = _response_json(response)
        except ValueError:
            logger.warning("scoped_history segments returned invalid JSON")
            return none_ok
        if not isinstance(payload, dict) or payload.get("status") != "processed":
            # The server sets top-level processed only after the entire batch is written to disk; without it, per-item results are untrustworthy
            logger.warning("scoped_history segments: response does not confirm processing")
            return none_ok
        results = payload.get("segments")
        if not isinstance(results, list) or len(results) != len(wire_segments):
            logger.warning("scoped_history segments returned a mismatched result list")
            return none_ok
        outcome = ScopedBatchResult(tuple(
            isinstance(result, dict) and result.get("status") == "ok" and _trust_settled(result)
            for result in results
        ))
        if idempotency_key is not None and outcome.failed_positions:
            # For keyed batches, the server succeeds or fails for the entire key (if the trust pool is not persisted, the whole key remains pending): retry the whole batch for that key only,
            # because a subset request containing only failed positions does not match the key's recorded request identity and will receive 422. Treat the entire batch as failed
            outcome = none_ok
        if outcome.failed_positions:
            logger.warning(
                "scoped_history segments not extracted: positions %s",
                list(outcome.failed_positions),
            )
        return outcome


def _response_json(response: httpx.Response) -> Any:
    """``response.json()`` with a deep-nesting ``RecursionError`` raised as ``ValueError``."""
    try:
        return response.json()
    except RecursionError as exc:
        # A deeply nested 2xx response body ("[[[[…") is malformed like any other bad JSON: callers handle only ValueError,
        # so leaking RecursionError would interrupt startup / revocation / backfill instead of retaining a retry on failure
        raise ValueError("response JSON is too deeply nested") from exc


def _trust_settled(result: Any) -> bool:
    """True only when the trust block confirms the write (``persisted`` true / null).

    ``app/memory_server/routes.py::_trust_response_block``: the server always
    sends a ``trust`` object with a ``persisted`` key; ``ok`` with ``persisted``
    true / null may be dropped, ``ok`` with ``persisted`` false must be kept
    and retried, otherwise an owner trust correction is lost. A missing,
    non-object or incomplete block is a truncated / malformed response and
    is treated like ``false``.
    """
    trust = result.get("trust") if isinstance(result, dict) else None
    # Missing block / non-object / missing persisted: without confirmation, do not discard; treat as not persisted
    # Compare by identity: 1 / 1.0 equals True, so tuple membership would treat them as "persisted"
    return isinstance(trust, dict) and "persisted" in trust and (
        trust["persisted"] is True or trust["persisted"] is None
    )


def _put_retry_identity(
    body: dict[str, Any],
    idempotency_key: str | None,
    client_requested_at: float | None,
    subject_epochs: dict[str, int] | None = None,
) -> None:
    # When None, omit all these keys: without them, the request body must remain byte-for-byte compatible with old callers;
    # when non-None, include them unchanged, with no rewriting.
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    if client_requested_at is not None:
        body["client_requested_at"] = client_requested_at
    if subject_epochs is not None:
        body["subject_epochs"] = dict(subject_epochs)


def _wire_segment(segment: dict[str, Any]) -> dict[str, Any]:
    # Field order and omission rules match the QQ post_scoped_memory_history_batch at revision b0b283e34.
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
