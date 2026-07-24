#!/usr/bin/env python
"""Measure Soniox first-token and source-EOS-to-``<end>`` latency on PCM WAV."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import websockets


REGION_URLS = {
    "us": "wss://stt-rt.soniox.com/transcribe-websocket",
    "eu": "wss://stt-rt.eu.soniox.com/transcribe-websocket",
    "jp": "wss://stt-rt.jp.soniox.com/transcribe-websocket",
}
CONTROL_TOKENS = frozenset({"<end>", "<fin>"})


@dataclass
class SmokeResult:
    path: str
    region: str
    ok: bool
    transcript: str
    duration_s: float
    first_token_ms: float | None
    endpoint_after_eos_ms: float | None
    total_ms: float
    messages: int
    bytes_sent: int
    error_type: str | None
    provider_error_code: str | None
    request_id: str | None


def _read_pcm_wav(path: Path) -> tuple[bytes, int, int, float]:
    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_count = wav_file.getnframes()
        data = wav_file.readframes(frame_count)
    if sample_width != 2 or channels != 1 or sample_rate != 16_000:
        raise ValueError("smoke input must be 16 kHz mono 16-bit PCM WAV")
    return data, sample_rate, channels, frame_count / sample_rate


def _render_tokens(
    message: dict[str, Any], final_tokens: list[str]
) -> tuple[str, bool, bool]:
    tokens = message.get("tokens")
    if not isinstance(tokens, list):
        return "".join(final_tokens), False, False
    provisional: list[str] = []
    saw_text = False
    saw_end = False
    for token in tokens:
        if not isinstance(token, dict):
            continue
        text = token.get("text")
        if not isinstance(text, str) or not text:
            continue
        if text == "<end>":
            saw_end = True
            continue
        if text in CONTROL_TOKENS:
            continue
        saw_text = True
        if token.get("is_final") is True:
            final_tokens.append(text)
        else:
            provisional.append(text)
    return "".join((*final_tokens, *provisional)), saw_text, saw_end


async def transcribe_file(
    path: Path,
    *,
    api_key: str,
    region: str,
    language_hints: list[str],
    chunk_ms: int,
    trailing_silence_ms: int,
    timeout_s: float,
) -> SmokeResult:
    started = time.perf_counter()
    duration_s = 0.0
    eos_at: float | None = None
    first_token_ms: float | None = None
    endpoint_after_eos_ms: float | None = None
    latest_text = ""
    final_tokens: list[str] = []
    message_count = 0
    bytes_sent = 0
    provider_error_code: str | None = None
    provider_request_id: str | None = None

    try:
        audio, sample_rate, channels, duration_s = await asyncio.to_thread(
            _read_pcm_wav, path
        )
        frame_bytes = sample_rate * channels * 2 * chunk_ms // 1000
        silence = b"\0" * (
            sample_rate * channels * 2 * trailing_silence_ms // 1000
        )
        async with websockets.connect(
            REGION_URLS[region],
            open_timeout=timeout_s,
            close_timeout=1.0,
            max_size=None,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "api_key": api_key,
                        "model": "stt-rt-v5",
                        "audio_format": "pcm_s16le",
                        "sample_rate": sample_rate,
                        "num_channels": channels,
                        "enable_endpoint_detection": True,
                        "enable_language_identification": True,
                        "language_hints": language_hints,
                    },
                    ensure_ascii=False,
                )
            )

            async def send_audio() -> None:
                nonlocal bytes_sent, eos_at
                for offset in range(0, len(audio), frame_bytes):
                    chunk = audio[offset : offset + frame_bytes]
                    await websocket.send(chunk)
                    bytes_sent += len(chunk)
                    await asyncio.sleep(len(chunk) / (sample_rate * channels * 2))
                eos_at = time.perf_counter()
                for offset in range(0, len(silence), frame_bytes):
                    chunk = silence[offset : offset + frame_bytes]
                    await websocket.send(chunk)
                    bytes_sent += len(chunk)
                    await asyncio.sleep(len(chunk) / (sample_rate * channels * 2))

            sender = asyncio.create_task(send_audio())
            try:
                while True:
                    raw = await asyncio.wait_for(websocket.recv(), timeout_s)
                    message_count += 1
                    if isinstance(raw, bytes):
                        continue
                    message = json.loads(raw)
                    if message.get("error_code") or message.get("error_message"):
                        provider_error_code = str(
                            message.get("error_code") or "unknown"
                        )[:64]
                        provider_request_id = str(message.get("request_id") or "")[:128]
                        raise RuntimeError("Soniox provider error")
                    rendered, saw_text, saw_end = _render_tokens(message, final_tokens)
                    now = time.perf_counter()
                    if saw_text and first_token_ms is None:
                        first_token_ms = (now - started) * 1000
                    if rendered:
                        latest_text = rendered
                    if saw_end:
                        latest_text = "".join(final_tokens).strip()
                        endpoint_after_eos_ms = (
                            (now - eos_at) * 1000 if eos_at is not None else None
                        )
                        await websocket.send(b"")
                        break
            finally:
                if not sender.done():
                    sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
    except Exception as exc:
        return SmokeResult(
            path=str(path),
            region=region,
            ok=False,
            transcript=latest_text,
            duration_s=duration_s,
            first_token_ms=first_token_ms,
            endpoint_after_eos_ms=endpoint_after_eos_ms,
            total_ms=(time.perf_counter() - started) * 1000,
            messages=message_count,
            bytes_sent=bytes_sent,
            error_type=type(exc).__name__,
            provider_error_code=provider_error_code,
            request_id=provider_request_id or None,
        )

    return SmokeResult(
        path=str(path),
        region=region,
        ok=True,
        transcript=latest_text,
        duration_s=duration_s,
        first_token_ms=first_token_ms,
        endpoint_after_eos_ms=endpoint_after_eos_ms,
        total_ms=(time.perf_counter() - started) * 1000,
        messages=message_count,
        bytes_sent=bytes_sent,
        error_type=None,
        provider_error_code=None,
        request_id=None,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="+", type=Path)
    parser.add_argument("--region", choices=tuple(REGION_URLS), default="us")
    parser.add_argument("--language-hints", default="en,ja,es")
    parser.add_argument("--chunk-ms", type=int, default=80)
    parser.add_argument("--trailing-silence-ms", type=int, default=1500)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    api_key = os.getenv("SONIOX_API_KEY", "").strip()
    if not api_key:
        print("Missing SONIOX_API_KEY", flush=True)
        return 2
    if not 50 <= args.chunk_ms <= 100:
        print("--chunk-ms must be between 50 and 100", flush=True)
        return 2
    hints = [part.strip() for part in args.language_hints.split(",") if part.strip()]
    results = [
        await transcribe_file(
            path,
            api_key=api_key,
            region=args.region,
            language_hints=hints,
            chunk_ms=args.chunk_ms,
            trailing_silence_ms=args.trailing_silence_ms,
            timeout_s=args.timeout_s,
        )
        for path in args.audio
    ]
    payload = json.dumps(
        [asdict(result) for result in results], ensure_ascii=False, indent=2
    )
    if args.output:

        def write_output() -> None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")

        await asyncio.to_thread(write_output)
        print(f"Wrote {args.output}")
    else:
        print(payload)
    return 0 if all(result.ok for result in results) else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
