from __future__ import annotations

import json
from pathlib import Path

from knowledge.models import KnowledgeEntry


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "knowledge_hybrid_retrieval_cases.json"
)


def test_hybrid_retrieval_eval_fixture_covers_quality_boundaries():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    entries = tuple(KnowledgeEntry(**row) for row in payload["entries"])
    kinds = {case["kind"] for case in payload["queries"]}

    assert payload["schema_version"] == 1
    assert entries
    assert {"exact", "alias", "semantic", "disabled", "negative"} <= kinds
    assert any(case["expected"] is None for case in payload["queries"])
