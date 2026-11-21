# -*- coding: utf-8 -*-
"""Stage-1 correction/update rule and its hand-off to fact dedup.

Before this rule the fact-extraction prompts never said what to do with a
correction, so "the scarf is actually coral" could come back as ``[]`` and
the stale "mint" fact stayed in facts.json. The rule asks Stage-1 for the NEW
state plus the claim it supersedes; retiring the old row stays with
memory/fact_dedup.py (near-dup nomination → arbiter ``replace``).

Contracts under test:

  1. All three extraction tables carry the rule in all 8 locales, inside the
     instruction block (never between the conversation watermarks), with the
     exclusions (hypotheticals / third party / assistant guesses / another
     object) spelled out.
  2. The insertion changed no placeholder and no watermark; the batch table,
     whose caller never substitutes {MASTER_NAME}, does not gain one.
  3. A correction fact written the way the rule asks for is nominated against
     the stale fact over the real near-dup index, and a ``replace`` decision
     leaves only the corrected fact.
"""
from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.prompts.prompts_memory import (
    FACT_EXTRACTION_AI_AWARE_PROMPT,
    FACT_EXTRACTION_BATCH_PROMPT,
    FACT_EXTRACTION_PROMPT,
)
from memory.fact_dedup import FactDedupResolver
from memory.facts import FactStore
from memory.timeindex import TimeIndexedMemory

LOCALES = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")

TABLES = {
    "basic": FACT_EXTRACTION_PROMPT,
    "batch": FACT_EXTRACTION_BATCH_PROMPT,
    "ai_aware": FACT_EXTRACTION_AI_AWARE_PROMPT,
}

EXPECTED_PLACEHOLDERS = {
    "basic": {"{LANLAN_NAME}", "{MASTER_NAME}", "{CONVERSATION}"},
    "batch": {"{LANLAN_NAME}", "{SEGMENT_NONCE}", "{SEGMENTS}"},
    "ai_aware": {
        "{LANLAN_NAME}", "{MASTER_NAME}", "{CONVERSATION}", "{KNOWN_POOL}",
    },
}

# (rule heading, "does not count as a correction" clause) per locale.
RULE_MARKERS = {
    "zh": ("- 纠正/更新：", "以下都不算纠正：假设、第三人的说法、{LANLAN_NAME}的猜测、说的其实是另一个对象"),
    "zh-TW": ("- 糾正/更新：", "以下都不算糾正：假設、第三人的說法、{LANLAN_NAME}的猜測、說的其實是另一個對象"),
    "en": ("- Corrections / updates:", "None of these count as a correction: hypotheticals, a third party's claims, {LANLAN_NAME}'s guesses, or a statement that is actually about a different thing"),
    "ja": ("- 訂正・更新：", "次のものは訂正とみなさない：仮定、第三者の発言、{LANLAN_NAME}の推測、実は別の対象についての話"),
    "ko": ("- 정정/갱신:", "다음은 정정으로 보지 않음: 가정, 제3자의 말, {LANLAN_NAME}의 추측, 실제로는 다른 대상에 관한 말"),
    "ru": ("- Исправления / обновления:", "Исправлением НЕ считаются: предположения, слова третьих лиц, догадки {LANLAN_NAME}, высказывания, которые на самом деле о другом предмете"),
    "es": ("- Correcciones / actualizaciones:", "No cuentan como corrección: hipótesis, lo que dice un tercero, las suposiciones de {LANLAN_NAME} ni lo que en realidad trata de otro objeto"),
    "pt": ("- Correções / atualizações:", "Não contam como correção: hipóteses, o que um terceiro diz, suposições de {LANLAN_NAME} ou algo que na verdade se refere a outro objeto"),
}

WATERMARK_OPEN = "======以下为对话======"
WATERMARK_CLOSE = "======以上为对话======"
_PLACEHOLDER = re.compile(r"\{[A-Z_]+\}")


def test_every_table_covers_exactly_the_eight_locales():
    for name, table in TABLES.items():
        assert set(table) == set(LOCALES), name


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", sorted(TABLES))
def test_correction_rule_is_present_in_the_instructions(name, locale):
    text = TABLES[name][locale]
    heading, exclusions = RULE_MARKERS[locale]
    assert text.count(heading) == 1, f"{name}/{locale}"
    assert exclusions in text, f"{name}/{locale} lost the exclusion clause"
    # The rule sits in the requirements list, never inside the quoted
    # conversation block where the model would read it as dialogue.
    assert text.index(heading) < text.index(WATERMARK_OPEN), f"{name}/{locale}"
    rule_line = next(line for line in text.splitlines() if line.startswith(heading))
    assert exclusions in rule_line, f"{name}/{locale} rule split across lines"


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", sorted(TABLES))
def test_watermark_and_placeholders_are_unchanged(name, locale):
    text = TABLES[name][locale]
    assert text.count(WATERMARK_OPEN) == 1, f"{name}/{locale}"
    assert text.count(WATERMARK_CLOSE) == 1, f"{name}/{locale}"
    assert text.index(WATERMARK_OPEN) < text.index(WATERMARK_CLOSE)
    assert set(_PLACEHOLDER.findall(text)) == EXPECTED_PLACEHOLDERS[name], (
        f"{name}/{locale}"
    )


# ── hand-off to dedup over the real near-dup index ───────────────────


def _cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    character_data = (
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    )
    cm.get_character_data = MagicMock(return_value=character_data)
    cm.aget_character_data = AsyncMock(return_value=character_data)
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    cm.aget_model_api_config = AsyncMock(
        side_effect=lambda mt, **_: cm.get_model_api_config(mt),
    )
    return cm


def _replace_llm(prompts: list[str]):
    resp = MagicMock()
    resp.content = json.dumps([{"index": 0, "action": "replace"}])

    async def _ainvoke(messages, *_a, **_k):
        prompts.append(str(messages))
        return resp

    async def _aclose():
        return None

    llm = MagicMock()
    llm.ainvoke = _ainvoke
    llm.aclose = _aclose
    return llm


@pytest.mark.asyncio
@pytest.mark.parametrize(("stale", "corrected"), [
    ("主人的测试围巾是薄荷色", "主人的测试围巾是珊瑚色（更正了之前说的薄荷色）"),
    (
        "主人's test scarf is mint",
        "主人's test scarf is coral (corrects the earlier claim that it was mint)",
    ),
])
async def test_correction_fact_is_paired_with_and_replaces_the_stale_one(
    tmp_path, stale, corrected,
):
    cm = _cm(str(tmp_path))
    with patch("memory.timeindex.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm):
        time_index = TimeIndexedMemory(recent_history_manager=MagicMock())
        store = FactStore(time_indexed_memory=time_index)
        store._config_manager = cm
        resolver = FactDedupResolver(store)
        resolver._config_manager = cm
        store.attach_dedup_resolver(resolver)

        async def _write(text):
            return await store._apersist_new_facts(
                "小天", [{"text": text, "importance": 7, "entity": "master"}],
                default_source="user_observation", semantic_dedup=True,
            )

        old = await _write(stale)
        assert len(old) == 1
        assert await resolver.aload_pending("小天") == []

        new = await _write(corrected)
        assert len(new) == 1, "the correction must be written, not swallowed"
        pending = await resolver.aload_pending("小天")
        assert [(p["candidate_id"], p["existing_id"]) for p in pending] == [
            (new[0]["id"], old[0]["id"]),
        ]

        prompts: list[str] = []
        with patch(
            "utils.llm_client.create_chat_llm", return_value=_replace_llm(prompts),
        ):
            assert await resolver.aresolve("小天") == 1

        # The arbiter saw both sides, including the superseded value.
        assert prompts and stale in prompts[0] and corrected in prompts[0]
        facts = await store.aload_facts("小天")
        assert [f["text"] for f in facts] == [corrected]
        assert old[0]["id"] in (facts[0].get("merged_from_ids") or [])
        assert await resolver.aload_pending("小天") == []
