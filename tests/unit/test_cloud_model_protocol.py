"""Shared cross-client fixtures and hostile metadata, without network or production data."""

import hashlib
import json
from pathlib import Path

import pytest

from services.chat_sync.model_protocol import decode, encode, resource_ids, web_url

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/cloud_model_v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("item", FIXTURES["valid"], ids=lambda item: item["name"])
def test_golden_model(item):
    raw = encode(item["record"])
    assert raw.decode() == item["canonical"]
    assert hashlib.sha256(raw).hexdigest() == item["sha256"]
    assert decode(raw) == item["record"]


@pytest.mark.parametrize("item", FIXTURES["invalid"], ids=lambda item: item["name"])
def test_invalid_model(item):
    with pytest.raises(ValueError):
        encode(item["record"])


def test_canonical_bytes_and_resource_references():
    item = next(item for item in FIXTURES["valid"] if item["name"] == "complete_model")
    raw = item["canonical"].encode()
    assert list(resource_ids(item["record"])) == [item["record"]["model"]["package"]["resource"]["resourceId"]]
    for bad in (b" " + raw, raw.replace(b'"schemaVersion":1', b'"schemaVersion":1,"schemaVersion":1')):
        with pytest.raises(ValueError):
            decode(bad)


@pytest.mark.parametrize("url", ["http://2130706433/a", "http://0x7f000001/a", "http://127.1/a", "http://[::1]/a"])
def test_numeric_loopback(url):
    with pytest.raises(ValueError):
        web_url(url)
