"""Canonical immutable batch encoding shared by the desktop evidence transport."""
from __future__ import annotations

import hashlib
import json

SCHEMA_VERSION = "desktop-proactive-feedback-v1"
COMMUNITY_ORIGIN = "https://community.project-neko.cn"
MAX_RESPONSE_BYTES = 512 * 1024


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def payload_hash(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def valid_receipt(batch: dict, receipt: dict) -> bool:
    """Require every frozen item exactly once; partial acknowledgements are unsafe."""
    if not isinstance(receipt, dict) or set(receipt) != {"schema_version", "batch_id", "source_id", "batch_hash", "results"}:
        return False
    if any(receipt.get(key) != batch.get(key) for key in ("schema_version", "batch_id", "source_id")):
        return False
    if receipt.get("batch_hash") != payload_hash(batch):
        return False
    results = receipt.get("results")
    if not isinstance(results, list) or len(results) != len(batch["items"]):
        return False
    expected = {(item["id"], item["kind"]): payload_hash(item["payload"]) for item in batch["items"]}
    seen = set()
    for item in results:
        if not isinstance(item, dict) or set(item) != {"id", "kind", "payload_hash", "status"}:
            return False
        if not isinstance(item["id"], str) or not isinstance(item["kind"], str):
            return False
        key = (item["id"], item["kind"])
        if key in seen or key not in expected or item["payload_hash"] != expected[key] or item["status"] not in ("stored", "duplicate"):
            return False
        seen.add(key)
    return seen == set(expected)
