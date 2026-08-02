"""Evaluate CAM++ speaker-shadow scores offline without retaining voice data."""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.asr_client.speaker_shadow.campplus import (  # noqa: E402
    CampPlusEmbeddingModel,
    CampPlusSpeakerShadowBackend,
)


SAMPLE_RATE_HZ = 16_000
MINIMUM_AUDIO_SECONDS = 1.5
MAXIMUM_CANDIDATE_SECONDS = 4.0
MINIMUM_REFERENCE_SEGMENTS = 3
MAXIMUM_REFERENCE_SEGMENTS = 5
THRESHOLDS = (0.40, 0.44, 0.48, 0.52, 0.55)


def _read_pcm16(path: Path, *, maximum_seconds: float | None = None) -> bytes:
    try:
        with wave.open(str(path), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != SAMPLE_RATE_HZ
                or source.getcomptype() != "NONE"
            ):
                raise ValueError("all WAV inputs must be mono PCM16LE at 16 kHz")
            frame_count = source.getnframes()
            if maximum_seconds is not None:
                frame_count = min(frame_count, round(SAMPLE_RATE_HZ * maximum_seconds))
            pcm16 = source.readframes(frame_count)
    except (OSError, EOFError, wave.Error):
        raise ValueError("wav_unreadable") from None
    if len(pcm16) < round(SAMPLE_RATE_HZ * MINIMUM_AUDIO_SECONDS) * 2:
        raise ValueError("all voice segments must contain at least 1.5 seconds")
    return pcm16


def _normalized_reference(embeddings: list[np.ndarray]) -> np.ndarray:
    reference = np.mean(
        np.stack(embeddings, axis=0),
        axis=0,
        dtype=np.float32,
    ).astype(np.float32, copy=False)
    norm = float(np.linalg.norm(reference))
    if not np.isfinite(reference).all() or not np.isfinite(norm) or norm <= 0.0:
        reference.fill(0)
        raise ValueError("CAM++ reference embedding must be finite and non-zero")
    reference /= np.float32(norm)
    return reference


def evaluate(
    *,
    reference_paths: list[Path],
    candidate_paths: list[Path],
    asset_dir: Path | None,
) -> dict[str, object]:
    """Return an in-memory score report containing no voice data or paths."""

    if not MINIMUM_REFERENCE_SEGMENTS <= len(reference_paths) <= MAXIMUM_REFERENCE_SEGMENTS:
        raise ValueError("reference requires 3 to 5 voice segments")
    if not candidate_paths:
        raise ValueError("at least one candidate voice segment is required")

    reference_pcm16 = [_read_pcm16(path) for path in reference_paths]
    embeddings: list[np.ndarray] = []
    reference: np.ndarray | None = None
    model = CampPlusEmbeddingModel(asset_dir=asset_dir)
    if not model.load():
        for index in range(len(reference_pcm16)):
            reference_pcm16[index] = b""
        model.close()
        raise RuntimeError("campplus_reference_load_failed")
    try:
        for pcm16 in reference_pcm16:
            embeddings.append(
                model.embedding_from_pcm16(pcm16, sample_rate_hz=SAMPLE_RATE_HZ)
            )
        reference = _normalized_reference(embeddings)
    finally:
        for index in range(len(reference_pcm16)):
            reference_pcm16[index] = b""
        for embedding in embeddings:
            embedding.fill(0)
        model.close()

    assert reference is not None
    backend = CampPlusSpeakerShadowBackend(
        reference,
        asset_dir=asset_dir,
        model_factory=lambda: CampPlusEmbeddingModel(asset_dir=asset_dir),
    )
    reference.fill(0)
    if not backend.load():
        backend.close()
        raise RuntimeError("campplus_candidate_load_failed")

    observations: list[dict[str, object]] = []
    try:
        for index, path in enumerate(candidate_paths):
            candidate_pcm16 = _read_pcm16(
                path,
                maximum_seconds=MAXIMUM_CANDIDATE_SECONDS,
            )
            try:
                similarity = float(backend.score(candidate_pcm16, SAMPLE_RATE_HZ))
            finally:
                candidate_pcm16 = b""
            observations.append(
                {
                    "candidate_index": index,
                    "similarity": similarity,
                    "would_block": {
                        f"{threshold:.2f}": similarity < threshold
                        for threshold in THRESHOLDS
                    },
                }
            )
    finally:
        backend.close()

    return {
        "schema_version": 1,
        "reference_segment_count": len(reference_paths),
        "candidate_count": len(candidate_paths),
        "observations": observations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, nargs="+", required=True)
    parser.add_argument("--candidate", type=Path, nargs="+", required=True)
    parser.add_argument("--asset-dir", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(
        reference_paths=list(args.reference),
        candidate_paths=list(args.candidate),
        asset_dir=args.asset_dir.resolve() if args.asset_dir else None,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
