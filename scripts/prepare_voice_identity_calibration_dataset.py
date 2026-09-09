"""Build privacy-minimized voice-identity calibration examples from WAV trials."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any
import wave

import numpy as np

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
    CAMPPLUS_SAMPLE_RATE_HZ,
)
from main_logic.asr_client.speaker_shadow.campplus import (
    CAMPPLUS_EMBEDDING_DIM,
    CampPlusEmbeddingModel,
)
from main_logic.asr_client.speaker_shadow.runtime import _summarize_pcm_quality
from main_logic.voice_identity_service.calibration import (
    DATASET_SCHEMA_VERSION,
    CalibrationError,
    CalibrationExample,
    CalibrationFeatures,
    CalibrationOutcome,
    CalibrationProtocol,
    DatasetSplit,
    TERMINAL_SHORT_MAXIMUM_AUDIO_MS_EXCLUSIVE,
    TERMINAL_SHORT_MINIMUM_AUDIO_MS,
)
from main_logic.voice_identity_service.audio_contract import (
    OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
    OWNER_CAMPPLUS_DESKTOP_CONTRACT_REVISION,
)
from main_logic.voice_identity_service.enrollment import (
    create_enrollment_reference_centroid,
    wipe_enrollment_embedding,
)


_TRIAL_KEYS = {
    "example_id",
    "group_id",
    "reference_speaker_id",
    "candidate_speaker_id",
    "candidate_session_id",
    "reference_session_ids",
    "candidate_recording_family_id",
    "reference_recording_family_ids",
    "source_recording_id",
    "reference_recording_ids",
    "split",
    "label",
    "candidate_wav",
    "reference_wavs",
}
_REFERENCE_SAMPLES = CAMPPLUS_SAMPLE_RATE_HZ * 3
_REFERENCE_BYTES = _REFERENCE_SAMPLES * 2
_CANDIDATE_MIN_SAMPLES = 720
_CANDIDATE_MAX_SAMPLES_EXCLUSIVE = 24_000


def _reject_json_constant(value: str) -> None:
    raise CalibrationError(f"non-finite JSON number is not allowed: {value}")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CalibrationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )
    if type(value) is not dict or set(value) != {"protocol", "trials"}:
        raise CalibrationError("manifest must contain exactly protocol and trials")
    if type(value["protocol"]) is not dict or type(value["trials"]) is not list:
        raise CalibrationError("manifest protocol/trials types are invalid")
    return value


def _read_wav(path: Path) -> bytearray:
    try:
        with wave.open(str(path), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != CAMPPLUS_SAMPLE_RATE_HZ
                or source.getcomptype() != "NONE"
            ):
                raise CalibrationError(
                    "wav_must_be_owner_contract_mono_pcm16le_16khz_output"
                )
            expected = source.getnframes() * 2
            raw = source.readframes(source.getnframes())
    except (EOFError, OSError, wave.Error):
        raise CalibrationError("wav_unreadable") from None
    if len(raw) != expected:
        raise CalibrationError("wav_truncated")
    result = bytearray(raw)
    raw = b""
    return result


def _wipe_bytes(value: bytearray | None) -> None:
    if value is not None:
        value[:] = b"\x00" * len(value)


def _trial_metadata(value: Mapping[str, Any], protocol: CalibrationProtocol) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _TRIAL_KEYS:
        raise CalibrationError("trial fields do not match schema")
    for name in (
        "reference_session_ids",
        "reference_recording_family_ids",
        "reference_recording_ids",
        "reference_wavs",
    ):
        if type(value[name]) is not list or len(value[name]) != 3:
            raise CalibrationError(f"{name} must contain exactly three items")
    if type(value["candidate_wav"]) is not str or not value["candidate_wav"]:
        raise CalibrationError("candidate_wav must be a non-empty string")
    if any(type(item) is not str or not item for item in value["reference_wavs"]):
        raise CalibrationError("reference_wavs entries must be non-empty strings")
    return {
        "example_id": value["example_id"],
        "group_id": value["group_id"],
        "reference_speaker_id": value["reference_speaker_id"],
        "candidate_speaker_id": value["candidate_speaker_id"],
        "candidate_session_id": value["candidate_session_id"],
        "reference_session_ids": tuple(value["reference_session_ids"]),
        "candidate_recording_family_id": value["candidate_recording_family_id"],
        "reference_recording_family_ids": tuple(value["reference_recording_family_ids"]),
        "source_recording_id": value["source_recording_id"],
        "reference_recording_ids": tuple(value["reference_recording_ids"]),
        "split": DatasetSplit(value["split"]),
        "label": CalibrationOutcome(value["label"]),
        "protocol": protocol,
    }


def _check_protocol(protocol: CalibrationProtocol, model: Any) -> None:
    if (
        protocol.model_id != CAMPPLUS_MODEL_ID
        or protocol.model_revision != CAMPPLUS_MODEL_REVISION
        or protocol.embedding_dimension != CAMPPLUS_EMBEDDING_DIM
        or protocol.preprocessing_contract_id
        != OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID
        or protocol.preprocessing_revision
        != OWNER_CAMPPLUS_DESKTOP_CONTRACT_REVISION
        or getattr(model, "model_id", None) != protocol.model_id
        or getattr(model, "model_revision", None) != protocol.model_revision
    ):
        raise CalibrationError("manifest protocol does not match the loaded CAMPPlus model")


def _reject_manifest_reference_substrings(
    trials: list[object], *, protocol: CalibrationProtocol, manifest_dir: Path
) -> None:
    reference_paths: list[Path] = []
    for value in trials:
        _trial_metadata(value, protocol)
        candidate_path = (manifest_dir / value["candidate_wav"]).resolve()
        trial_reference_paths = [
            (manifest_dir / path).resolve() for path in value["reference_wavs"]
        ]
        normalized = [
            os.path.normcase(str(path))
            for path in (candidate_path, *trial_reference_paths)
        ]
        if len(set(normalized)) != 4:
            raise CalibrationError("trial WAV paths must be distinct")
        trial_digests: list[str] = []
        for reference_path in trial_reference_paths:
            reference = _read_wav(reference_path)
            try:
                if len(reference) < _REFERENCE_BYTES:
                    raise CalibrationError("reference_wav_must_contain_at_least_3_seconds")
                trial_digests.append(
                    hashlib.sha256(memoryview(reference)[:_REFERENCE_BYTES]).hexdigest()
                )
            finally:
                _wipe_bytes(reference)
        if len(set(trial_digests)) != 3:
            raise CalibrationError("reference model inputs must have distinct content")
        reference_paths.extend(trial_reference_paths)
    for value in trials:
        candidate = _read_wav(manifest_dir / value["candidate_wav"])
        try:
            sample_count = len(candidate) // 2
            audio_ms = sample_count * 1000.0 / CAMPPLUS_SAMPLE_RATE_HZ
            if not (
                _CANDIDATE_MIN_SAMPLES
                <= sample_count
                < _CANDIDATE_MAX_SAMPLES_EXCLUSIVE
            ):
                raise CalibrationError(
                    "candidate_wav_requires_720_to_23999_samples_terminal_short_domain"
                )
            if not (
                TERMINAL_SHORT_MINIMUM_AUDIO_MS <= audio_ms
                < TERMINAL_SHORT_MAXIMUM_AUDIO_MS_EXCLUSIVE
            ):
                raise CalibrationError("candidate_wav_terminal_short_duration_mismatch")
            for reference_path in reference_paths:
                reference = _read_wav(reference_path)
                try:
                    if len(reference) < _REFERENCE_BYTES:
                        raise CalibrationError("reference_wav_must_contain_at_least_3_seconds")
                    if len(reference) > _REFERENCE_BYTES:
                        reference[_REFERENCE_BYTES:] = b"\x00" * (
                            len(reference) - _REFERENCE_BYTES
                        )
                        del reference[_REFERENCE_BYTES:]
                    if candidate in reference:
                        raise CalibrationError("candidate content is a reference-content substring")
                finally:
                    _wipe_bytes(reference)
        finally:
            _wipe_bytes(candidate)


def _build_trial(
    value: Mapping[str, Any],
    *,
    protocol: CalibrationProtocol,
    model: Any,
    manifest_dir: Path,
) -> tuple[CalibrationExample, tuple[tuple[str, str, DatasetSplit], ...]]:
    metadata = _trial_metadata(value, protocol)
    candidate_path = (manifest_dir / value["candidate_wav"]).resolve()
    reference_paths = tuple(
        (manifest_dir / relative_path).resolve() for relative_path in value["reference_wavs"]
    )
    normalized_paths = [os.path.normcase(str(path)) for path in (candidate_path, *reference_paths)]
    if len(set(normalized_paths)) != 4:
        raise CalibrationError("trial WAV paths must be distinct")
    reference_embeddings: list[np.ndarray] = []
    reference_pcm_inputs: list[bytearray] = []
    reference_digests: list[str] = []
    centroid: np.ndarray | None = None
    candidate_embedding: np.ndarray | None = None
    candidate_pcm: bytearray | None = None
    try:
        for reference_path in reference_paths:
            pcm = _read_wav(reference_path)
            reference_pcm_inputs.append(pcm)
            raw_reference: np.ndarray | None = None
            try:
                if len(pcm) < _REFERENCE_BYTES:
                    raise CalibrationError("reference_wav_must_contain_at_least_3_seconds")
                if len(pcm) > _REFERENCE_BYTES:
                    pcm[_REFERENCE_BYTES:] = b"\x00" * (len(pcm) - _REFERENCE_BYTES)
                    del pcm[_REFERENCE_BYTES:]
                reference_digests.append(hashlib.sha256(pcm).hexdigest())
                # The existing CAMPPlus public contract accepts only ``bytes``.
                # Keep this immutable copy call-scoped; the owned bytearray is
                # still overwritten below on every exit path.
                raw_reference = model.embedding_from_pcm16(
                    bytes(pcm), sample_rate_hz=CAMPPLUS_SAMPLE_RATE_HZ
                )
                reference_embeddings.append(
                    np.array(raw_reference, dtype=np.float32, copy=True)
                )
            finally:
                wipe_enrollment_embedding(raw_reference)
        if len(set(reference_digests)) != 3:
            raise CalibrationError("reference model inputs must have distinct content")
        centroid = create_enrollment_reference_centroid(reference_embeddings)
        candidate_pcm = _read_wav(candidate_path)
        if not (
            _CANDIDATE_MIN_SAMPLES
            <= len(candidate_pcm) // 2
            < _CANDIDATE_MAX_SAMPLES_EXCLUSIVE
        ):
            raise CalibrationError(
                "candidate_wav_requires_720_to_23999_samples_terminal_short_domain"
            )
        if any(candidate_pcm in reference_pcm for reference_pcm in reference_pcm_inputs):
            raise CalibrationError("candidate content is a reference-content substring")
        candidate_digest = hashlib.sha256(candidate_pcm).hexdigest()
        quality = _summarize_pcm_quality(candidate_pcm)
        raw_candidate: np.ndarray | None = None
        try:
            # See the reference path above for the unavoidable bytes boundary.
            raw_candidate = model.probe_short_input_embedding_from_pcm16(
                bytes(candidate_pcm), sample_rate_hz=CAMPPLUS_SAMPLE_RATE_HZ
            )
            candidate_embedding = np.array(raw_candidate, dtype=np.float32, copy=True)
        finally:
            wipe_enrollment_embedding(raw_candidate)
        similarity = float(np.dot(centroid, candidate_embedding))
        if not math.isfinite(similarity):
            raise CalibrationError("candidate similarity is non-finite")
        features = CalibrationFeatures(
            raw_similarity=max(-1.0, min(1.0, similarity)),
            audio_ms=len(candidate_pcm) * 500.0 / CAMPPLUS_SAMPLE_RATE_HZ,
            rms=quality.rms_milli / 1_000 if quality.rms_milli is not None else None,
            peak=quality.peak_milli / 1_000 if quality.peak_milli is not None else None,
            near_silence=(
                quality.near_silence_ratio_milli / 1_000
                if quality.near_silence_ratio_milli is not None
                else None
            ),
            clipping=(
                quality.clipping_ratio_milli / 1_000
                if quality.clipping_ratio_milli is not None
                else None
            ),
        )
        example = CalibrationExample(**metadata, features=features)
        bindings = (
            (example.source_recording_id, candidate_digest, example.split),
            *tuple(
                (recording_id, digest, example.split)
                for recording_id, digest in zip(
                    example.reference_recording_ids,
                    reference_digests,
                    strict=True,
                )
            ),
        )
        return example, bindings
    finally:
        _wipe_bytes(candidate_pcm)
        wipe_enrollment_embedding(candidate_embedding)
        wipe_enrollment_embedding(centroid)
        for embedding in reference_embeddings:
            wipe_enrollment_embedding(embedding)
        for pcm in reference_pcm_inputs:
            _wipe_bytes(pcm)


def build_dataset(
    manifest: Mapping[str, Any],
    *,
    manifest_dir: Path,
    model_factory: Callable[[], Any] = CampPlusEmbeddingModel,
) -> dict[str, Any]:
    if type(manifest) is not dict or set(manifest) != {"protocol", "trials"}:
        raise CalibrationError("manifest fields do not match schema")
    protocol = CalibrationProtocol.from_dict(manifest["protocol"])
    if type(manifest["trials"]) is not list or not manifest["trials"]:
        raise CalibrationError("manifest trials must be a non-empty JSON array")
    model = model_factory()
    try:
        if not bool(model.load()):
            raise CalibrationError("CAMPPlus model could not be loaded")
        _check_protocol(protocol, model)
        _reject_manifest_reference_substrings(
            manifest["trials"], protocol=protocol, manifest_dir=manifest_dir
        )
        built = [
            _build_trial(item, protocol=protocol, model=model, manifest_dir=manifest_dir)
            for item in manifest["trials"]
        ]
        by_recording_id: dict[str, tuple[str, DatasetSplit]] = {}
        by_digest: dict[str, tuple[str, DatasetSplit]] = {}
        for _example, bindings in built:
            for recording_id, digest, split in bindings:
                existing_recording = by_recording_id.setdefault(recording_id, (digest, split))
                if existing_recording != (digest, split):
                    raise CalibrationError("recording ID maps to inconsistent content or split")
                existing_digest = by_digest.setdefault(digest, (recording_id, split))
                if existing_digest != (recording_id, split):
                    raise CalibrationError("recording content is aliased or crosses dataset splits")
        examples = [example for example, _bindings in built]
        return {
            "schema_version": DATASET_SCHEMA_VERSION,
            "examples": [example.to_dict() for example in examples],
        }
    finally:
        model.close()


def write_dataset_atomic(path: Path, dataset: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump(dataset, output, ensure_ascii=True, sort_keys=True, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build calibration examples from WAV files that are already the "
            "declared owner desktop preprocessing contract's mono PCM16LE 16 kHz output. "
            "References use their first 3.0 seconds; candidates use the complete WAV and "
            "must contain 720-23,999 samples (45 ms inclusive to 1,500 ms exclusive)."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help=(
            "strict manifest; reference WAVs need >=3.0s and candidate WAVs need "
            "720-23,999 samples of protocol-bound 16 kHz PCM output"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        dataset = build_dataset(manifest, manifest_dir=args.manifest.parent)
        write_dataset_atomic(args.output, dataset)
    except Exception:
        parser.error("calibration dataset build failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
