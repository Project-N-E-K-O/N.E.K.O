# -*- coding: utf-8 -*-
"""Offline smoke: PCM/WAV -> faster-whisper worker -> printed transcript."""
from __future__ import annotations

import argparse
import asyncio
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_logic.asr_client._infra import (  # noqa: E402
    AsrSessionConfig,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
)
from main_logic.asr_client.workers.faster_whisper import (  # noqa: E402
    faster_whisper_asr_worker,
)


def _load_pcm16_16k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise SystemExit("WAV must be mono PCM16")
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if rate == 16_000:
        return frames
    raise SystemExit(f"WAV sample rate must be 16000 Hz (got {rate})")


async def _run(pcm: bytes) -> str:
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        faster_whisper_asr_worker(
            requests,
            responses,
            "",
            AsrSessionConfig(language="zh-CN"),
        )
    )
    ready = await asyncio.wait_for(responses.get(), 300)
    if ready.kind != "ready":
        raise SystemExit(f"expected ready, got {ready.kind}: {ready.error_code}")
    key = {"generation": 0, "buffer_epoch": 0, "utterance_id": 1}
    # Chunk into ~100ms frames
    step = 3200
    for i in range(0, len(pcm), step):
        await requests.put(
            _AsrWorkerRequest(kind="audio", audio=pcm[i : i + step], **key)
        )
    await requests.put(_AsrWorkerRequest(kind="commit", **key))
    text = ""
    while True:
        event = await asyncio.wait_for(responses.get(), 300)
        if event.kind == "final":
            text = event.text or ""
            break
        if event.kind == "error":
            raise SystemExit(f"{event.error_code}: {event.error_message}")
    await requests.put(
        _AsrWorkerRequest(kind="shutdown", generation=0, buffer_epoch=0, utterance_id=2)
    )
    await asyncio.wait_for(task, 30)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Local faster-whisper ASR smoke")
    parser.add_argument("wav", type=Path, help="mono 16kHz PCM16 WAV")
    args = parser.parse_args()
    pcm = _load_pcm16_16k(args.wav)
    print(asyncio.run(_run(pcm)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
