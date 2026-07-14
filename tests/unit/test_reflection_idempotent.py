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
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
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
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
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


# ── synth 失败退避 / dead-letter（用户审计 #2）────────────────────


@pytest.mark.asyncio
async def test_synth_failure_bumps_backoff_and_dead_letters(tmp_path):
    """LLM 持续失败 → 每次 bump synth_backoff，达 MEMORY_LIVENESS_MAX_ATTEMPTS
    后 synthesize 不再调 LLM（dead-letter），不再每 180s 原样空烧。"""
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
            # 非 dict → 触发失败分支（non-dict response）
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
            # 调用 MAX + 3 次：前 MAX 次真正打 LLM 并 bump，之后 dead-letter 跳过
            for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS + 3):
                out = await re.synthesize_reflections("小天")
                assert out == []

        assert llm_call_count["n"] == MEMORY_LIVENESS_MAX_ATTEMPTS, (
            f"dead-letter 后不应再调 LLM；实际调了 {llm_call_count['n']} 次"
        )

        # backoff 文件落了该 rid 的计数（新格式 {key: {"n", "at"}}）
        from memory.reflection import _reflection_id_from_facts
        rid = _reflection_id_from_facts(sorted(f"f{i}" for i in range(6)))
        backoff = await re._aload_synth_backoff("小天")
        assert backoff.get(rid, {}).get("n") == MEMORY_LIVENESS_MAX_ATTEMPTS
        assert backoff.get(rid, {}).get("at"), "失败应戳上时间戳供自愈"


@pytest.mark.asyncio
async def test_synth_success_clears_backoff(tmp_path):
    """一次成功合成必须清掉该 rid 的失败退避记录（输入恢复正常即复活）。"""
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
        # 预置一条失败退避记录
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
    """写 facts.json，每条带显式 importance，用于构造 top-N cap 场景。"""
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
    """Codex P2：退避 key 必须覆盖全部 unabsorbed，而非 capped top-N(rid)。

    poison 的 top-20 dead-letter 后，新到的 fact（importance≥5 进得了
    unabsorbed 池，但低于 top-20 那批、进不了 cap）不会改 rid——若按 rid 退避
    会永久跳过、饿死整池。按全集 key 退避则新 fact 改 key → 预算复位 → 重试。

    注：aget_unabsorbed_facts 有 min_importance=5 闸门，importance<5 的 fact
    根本不进池，所以构造新 fact 用 importance=5（poison 批用 9 占满 top-20）。
    """
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS, REFLECTION_SYNTHESIS_FACTS_MAX

    mock_cm = _build_mock_cm(str(tmp_path))
    # 20 条高 importance（=top-N poison 批次）
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
            resp.content = "not json"  # 触发失败分支
            return resp

        class _FakeLLM:
            def __init__(self, *a, **kw): pass
            ainvoke = _fail_ainvoke

            async def aclose(self):
                return None

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch("config.prompts.prompts_memory.get_reflection_prompt", lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}"), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            # 跑到 dead-letter：MAX 次真打 LLM，之后跳过
            for _ in range(MEMORY_LIVENESS_MAX_ATTEMPTS + 2):
                assert await re.synthesize_reflections("小天") == []
            assert llm_calls["n"] == MEMORY_LIVENESS_MAX_ATTEMPTS

            # 加一条 importance=5 的 fact：进得了 unabsorbed 池（≥min_importance），
            # 但低于 poison 批的 9、挤不进 top-20，rid 不变，但全集 key 变
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
    """池子不变（挂机）时，dead-letter 过 5h 冷却后应放行一次 probe。

    模拟方式：把失败记录的时间戳手动改成 6h 前，再调 synthesize，断言 LLM
    被重新调用（cooldown_elapsed → 不再跳过）。
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
            assert llm_calls["n"] == MEMORY_LIVENESS_MAX_ATTEMPTS  # 已 dead-letter

            # 把失败时间戳改到 6h 前（池子完全不变）。退避现在以进程内镜像为准，
            # 改镜像而非磁盘文件。
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
    """synth_backoff 写盘一直失败（只读 FS / 权限）时，进程内镜像仍累计失败
    计数 → dead-letter 照常在 MAX 次后生效，不会每 180s 重打 LLM（Codex P2）。"""
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
