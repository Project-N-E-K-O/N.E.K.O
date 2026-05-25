from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.study_companion.memory_candidates import upsert_memory_candidate

pytestmark = pytest.mark.unit


class _CandidateStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def upsert_candidate_item(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": "candidate-1", **kwargs}


def test_upsert_memory_candidate_uses_stable_sorted_digest_and_store_contract() -> None:
    first_store = _CandidateStore()
    second_store = _CandidateStore()
    payload_a = {"meaning": "value", "word": "term"}
    payload_b = {"word": "term", "meaning": "value"}

    first = upsert_memory_candidate(first_store, "word", payload_a)
    second = upsert_memory_candidate(second_store, "word", payload_b)

    assert first["item_type"] == "memory_draft"
    assert first["source"] == "memory_llm_fallback"
    assert first["status"] == "candidate"
    assert first["dedupe_key"].startswith("word:")
    assert first["dedupe_key"] == second["dedupe_key"]
    assert first_store.calls[0]["payload"] is payload_a
