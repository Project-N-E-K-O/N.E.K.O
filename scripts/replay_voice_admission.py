"""Local-only admission replay. Emits numbers, never audio or transcripts.

Usage: uv run python scripts/replay_voice_admission.py DIRECTORY
Files are not sent to an ASR provider. Each clip starts a fresh VAD context.
This evaluates onset evidence, not provider endpoints, text, or barge-in.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import av

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_logic.asr_client.endpointing.silero_vad import SileroVad
from main_logic.voice_turn.admission import AdmissionDecision, CandidateAdmission
from utils.audio_processor import AudioProcessor


def decode_pcm(path: Path, sample_rate: int = 16000) -> bytes:
    resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
    chunks = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            chunks.extend(
                output.to_ndarray().tobytes() for output in resampler.resample(frame)
            )
        chunks.extend(
            output.to_ndarray().tobytes() for output in resampler.resample(None)
        )
    return b"".join(chunks)


def evaluate(
    path: Path, clip_id: int, vad: SileroVad, sample_rate: int = 16000
) -> dict:
    pcm = decode_pcm(path, sample_rate)
    input_duration_ms = len(pcm) * 1000 / (sample_rate * 2)
    rnnoise_available = False
    if sample_rate == 48000:
        processor = AudioProcessor(noise_reduce_enabled=True)
        try:
            rnnoise_available = processor.rnnoise_available
            # Match 20ms capture chunks, retaining processor/resampler state.
            chunks = []
            for i in range(0, len(pcm), 1920):
                chunk = pcm[i : i + 1920]
                # Complete only the final RNNoise 10ms frame; flush the FIR
                # tail instead of losing the end of a short instruction.
                chunk += b"\0" * (-len(chunk) % 960)
                chunks.append(processor.process_chunk(chunk))
            chunks.append(processor.finalize_stream())
            pcm = b"".join(chunks)
        finally:
            processor.close()
    vad.reset_stream()
    candidate = CandidateAdmission(str(clip_id))
    first_admit = None
    transitions = []
    previous = None
    # Full offline clips have no provider boundary. Report admission of the
    # first utterance only; do not invent endpoints from wall time or text.
    for i, probability in enumerate(vad.process_pcm16(pcm)):
        snapshot = candidate.observe(i * 512, (i + 1) * 512, probability)
        if snapshot is None:
            continue
        key = (snapshot.candidate_id, snapshot.decision)
        if key != previous:
            transitions.append(
                {
                    "candidate_id": key[0],
                    "decision": key[1].value,
                    "end_ms": snapshot.audio_end_sample / 16,
                    "voiced_ms": snapshot.voiced_audio_ms,
                    "reason": snapshot.reason,
                }
            )
            previous = key
        if snapshot.decision is AdmissionDecision.ADMIT and first_admit is None:
            first_admit = snapshot.audio_end_sample / 16
    return {
        "clip_id": clip_id,
        "input_sample_rate": sample_rate,
        "input_duration_ms": input_duration_ms,
        "rnnoise_available": rnnoise_available,
        "duration_ms": len(pcm) / 32,
        "first_admit_ms": first_admit,
        "transitions": transitions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--sample-rate", type=int, choices=(16000, 48000), default=16000
    )
    args = parser.parse_args()
    vad = SileroVad(enabled=True)
    if not vad.load():
        raise RuntimeError(f"local Silero unavailable: {vad.unavailable_reason}")
    paths = sorted(
        path
        for path in args.directory.iterdir()
        if path.suffix.lower() in {".wav", ".m4a", ".flac", ".mp3"}
    )
    if not paths:
        raise ValueError("no audio clips in directory")
    for i, path in enumerate(paths, 1):
        print(json.dumps(evaluate(path, i, vad, args.sample_rate)))


if __name__ == "__main__":
    main()
