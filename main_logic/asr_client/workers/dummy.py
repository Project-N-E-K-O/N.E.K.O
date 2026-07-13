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

"""Local-only ASR worker used to exercise the public session contract."""

from __future__ import annotations

import asyncio
import os

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest

_DEFAULT_TRANSCRIPT = "测试识别文本"
_DEFAULT_DELAY_MS = 500
_MAX_DELAY_MS = 10_000
_ASR_DUMMY_VALID_MODES = frozenset({"normal", "duplicate", "delayed", "error"})


async def _emit_later(
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    event: _AsrWorkerEvent,
    delay_seconds: float,
) -> None:
    """Emit a response later without blocking the worker command loop."""

    await asyncio.sleep(delay_seconds)
    await response_queue.put(event)


async def dummy_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
) -> None:
    """Run a deterministic ASR worker for local integration testing."""

    # The dummy must accept the same signature as real workers, but it never
    # reads credentials or provider-specific configuration.
    _ = (api_key, config)
    last_generation = 0
    closed_sent = False
    buffered_frame_counts: dict[tuple[int, int, int | None], int] = {}
    pending_emissions: set[asyncio.Task[None]] = set()

    try:
        transcript = os.getenv("ASR_DUMMY_TRANSCRIPT", _DEFAULT_TRANSCRIPT)
        mode = os.getenv("ASR_DUMMY_MODE", "normal").strip().lower()
        delay_raw = os.getenv("ASR_DUMMY_DELAY_MS", str(_DEFAULT_DELAY_MS)).strip()

        try:
            delay_ms = int(delay_raw)
        except ValueError:
            delay_ms = -1

        if mode not in _ASR_DUMMY_VALID_MODES or not 0 <= delay_ms <= _MAX_DELAY_MS:
            await response_queue.put(
                _AsrWorkerEvent(
                    kind="error",
                    generation=last_generation,
                    error_code="ASR_DUMMY_INVALID_CONFIG",
                    error_message="dummy worker configuration is invalid",
                )
            )
            return

        await response_queue.put(
            _AsrWorkerEvent(kind="ready", generation=last_generation)
        )

        while True:
            request = await request_queue.get()
            last_generation = request.generation

            if request.kind == "audio":
                key = (
                    request.generation,
                    request.buffer_epoch,
                    request.utterance_id,
                )
                buffered_frame_counts[key] = buffered_frame_counts.get(key, 0) + 1
                continue

            if request.kind == "clear":
                buffered_frame_counts.clear()
                continue

            if request.kind == "shutdown":
                buffered_frame_counts.clear()
                await response_queue.put(
                    _AsrWorkerEvent(kind="closed", generation=request.generation)
                )
                closed_sent = True
                break

            if request.kind != "commit":
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="error",
                        generation=request.generation,
                        buffer_epoch=request.buffer_epoch,
                        utterance_id=request.utterance_id,
                        error_code="ASR_DUMMY_PROTOCOL_ERROR",
                        error_message="dummy worker received an unsupported command",
                    )
                )
                return

            key = (
                request.generation,
                request.buffer_epoch,
                request.utterance_id,
            )
            has_audio = buffered_frame_counts.pop(key, 0) > 0
            if not has_audio:
                continue

            if mode == "error":
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="error",
                        generation=request.generation,
                        buffer_epoch=request.buffer_epoch,
                        utterance_id=request.utterance_id,
                        error_code="ASR_DUMMY_ERROR",
                        error_message="simulated provider failure",
                    )
                )
                return

            final_event = _AsrWorkerEvent(
                kind="final",
                generation=request.generation,
                buffer_epoch=request.buffer_epoch,
                utterance_id=request.utterance_id,
                text=transcript,
            )
            if mode == "delayed":
                task = asyncio.create_task(
                    _emit_later(response_queue, final_event, delay_ms / 1000)
                )
                pending_emissions.add(task)
                task.add_done_callback(pending_emissions.discard)
                continue

            await response_queue.put(final_event)
            if mode == "duplicate":
                await response_queue.put(final_event)
    except asyncio.CancelledError:
        raise
    except Exception:
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=last_generation,
                error_code="ASR_DUMMY_WORKER_FAILED",
                error_message="dummy worker failed",
            )
        )
    finally:
        if not closed_sent:
            await response_queue.put(
                _AsrWorkerEvent(kind="closed", generation=last_generation)
            )
        if pending_emissions:
            await asyncio.gather(*pending_emissions, return_exceptions=True)
