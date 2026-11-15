"""Preview endpoint reports ETA inputs (persona fusion calls + candidate tokens).

The frontend uses ``persona_fusion_calls`` (one LLM round-trip per persona entity)
and ``persona_candidate_tokens`` to show an estimated fuse time with the 240s
backend ceiling. facts never hit the LLM, so a facts-only import estimates zero.
"""
from __future__ import annotations

import asyncio

from main_routers.memory_router import preview_external_memory_import


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _preview(payload):
    return asyncio.run(preview_external_memory_import(_FakeRequest(payload)))


def test_preview_reports_two_fusion_calls_for_soul_and_user():
    result = _preview({
        "character_name": "Neko",
        "files": [
            {"path": "workspace/USER.md",
             "content": "# USER.md\n- **Name:** Alice\n- Prefers concise answers\n"},
            {"path": "workspace/SOUL.md",
             "content": "# SOUL.md\n## Vibe\n- Warm but direct\n"},
            {"path": "workspace/MEMORY.md",
             "content": "# Projects\n- Project N.E.K.O uses Python\n"},
        ],
    })

    assert result["success"] is True
    # Group persona fusion by entity: USER.md->master + SOUL.md->neko = two LLM round trips.
    assert result["persona_fusion_calls"] == 2
    # Persona candidates (excluding MEMORY.md facts) have positive total tokens for frontend timing estimates.
    assert result["persona_candidate_tokens"] > 0
    assert result["counts"]["facts"] >= 1
    # No daily files -> zero daily extraction calls.
    assert result["daily_extraction_calls"] == 0
    assert result["daily_candidate_tokens"] == 0


def test_preview_reports_one_fusion_call_for_user_only():
    result = _preview({
        "character_name": "Neko",
        "files": [
            {"path": "workspace/USER.md",
             "content": "# USER.md\n- **Name:** Alice\n- Lives in Berlin\n"},
        ],
    })

    assert result["success"] is True
    # Only USER.md -> one entity (master) -> one fusion call.
    assert result["persona_fusion_calls"] == 1
    assert result["persona_candidate_tokens"] > 0


def test_preview_reports_zero_fusion_calls_for_facts_only():
    result = _preview({
        "character_name": "Neko",
        "files": [
            {"path": "workspace/MEMORY.md",
             "content": "# Projects\n- Ships weekly\n- Uses Python\n"},
        ],
    })

    assert result["success"] is True
    # Pure fact import makes no LLM calls -> zero calls/tokens (frontend falls back to no-estimate text).
    assert result["persona_fusion_calls"] == 0
    assert result["persona_candidate_tokens"] == 0
    assert result["counts"]["persona"] == 0
    assert result["daily_extraction_calls"] == 0


def test_preview_counts_daily_extraction_calls_per_day():
    result = _preview({
        "character_name": "Neko",
        "files": [
            {"path": "workspace/memory/2026-07-12.md",
             "content": "Woke early and shipped the importer fix.\n"},
            {"path": "workspace/memory/2026-07-13.md",
             "content": "Reviewed feedback and refactored tests.\n"},
        ],
    })

    assert result["success"] is True
    # Daily extraction makes one LLM call per day (=source_file); frontend conservatively sums tokens to estimate time.
    assert result["daily_extraction_calls"] == 2
    assert result["daily_candidate_tokens"] > 0
    assert result["persona_fusion_calls"] == 0
