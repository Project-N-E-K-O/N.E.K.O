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
    # persona 融合按 entity 分组：USER.md→master + SOUL.md→neko = 2 次 LLM 往返。
    assert result["persona_fusion_calls"] == 2
    # persona 候选（不含 MEMORY.md 的 facts）总 token 为正，供前端估时。
    assert result["persona_candidate_tokens"] > 0
    assert result["counts"]["facts"] >= 1
    # 无 daily 文件 → daily 抽取 0 次调用。
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
    # 只有 USER.md → 单一 entity(master) → 1 次融合调用。
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
    # 纯 facts 导入不调 LLM → 0 次调用、0 token（前端回退到无预估文案）。
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
    # daily 抽取按天（=source_file）各一次 LLM 调用；token 供前端保守 sum 估时。
    assert result["daily_extraction_calls"] == 2
    assert result["daily_candidate_tokens"] > 0
    assert result["persona_fusion_calls"] == 0
