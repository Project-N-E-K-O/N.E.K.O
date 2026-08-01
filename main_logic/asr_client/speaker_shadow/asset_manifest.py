"""Pinned CAM++ asset resolution and verification without runtime downloads."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CAMPPLUS_FILENAME = "campplus-zh-en-advanced.onnx"
CAMPPLUS_MODEL_ID = "iic/speech_campplus_sv_zh_en_16k-common_advanced"
CAMPPLUS_MODEL_REVISION = "v1.0.0"
CAMPPLUS_LICENSE = "Apache-2.0"
CAMPPLUS_SOURCE = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)
CAMPPLUS_EXPORT_SOURCE = "k2-fsa/sherpa-onnx"
CAMPPLUS_SIZE_BYTES = 28_281_164
CAMPPLUS_SHA256 = "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"
CAMPPLUS_SAMPLE_RATE_HZ = 16_000
CAMPPLUS_PREPROCESSING = "kaldi-native-fbank-sniped-global-mean-v1"
CAMPPLUS_INPUT_CONTRACT = "float32[batch,time,80]"
CAMPPLUS_OUTPUT_CONTRACT = "float32[batch,192]"

MANIFEST_FILENAME = "manifest.json"
ASSET_RELATIVE_PATH = (
    Path("main_logic") / "asr_client" / "speaker_shadow" / "models"
)


class CampPlusAssetError(RuntimeError):
    """A low-cardinality CAM++ asset validation failure."""


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


_EXPECTED_MANIFEST = CampPlusManifest(
    filename=CAMPPLUS_FILENAME,
    model_id=CAMPPLUS_MODEL_ID,
    revision=CAMPPLUS_MODEL_REVISION,
    license=CAMPPLUS_LICENSE,
    source=CAMPPLUS_SOURCE,
    export_source=CAMPPLUS_EXPORT_SOURCE,
    size_bytes=CAMPPLUS_SIZE_BYTES,
    sha256=CAMPPLUS_SHA256,
    sample_rate_hz=CAMPPLUS_SAMPLE_RATE_HZ,
    preprocessing=CAMPPLUS_PREPROCESSING,
    input_contract=CAMPPLUS_INPUT_CONTRACT,
    output_contract=CAMPPLUS_OUTPUT_CONTRACT,
)


def _manifest_from_mapping(value: dict[str, Any]) -> CampPlusManifest:
    fields = frozenset(CampPlusManifest.__dataclass_fields__)
    if set(value) != fields:
        raise CampPlusAssetError("manifest_invalid")
    string_fields = fields - {"size_bytes", "sample_rate_hz"}
    if any(not isinstance(value[field], str) for field in string_fields):
        raise CampPlusAssetError("manifest_invalid")
    if type(value["size_bytes"]) is not int or value["size_bytes"] <= 0:
        raise CampPlusAssetError("manifest_invalid")
    if type(value["sample_rate_hz"]) is not int:
        raise CampPlusAssetError("manifest_invalid")
    if Path(value["filename"]).name != value["filename"]:
        raise CampPlusAssetError("manifest_invalid")
    sha256 = value["sha256"]
    if len(sha256) != 64 or any(
        character not in "0123456789abcdef" for character in sha256
    ):
        raise CampPlusAssetError("manifest_invalid")
    return CampPlusManifest(**value)


def load_campplus_manifest(directory: Path) -> CampPlusManifest:
    """Load and pin the reviewed CAM++ manifest from exactly ``directory``."""

    try:
        raw = json.loads((directory / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CampPlusAssetError("manifest_unreadable") from None
    if not isinstance(raw, dict):
        raise CampPlusAssetError("manifest_invalid")
    manifest = _manifest_from_mapping(raw)
    if manifest != _EXPECTED_MANIFEST:
        raise CampPlusAssetError("manifest_identity_mismatch")
    return manifest


def candidate_campplus_asset_directories(
    override: Path | None = None,
) -> tuple[Path, ...]:
    """Return the fixed asset lookup order without probing the filesystem.

    An explicit override is authoritative and therefore disables every fallback.
    """

    if override is not None:
        return (Path(override).resolve(),)

    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / ASSET_RELATIVE_PATH)
    candidates.append(Path(sys.executable).resolve().parent / ASSET_RELATIVE_PATH)
    candidates.append(Path(__file__).resolve().parent / "models")

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
    except OSError:
        raise CampPlusAssetError("asset_unreadable") from None
    return digest.hexdigest()


def verify_campplus_asset(
    directory: Path,
    manifest: CampPlusManifest | None = None,
) -> Path:
    """Verify one pinned weight without falling back or exposing its path."""

    pinned = manifest or load_campplus_manifest(directory)
    if pinned != _EXPECTED_MANIFEST:
        raise CampPlusAssetError("manifest_identity_mismatch")
    model_path = directory / pinned.filename
    try:
        if not model_path.is_file():
            raise CampPlusAssetError("asset_missing")
        if model_path.stat().st_size != pinned.size_bytes:
            raise CampPlusAssetError("asset_size_mismatch")
    except CampPlusAssetError:
        raise
    except OSError:
        raise CampPlusAssetError("asset_unreadable") from None
    if _sha256_file(model_path) != pinned.sha256:
        raise CampPlusAssetError("asset_sha256_mismatch")
    return model_path


def resolve_verified_campplus_asset(override: Path | None = None) -> Path:
    """Resolve the first fully verified asset in the fixed lookup order."""

    for directory in candidate_campplus_asset_directories(override):
        try:
            return verify_campplus_asset(directory)
        except CampPlusAssetError:
            continue
    raise CampPlusAssetError("no_verified_asset")
