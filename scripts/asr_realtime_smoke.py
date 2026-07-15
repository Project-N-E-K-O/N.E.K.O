#!/usr/bin/env python
# -- coding: utf-8 --
"""Exercise one Phase 2 realtime ASR worker with PCM16 mono WAV turns.

This is deliberately an opt-in development probe.  It bypasses the registry's
``blocked_credentials`` gate so a provider can be verified before its registry
status is promoted to ``implemented``.  The production factory remains the
only supported application entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    AsrWorkerFn,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
    _RealtimeAsrSessionImpl,
)
from main_logic.asr_client._registry_meta import ASR_PROVIDER_REGISTRY
from main_logic.asr_client._voice_turn import _create_voice_turn_adapter
from main_logic.voice_turn.contracts import SpeechActivityEvent


_CREDENTIAL_FIELDS = {
    "qwen": "ASSIST_API_KEY_QWEN",
    "qwen_intl": "ASSIST_API_KEY_QWEN_INTL",
    "openai": "ASSIST_API_KEY_OPENAI",
    "step": "ASSIST_API_KEY_STEP",
    "grok": "ASSIST_API_KEY_GROK",
    "glm": "ASSIST_API_KEY_GLM",
    "gemini": "ASSIST_API_KEY_GEMINI",
}

_DEFAULT_CREDENTIAL_ENVS = {
    "openai": ("OPENAI_API_KEY", "ASSIST_API_KEY_OPENAI"),
}

_AUTH_FAILURE_CODES = {
    "ASR_CREDENTIALS_MISSING",
    "ASR_CREDENTIALS_REJECTED",
}


@dataclass(frozen=True, slots=True)
class _AudioTurn:
    path: Path
    pcm: bytes
    sample_rate_hz: int
    duration_s: float


@dataclass(slots=True)
class SmokeResult:
    provider: str
    endpointing_mode: str
    ok: bool = False
    expected_auth_failure: bool = False
    auth_failure_observed: bool = False
    audio_sample_rates_hz: list[int] = field(default_factory=list)
    turns_requested: int = 0
    expected_finals: int = 0
    business_finals: int = 0
    smart_turn_auto: bool = False
    normalized_audio_seconds: float = 0.0
    commit_count: int = 0
    ready_ms: float | None = None
    first_partial_ms: float | None = None
    commit_ms: list[float] = field(default_factory=list)
    final_ms: list[float] = field(default_factory=list)
    activity_events: list[dict[str, float | str]] = field(default_factory=list)
    close_ms: float | None = None
    statuses: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    transcripts: list[str] | None = None


@dataclass(slots=True)
class _Observation:
    started_at: float
    first_partial_at: float | None = None
    audio_bytes: int = 0
    commit_at: list[float] = field(default_factory=list)
    final_at: list[float] = field(default_factory=list)
    activity_at: list[tuple[str, float]] = field(default_factory=list)


class _RequestQueueObserver:
    """Count normalized audio and commits without retaining audio payloads."""

    def __init__(
        self,
        target: asyncio.Queue[_AsrWorkerRequest],
        observation: _Observation,
    ) -> None:
        self._target = target
        self._observation = observation

    async def get(self) -> _AsrWorkerRequest:
        request = await self._target.get()
        if request.kind == "audio":
            self._observation.audio_bytes += len(request.audio)
        elif request.kind == "commit":
            self._observation.commit_at.append(time.perf_counter())
        return request

    def task_done(self) -> None:
        self._target.task_done()


class _ResponseQueueObserver:
    """Observe event timing without logging event payloads or credentials."""

    def __init__(
        self,
        target: asyncio.Queue[_AsrWorkerEvent],
        observation: _Observation,
    ) -> None:
        self._target = target
        self._observation = observation

    async def put(self, event: _AsrWorkerEvent) -> None:
        now = time.perf_counter()
        if event.kind == "partial" and self._observation.first_partial_at is None:
            self._observation.first_partial_at = now
        elif event.kind == "final":
            self._observation.final_at.append(now)
        await self._target.put(event)

    def put_nowait(self, event: _AsrWorkerEvent) -> None:
        now = time.perf_counter()
        if event.kind == "partial" and self._observation.first_partial_at is None:
            self._observation.first_partial_at = now
        elif event.kind == "final":
            self._observation.final_at.append(now)
        self._target.put_nowait(event)


def _read_wav_pcm16(path: Path) -> _AudioTurn:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate_hz = wav_file.getframerate()
        frames = wav_file.getnframes()
        pcm = wav_file.readframes(frames)

    if channels != 1:
        raise ValueError(f"{path}: expected mono WAV, got {channels} channels")
    if sample_width != 2:
        raise ValueError(f"{path}: expected PCM16 WAV")
    if sample_rate_hz not in (16_000, 48_000):
        raise ValueError(f"{path}: expected 16000 or 48000 Hz WAV")
    if not pcm:
        raise ValueError(f"{path}: audio is empty")
    return _AudioTurn(
        path=path,
        pcm=pcm,
        sample_rate_hz=sample_rate_hz,
        duration_s=frames / sample_rate_hz,
    )


def _resolve_provider(provider: str) -> AsrWorkerFn:
    if provider in {"qwen", "qwen_intl"}:
        from main_logic.asr_client.workers.qwen import qwen_asr_worker

        region = "intl" if provider == "qwen_intl" else "cn"
        return partial(qwen_asr_worker, region=region)
    if provider == "openai":
        from main_logic.asr_client.workers.openai import openai_asr_worker

        return openai_asr_worker
    if provider == "step":
        from main_logic.asr_client.workers.step import step_asr_worker

        return step_asr_worker
    if provider == "grok":
        from main_logic.asr_client.workers.grok import grok_asr_worker

        return grok_asr_worker
    if provider == "glm":
        from main_logic.asr_client.workers.glm import glm_asr_worker

        return glm_asr_worker
    if provider == "gemini":
        from main_logic.asr_client.workers.gemini import gemini_asr_worker

        return gemini_asr_worker
    raise ValueError(f"unknown provider: {provider}")


def _resolve_api_key(provider: str, override_env: str) -> str:
    field_name = _CREDENTIAL_FIELDS[provider]
    override = override_env.strip()
    env_names = (
        (override,)
        if override
        else _DEFAULT_CREDENTIAL_ENVS.get(provider, (field_name,))
    )
    for env_name in env_names:
        api_key = os.getenv(env_name, "").strip()
        if api_key:
            return api_key

    from utils.config_manager import get_config_manager

    core_config = get_config_manager().get_core_config() or {}
    api_key = str(core_config.get(field_name) or "").strip()
    if not api_key:
        raise RuntimeError(f"ASR_CREDENTIALS_MISSING: {field_name}")
    return api_key


def _observe_worker(
    worker_fn: AsrWorkerFn,
    observation: _Observation,
) -> AsrWorkerFn:
    async def observed_worker(
        request_queue: asyncio.Queue[Any],
        response_queue: asyncio.Queue[_AsrWorkerEvent],
        api_key: str,
        config: AsrSessionConfig,
    ) -> None:
        request_observer = _RequestQueueObserver(request_queue, observation)
        observer = _ResponseQueueObserver(response_queue, observation)
        await worker_fn(  # type: ignore[arg-type]
            request_observer,
            observer,
            api_key,
            config,
        )

    return observed_worker


async def _sleep_until(deadline: float) -> None:
    """Pace against perf-counter; Windows asyncio timers can fire 10 ms early."""

    remaining = deadline - time.perf_counter()
    if remaining > 0:
        await asyncio.to_thread(time.sleep, remaining)


async def _stream_turn(
    session: _RealtimeAsrSessionImpl,
    turn: _AudioTurn,
    *,
    chunk_ms: int,
    endpointing_mode: str,
    realtime: bool,
    vad_silence_ms: int,
    smart_turn_auto: bool = False,
    smart_turn_silence_ms: int = 0,
) -> None:
    bytes_per_frame = 2
    next_chunk_at = time.perf_counter()
    chunk_bytes = max(
        bytes_per_frame,
        turn.sample_rate_hz * bytes_per_frame * chunk_ms // 1000,
    )
    chunk_bytes -= chunk_bytes % bytes_per_frame
    for offset in range(0, len(turn.pcm), chunk_bytes):
        chunk = turn.pcm[offset : offset + chunk_bytes]
        await session.stream_audio(chunk, sample_rate_hz=turn.sample_rate_hz)
        if realtime:
            next_chunk_at += len(chunk) / bytes_per_frame / turn.sample_rate_hz
            await _sleep_until(next_chunk_at)
    silence_ms = (
        smart_turn_silence_ms
        if smart_turn_auto
        else vad_silence_ms if endpointing_mode == "provider" else 0
    )
    if silence_ms:
        silence = bytes(turn.sample_rate_hz * bytes_per_frame * silence_ms // 1000)
        for offset in range(0, len(silence), chunk_bytes):
            chunk = silence[offset : offset + chunk_bytes]
            await session.stream_audio(chunk, sample_rate_hz=turn.sample_rate_hz)
            if realtime:
                next_chunk_at += len(chunk) / bytes_per_frame / turn.sample_rate_hz
                await _sleep_until(next_chunk_at)
    if not smart_turn_auto:
        await session.signal_user_activity_end()


async def _wait_for_final_count(
    event: asyncio.Event,
    transcripts: list[str],
    expected: int,
    timeout_s: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while len(transcripts) < expected:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for final {expected}")
        event.clear()
        if len(transcripts) >= expected:
            break
        await asyncio.wait_for(event.wait(), timeout=remaining)


async def _run_provider_smoke(args: argparse.Namespace) -> SmokeResult:
    turns = [_read_wav_pcm16(path) for path in args.audio]
    if any(turn.sample_rate_hz != turns[0].sample_rate_hz for turn in turns[1:]):
        raise ValueError(
            "all WAV turns in one smoke session must use the same sample rate"
        )
    smart_turn_auto = bool(getattr(args, "smart_turn_auto", False))
    expected_finals = int(getattr(args, "expected_finals", 0) or len(turns))
    smart_turn_silence_ms = int(getattr(args, "smart_turn_silence_ms", 0))
    result = SmokeResult(
        provider=args.provider,
        endpointing_mode=args.endpointing_mode,
        expected_auth_failure=bool(args.invalid_credential),
        audio_sample_rates_hz=[turn.sample_rate_hz for turn in turns],
        turns_requested=len(turns),
        expected_finals=expected_finals,
        smart_turn_auto=smart_turn_auto,
        transcripts=[] if args.show_transcripts else None,
    )
    api_key = (
        "invalid-asr-smoke-key"
        if args.invalid_credential
        else _resolve_api_key(args.provider, args.api_key_env)
    )
    observation = _Observation(started_at=time.perf_counter())
    worker_fn = _observe_worker(_resolve_provider(args.provider), observation)
    final_event = asyncio.Event()
    credential_result_event = asyncio.Event()
    transcripts: list[str] = []

    async def on_transcript(text: str) -> None:
        transcripts.append(text)
        final_event.set()
        credential_result_event.set()

    async def on_error(error: str) -> None:
        result.errors.append(error)
        credential_result_event.set()

    async def on_status(status: str) -> None:
        result.statuses.append(status)

    async def on_activity(event: SpeechActivityEvent) -> None:
        observation.activity_at.append((event.value, time.perf_counter()))

    session = _RealtimeAsrSessionImpl(
        worker_fn=worker_fn,
        api_key=api_key,
        config=AsrSessionConfig(
            language=args.language,
            input_sample_rate_hz=turns[0].sample_rate_hz,
            endpointing_mode=args.endpointing_mode,
        ),
        on_input_transcript=on_transcript,
        on_connection_error=on_error,
        on_status_message=on_status,
        voice_turn_factory=(
            partial(_create_voice_turn_adapter, on_activity=on_activity)
            if smart_turn_auto
            else None
        ),
    )

    connected_at: float | None = None
    close_started: float | None = None
    try:
        await session.connect()
        connected_at = time.perf_counter()
        result.ready_ms = (connected_at - observation.started_at) * 1000

        if not args.skip_clear:
            await session.clear_audio_buffer()

        if args.invalid_credential:
            await _stream_turn(
                session,
                turns[0],
                chunk_ms=args.chunk_ms,
                endpointing_mode=args.endpointing_mode,
                realtime=not args.no_realtime,
                vad_silence_ms=args.vad_silence_ms,
                smart_turn_auto=smart_turn_auto,
                smart_turn_silence_ms=smart_turn_silence_ms,
            )
            try:
                await asyncio.wait_for(
                    credential_result_event.wait(), timeout=args.timeout_s
                )
            except TimeoutError:
                raise RuntimeError(
                    "invalid credential was not rejected before timeout"
                ) from None
            error_codes = {error.partition(":")[0] for error in result.errors}
            if not error_codes & _AUTH_FAILURE_CODES:
                raise RuntimeError(
                    "invalid credential unexpectedly reached a provider result"
                )
        else:
            for index, turn in enumerate(turns, start=1):
                await _stream_turn(
                    session,
                    turn,
                    chunk_ms=args.chunk_ms,
                    endpointing_mode=args.endpointing_mode,
                    realtime=not args.no_realtime,
                    vad_silence_ms=args.vad_silence_ms,
                    smart_turn_auto=smart_turn_auto,
                    smart_turn_silence_ms=smart_turn_silence_ms,
                )
                if not smart_turn_auto:
                    await _wait_for_final_count(
                        final_event,
                        transcripts,
                        index,
                        args.timeout_s,
                    )
                if not session.is_ready:
                    raise RuntimeError(
                        "commit or provider endpointing closed the session"
                    )
            if smart_turn_auto:
                await _wait_for_final_count(
                    final_event,
                    transcripts,
                    expected_finals,
                    args.timeout_s,
                )

        if result.errors and not args.invalid_credential:
            raise RuntimeError(result.errors[-1])
        result.ok = len(transcripts) == expected_finals
    except Exception as exc:
        if args.invalid_credential:
            if session.is_ready:
                result.errors.append("ASR_INVALID_CREDENTIAL_ACCEPTED")
            elif not result.errors:
                safe_error = str(exc).strip() or type(exc).__name__
                result.errors.append(safe_error[:300])
        else:
            safe_error = str(exc).strip() or type(exc).__name__
            if safe_error not in result.errors:
                result.errors.append(safe_error[:300])
    finally:
        close_started = time.perf_counter()
        try:
            await session.close()
        except Exception as exc:
            safe_error = str(exc).strip() or type(exc).__name__
            if safe_error not in result.errors:
                result.errors.append(safe_error[:300])
            result.ok = False
        result.close_ms = (time.perf_counter() - close_started) * 1000

    result.business_finals = len(transcripts)
    result.normalized_audio_seconds = observation.audio_bytes / (16_000 * 2)
    result.commit_count = len(observation.commit_at)
    if result.transcripts is not None:
        result.transcripts.extend(transcripts)
    if observation.first_partial_at is not None:
        result.first_partial_ms = (
            observation.first_partial_at - observation.started_at
        ) * 1000
    result.final_ms = [
        (timestamp - observation.started_at) * 1000
        for timestamp in observation.final_at
    ]
    result.commit_ms = [
        (timestamp - observation.started_at) * 1000
        for timestamp in observation.commit_at
    ]
    result.activity_events = [
        {
            "event": event,
            "ms": (timestamp - observation.started_at) * 1000,
        }
        for event, timestamp in observation.activity_at
    ]
    if args.invalid_credential:
        error_codes = {error.partition(":")[0] for error in result.errors}
        result.auth_failure_observed = bool(error_codes & _AUTH_FAILURE_CODES)
        result.ok = result.auth_failure_observed and error_codes <= _AUTH_FAILURE_CODES
    else:
        if len(result.final_ms) != len(transcripts):
            result.errors.append(
                "worker final count did not match business callback count"
            )
        if result.errors:
            result.ok = False
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "provider",
        choices=sorted(_CREDENTIAL_FIELDS),
        help="Phase 2 Core/provider route to probe.",
    )
    parser.add_argument(
        "audio",
        nargs="+",
        type=Path,
        help="One or more mono PCM16 WAV turns at 16 kHz or 48 kHz.",
    )
    parser.add_argument(
        "--endpointing-mode",
        choices=("manual", "provider"),
        default=None,
        help="Defaults to a supported provider mode (Grok uses provider; others manual).",
    )
    parser.add_argument("--language", default="zh")
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--chunk-ms", type=int, default=10)
    parser.add_argument(
        "--vad-silence-ms",
        type=int,
        default=1000,
        help="Silence appended to each provider-endpointed turn so it can finalize.",
    )
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--no-realtime", action="store_true")
    parser.add_argument("--skip-clear", action="store_true")
    parser.add_argument("--invalid-credential", action="store_true")
    parser.add_argument("--show-transcripts", action="store_true")
    parser.add_argument(
        "--smart-turn-auto",
        action="store_true",
        help="Use the production Smart Turn adapter and do not issue manual commits.",
    )
    parser.add_argument(
        "--smart-turn-silence-ms",
        type=int,
        default=1000,
        help="Trailing silence streamed after each WAV in Smart Turn auto mode.",
    )
    parser.add_argument(
        "--expected-finals",
        type=int,
        default=None,
        help="Expected final callbacks; defaults to the number of WAV inputs.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.chunk_ms <= 0:
        parser.error("--chunk-ms must be positive")
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be positive")
    if args.vad_silence_ms < 0:
        parser.error("--vad-silence-ms must not be negative")
    if args.smart_turn_silence_ms < 0:
        parser.error("--smart-turn-silence-ms must not be negative")
    if args.expected_finals is None:
        args.expected_finals = len(args.audio)
    elif args.expected_finals <= 0:
        parser.error("--expected-finals must be positive")
    supported_modes = ASR_PROVIDER_REGISTRY[
        "qwen" if args.provider == "qwen_intl" else args.provider
    ].supported_endpointing_modes
    if args.endpointing_mode is None:
        args.endpointing_mode = "manual" if "manual" in supported_modes else "provider"
    if args.endpointing_mode not in supported_modes:
        parser.error(
            f"{args.provider} does not support {args.endpointing_mode} endpointing"
        )
    provider_key = "qwen" if args.provider == "qwen_intl" else args.provider
    provider_meta = ASR_PROVIDER_REGISTRY[provider_key]
    if args.smart_turn_auto:
        if args.endpointing_mode != "manual" or not provider_meta.requires_smart_turn:
            parser.error("--smart-turn-auto requires a Smart Turn manual provider")
        if args.no_realtime:
            parser.error("--smart-turn-auto requires real-time chunk pacing")
    return args


async def main_async() -> int:
    args = parse_args()
    result = await _run_provider_smoke(args)
    payload = json.dumps(asdict(result), ensure_ascii=False, indent=2)
    if args.output is None:
        print(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    return 0 if result.ok else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
