"""Stable hand-off contract for future knowledge-package providers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass


SUBSCRIPTION_PROTOCOL_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PROVIDER_PACKAGE_ID_MAX = 9_999_999_999_999_999_999
_PROVIDER_PACKAGE_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$")


@dataclass(frozen=True, slots=True)
class KnowledgeSubscription:
    provider: str
    remote_id: str
    version: str
    channel: str
    artifact_sha256: str
    material_type: str
    provider_package_id: str = ""
    index_manifest_sha256: str = ""
    vectors_sha256: str = ""
    trust: str = "trusted_market"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def validate_subscription(payload: object) -> KnowledgeSubscription:
    if not isinstance(payload, dict):
        raise ValueError("subscription metadata must be an object")
    allowed = {
        "provider",
        "remote_id",
        "version",
        "channel",
        "artifact_sha256",
        "material_type",
        "provider_package_id",
        "index_manifest_sha256",
        "vectors_sha256",
        "trust",
    }
    if set(payload) - allowed:
        raise ValueError("subscription metadata contains unsupported fields")
    provider = _required_text(payload.get("provider"), "provider", 64)
    remote_id = _required_text(payload.get("remote_id"), "remote_id", 200)
    version = _required_text(payload.get("version"), "version", 100)
    channel = _required_text(payload.get("channel"), "channel", 40)
    digest = _required_text(
        payload.get("artifact_sha256"), "artifact_sha256", 64
    ).lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError("artifact_sha256 must be a SHA-256 digest")
    manifest_digest = _optional_digest(
        payload.get("index_manifest_sha256"),
        "index_manifest_sha256",
    )
    vectors_digest = _optional_digest(payload.get("vectors_sha256"), "vectors_sha256")
    if bool(manifest_digest) != bool(vectors_digest):
        raise ValueError("subscription requires both index artifact digests")
    material_type = _required_text(payload.get("material_type"), "material_type", 16)
    if material_type not in {"knowledge", "corpus"}:
        raise ValueError("subscription material_type is unsupported")
    trust = _required_text(payload.get("trust"), "trust", 40)
    if trust != "trusted_market":
        raise ValueError("subscription trust is unsupported")
    provider_package_id = _optional_provider_package_id(
        payload.get("provider_package_id")
    )
    return KnowledgeSubscription(
        provider=provider,
        remote_id=remote_id,
        version=version,
        channel=channel,
        artifact_sha256=digest,
        material_type=material_type,
        provider_package_id=provider_package_id,
        index_manifest_sha256=manifest_digest,
        vectors_sha256=vectors_digest,
        trust=trust,
    )


def canonical_pack_bytes(payload: object) -> bytes:
    """Canonical JSON bytes hashed by both provider and local hand-off."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def load_canonical_pack_artifact(raw: bytes) -> object:
    """Decode a market artifact and require its bytes to be canonical JSON."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("knowledge artifact is not valid UTF-8 JSON") from exc
    if raw != canonical_pack_bytes(payload):
        raise ValueError("knowledge artifact is not canonical JSON")
    return payload


def _required_text(value: object, field: str, max_chars: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > max_chars
    ):
        raise ValueError(f"subscription {field} is invalid")
    return value.strip()


def _optional_digest(value: object, field: str) -> str:
    if value in (None, ""):
        return ""
    digest = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"subscription {field} is invalid")
    return digest


def _optional_provider_package_id(value: object) -> str:
    if value in (None, ""):
        return ""
    return normalize_provider_package_id(value)


def normalize_provider_package_id(value: object) -> str:
    """Return the one ASCII representation accepted for provider identities."""
    text = str(value).strip()
    if not _PROVIDER_PACKAGE_ID_RE.fullmatch(text):
        raise ValueError("subscription provider_package_id is invalid")
    return text
