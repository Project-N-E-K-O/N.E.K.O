"""Verified, offline CAM++ embeddings for observation-only speaker shadowing."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Sequence

import numpy as np

from .speaker_shadow import SpeakerShadowConfig, SpeakerShadowRuntime


CAMPPLUS_FILENAME = "campplus-zh-en-advanced.onnx"
CAMPPLUS_MODEL_ID = "iic/speech_campplus_sv_zh_en_16k-common_advanced"
CAMPPLUS_MODEL_REVISION = "v1.0.0"
CAMPPLUS_SOURCE = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)
CAMPPLUS_SHA256 = "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"
CAMPPLUS_SIZE_BYTES = 28_281_164
CAMPPLUS_SAMPLE_RATE_HZ = 16_000
CAMPPLUS_EMBEDDING_DIM = 192
CAMPPLUS_MINIMUM_SAMPLES = 24_000
_FRAME_LENGTH = 400
_FRAME_SHIFT = 160
_PADDED_FRAME_LENGTH = 512
_MEL_BIN_COUNT = 80
_PREEMPHASIS_COEFFICIENT = np.float32(0.97)
_FLOAT32_EPSILON = np.finfo(np.float32).eps


class CampPlusAssetError(RuntimeError):
    """The pinned CAM++ asset or manifest did not match its reviewed identity."""


@dataclass(frozen=True, slots=True)
class CampPlusManifest:
    filename: str
    model_id: str
    revision: str
    license: str
    source: str
    export_source: str
    size_bytes: int
    sha256: str
    sample_rate_hz: int
    preprocessing: str
    input_contract: str
    output_contract: str


@dataclass(slots=True)
class CampPlusModelMetrics:
    load_count: int = 0
    unload_count: int = 0
    load_failure_count: int = 0
    inference_count: int = 0
    inference_failure_count: int = 0
    asset_verification_ms: int = 0
    onnxruntime_import_ms: int = 0
    session_load_ms: int = 0
    feature_ms: int = 0
    inference_ms: int = 0
    onnxruntime_import_rss_delta_bytes: int = 0
    session_rss_delta_bytes: int = 0
    inference_le_10ms_count: int = 0
    inference_le_25ms_count: int = 0
    inference_le_50ms_count: int = 0
    inference_le_100ms_count: int = 0
    inference_gt_100ms_count: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)


def _validate_manifest_identity(manifest: CampPlusManifest) -> None:
    expected: dict[str, object] = {
        "filename": CAMPPLUS_FILENAME,
        "model_id": CAMPPLUS_MODEL_ID,
        "revision": CAMPPLUS_MODEL_REVISION,
        "license": "Apache-2.0",
        "source": CAMPPLUS_SOURCE,
        "export_source": "k2-fsa/sherpa-onnx",
        "size_bytes": CAMPPLUS_SIZE_BYTES,
        "sha256": CAMPPLUS_SHA256,
        "sample_rate_hz": CAMPPLUS_SAMPLE_RATE_HZ,
        "preprocessing": "kaldi-native-fbank-sniped-global-mean-v1",
        "input_contract": "float32[batch,time,80]",
        "output_contract": "float32[batch,192]",
    }
    for field, expected_value in expected.items():
        actual = getattr(manifest, field)
        if actual != expected_value:
            raise CampPlusAssetError(
                f"CAM++ manifest {field} mismatch: expected {expected_value!r}, got {actual!r}"
            )


def load_campplus_manifest(directory: Path) -> CampPlusManifest:
    manifest_path = directory / "manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampPlusAssetError(f"cannot read CAM++ manifest {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CampPlusAssetError("CAM++ manifest must be a JSON object")
    fields = tuple(CampPlusManifest.__dataclass_fields__)
    missing = [field for field in fields if field not in raw]
    if missing:
        raise CampPlusAssetError(f"CAM++ manifest is missing fields: {', '.join(missing)}")
    try:
        manifest = CampPlusManifest(
            **{
                **{field: str(raw[field]) for field in fields},
                "size_bytes": int(raw["size_bytes"]),
                "sample_rate_hz": int(raw["sample_rate_hz"]),
            }
        )
    except (TypeError, ValueError) as exc:
        raise CampPlusAssetError(f"invalid CAM++ manifest: {exc}") from exc
    if Path(manifest.filename).name != manifest.filename:
        raise CampPlusAssetError("CAM++ filename must not contain a path")
    if len(manifest.sha256) != 64 or any(
        character not in "0123456789abcdef" for character in manifest.sha256.lower()
    ):
        raise CampPlusAssetError("CAM++ manifest SHA-256 is invalid")
    _validate_manifest_identity(manifest)
    return manifest


def _candidate_asset_directories(override: Path | None) -> tuple[Path, ...]:
    if override is not None:
        return (override.resolve(),)
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "data" / "speaker_models")
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        candidates.append(
            Path(sys.executable).resolve().parent / "data" / "speaker_models"
        )
    candidates.append(Path(__file__).resolve().parents[2] / "data" / "speaker_models")
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return tuple(unique)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CampPlusAssetError(f"cannot read CAM++ model {path}: {exc}") from exc
    return digest.hexdigest()


def verify_campplus_asset(directory: Path) -> Path:
    """Verify the pinned model in exactly ``directory`` without fallbacks."""

    manifest = load_campplus_manifest(directory)
    model_path = directory / manifest.filename
    if not model_path.is_file():
        raise CampPlusAssetError(f"CAM++ model is missing: {model_path}")
    actual_size = model_path.stat().st_size
    if actual_size != manifest.size_bytes:
        raise CampPlusAssetError(
            f"CAM++ model size mismatch: expected {manifest.size_bytes}, got {actual_size}"
        )
    actual_sha256 = _sha256_file(model_path)
    if actual_sha256.lower() != manifest.sha256.lower():
        raise CampPlusAssetError(
            "CAM++ model SHA-256 mismatch: "
            f"expected {manifest.sha256}, got {actual_sha256}"
        )
    return model_path


def resolve_verified_campplus_asset(asset_dir: Path | None = None) -> Path:
    failures: list[str] = []
    for directory in _candidate_asset_directories(asset_dir):
        try:
            return verify_campplus_asset(directory)
        except CampPlusAssetError as exc:
            failures.append(str(exc))
    reason = "; ".join(failures) if failures else "no candidate directories"
    raise CampPlusAssetError(f"no verified CAM++ asset: {reason}")


def _povey_window() -> np.ndarray:
    indices = np.arange(_FRAME_LENGTH, dtype=np.float64)
    window = (0.5 - 0.5 * np.cos(2 * np.pi * indices / (_FRAME_LENGTH - 1))) ** 0.85
    return window.astype(np.float32)


def _mel_scale(frequency_hz: float | np.ndarray) -> float | np.ndarray:
    return 1127.0 * np.log1p(np.asarray(frequency_hz) / 700.0)


def _mel_filter_bank() -> np.ndarray:
    low_mel = float(_mel_scale(20.0))
    high_mel = float(_mel_scale(CAMPPLUS_SAMPLE_RATE_HZ / 2))
    mel_delta = (high_mel - low_mel) / (_MEL_BIN_COUNT + 1)
    frequencies = (
        np.arange(_PADDED_FRAME_LENGTH // 2 + 1, dtype=np.float64)
        * CAMPPLUS_SAMPLE_RATE_HZ
        / _PADDED_FRAME_LENGTH
    )
    mel_frequencies = _mel_scale(frequencies)
    filters = np.zeros(
        (_MEL_BIN_COUNT, _PADDED_FRAME_LENGTH // 2 + 1), dtype=np.float32
    )
    segments = np.floor((mel_frequencies - low_mel) / mel_delta).astype(np.int32)
    for fft_bin, segment in enumerate(segments):
        if segment < 0 or segment > _MEL_BIN_COUNT:
            continue
        weight = (
            mel_frequencies[fft_bin] - (low_mel + segment * mel_delta)
        ) / mel_delta
        if segment < _MEL_BIN_COUNT:
            filters[segment, fft_bin] += np.float32(weight)
        if segment > 0:
            filters[segment - 1, fft_bin] += np.float32(1.0 - weight)
    return filters


_POVEY_WINDOW = _povey_window()
_MEL_FILTER_BANK = _mel_filter_bank()


def compute_campplus_features(pcm16: bytes, *, sample_rate_hz: int) -> np.ndarray:
    """Match the official sherpa-onnx 3D-Speaker Kaldi fbank frontend."""

    if sample_rate_hz != CAMPPLUS_SAMPLE_RATE_HZ:
        raise ValueError("CAM++ requires sample_rate_hz=16000")
    if not isinstance(pcm16, bytes) or not pcm16:
        raise ValueError("CAM++ requires non-empty PCM16LE bytes")
    if len(pcm16) % 2:
        raise ValueError("CAM++ PCM16LE byte length must be even")
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / np.float32(32768.0)
    if samples.size < _FRAME_LENGTH:
        raise ValueError("CAM++ requires enough PCM for at least one frame")
    frame_count = 1 + (samples.size - _FRAME_LENGTH) // _FRAME_SHIFT
    shape = (frame_count, _FRAME_LENGTH)
    strides = (samples.strides[0] * _FRAME_SHIFT, samples.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        samples,
        shape=shape,
        strides=strides,
        writeable=False,
    ).copy()
    frames -= frames.mean(axis=1, keepdims=True, dtype=np.float32)
    frames[:, 1:] -= _PREEMPHASIS_COEFFICIENT * frames[:, :-1]
    frames[:, 0] *= np.float32(1.0) - _PREEMPHASIS_COEFFICIENT
    frames *= _POVEY_WINDOW
    spectrum = np.fft.rfft(frames, n=_PADDED_FRAME_LENGTH, axis=1)
    power = (spectrum.real * spectrum.real + spectrum.imag * spectrum.imag).astype(
        np.float32
    )
    mel_energies = power @ _MEL_FILTER_BANK.T
    np.maximum(mel_energies, _FLOAT32_EPSILON, out=mel_energies)
    features = np.log(mel_energies, dtype=np.float32)
    features -= features.mean(axis=0, keepdims=True, dtype=np.float32)
    return np.ascontiguousarray(features, dtype=np.float32)


def _process_rss_bytes() -> int:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except Exception:
        return 0


class CampPlusEmbeddingModel:
    """Lazy single-session CPU runtime for the pinned CAM++ ONNX export."""

    model_id = CAMPPLUS_MODEL_ID
    model_revision = CAMPPLUS_MODEL_REVISION

    def __init__(self, *, asset_dir: Path | None = None) -> None:
        self._asset_dir = asset_dir
        self._session: Any | None = None
        self._state = "unloaded"
        self._reason: str | None = None
        self._load_lock = Lock()
        self._inference_lock = Lock()
        self._metrics = CampPlusModelMetrics()

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    @property
    def is_ready(self) -> bool:
        return self._state == "ready" and self._session is not None

    def snapshot(self) -> dict[str, int]:
        return self._metrics.snapshot()

    def validate_assets(self) -> Path:
        started = time.perf_counter()
        try:
            return resolve_verified_campplus_asset(self._asset_dir)
        finally:
            self._metrics.asset_verification_ms += int(
                (time.perf_counter() - started) * 1_000
            )

    def load(self) -> bool:
        if self.is_ready:
            return True
        if self._state in {"closed", "unavailable"}:
            return False
        with self._load_lock:
            if self.is_ready:
                return True
            if self._state in {"closed", "unavailable"}:
                return False
            try:
                model_path = self.validate_assets()
                rss_before_import = _process_rss_bytes()
                import_started = time.perf_counter()
                import onnxruntime as ort

                self._metrics.onnxruntime_import_ms += int(
                    (time.perf_counter() - import_started) * 1_000
                )
                rss_after_import = _process_rss_bytes()
                self._metrics.onnxruntime_import_rss_delta_bytes += max(
                    0, rss_after_import - rss_before_import
                )
                options = ort.SessionOptions()
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                options.enable_cpu_mem_arena = False
                session_started = time.perf_counter()
                session = ort.InferenceSession(
                    str(model_path),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                self._validate_session_contract(session)
                self._metrics.session_load_ms += int(
                    (time.perf_counter() - session_started) * 1_000
                )
                self._metrics.session_rss_delta_bytes += max(
                    0, _process_rss_bytes() - rss_after_import
                )
            except Exception as exc:
                self._session = None
                self._state = "unavailable"
                self._reason = f"load_error:{exc}"
                self._metrics.load_failure_count += 1
                return False
            self._session = session
            self._state = "ready"
            self._reason = None
            self._metrics.load_count += 1
            return True

    @staticmethod
    def _validate_session_contract(session: Any) -> None:
        inputs = session.get_inputs()
        outputs = session.get_outputs()
        if len(inputs) != 1:
            raise ValueError("CAM++ input count must be 1")
        if inputs[0].name != "x":
            raise ValueError("CAM++ input name must be x")
        if inputs[0].type != "tensor(float)":
            raise ValueError("CAM++ input type must be tensor(float)")
        if len(inputs[0].shape) != 3 or inputs[0].shape[2] != 80:
            raise ValueError("CAM++ input shape must be [N,T,80]")
        if len(outputs) != 1:
            raise ValueError("CAM++ output count must be 1")
        if outputs[0].name != "embedding":
            raise ValueError("CAM++ output name must be embedding")
        if outputs[0].type != "tensor(float)":
            raise ValueError("CAM++ output type must be tensor(float)")
        if len(outputs[0].shape) != 2 or outputs[0].shape[1] != 192:
            raise ValueError("CAM++ output shape must be [N,192]")
        metadata = session.get_modelmeta().custom_metadata_map
        expected_metadata = {
            "framework": "3d-speaker",
            "language": "Chinese-English",
            "feature_normalize_type": "global-mean",
            "sample_rate": "16000",
            "output_dim": "192",
            "normalize_samples": "1",
        }
        for key, expected in expected_metadata.items():
            if metadata.get(key) != expected:
                raise ValueError(f"CAM++ metadata {key} must be {expected}")

    def embedding_from_pcm16(self, pcm16: bytes, *, sample_rate_hz: int) -> np.ndarray:
        if not self.is_ready:
            raise RuntimeError(self._reason or "CAM++ model is not loaded")
        if not isinstance(pcm16, bytes) or not pcm16:
            raise ValueError("CAM++ requires non-empty PCM16LE bytes")
        if len(pcm16) % 2:
            raise ValueError("CAM++ PCM16LE byte length must be even")
        if sample_rate_hz != CAMPPLUS_SAMPLE_RATE_HZ:
            raise ValueError("CAM++ requires sample_rate_hz=16000")
        if len(pcm16) // 2 < CAMPPLUS_MINIMUM_SAMPLES:
            raise ValueError("CAM++ requires at least 1.5 seconds of audio")
        feature_started = time.perf_counter()
        features = compute_campplus_features(pcm16, sample_rate_hz=sample_rate_hz)
        self._metrics.feature_ms += int((time.perf_counter() - feature_started) * 1_000)
        inference_started = time.perf_counter()
        try:
            with self._inference_lock:
                session = self._session
                if session is None or self._state != "ready":
                    raise RuntimeError("CAM++ model was unloaded")
                outputs = session.run(
                    ["embedding"],
                    {"x": features[np.newaxis, :, :]},
                )
            embedding = np.asarray(outputs[0], dtype=np.float32)
            if embedding.shape != (1, CAMPPLUS_EMBEDDING_DIM):
                raise ValueError(
                    f"CAM++ output shape changed: expected (1, 192), got {embedding.shape}"
                )
            result = np.array(embedding[0], dtype=np.float32, copy=True)
            if not np.isfinite(result).all():
                raise ValueError("CAM++ embedding contains non-finite values")
            norm = float(np.linalg.norm(result))
            if not math.isfinite(norm) or norm <= 1e-12:
                raise ValueError("CAM++ embedding has invalid L2 norm")
            result /= np.float32(norm)
        except Exception:
            self._metrics.inference_failure_count += 1
            raise
        elapsed_ms = (time.perf_counter() - inference_started) * 1_000
        self._metrics.inference_ms += int(elapsed_ms)
        self._record_inference_latency(elapsed_ms)
        self._metrics.inference_count += 1
        return result

    def _record_inference_latency(self, elapsed_ms: float) -> None:
        if elapsed_ms <= 10:
            self._metrics.inference_le_10ms_count += 1
        elif elapsed_ms <= 25:
            self._metrics.inference_le_25ms_count += 1
        elif elapsed_ms <= 50:
            self._metrics.inference_le_50ms_count += 1
        elif elapsed_ms <= 100:
            self._metrics.inference_le_100ms_count += 1
        else:
            self._metrics.inference_gt_100ms_count += 1

    def unload(self) -> bool:
        with self._load_lock:
            with self._inference_lock:
                if self._state == "closed":
                    return False
                if self._state == "unloaded":
                    return True
                if self._state == "unavailable":
                    return False
                self._session = None
                self._state = "unloaded"
                self._reason = None
                self._metrics.unload_count += 1
                return True

    def close(self) -> None:
        with self._load_lock:
            with self._inference_lock:
                if self._state == "closed":
                    return
                if self._session is not None:
                    self._metrics.unload_count += 1
                self._session = None
                self._state = "closed"
                self._reason = "closed"


class CampPlusSpeakerProfile:
    """In-memory reference embedding with no enrollment PCM or user identity."""

    def __init__(
        self,
        reference_embedding: np.ndarray,
        *,
        profile_revision: int,
        model_id: str = CAMPPLUS_MODEL_ID,
        model_revision: str = CAMPPLUS_MODEL_REVISION,
    ) -> None:
        if profile_revision < 0:
            raise ValueError("profile_revision must not be negative")
        embedding = np.array(reference_embedding, dtype=np.float32, copy=True)
        if embedding.shape != (CAMPPLUS_EMBEDDING_DIM,):
            raise ValueError("reference embedding must have shape (192,)")
        if not np.isfinite(embedding).all():
            raise ValueError("reference embedding must contain only finite values")
        norm = float(np.linalg.norm(embedding))
        if not math.isfinite(norm) or norm <= 1e-12:
            raise ValueError("reference embedding must have a non-zero L2 norm")
        embedding /= np.float32(norm)
        self._reference_embedding = embedding
        self._profile_revision = int(profile_revision)
        self._model_id = str(model_id)
        self._model_revision = str(model_revision)
        self._closed = False

    @property
    def reference_embedding(self) -> np.ndarray:
        if self._closed:
            raise RuntimeError("speaker profile is closed")
        return np.array(self._reference_embedding, dtype=np.float32, copy=True)

    @property
    def profile_revision(self) -> int:
        return self._profile_revision

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_revision(self) -> str:
        return self._model_revision

    def close(self) -> None:
        if self._closed:
            return
        self._reference_embedding.fill(0)
        self._closed = True


def build_campplus_speaker_profile(
    model: Any,
    enrollment_pcm16: Sequence[bytes],
    *,
    sample_rate_hz: int,
    profile_revision: int,
) -> CampPlusSpeakerProfile:
    segments = tuple(enrollment_pcm16)
    if not 3 <= len(segments) <= 5:
        raise ValueError("CAM++ enrollment requires 3 to 5 segments")
    if sample_rate_hz != CAMPPLUS_SAMPLE_RATE_HZ:
        raise ValueError("CAM++ enrollment requires sample_rate_hz=16000")
    for segment in segments:
        if not isinstance(segment, bytes) or not segment or len(segment) % 2:
            raise ValueError("CAM++ enrollment requires valid PCM16LE segments")
        if len(segment) // 2 < CAMPPLUS_MINIMUM_SAMPLES:
            raise ValueError("each CAM++ enrollment segment requires at least 1.5 seconds")
    embeddings: list[np.ndarray] = []
    try:
        for segment in segments:
            embedding = model.embedding_from_pcm16(
                segment,
                sample_rate_hz=sample_rate_hz,
            )
            embeddings.append(np.array(embedding, dtype=np.float32, copy=True))
        mean_embedding = np.mean(np.stack(embeddings, axis=0), axis=0, dtype=np.float32)
        return CampPlusSpeakerProfile(
            mean_embedding,
            profile_revision=profile_revision,
            model_id=str(getattr(model, "model_id", CAMPPLUS_MODEL_ID)),
            model_revision=str(
                getattr(model, "model_revision", CAMPPLUS_MODEL_REVISION)
            ),
        )
    finally:
        for embedding in embeddings:
            embedding.fill(0)


class CampPlusSpeakerShadowBackend:
    """Convert candidate PCM to a cosine score without execution authority."""

    def __init__(
        self,
        profile: CampPlusSpeakerProfile,
        *,
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        if profile.model_id != CAMPPLUS_MODEL_ID:
            raise ValueError("speaker profile model_id does not match CAM++")
        if profile.model_revision != CAMPPLUS_MODEL_REVISION:
            raise ValueError("speaker profile model_revision does not match CAM++")
        self._reference_embedding = profile.reference_embedding
        self._profile_revision = profile.profile_revision
        self._model_factory = model_factory or CampPlusEmbeddingModel
        self._model: Any | None = None
        self._model_metrics: dict[str, int] = {}
        self._closed = False

    @property
    def profile_revision(self) -> int:
        return self._profile_revision

    def load(self) -> bool:
        if self._closed:
            return False
        if self._model is not None:
            return True
        model = self._model_factory()
        if not bool(model.load()):
            model.close()
            return False
        if getattr(model, "model_id", None) != CAMPPLUS_MODEL_ID or getattr(
            model, "model_revision", None
        ) != CAMPPLUS_MODEL_REVISION:
            model.close()
            return False
        self._model = model
        return True

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        if self._closed:
            raise RuntimeError("CAM++ speaker backend is closed")
        model = self._model
        if model is None:
            raise RuntimeError("CAM++ speaker backend is not loaded")
        candidate = model.embedding_from_pcm16(
            pcm16,
            sample_rate_hz=sample_rate_hz,
        )
        similarity = float(np.dot(self._reference_embedding, candidate))
        if not math.isfinite(similarity):
            raise ValueError("CAM++ cosine similarity is not finite")
        return min(1.0, max(-1.0, similarity))

    def snapshot(self) -> dict[str, int]:
        model = self._model
        return dict(self._model_metrics) if model is None else dict(model.snapshot())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        model, self._model = self._model, None
        if model is not None:
            model.close()
            self._model_metrics = dict(model.snapshot())
        self._reference_embedding.fill(0)


class _CampPlusBackendFactory:
    def __init__(
        self,
        profile: CampPlusSpeakerProfile,
        *,
        asset_dir: Path | None,
        model_factory: Callable[..., Any],
    ) -> None:
        self._profile = profile
        self._asset_dir = asset_dir
        self._model_factory = model_factory
        self._closed = False

    def __call__(self) -> CampPlusSpeakerShadowBackend:
        if self._closed:
            raise RuntimeError("CAM++ backend factory is closed")
        return CampPlusSpeakerShadowBackend(
            self._profile,
            model_factory=lambda: self._model_factory(asset_dir=self._asset_dir),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._profile.close()


class CampPlusSpeakerShadowFactory:
    """Private session factory: disabled/missing profiles are exactly zero-work."""

    def __init__(
        self,
        *,
        enabled: bool,
        profile: CampPlusSpeakerProfile | None,
        asset_dir: Path | None = None,
        model_factory: Callable[..., Any] = CampPlusEmbeddingModel,
        on_observation: Callable[..., Any] | None = None,
        config: SpeakerShadowConfig | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._profile = profile
        self._asset_dir = asset_dir
        self._model_factory = model_factory
        self._on_observation = on_observation
        self._config = config

    def __call__(self) -> SpeakerShadowRuntime | None:
        profile = self._profile
        if not self._enabled or profile is None:
            return None
        if (
            profile.model_id != CAMPPLUS_MODEL_ID
            or profile.model_revision != CAMPPLUS_MODEL_REVISION
        ):
            return None
        try:
            resolve_verified_campplus_asset(self._asset_dir)
            profile_snapshot = CampPlusSpeakerProfile(
                profile.reference_embedding,
                profile_revision=profile.profile_revision,
                model_id=profile.model_id,
                model_revision=profile.model_revision,
            )
        except (CampPlusAssetError, RuntimeError, ValueError):
            return None

        create_backend = _CampPlusBackendFactory(
            profile_snapshot,
            asset_dir=self._asset_dir,
            model_factory=self._model_factory,
        )

        return SpeakerShadowRuntime(
            backend_factory=create_backend,
            config=self._config or SpeakerShadowConfig(enabled=True),
            on_observation=self._on_observation,
        )
