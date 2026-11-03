"""Cross-language golden bytes, strict field rejection and decoding boundaries."""

import hashlib
import json
from pathlib import Path

import pytest

from services.chat_sync.protocol import decode_chat_original, encode_chat_original


FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/chat_sync_original_v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURES["valid"], ids=lambda case: case["name"])
def test_shared_original_golden_bytes(case):
    """Independent TS and Python encoders must match the same UTF-8/hash fixture."""
    encoded = encode_chat_original(case["record"])
    assert encoded == case["canonical"].encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == case["sha256"]
    assert decode_chat_original(encoded) == case["record"]


@pytest.mark.parametrize("case", FIXTURES["invalid"], ids=lambda case: case["name"])
def test_shared_invalid_original_is_rejected(case):
    """Reject projections and invalid identities instead of normalizing them."""
    record = {**FIXTURES["valid"][0]["record"], **case.get("changes", {})}
    for field in case.get("remove", []):
        record.pop(field)
    with pytest.raises(ValueError, match="^invalid_chat_original$"):
        encode_chat_original(record)


def test_field_order_does_not_change_encoded_original():
    """Hash identity depends on values, not a caller's dictionary insertion order."""
    record = FIXTURES["valid"][0]["record"]
    assert encode_chat_original(dict(reversed(list(record.items())))) == encode_chat_original(record)


@pytest.mark.parametrize("kind", ["space", "order", "duplicate", "escape", "bom", "integer-spelling"])
def test_decode_requires_one_canonical_byte_representation(kind):
    """An equivalent JSON value cannot substitute different persisted bytes."""
    canonical = FIXTURES["valid"][0]["canonical"]
    alternatives = {
        "space": " " + canonical,
        "order": json.dumps(FIXTURES["valid"][0]["record"], ensure_ascii=False, separators=(",", ":")),
        "duplicate": canonical[:-1] + ',"schemaVersion":1}',
        "escape": canonical.replace("你好", "\\u4f60\\u597d"),
        "bom": "\ufeff" + canonical,
        "integer-spelling": canonical.replace('"originSeq":1,', '"originSeq":1.0,'),
    }
    with pytest.raises(ValueError):
        decode_chat_original(alternatives[kind].encode("utf-8"))


@pytest.mark.parametrize("payload", [b"\xff", b"null", b"[]", b"{}", b"{", "{}"])
def test_decode_rejects_malformed_or_non_record_payloads(payload):
    """The transport boundary accepts bytes of a validated original object only."""
    with pytest.raises(ValueError, match="^invalid_chat_original$"):
        decode_chat_original(payload)


def test_same_text_does_not_make_different_messages_equal():
    """A hash verifies the complete identity payload; it is not text deduplication."""
    record = FIXTURES["valid"][0]["record"]
    assert encode_chat_original(record) != encode_chat_original({**record, "messageId": "e" * 32})
