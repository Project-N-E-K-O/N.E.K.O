"""Run offline, observation-only CAM++ scoring without retaining voice data."""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.asr_client.campplus import (  # noqa: E402
    CAMPPLUS_SAMPLE_RATE_HZ,
    CampPlusEmbeddingModel,
    CampPlusSpeakerShadowBackend,
    build_campplus_speaker_profile,
)


def _read_pcm16(path: Path, *, maximum_seconds: float | None = None) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getframerate() != CAMPPLUS_SAMPLE_RATE_HZ
            or source.getcomptype() != "NONE"
        ):
            raise ValueError("all WAV inputs must be mono PCM16LE at 16 kHz")
        frame_count = source.getnframes()
        if maximum_seconds is not None:
            frame_count = min(
                frame_count,
                round(CAMPPLUS_SAMPLE_RATE_HZ * maximum_seconds),
            )
        pcm16 = source.readframes(frame_count)
    if len(pcm16) < CAMPPLUS_SAMPLE_RATE_HZ * 3:
        raise ValueError("all voice segments must contain at least 1.5 seconds")
    return pcm16


def evaluate(
    *,
    enrollment_paths: list[Path],
    candidate_paths: list[Path],
    asset_dir: Path | None,
) -> dict[str, object]:
    enrollment_pcm16 = [_read_pcm16(path) for path in enrollment_paths]
    enrollment_model = CampPlusEmbeddingModel(asset_dir=asset_dir)
    if not enrollment_model.load():
        raise RuntimeError(enrollment_model.unavailable_reason or "CAM++ load failed")
    try:
        profile = build_campplus_speaker_profile(
            enrollment_model,
            enrollment_pcm16,
            sample_rate_hz=CAMPPLUS_SAMPLE_RATE_HZ,
            profile_revision=1,
        )
        enrollment_metrics = enrollment_model.snapshot()
    finally:
        for index in range(len(enrollment_pcm16)):
            enrollment_pcm16[index] = b""
        enrollment_model.close()

    backend = CampPlusSpeakerShadowBackend(
        profile,
        model_factory=lambda: CampPlusEmbeddingModel(asset_dir=asset_dir),
    )
    if not backend.load():
        profile.close()
        raise RuntimeError("CAM++ candidate backend load failed")
    observations: list[dict[str, object]] = []
    thresholds = (0.40, 0.44, 0.48, 0.52, 0.55)
    try:
        for index, path in enumerate(candidate_paths):
            candidate_pcm16 = _read_pcm16(path, maximum_seconds=4.0)
            try:
                similarity = backend.score(candidate_pcm16, CAMPPLUS_SAMPLE_RATE_HZ)
            finally:
                candidate_pcm16 = b""
            observations.append(
                {
                    "candidate_index": index,
                    "similarity": similarity,
                    "would_block": {
                        f"{threshold:.2f}": similarity < threshold
                        for threshold in thresholds
                    },
                }
            )
        candidate_metrics = backend.snapshot()
    finally:
        backend.close()
        profile.close()
    return {
        "model_id": "iic/speech_campplus_sv_zh_en_16k-common_advanced",
        "model_revision": "v1.0.0",
        "enrollment_segment_count": len(enrollment_paths),
        "observations": observations,
        "enrollment_metrics": enrollment_metrics,
        "candidate_metrics": candidate_metrics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enrollment", type=Path, nargs="+", required=True)
    parser.add_argument("--candidate", type=Path, nargs="+", required=True)
    parser.add_argument("--asset-dir", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(
        enrollment_paths=[path.resolve() for path in args.enrollment],
        candidate_paths=[path.resolve() for path in args.candidate],
        asset_dir=args.asset_dir.resolve() if args.asset_dir else None,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
