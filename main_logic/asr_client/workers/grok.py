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

"""xAI Grok streaming speech-to-text worker."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any, Literal
from urllib.parse import urlencode

import websockets

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest
from ._shared import is_auth_rejection, normalize_zh_en_language


_GROK_STT_URL = "wss://api.x.ai/v1/stt"
_CLOSE_TIMEOUT_SECONDS = 0.5
_SHUTDOWN_TIMEOUT_SECONDS = 3.0
# xAI reports a server-VAD endpoint only through the speech_final transcript
# itself; there is no separate endpoint event to arm a stalled deadline at
# (unlike the OpenAI/Step/Qwen workers, which arm at their endpoint events).
# The deadline therefore arms at the FIRST partial of a server utterance and
# every later partial refreshes it: continuous speech keeps streaming
# partials and can never expire mid-utterance, while a provider that goes
# silent after a nonterminal partial (lost speech_final) completes the turn
# within this bound instead of pinning it open forever.
_GROK_STALLED_TURN_TIMEOUT_SECONDS = 30.0

_UtteranceKey = tuple[int, int, int | None]
_ConnectionAction = Literal["reconnect", "shutdown", "failed"]


def _normalize_grok_language(language: str) -> str | None:
    return normalize_zh_en_language(language, provider_name="xAI")


def _grok_is_auth_rejection(exc: BaseException) -> bool:
    return is_auth_rejection(exc)


def _utterance_final_text(terminal_text: str, chunk_texts: list[str]) -> str:
    """Transcript for one xAI utterance that ended with ``speech_final``.

    xAI's utterance final restates the WHOLE utterance -- every chunk final
    (``is_final=true, speech_final=false``) locked inside it is included
    again. Measured against wss://api.x.ai/v1/stt: two locked chunks were
    followed by a terminal event carrying both, so concatenating the locked
    chunks onto it repeated the whole locked prefix in the transcript.
    Interim events are the opposite -- they only cover the audio after the
    last lock -- which is why the preview still concatenates and only the
    final replaces.

    An empty restatement falls back to the locked chunks so a blank terminal
    event cannot swallow speech the provider already committed.
    """
    return terminal_text or "".join(chunk_texts)


async def grok_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
) -> None:
    """Stream raw 16 kHz PCM to xAI's dedicated STT WebSocket."""

    last_generation = 0
    failure_sent = False
    ready_sent = False
    shutdown_requested = asyncio.Event()
    websocket = None
    # Worker-scoped (not connection-scoped) so a stalled-turn expiry
    # reconnect within one generation and buffer epoch can never reuse the
    # expired utterance's id on the fresh connection.
    next_server_utterance_id: int | None = None

    async def _emit_error(error_code: str, error_message: str) -> None:
        nonlocal failure_sent
        if failure_sent:
            return
        failure_sent = True
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=last_generation,
                error_code=error_code,
                error_message=error_message,
            )
        )

    async def _connect_ready(url: str):
        connection = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            close_timeout=_CLOSE_TIMEOUT_SECONDS,
        )
        try:
            while True:
                message = await connection.recv()
                if isinstance(message, bytes):
                    await _emit_error(
                        "ASR_GROK_PROTOCOL_ERROR",
                        "xAI returned an unexpected binary event",
                    )
                    await connection.close()
                    return None
                try:
                    event = json.loads(message)
                except (TypeError, json.JSONDecodeError):
                    await _emit_error(
                        "ASR_GROK_PROTOCOL_ERROR",
                        "xAI returned an invalid event",
                    )
                    await connection.close()
                    return None
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                if event_type == "transcript.created":
                    return connection
                if event_type == "error":
                    await _emit_error(
                        "ASR_GROK_ERROR",
                        "xAI streaming transcription failed",
                    )
                    await connection.close()
                    return None
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await connection.close()
            except Exception:
                pass
            raise

    async def _run_connection(connection) -> _ConnectionAction:
        nonlocal last_generation

        latest_audio_key: _UtteranceKey | None = None
        pending_manual_commits: deque[_UtteranceKey] = deque()
        # Completed utterances of a PTT hold (one entry per ``speech_final``
        # natural endpointing fired mid-hold), kept apart from the chunk
        # finals of the utterance still in flight: separate utterances
        # concatenate, chunks of one utterance are superseded by its final.
        manual_locked_segments: dict[_UtteranceKey, list[str]] = {}
        manual_chunk_segments: dict[_UtteranceKey, list[str]] = {}
        active_server_key: _UtteranceKey | None = None
        server_locked_segments: list[str] = []
        stalled_deadline: float | None = None
        stalled_deadline_armed = asyncio.Event()
        stalled_expired = False
        intentional_close = asyncio.Event()

        async def _receive_events() -> None:
            nonlocal active_server_key, next_server_utterance_id
            nonlocal stalled_deadline
            try:
                async for message in connection:
                    if isinstance(message, bytes):
                        await _emit_error(
                            "ASR_GROK_PROTOCOL_ERROR",
                            "xAI returned an unexpected binary event",
                        )
                        return
                    try:
                        event = json.loads(message)
                    except (TypeError, json.JSONDecodeError):
                        await _emit_error(
                            "ASR_GROK_PROTOCOL_ERROR",
                            "xAI returned an invalid event",
                        )
                        return
                    if not isinstance(event, dict):
                        continue

                    event_type = event.get("type")
                    if event_type == "error":
                        await _emit_error(
                            "ASR_GROK_ERROR",
                            "xAI streaming transcription failed",
                        )
                        return
                    if event_type == "transcript.done":
                        return
                    if event_type != "transcript.partial":
                        continue

                    text = event.get("text", "")
                    if not isinstance(text, str):
                        text = ""
                    is_final = event.get("is_final") is True
                    speech_final = event.get("speech_final") is True

                    if not text and not (is_final and speech_final):
                        # xAI transcribes non-speech audio as EMPTY partials
                        # rather than staying quiet: 25 s of room-noise-level
                        # input produced 34 transcript.partial events (interim
                        # AND is_final chunk finals), all with text="", and no
                        # speech_final at all. Acting on one starts a phantom
                        # utterance that no final can ever close, which stalls
                        # every real utterance queued behind it. They carry no
                        # transcript, so skipping loses nothing. A terminal
                        # event is exempt: an empty utterance final is how a
                        # real turn legitimately ends with nothing said.
                        continue

                    if config.endpointing_mode == "manual":
                        key = (
                            pending_manual_commits[0]
                            if pending_manual_commits
                            else latest_audio_key
                        )
                        if key is None:
                            continue
                        if is_final and speech_final:
                            # The terminal event restates this utterance whole,
                            # so its own chunk finals are dropped here instead
                            # of being concatenated onto it.
                            chunk_texts = manual_chunk_segments.pop(key, [])
                            utterance_text = _utterance_final_text(text, chunk_texts)
                            if pending_manual_commits:
                                final_key = pending_manual_commits.popleft()
                                segments = manual_locked_segments.pop(final_key, [])
                                segments.append(utterance_text)
                                await response_queue.put(
                                    _AsrWorkerEvent(
                                        kind="final",
                                        generation=final_key[0],
                                        buffer_epoch=final_key[1],
                                        utterance_id=final_key[2],
                                        text="".join(segments),
                                    )
                                )
                            else:
                                # Natural endpointing can race ahead of a PTT
                                # commit. Keep every locked segment as partial;
                                # the public commit still sends ``finalize`` so
                                # later speech cannot be lost or overwritten.
                                segments = manual_locked_segments.setdefault(key, [])
                                segments.append(utterance_text)
                                await response_queue.put(
                                    _AsrWorkerEvent(
                                        kind="partial",
                                        generation=key[0],
                                        buffer_epoch=key[1],
                                        utterance_id=key[2],
                                        text="".join(segments),
                                    )
                                )
                            continue
                        # Both mutable interim results and locked chunks
                        # (is_final=true, speech_final=false) remain partial.
                        # A locked chunk is retained because the interim
                        # events that follow it only carry the audio after
                        # the lock; without it the preview would lose the
                        # already-spoken head mid-hold.
                        chunk_texts = manual_chunk_segments.setdefault(key, [])
                        if is_final:
                            chunk_texts.append(text)
                            tail = ""
                        else:
                            tail = text
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="partial",
                                generation=key[0],
                                buffer_epoch=key[1],
                                utterance_id=key[2],
                                text="".join(
                                    [
                                        *manual_locked_segments.get(key, []),
                                        *chunk_texts,
                                        tail,
                                    ]
                                ),
                            )
                        )
                        continue

                    if stalled_expired:
                        # The stalled-turn deadline already completed this
                        # connection's utterance and a reconnect is in
                        # flight. xAI events carry no item ids, so a late
                        # event here (e.g. the lost speech_final finally
                        # arriving) cannot be told apart from a new
                        # utterance; drop it so it dies with this socket.
                        continue
                    if active_server_key is None:
                        if latest_audio_key is None:
                            continue
                        if not text:
                            # Empty terminal event with no utterance in
                            # flight: nothing to close and nothing to say.
                            # Opening one here would report speech for
                            # silence.
                            continue
                        if next_server_utterance_id is None:
                            next_server_utterance_id = latest_audio_key[2] or 1
                        active_server_key = (
                            latest_audio_key[0],
                            latest_audio_key[1],
                            next_server_utterance_id,
                        )
                        next_server_utterance_id += 1
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="utterance_started",
                                generation=active_server_key[0],
                                buffer_epoch=active_server_key[1],
                                utterance_id=active_server_key[2],
                            )
                        )
                        stalled_deadline = time.monotonic()
                        stalled_deadline_armed.set()

                    key = active_server_key
                    if is_final and speech_final:
                        # The terminal event restates the whole utterance,
                        # locked segments included, so it REPLACES them (same
                        # rule as the manual branch). Appending here is what
                        # made every locked chunk show up twice in one turn.
                        final_text = _utterance_final_text(
                            text, server_locked_segments
                        )
                        server_locked_segments.clear()
                        active_server_key = None
                        stalled_deadline = None
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="final",
                                generation=key[0],
                                buffer_epoch=key[1],
                                utterance_id=key[2],
                                text=final_text,
                            )
                        )
                        continue
                    stalled_deadline = time.monotonic()
                    if is_final:
                        # Locked segment: retain it so the eventual final
                        # covers the whole utterance.
                        server_locked_segments.append(text)
                        partial_text = "".join(server_locked_segments)
                    else:
                        # Cumulative preview: render locked segments plus
                        # the mutable tail so the preview matches what the
                        # final will say.
                        partial_text = "".join(
                            [*server_locked_segments, text]
                        )
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="partial",
                            generation=key[0],
                            buffer_epoch=key[1],
                            utterance_id=key[2],
                            text=partial_text,
                        )
                    )
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosed:
                pass
            except Exception:
                await _emit_error(
                    "ASR_GROK_WORKER_FAILED",
                    "xAI streaming transcription failed",
                )
                return

            if (
                not intentional_close.is_set()
                and not shutdown_requested.is_set()
                and not failure_sent
            ):
                await _emit_error(
                    "ASR_GROK_DISCONNECTED",
                    "xAI streaming transcription disconnected unexpectedly",
                )

        async def _watch_stalled_turn() -> None:
            # Runs beside the receiver because the receiver blocks on
            # provider frames; a provider that goes silent mid-utterance
            # would otherwise never trigger the sweep. Only server-VAD
            # utterances arm the deadline, so in manual mode this task
            # idles until cancelled.
            nonlocal active_server_key, stalled_deadline, stalled_expired
            while True:
                if stalled_deadline is None:
                    await stalled_deadline_armed.wait()
                    stalled_deadline_armed.clear()
                    continue
                remaining = (
                    stalled_deadline
                    + _GROK_STALLED_TURN_TIMEOUT_SECONDS
                    - time.monotonic()
                )
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                key = active_server_key
                stalled_deadline = None
                if key is None:
                    continue
                # The provider went silent after a nonterminal partial and
                # the speech_final never arrived. Locked segments are text
                # the provider already committed (is_final=true), so emit
                # them instead of dropping real speech; with none locked
                # this stays the empty final the sibling workers emit on
                # expiry, and upstream handles both (an empty final only
                # clears the preview). Returning forces a reconnect:
                # without item ids a late speech_final cannot be told
                # apart from a new utterance, so late events must die with
                # this socket (mirrors the Step worker's ambiguous-expiry
                # connection reset).
                stalled_expired = True
                active_server_key = None
                final_text = "".join(server_locked_segments)
                server_locked_segments.clear()
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="final",
                        generation=key[0],
                        buffer_epoch=key[1],
                        utterance_id=key[2],
                        text=final_text,
                    )
                )
                return

        async def _send_requests() -> _ConnectionAction:
            nonlocal latest_audio_key, last_generation
            while True:
                request = await request_queue.get()
                try:
                    last_generation = request.generation
                    key = (
                        request.generation,
                        request.buffer_epoch,
                        request.utterance_id,
                    )

                    if request.kind == "audio":
                        latest_audio_key = key
                        if request.audio:
                            await connection.send(request.audio)
                        continue

                    if request.kind == "commit":
                        if config.endpointing_mode != "manual":
                            await _emit_error(
                                "ASR_GROK_PROTOCOL_ERROR",
                                "xAI server VAD received an unexpected commit",
                            )
                            return "failed"
                        pending_manual_commits.append(key)
                        await connection.send(json.dumps({"type": "finalize"}))
                        continue

                    if request.kind == "clear":
                        # xAI STT has no native clear. Reconnect to the same
                        # provider, and don't consume later queued audio until the
                        # new transcript.created handshake completes.
                        intentional_close.set()
                        return "reconnect"

                    if request.kind == "shutdown":
                        shutdown_requested.set()
                        await connection.send(json.dumps({"type": "audio.done"}))
                        return "shutdown"

                    await _emit_error(
                        "ASR_GROK_PROTOCOL_ERROR",
                        "xAI worker received an unsupported command",
                    )
                    return "failed"
                finally:
                    request_queue.task_done()

        receiver_task = asyncio.create_task(_receive_events(), name="grok-asr-receiver")
        sender_task = asyncio.create_task(_send_requests(), name="grok-asr-sender")
        watch_task = asyncio.create_task(
            _watch_stalled_turn(), name="grok-asr-stalled-watch"
        )
        try:
            done, _ = await asyncio.wait(
                {sender_task, receiver_task, watch_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if (
                watch_task in done
                and sender_task not in done
                and receiver_task not in done
            ):
                # Stalled-turn expiry: the watch already emitted the final.
                # Retire the connection so any late event for the expired
                # utterance dies with the old socket.
                await watch_task
                intentional_close.set()
                try:
                    await connection.close()
                except Exception:
                    pass
                sender_task.cancel()
                receiver_task.cancel()
                await asyncio.gather(
                    sender_task, receiver_task, return_exceptions=True
                )
                return "reconnect"

            action = await sender_task if sender_task in done else None
            if receiver_task in done:
                await receiver_task
                if action is not None:
                    return action
                if not sender_task.done():
                    sender_task.cancel()
                    await asyncio.gather(sender_task, return_exceptions=True)
                return "failed"

            assert action is not None
            if action == "shutdown":
                try:
                    await asyncio.wait_for(
                        asyncio.shield(receiver_task),
                        timeout=_SHUTDOWN_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    receiver_task.cancel()
                    await asyncio.gather(receiver_task, return_exceptions=True)
                return action

            intentional_close.set()
            try:
                await connection.close()
            except Exception:
                pass
            if not receiver_task.done():
                receiver_task.cancel()
            await asyncio.gather(receiver_task, return_exceptions=True)
            return action
        finally:
            if not watch_task.done():
                watch_task.cancel()
            await asyncio.gather(watch_task, return_exceptions=True)

    try:
        if config.endpointing_mode not in {"manual", "provider"}:
            await _emit_error(
                "ASR_ENDPOINTING_NOT_SUPPORTED",
                "xAI endpointing mode is unsupported",
            )
            return

        language = _normalize_grok_language(config.language)
        query: dict[str, Any] = {
            "sample_rate": 16_000,
            "encoding": "pcm",
            "interim_results": "true",
        }
        if config.endpointing_mode == "provider":
            # Silence (ms) before xAI fires speech_final. Pinned so provider
            # behaviour cannot drift silently -- this is NOT the upstream
            # default (400 ms), it is deliberately far shorter: measured
            # against wss://api.x.ai/v1/stt, 10 ms ends the utterance at the
            # pause between two sentences and transcribes each cleanly, while
            # 400 ms merged a 14 s take into one utterance whose final came
            # back mangled.
            query["endpointing"] = 10
        if language is not None:
            query["language"] = language
        url = f"{_GROK_STT_URL}?{urlencode(query)}"

        while True:
            websocket = await _connect_ready(url)
            if websocket is None:
                return
            if not ready_sent:
                ready_sent = True
                await response_queue.put(
                    _AsrWorkerEvent(kind="ready", generation=last_generation)
                )

            action = await _run_connection(websocket)
            try:
                await websocket.close()
            except Exception:
                pass
            websocket = None

            if action == "reconnect":
                continue
            return
    except asyncio.CancelledError:
        raise
    except ValueError as exc:
        await _emit_error(
            "ASR_LANGUAGE_NOT_SUPPORTED",
            str(exc).partition(": ")[2] or "xAI language is unsupported",
        )
    except Exception as exc:
        await _emit_error(
            (
                "ASR_CREDENTIALS_REJECTED"
                if _grok_is_auth_rejection(exc)
                else "ASR_GROK_WORKER_FAILED"
            ),
            (
                "xAI credentials were rejected"
                if _grok_is_auth_rejection(exc)
                else "xAI streaming transcription failed"
            ),
        )
    finally:
        shutdown_requested.set()
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
        await response_queue.put(
            _AsrWorkerEvent(kind="closed", generation=last_generation)
        )
