"""Canonical immutable batch encoding shared by the desktop evidence transport."""
from __future__ import annotations

import hashlib
import json

SCHEMA_VERSION = "desktop-proactive-feedback-v1"
SCHEMA_VERSION_V2 = "desktop-proactive-feedback-v2"
COMMUNITY_ORIGIN = "https://community.project-neko.cn"
MAX_RESPONSE_BYTES = 512 * 1024
V2_TERMINAL = frozenset({"stored", "duplicate", "expired_not_stored", "cleared_not_stored"})
V2_DEFERRED = frozenset({"time_deferred", "dependency_deferred"})


def immutable_value(value):
    """Proofs may be refined; original identity, capture and payload may not."""
    if isinstance(value, dict):
        return {key: immutable_value(item) for key, item in value.items() if key != "time_proof"}
    if isinstance(value, list):
        return [immutable_value(item) for item in value]
    return value


def fact_hash_v2(item: dict, binding: dict) -> str:
    return payload_hash(immutable_value({
        "id": item["id"], "kind": item["kind"], "payload": item["payload"],
        "capture": item["capture"], "binding": binding,
    }))


def valid_receipt_v2(batch: dict, receipt: dict) -> bool:
    if not isinstance(receipt, dict) or set(receipt) != {
            "schema_version", "batch_id", "source_id", "batch_hash", "binding", "results"}:
        return False
    if batch.get("schema_version") != SCHEMA_VERSION_V2:
        return False
    if any(receipt.get(key) != batch.get(key) for key in (
            "schema_version", "batch_id", "source_id", "binding")):
        return False
    if receipt.get("batch_hash") != payload_hash(batch):
        return False
    results = receipt.get("results")
    if not isinstance(results, list) or len(results) != len(batch["items"]):
        return False
    expected = {(item["id"], item["kind"]): fact_hash_v2(item, batch["binding"])
                for item in batch["items"]}
    if len(expected) != len(batch["items"]):
        return False
    seen = set()
    for item in results:
        if not isinstance(item, dict) or set(item) != {"id", "kind", "payload_hash", "status"}:
            return False
        if not all(isinstance(item.get(key), str) for key in item):
            return False
        key = item["id"], item["kind"]
        if (key in seen or expected.get(key) != item["payload_hash"]
                or item["status"] not in V2_TERMINAL | V2_DEFERRED):
            return False
        seen.add(key)
    return seen == set(expected)


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def payload_hash(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def valid_receipt(batch: dict, receipt: dict) -> bool:
    """Require every frozen item exactly once; partial acknowledgements are unsafe."""
    if batch.get("schema_version") == SCHEMA_VERSION_V2:
        return valid_receipt_v2(batch, receipt)
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
