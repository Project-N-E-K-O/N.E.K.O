# -*- coding: utf-8 -*-
"""
Unit tests for P1.a — deterministic reflection id + synthesize_reflections
idempotency.

Fixes 致命点 3: synthesize_reflections 的 save_reflections + mark_absorbed
是半原子操作——两步之间 kill 会导致同一批 facts 下次重新合成产生重复
reflection（旧方案 id 带 timestamp，每次都不同）。新方案 id =
sha256(sorted(source_fact_ids))[:16]，同批 facts 永远映射到同一 id。
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _reflection_id_from_facts: 纯函数 ─────────────────────────────


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
    """分隔符防止 ["ab", "c"] 与 ["a", "bc"] 拼接后哈希冲突。"""
    from memory.reflection import _reflection_id_from_facts

    assert _reflection_id_from_facts(["ab", "c"]) != _reflection_id_from_facts(
        ["a", "bc"]
    )


def test_reflection_id_format():
    from memory.reflection import _reflection_id_from_facts

    rid = _reflection_id_from_facts(["f1", "f2"])
    assert rid.startswith("ref_")
    # sha256[:16] 即 16 个 hex
    assert len(rid) == len("ref_") + 16
    assert all(c in "0123456789abcdef" for c in rid[4:])


# ── synthesize_reflections idempotency（核心用例）──────────────────


def _build_mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    # aget_character_data 是 async method；默认 return_value 不会自动变 awaitable
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake-model", "base_url": "http://fake", "api_key": "sk-fake",
    })
    return cm


def _write_unabsorbed_facts(tmpdir: str, character: str, fact_ids: list[str]):
    """把 fact 直接写进 facts.json，模拟 extract_facts 已经跑过。"""
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
    """连续两次 synthesize 同一批 unabsorbed facts（模拟 mark_absorbed 崩溃后重启）
    → reflections.json 里只有一条 reflection；第二次也不会再调 LLM。"""
    mock_cm = _build_mock_cm(str(tmp_path))
    _write_unabsorbed_facts(str(tmp_path), "小天", [f"f{i}" for i in range(6)])

    with patch("memory.reflection.get_config_manager", return_value=mock_cm), \
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

        # Mock 掉 LLM 链：返回固定 reflection text。用一个计数器验证只调一次。
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
             patch("config.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            # 首次 synth
            first = await re.synthesize_reflections("小天")
            assert len(first) == 1
            first_rid = first[0]["id"]
            assert first_rid.startswith("ref_")

            # 模拟 mark_absorbed 崩溃：手动把 facts 再改回 absorbed=False
            import json
            fpath = os.path.join(str(tmp_path), "小天", "facts.json")
            with open(fpath, encoding="utf-8") as f:
                facts = json.load(f)
            for fact in facts:
                fact["absorbed"] = False
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(facts, f, ensure_ascii=False)
            # 清 FactStore 缓存以重读
            fs._facts.pop("小天", None)

            # 第二次 synth：应当发现 reflection 已存在 → 跳过 LLM → mark_absorbed
            second = await re.synthesize_reflections("小天")
            assert second == []  # 返回 [] 表示"没有新 reflection 产生"
            assert llm_call_count["n"] == 1, "同批 facts 不应再次调 LLM"

        # reflections.json 里只有一条（id 不重复）
        import json
        rpath = os.path.join(str(tmp_path), "小天", "reflections.json")
        with open(rpath, encoding="utf-8") as f:
            reflections = json.load(f)
        assert len(reflections) == 1
        assert reflections[0]["id"] == first_rid

        # facts 已被第二次 synth 的 mark_absorbed 修好
        with open(fpath, encoding="utf-8") as f:
            facts = json.load(f)
        assert all(ft["absorbed"] for ft in facts)


@pytest.mark.asyncio
async def test_synth_different_fact_set_produces_different_id(tmp_path):
    """facts 变化 → 新 reflection 写入（不会被错误 dedup）。"""
    mock_cm = _build_mock_cm(str(tmp_path))

    with patch("memory.reflection.get_config_manager", return_value=mock_cm), \
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
             patch("config.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            # 第一批：f1-f5
            _write_unabsorbed_facts(str(tmp_path), "小天", [f"f{i}" for i in range(5)])
            fs._facts.pop("小天", None)
            first = await re.synthesize_reflections("小天")
            assert len(first) == 1

            # 换一批 fact ids：f10-f14 （facts 都 absorbed=False，但 id 不同）
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
    """并发场景：第一次 aload 没看到 rid → LLM 跑完 → 第二次 aload 发现
    rid 已被对方协程持久化 → 必须返回 [] 而不是内存里的副本，否则调用方
    拿到的反思不在磁盘上、文本可能与磁盘版不同。"""
    from memory.reflection import _reflection_id_from_facts

    mock_cm = _build_mock_cm(str(tmp_path))
    fact_ids = [f"f{i}" for i in range(6)]
    _write_unabsorbed_facts(str(tmp_path), "小天", fact_ids)
    expected_rid = _reflection_id_from_facts(sorted(fact_ids))

    with patch("memory.reflection.get_config_manager", return_value=mock_cm), \
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

        # aload_reflections 第一次（line 349 短路检查）→ []，让 LLM 跑起来
        # 第二次（line 416 race re-load）→ 返回另一协程已写入的版本
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
            # 模拟并发：另一协程已落盘
            return [ghost_reflection]

        save_called = {"n": 0}
        original_asave = re.asave_reflections

        async def mock_asave(name, refs):
            save_called["n"] += 1
            await original_asave(name, refs)

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"), \
             patch.object(re, "aload_reflections", side_effect=mock_aload), \
             patch.object(re, "asave_reflections", side_effect=mock_asave):
            result = await re.synthesize_reflections("小天")

        # 关键断言：dedup 分支必须返回 []，不能把内存里未落盘的副本交出去
        assert result == [], (
            f"concurrent dedup must return [] (caller would otherwise see an "
            f"un-persisted reflection that may differ from disk). got: {result}"
        )
        # 我们这次没真正 save（dedup 命中跳过 append+save）
        assert save_called["n"] == 0
