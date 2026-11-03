# -*- coding: utf-8 -*-
"""
Unit tests for P1.a — deterministic reflection id + synthesize_reflections
idempotency.

Fixes critical issue 3: synthesize_reflections' save_reflections + mark_absorbed
is only partially atomic; termination between them resynthesizes the same facts and creates duplicate
reflections after restart (old IDs contained changing timestamps). New ID =
sha256(sorted(source_fact_ids))[:16], mapping identical fact batches to identical IDs.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _reflection_id_from_facts: pure function ─────────────────────────────


def test_reflection_id_is_order_independent():
    from memory.reflection import _reflection_id_from_facts

    a = _reflection_id_from_facts(["f3", "f1", "f2"])
    b = _reflection_id_from_facts(["f1", "f2", "f3"])
    c = _reflection_id_from_facts(["f2", "f3", "f1"])
    assert a == b == c


def test_reflection_id_changes_when_fact_set_changes():
    from memory.reflection import _reflection_id_from_facts

    assert _reflection_id_from_facts(["f1", "f2"]) != _reflection_id_from_facts(
        ["f1", "f2", "f3"]
    )


def test_reflection_id_collision_resistance_across_boundary():
    """Separators prevent concatenation hash collisions between [\"ab\", \"c\"] and [\"a\", \"bc\"]."""
    from memory.reflection import _reflection_id_from_facts

    assert _reflection_id_from_facts(["ab", "c"]) != _reflection_id_from_facts(
        ["a", "bc"]
    )


def test_reflection_id_format():
    from memory.reflection import _reflection_id_from_facts

    rid = _reflection_id_from_facts(["f1", "f2"])
    assert rid.startswith("ref_")
    # sha256[:16] means 16 hexadecimal characters
    assert len(rid) == len("ref_") + 16
    assert all(c in "0123456789abcdef" for c in rid[4:])


# ── synthesize_reflections idempotency (core cases) ──────────────────


def _build_mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    # aget_character_data is async; a default return_value does not automatically become awaitable
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake-model", "base_url": "http://fake", "api_key": "sk-fake",
    })
    cm.aget_model_api_config = AsyncMock(side_effect=lambda mt, **_: cm.get_model_api_config(mt))
    return cm


def _write_unabsorbed_facts(tmpdir: str, character: str, fact_ids: list[str]):
    """Write facts directly to facts.json, simulating completed extract_facts."""
    import json

    char_dir = os.path.join(tmpdir, character)
    os.makedirs(char_dir, exist_ok=True)
    facts = [
        {
            "id": fid,
            "text": f"fact text {fid}",
            "entity": "master",
            "importance": 8,
            "absorbed": False,
        }
        for fid in fact_ids
    ]
    with open(os.path.join(char_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False)


@pytest.mark.asyncio
async def test_synth_with_same_facts_does_not_duplicate_reflection(tmp_path):
    """Synthesize the same unabsorbed facts twice, simulating restart after mark_absorbed crashes,
    leaving one reflection in reflections.json and no second LLM call."""
    mock_cm = _build_mock_cm(str(tmp_path))
    _write_unabsorbed_facts(str(tmp_path), "小天", [f"f{i}" for i in range(6)])

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.facts import FactStore
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = mock_cm

        # Mock the LLM chain to return fixed reflection text; count calls to verify exactly one.
        llm_call_count = {"n": 0}

        async def _fake_ainvoke(self, prompt):
            llm_call_count["n"] += 1
            resp = MagicMock()
            resp.content = (
                '{"reflection": "主人 likes coffee", "entity": "master"}'
            )
            return resp

        async def _fake_aclose(self):
            return None

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fake_ainvoke
            aclose = _fake_aclose

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            # First synthesis
            first = await re.synthesize_reflections("小天")
            assert len(first) == 1
            first_rid = first[0]["id"]
            assert first_rid.startswith("ref_")

            # Simulate mark_absorbed crashing by manually restoring facts to absorbed=False
            import json
            fpath = os.path.join(str(tmp_path), "小天", "facts.json")
            with open(fpath, encoding="utf-8") as f:
                facts = json.load(f)
            for fact in facts:
                fact["absorbed"] = False
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(facts, f, ensure_ascii=False)
            # Clear FactStore cache to reread
            fs._facts.pop("小天", None)

            # Second synthesis detects the existing reflection, skips the LLM, and marks absorbed
            second = await re.synthesize_reflections("小天")
            assert second == []  # Returning [] means no new reflection was generated
            assert llm_call_count["n"] == 1, "同批 facts 不应再次调 LLM"

        # reflections.json contains one entry with no duplicate ID
        import json
        rpath = os.path.join(str(tmp_path), "小天", "reflections.json")
        with open(rpath, encoding="utf-8") as f:
            reflections = json.load(f)
        assert len(reflections) == 1
        assert reflections[0]["id"] == first_rid

        # The second synthesis's mark_absorbed repaired the facts
        with open(fpath, encoding="utf-8") as f:
            facts = json.load(f)
        assert all(ft["absorbed"] for ft in facts)


@pytest.mark.asyncio
async def test_synth_different_fact_set_produces_different_id(tmp_path):
    """Changed facts produce a new reflection without erroneous deduplication."""
    mock_cm = _build_mock_cm(str(tmp_path))

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.facts import FactStore
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = mock_cm

        async def _fake_ainvoke(self, prompt):
            resp = MagicMock()
            resp.content = '{"reflection": "stub", "entity": "master"}'
            return resp

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fake_ainvoke

            async def aclose(self):
                return None

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            # First batch: f1–f5
            _write_unabsorbed_facts(str(tmp_path), "小天", [f"f{i}" for i in range(5)])
            fs._facts.pop("小天", None)
            first = await re.synthesize_reflections("小天")
            assert len(first) == 1

            # Switch fact IDs to f10–f14: facts remain absorbed=False, but IDs differ
            _write_unabsorbed_facts(str(tmp_path), "小天", [f"f{i}" for i in range(10, 15)])
            fs._facts.pop("小天", None)
            second = await re.synthesize_reflections("小天")
            assert len(second) == 1
            assert second[0]["id"] != first[0]["id"]

        import json
        rpath = os.path.join(str(tmp_path), "小天", "reflections.json")
        with open(rpath, encoding="utf-8") as f:
            reflections = json.load(f)
        assert len(reflections) == 2


@pytest.mark.asyncio
async def test_synth_concurrent_dedup_returns_empty(tmp_path):
    """Concurrency: first aload lacks rid → LLM completes → second aload discovers
    another coroutine persisted rid → return [] rather than the in-memory copy, or callers
    receive an unpersisted reflection whose text may differ from disk."""
    from memory.reflection import _reflection_id_from_facts

    mock_cm = _build_mock_cm(str(tmp_path))
    fact_ids = [f"f{i}" for i in range(6)]
    _write_unabsorbed_facts(str(tmp_path), "小天", fact_ids)
    expected_rid = _reflection_id_from_facts(sorted(fact_ids))

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.facts import FactStore
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = mock_cm

        async def _fake_ainvoke(self, prompt):
            resp = MagicMock()
            resp.content = (
                '{"reflection": "本进程的反思文本", "entity": "master"}'
            )
            return resp

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fake_ainvoke

            async def aclose(self):
                return None

        # First aload_reflections (line 349 short-circuit check) → [], allowing the LLM call
        # Second load (line 416 race reload) → version already persisted by another coroutine
        ghost_reflection = {
            "id": expected_rid,
            "text": "并发协程写下的另一份反思文本",
            "entity": "master",
            "status": "pending",
            "source_fact_ids": sorted(fact_ids),
        }
        load_call_count = {"n": 0}
        original_aload = re.aload_reflections

        async def mock_aload(name):
            load_call_count["n"] += 1
            if load_call_count["n"] == 1:
                return await original_aload(name)
            # Simulate concurrent persistence by another coroutine
            return [ghost_reflection]

        save_called = {"n": 0}
        original_asave = re.asave_reflections

        async def mock_asave(name, refs):
            save_called["n"] += 1
            await original_asave(name, refs)

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"), \
             patch.object(re, "aload_reflections", side_effect=mock_aload), \
             patch.object(re, "asave_reflections", side_effect=mock_asave):
            result = await re.synthesize_reflections("小天")

        # Key assertion: dedup must return [] instead of exposing an unpersisted in-memory copy
        assert result == [], (
            f"concurrent dedup must return [] (caller would otherwise see an "
            f"un-persisted reflection that may differ from disk). got: {result}"
        )
        # This invocation never saves; a dedup hit skips append+save
        assert save_called["n"] == 0


# ── Synthesis failure backoff / dead-letter (user audit #2) ────────────────────


@pytest.mark.asyncio
async def test_synth_failure_bumps_backoff_and_dead_letters(tmp_path):
    """Persistent LLM failures bump synth_backoff until MEMORY_LIVENESS_MAX_ATTEMPTS,
    then synthesis stops LLM calls through dead-letter instead of wasting another call every 180s."""
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    mock_cm = _build_mock_cm(str(tmp_path))
    _write_unabsorbed_facts(str(tmp_path), "小天", [f"f{i}" for i in range(6)])

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.facts import FactStore
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = mock_cm

        llm_call_count = {"n": 0}

        async def _fake_ainvoke(self, prompt):
            llm_call_count["n"] += 1
            resp = MagicMock()
            # A non-dict response triggers failure
            resp.content = "not a json object at all"
            return resp

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fake_ainvoke

            async def aclose(self):
                return None

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            # Call MAX + 3 times: first MAX call the LLM and bump counts, then dead-letter skips
            for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS + 3):
                out = await re.synthesize_reflections("小天")
                assert out == []

        assert llm_call_count["n"] == MEMORY_LIVENESS_MAX_ATTEMPTS, (
            f"dead-letter 后不应再调 LLM；实际调了 {llm_call_count['n']} 次"
        )

        # The backoff file stores this rid's count in the new {key: {"n", "at"}} format
        from memory.reflection import _reflection_id_from_facts
        rid = _reflection_id_from_facts(sorted(f"f{i}" for i in range(6)))
        backoff = await re._aload_synth_backoff("小天")
        assert backoff.get(rid, {}).get("n") == MEMORY_LIVENESS_MAX_ATTEMPTS
        assert backoff.get(rid, {}).get("at"), "失败应戳上时间戳供自愈"


@pytest.mark.asyncio
async def test_synth_success_clears_backoff(tmp_path):
    """Successful synthesis clears this rid's failure backoff, reviving processing when input recovers."""
    mock_cm = _build_mock_cm(str(tmp_path))
    fact_ids = [f"f{i}" for i in range(6)]
    _write_unabsorbed_facts(str(tmp_path), "小天", fact_ids)

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.facts import FactStore
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine, _reflection_id_from_facts

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = mock_cm

        rid = _reflection_id_from_facts(sorted(fact_ids))
        # Seed a failure-backoff record
        await re._abump_synth_backoff("小天", rid, "seed failure")
        assert (await re._aload_synth_backoff("小天")).get(rid, {}).get("n") == 1

        async def _fake_ainvoke(self, prompt):
            resp = MagicMock()
            resp.content = '{"reflection": "主人 likes tea", "entity": "master"}'
            return resp

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fake_ainvoke

            async def aclose(self):
                return None

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            out = await re.synthesize_reflections("小天")
            assert len(out) == 1

        assert rid not in (await re._aload_synth_backoff("小天")), (
            "成功合成后应清掉该 rid 的失败退避记录"
        )


def _write_facts_with_importance(tmpdir: str, character: str, specs: list[tuple[str, int]]):
    """Write facts.json with explicit importance values to construct top-N cap scenarios."""
    import json

    char_dir = os.path.join(tmpdir, character)
    os.makedirs(char_dir, exist_ok=True)
    facts = [
        {"id": fid, "text": f"fact text {fid}", "entity": "master",
         "importance": imp, "absorbed": False}
        for fid, imp in specs
    ]
    with open(os.path.join(char_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False)


@pytest.mark.asyncio
async def test_synth_dead_letter_resets_when_full_input_changes(tmp_path):
    """Codex P2: backoff keys must cover all unabsorbed facts, not the capped top-N rid.

    After toxic top-20 dead-lettering, new facts with importance≥5 enter
    unabsorbed pools but rank below top-20 and cannot change rid. Backoff by rid
    would permanently starve the whole pool; full-pool keys change with new facts, resetting retry budgets.

    Note: aget_unabsorbed_facts enforces min_importance=5, excluding facts below 5,
    so new facts use importance=5 while toxic facts at 9 occupy top-20.
    """
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS, REFLECTION_SYNTHESIS_FACTS_MAX

    mock_cm = _build_mock_cm(str(tmp_path))
    # Twenty high-importance facts form the toxic top-N batch
    poison = [(f"p{i:02d}", 9) for i in range(REFLECTION_SYNTHESIS_FACTS_MAX)]
    _write_facts_with_importance(str(tmp_path), "小天", poison)

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.facts import FactStore
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = mock_cm

        llm_calls = {"n": 0}

        async def _fail_ainvoke(self, prompt):
            llm_calls["n"] += 1
            resp = MagicMock()
            resp.content = "not json"  # Trigger the failure branch
            return resp

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fail_ainvoke

            async def aclose(self):
                return None

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            # Reach dead-letter: MAX actual LLM calls, then skipping
            for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS + 2):
                assert await re.synthesize_reflections("小天") == []
            assert llm_calls["n"] == MEMORY_LIVENESS_MAX_ATTEMPTS

            # Add importance=5: enters the unabsorbed pool (≥min_importance),
            # but below the toxic batch's 9 and outside top-20, preserving rid while changing the full-pool key
            new_facts = poison + [("low0", 5)]
            _write_facts_with_importance(str(tmp_path), "小天", new_facts)
            fs._facts.pop("小天", None)

            calls_before = llm_calls["n"]
            await re.synthesize_reflections("小天")
            assert llm_calls["n"] == calls_before + 1, (
                "新 fact 改变 unabsorbed 全集 → 退避 key 变 → 应复位重试，"
                "而不是按旧 rid 永久跳过"
            )


@pytest.mark.asyncio
async def test_synth_dead_letter_self_heals_after_cooldown(tmp_path):
    """With an unchanged idle pool, permit one dead-letter probe after the 5h cooldown.

    Simulate by moving failure time 6h into the past, calling synthesis again, and asserting
    the LLM runs again because cooldown_elapsed disables skipping.
    """
    from datetime import datetime, timedelta
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    mock_cm = _build_mock_cm(str(tmp_path))
    _write_unabsorbed_facts(str(tmp_path), "小天", [f"f{i}" for i in range(6)])

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.facts import FactStore
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine, _reflection_id_from_facts

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = mock_cm

        llm_calls = {"n": 0}

        async def _fail_ainvoke(self, prompt):
            llm_calls["n"] += 1
            resp = MagicMock()
            resp.content = "not json"
            return resp

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fail_ainvoke

            async def aclose(self):
                return None

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS + 2):
                assert await re.synthesize_reflections("小天") == []
            assert llm_calls["n"] == MEMORY_LIVENESS_MAX_ATTEMPTS  # Already dead-lettered

            # Set failure time to 6h ago without changing the pool. Backoff now uses the process-local mirror,
            # so change that mirror instead of disk.
            key = _reflection_id_from_facts(sorted(f"f{i}" for i in range(6)))
            re._synth_backoff_mem["小天"][key]["at"] = (
                datetime.now() - timedelta(hours=6)
            ).isoformat()

            calls_before = llm_calls["n"]
            await re.synthesize_reflections("小天")
            assert llm_calls["n"] == calls_before + 1, (
                "冷却期已过应放行一次 probe（时间自愈），即使池子没变"
            )


@pytest.mark.asyncio
async def test_synth_backoff_survives_disk_write_failure(tmp_path):
    """Even with persistent synth_backoff write failures (read-only filesystem/permissions), process-local failure counts
    still trigger dead-letter after MAX attempts rather than retrying LLM every 180s (Codex P2)."""
    from unittest.mock import AsyncMock
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    mock_cm = _build_mock_cm(str(tmp_path))
    _write_unabsorbed_facts(str(tmp_path), "小天", [f"f{i}" for i in range(6)])

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.facts import FactStore
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = mock_cm

        llm_calls = {"n": 0}

        async def _fail_ainvoke(self, prompt):
            llm_calls["n"] += 1
            resp = MagicMock()
            resp.content = "not json"
            return resp

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fail_ainvoke

            async def aclose(self):
                return None

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"), \
             patch("memory.reflection.persistence.atomic_write_json_async",
                   AsyncMock(side_effect=OSError("read-only fs"))):
            for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS + 3):
                assert await re.synthesize_reflections("小天") == []

        assert llm_calls["n"] == MEMORY_LIVENESS_MAX_ATTEMPTS, (
            f"写盘失败不应丢失失败计数；dead-letter 应在 MAX 次后生效，"
            f"实际调了 {llm_calls['n']} 次 LLM"
        )
