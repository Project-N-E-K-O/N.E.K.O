# -*- coding: utf-8 -*-
"""Unit tests for Memory Browser Phase 1: analytics imprint, 6-axis diagnostics, and manual corrections."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.analytics.imprint import abuild_memory_overview, build_memory_overview
from memory.analytics.imprint_note import agenerate_imprint_note
from memory.event_log import EventLog
from memory.facts import FactStore
from memory.persona import PersonaManager
from memory.reflection import ReflectionEngine
from memory.user_directives import UserDirectivesManager


class _FakeConfigManager:
    def __init__(self, root: Path):
        self.memory_dir = root / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.app_docs_dir = root

    async def aget_model_api_config(self, tier: str) -> dict:
        return {
            "model": f"fake-{tier}-model",
            "base_url": "https://example.invalid/v1",
            "api_key": "sk-test",
            "provider_type": "openai",
        }


def _seed_timeindex(db_path: Path, timestamps: list[str]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE time_indexed_original (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, role TEXT, content TEXT)"
        )
        for ts in timestamps:
            conn.execute(
                "INSERT INTO time_indexed_original (timestamp, role, content) VALUES (?, ?, ?)",
                (ts, "human", "hello"),
            )
        conn.commit()
    finally:
        conn.close()


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.asyncio
async def test_overview_builds_imprint_and_6axis_diagnostics(tmp_path: Path):
    cm = _FakeConfigManager(tmp_path)
    char_name = "小猫"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    now = datetime(2026, 6, 20, 12, 0, 0)
    ten_days_ago = (now - timedelta(days=10)).isoformat()
    eight_days_ago = (now - timedelta(days=8)).isoformat()

    _seed_timeindex(
        char_dir / "time_indexed.db",
        [(now - timedelta(days=14)).isoformat(), now.isoformat()],
    )

    _write_json(
        char_dir / "persona.json",
        {
            "master": {
                "facts": [
                    {
                        "id": "card_001",
                        "text": "出厂角色卡：喜欢和主人聊天",
                        "source": "character_card",
                        "protected": True,
                    },
                    {
                        "id": "prom_001",
                        "text": "主人最近改喝燕麦拿铁，不再喝美式",
                        "source": "reflection",
                        "source_id": "ref_001",
                        "created_at": ten_days_ago,
                        "reinforcement": 2.2,
                        "user_fact_reinforce_count": 2,
                        "version_history": [
                            {
                                "text": "主人每天早上喝冰美式",
                                "replaced_at": ten_days_ago,
                                "reason": "correction",
                            }
                        ],
                    },
                    {
                        "id": "prom_002",
                        "text": "主人最近改喝燕麦拿铁，不再喝美式了",
                        "source": "reflection",
                        "created_at": ten_days_ago,
                        "embedding": "!!!corrupt_base64!!!",
                        "suppress": True,
                    },
                ]
            },
            "relationship": {
                "facts": [
                    {
                        "id": "rel_001",
                        "text": "我们约定每周五晚上一起看一部电影",
                        "source": "manual",
                        "created_at": ten_days_ago,
                    }
                ]
            },
            "neko": {
                "facts": [
                    {
                        "id": "neko_001",
                        "text": "学会了在主人加班后先递一杯热茶安慰",
                        "source": "reflection",
                        "source_id": "ref_002",
                        "created_at": ten_days_ago,
                    }
                ]
            },
        },
    )

    _write_json(
        char_dir / "persona_corrections.json",
        [
            {
                "entity": "master",
                "old_text": "主人住在东京",
                "new_text": "主人搬去了大阪",
                "queued_at": ten_days_ago,
            }
        ],
    )

    _write_json(
        char_dir / "reflections.json",
        [
            {
                "id": "ref_pending_old",
                "entity": "master",
                "text": "主人周末常常睡到中午才起",
                "status": "pending",
                "relation_type": "habit",
                "created_at": eight_days_ago,
                "source_fact_ids": ["missing_fact_999"],
                "disputation": 1.5,
            },
            {
                "id": "ref_milestone",
                "entity": "relationship",
                "text": "一起通宵打通了双人成行",
                "status": "confirmed",
                "relation_type": "milestone",
                "created_at": ten_days_ago,
                "source_fact_ids": ["fact_001"],
            },
        ],
    )

    _write_json(
        char_dir / "facts.json",
        [
            {
                "id": "fact_001",
                "entity": "relationship",
                "text": "一起通宵通关了双人成行",
                "importance": 9,
                "created_at": ten_days_ago,
                "absorbed": False,
            }
        ],
    )

    with patch("memory.analytics.imprint.get_config_manager", return_value=cm):
        overview = await abuild_memory_overview(char_name, memory_dir=cm.memory_dir, now=now)

    stats = overview["imprint"]["stats"]
    assert stats["companion_days"] == 15
    assert stats["total_messages"] == 2
    assert stats["imprint_persona_count"] == 4
    assert stats["character_card_count"] == 1
    assert stats["facts_total"] == 1
    assert stats["pending_attention_count"] >= 2

    # Evolution & milestones populated
    assert len(overview["imprint"]["evolution"]) == 1
    assert overview["imprint"]["evolution"][0]["previous_text"] == "主人每天早上喝冰美式"
    assert len(overview["imprint"]["milestones"]) >= 1

    # 6 health dimensions present with expected technical findings
    dims = {d["key"]: d for d in overview["health"]["dimensions"]}
    assert set(dims.keys()) == {
        "consistency",
        "redundancy",
        "embedding",
        "structure",
        "pipeline",
        "freshness",
    }
    cons_codes = {f["code"] for f in dims["consistency"]["findings"]}
    assert "N1" in cons_codes
    assert "B1" in cons_codes

    emb_codes = {f["code"] for f in dims["embedding"]["findings"]}
    assert "E3" in emb_codes

    struct_codes = {f["code"] for f in dims["structure"]["findings"]}
    assert "D4" in struct_codes
    assert "D2" in struct_codes

    pipe_codes = {f["code"] for f in dims["pipeline"]["findings"]}
    assert "F3" in pipe_codes

    red_codes = {f["code"] for f in dims["redundancy"]["findings"]}
    assert "A1" in red_codes
    assert len(overview["memories"]["near_duplicates"]) >= 1


@pytest.mark.asyncio
async def test_manual_persona_reflection_fact_and_directive_mutations(tmp_path: Path):
    cm = _FakeConfigManager(tmp_path)
    char_name = "测试猫娘"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        char_dir / "persona.json",
        {
            "master": {
                "facts": [
                    {
                        "id": "card_1",
                        "text": "角色卡受保护条目",
                        "source": "character_card",
                        "protected": True,
                    },
                    {
                        "id": "prom_1",
                        "text": "主人喜欢黑咖啡",
                        "source": "reflection",
                        "embedding": "stale_vec",
                        "embedding_text_hash": "oldhash",
                    },
                ]
            }
        },
    )
    _write_json(
        char_dir / "persona_corrections.json",
        [
            {
                "entity": "master",
                "old_text": "主人喜欢喝拿铁",
                "new_text": "主人改喝乌龙茶了",
            }
        ],
    )
    _write_json(
        char_dir / "reflections.json",
        [
            {
                "id": "ref_1",
                "entity": "master",
                "text": "主人晚上喜欢散步",
                "status": "pending",
                "created_at": datetime.now().isoformat(),
            }
        ],
    )
    _write_json(
        char_dir / "facts.json",
        [
            {
                "id": "fact_1",
                "entity": "master",
                "text": "今天去了公园",
                "importance": 6,
                "created_at": datetime.now().isoformat(),
            }
        ],
    )

    with (
        patch("memory.event_log.get_config_manager", return_value=cm),
        patch("memory.persona.manager.get_config_manager", return_value=cm),
        patch("memory.persona.persistence.assert_cloudsave_writable"),
        patch("memory.persona.corrections.assert_cloudsave_writable"),
        patch("memory.reflection.manager.get_config_manager", return_value=cm),
        patch("memory.reflection.evidence_flow.assert_cloudsave_writable"),
        patch("memory.reflection.persistence.assert_cloudsave_writable"),
        patch("memory.facts.get_config_manager", return_value=cm),
        patch("memory.facts.assert_cloudsave_writable"),
        patch("memory.user_directives.get_config_manager", return_value=cm),
    ):
        ev_log = EventLog()
        pm = PersonaManager(event_log=ev_log)
        pm._config_manager = cm
        fs = FactStore()
        fs._config_manager = cm
        re_eng = ReflectionEngine(fact_store=fs, persona_manager=pm, event_log=ev_log)
        re_eng._config_manager = cm

        # 1. Protected entry cannot be edited
        with pytest.raises(PermissionError):
            await pm.aedit_persona_entry(char_name, "master", "card_1", "非法修改")

        # 2. Edit persona pushes version_history & clears embedding cache
        edited = await pm.aedit_persona_entry(char_name, "master", "prom_1", "主人喜欢喝拿铁")
        assert edited is not None
        assert edited["text"] == "主人喜欢喝拿铁"
        assert not edited.get("embedding")
        assert len(edited["version_history"]) == 1
        assert edited["version_history"][0]["text"] == "主人喜欢黑咖啡"

        # 3. Toggle user_pinned & suppress flags
        flagged = await pm.aset_persona_flags(
            char_name, "master", "prom_1", user_pinned=True, suppress=True,
        )
        assert flagged["user_pinned"] is True
        assert flagged["suppress"] is True

        # 4. Restore historical version
        restored = await pm.arestore_persona_version(char_name, "master", "prom_1", 0)
        assert restored["text"] == "主人喜欢黑咖啡"
        assert len(restored["version_history"]) == 2

        # 5. Resolve pending correction manually
        res_corr = await pm.aresolve_correction_manual(
            char_name, 0, "merge", merged_text="主人以前喝拿铁，现在改喝乌龙茶",
        )
        assert res_corr["remaining_corrections"] == 0

        # 6. Review reflection: confirm + forget
        rev = await re_eng.amanual_review_reflection(
            char_name, "ref_1", "confirm", edited_text="主人晚饭后常去散步",
        )
        assert rev["status"] == "confirmed"
        assert rev["text"] == "主人晚饭后常去散步"
        forgot = await re_eng.amanual_review_reflection(char_name, "ref_1", "forget")
        assert forgot["archived"] is True

        # 7. Edit & archive fact
        edited_fact = await fs.aedit_fact_manual(char_name, "fact_1", "今天去了植物园")
        assert edited_fact["text"] == "今天去了植物园"
        assert await fs.aarchive_fact_manual(char_name, "fact_1") is True
        assert await fs.aload_facts(char_name) == []

        # 8. User directives add & remove
        dm = UserDirectivesManager()
        created = await dm.arecord(
            char_name, locale="zh", kind="ban_topic", term="加班", source="manual",
        )
        assert created["term"] == "加班"
        assert len(await dm.aget_active(char_name)) == 1
        assert await dm.aremove(char_name, "加班") is True
        assert await dm.aget_active(char_name) == []


@pytest.mark.asyncio
async def test_generate_imprint_note_caches_and_obeys_llm_rules(tmp_path: Path):
    cm = _FakeConfigManager(tmp_path)
    char_name = "小猫"
    (cm.memory_dir / char_name).mkdir(parents=True, exist_ok=True)

    fake_resp = MagicMock()
    fake_resp.content = json.dumps(
        {
            "summary": "这些天里，你们一起留下了温暖的对话足迹。",
            "commentary": "你总是很细腻地分享日常里的微小瞬间。",
            "blessing": "愿往后的日子继续温暖相伴。",
        },
        ensure_ascii=False,
    )
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=fake_resp)
    fake_llm.aclose = AsyncMock()

    create_mock = AsyncMock(return_value=fake_llm)

    with (
        patch("memory.analytics.imprint_note.assert_cloudsave_writable"),
        patch("memory.analytics.imprint_note.create_chat_llm_async", create_mock),
        patch("memory.analytics.imprint.get_config_manager", return_value=cm),
    ):
        note = await agenerate_imprint_note(char_name, language="zh-CN", config_manager=cm)

    # Verify LLM contract: no temperature, explicit timeout + max_completion_tokens
    _, kwargs = create_mock.call_args
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] > 0
    assert kwargs["timeout"] > 0
    assert note["summary"]
    assert note["blessing"]

    # Subsequent overview reads the cached note without calling LLM
    overview = build_memory_overview(char_name, memory_dir=cm.memory_dir)
    cached = overview["imprint"]["warm_summary"]["cached_note"]
    assert cached is not None
    assert cached["summary"] == note["summary"]


@pytest.mark.asyncio
async def test_star_map_coldstart_ready_weekly_cache_and_related(tmp_path: Path):
    import base64
    import numpy as np

    from memory.analytics.star_map import (
        abuild_star_map,
        afind_related_memories,
        alabel_star_map_clusters,
    )

    cm = _FakeConfigManager(tmp_path)
    char_name = "星图猫"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    def _encode_vec(vals: list[float]) -> str:
        arr = np.asarray(vals, dtype=np.float16)
        return base64.b64encode(arr.tobytes()).decode("ascii")

    # 1. Cold start (< 15 embedded entries)
    _write_json(
        char_dir / "facts.json",
        [
            {"id": "f0", "entity": "master", "text": "早上喝拿铁", "embedding": _encode_vec([1.0, 0.0, 0.0, 0.0])},
            {"id": "f1", "entity": "master", "text": "晚上喝乌龙茶", "embedding": _encode_vec([0.9, 0.1, 0.0, 0.0])},
        ],
    )
    cold = await abuild_star_map(char_name, memory_dir=cm.memory_dir)
    assert cold["ready"] is False
    assert cold["embedded_count"] == 2
    assert cold["points"] == []

    # 2. Seed 16 embedded memories in 2 clear clusters
    facts = []
    for i in range(8):
        facts.append({
            "id": f"coffee_{i}",
            "entity": "master",
            "text": f"咖啡偏好记录 {i}：喜欢燕麦拿铁和手冲",
            "importance": 7,
            "embedding": _encode_vec([1.0, 0.05 * i, 0.0, 0.0]),
        })
    for i in range(8):
        facts.append({
            "id": f"movie_{i}",
            "entity": "relationship",
            "text": f"电影夜记录 {i}：周五一起看治愈生活电影",
            "importance": 8,
            "embedding": _encode_vec([0.0, 0.0, 1.0, 0.05 * i]),
        })
    _write_json(char_dir / "facts.json", facts)

    ready_map = await abuild_star_map(char_name, memory_dir=cm.memory_dir)
    assert ready_map["ready"] is True
    assert len(ready_map["points"]) == 16
    assert len(ready_map["clusters"]) >= 2

    # 3. Manual theme labeling obeys LLM contract + persists cache
    fake_resp = MagicMock()
    fake_resp.content = json.dumps({"0": "咖啡日常", "1": "周五电影夜"}, ensure_ascii=False)
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=fake_resp)
    fake_llm.aclose = AsyncMock()
    create_mock = AsyncMock(return_value=fake_llm)

    now = datetime(2026, 6, 20, 12, 0, 0)
    with (
        patch("memory.analytics.star_map.assert_cloudsave_writable"),
        patch("memory.analytics.star_map.create_chat_llm_async", create_mock),
    ):
        labeled = await alabel_star_map_clusters(
            char_name, language="zh-CN", config_manager=cm, force=True, now=now,
        )
        assert labeled["skipped"] is False
        assert labeled["labels"]["0"] == "咖啡日常"
        _, kwargs = create_mock.call_args
        assert "temperature" not in kwargs
        assert kwargs["max_completion_tokens"] > 0
        assert kwargs["timeout"] > 0

        # Weekly background call (force=False) skips when corpus unchanged
        skipped = await alabel_star_map_clusters(
            char_name, language="zh-CN", config_manager=cm, force=False, now=now + timedelta(days=1),
        )
        assert skipped["skipped"] is True
        assert skipped["reason"] == "corpus_unchanged"
        assert create_mock.call_count == 1

    # 4. Related memories lookup returns neighbor memories
    related = await afind_related_memories(
        char_name,
        kind="fact",
        entry_id="coffee_0",
        text="咖啡偏好记录 0：喜欢燕麦拿铁和手冲",
        top_k=3,
        memory_dir=cm.memory_dir,
    )
    assert len(related) == 3
    assert all(r["id"].startswith("coffee_") and r["id"] != "coffee_0" for r in related)


@pytest.mark.asyncio
async def test_lineage_story_builds_3_steps_with_persisted_and_fallback_links(tmp_path: Path):
    from memory.analytics.lineage import abuild_memory_lineage_story

    cm = _FakeConfigManager(tmp_path)
    char_name = "溯源猫"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)
    ts = "2026-06-15T21:30:00"

    # Seed using production time_indexed_original schema: (id, session_id, timestamp, message JSON)
    conn = sqlite3.connect(str(char_dir / "time_indexed.db"))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS time_indexed_original "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp TEXT, message TEXT)"
        )
        conn.execute(
            "INSERT INTO time_indexed_original (session_id, timestamp, message) VALUES (?, ?, ?)",
            (
                "sess_1",
                ts,
                json.dumps(
                    {"type": "human", "data": {"content": "我最近早上都改喝燕麦拿铁啦，冰美式有点伤胃"}},
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _write_json(
        char_dir / "facts.json",
        [
            {
                "id": "fact_latte",
                "entity": "master",
                "text": "早上改喝燕麦拿铁，不再喝冰美式",
                "importance": 8,
                "created_at": ts,
            }
        ],
    )
    _write_json(
        char_dir / "reflections.json",
        [
            {
                "id": "ref_latte",
                "entity": "master",
                "text": "偏好燕麦拿铁胜过冰美式",
                "status": "promoted",
                "source_fact_ids": ["fact_latte"],
                "created_at": ts,
            }
        ],
    )
    _write_json(
        char_dir / "persona.json",
        {
            "master": {
                "facts": [
                    {
                        "id": "prom_latte",
                        "text": "早上习惯喝燕麦拿铁",
                        "source": "reflection",
                        "source_id": "ref_latte",
                        "created_at": ts,
                    }
                ]
            }
        },
    )

    story = await abuild_memory_lineage_story(
        char_name,
        kind="persona",
        entry_id="prom_latte",
        memory_dir=cm.memory_dir,
    )
    assert story is not None
    assert story["target"]["id"] == "prom_latte"
    assert len(story["steps"]["facts"]) == 1
    assert story["steps"]["facts"][0]["id"] == "fact_latte"
    assert story["steps"]["facts_inferred"] is False
    assert any("燕麦拿铁" in c["text"] for c in story["steps"]["conversations"])
    assert len(story["steps"]["reflections"]) == 1


@pytest.mark.asyncio
async def test_phase3_snapshots_create_cap_and_restore_with_cache_invalidation(tmp_path: Path):
    from memory.analytics.snapshots import (
        acreate_snapshot,
        alist_snapshots,
        arestore_snapshot,
    )

    cm = _FakeConfigManager(tmp_path)
    char_name = "保险箱猫"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        char_dir / "persona.json",
        {"master": {"facts": [{"id": "p1", "text": "初始版本：喜欢冰美式"}]}},
    )
    _write_json(char_dir / "facts.json", [{"id": "f1", "text": "早上喝冰美式"}])

    with patch("memory.analytics.snapshots.assert_cloudsave_writable"):
        snap1 = await acreate_snapshot(
            char_name,
            label="整理前",
            config_manager=cm,
            max_keep=3,
        )
        assert snap1["snapshot_id"].startswith("snap_")
        assert snap1["stats"]["persona_count"] == 1

        # Mutate live files
        _write_json(
            char_dir / "persona.json",
            {"master": {"facts": [{"id": "p1", "text": "修改后：只喝乌龙茶"}]}},
        )
        await acreate_snapshot(char_name, label="v2", config_manager=cm, max_keep=3)
        await acreate_snapshot(char_name, label="v3", config_manager=cm, max_keep=3)
        # Rolling cap keeps newest 3 (snap1 gets pruned if we add a 4th, so test cap with max_keep=4 first)
        listed = await alist_snapshots(char_name, memory_dir=cm.memory_dir)
        assert len(listed) == 3

        # Fake managers with warm caches & locks
        class _DummyPersona:
            def __init__(self):
                self._personas = {char_name: {"stale": True}}
                self._lock = asyncio.Lock()

            def _get_alock(self, _n):
                return self._lock

        class _DummyFacts:
            def __init__(self):
                self._facts = {char_name: [{"stale": True}]}
                self._alock = asyncio.Lock()
                self._tlock = threading.Lock()

            def _get_persist_alock(self, _n):
                return self._alock

            def _get_lock(self, _n):
                return self._tlock

        import asyncio
        import threading

        pm = _DummyPersona()
        fs = _DummyFacts()
        # Restore the OLDEST snapshot while vault is AT CAPACITY (len == max_keep == 3)
        # to verify pre_restore pruning never deletes the snapshot before reading it.
        res = await arestore_snapshot(
            char_name,
            snap1["snapshot_id"],
            config_manager=cm,
            persona_manager=pm,
            fact_store=fs,
            max_keep=3,
        )
        assert res["restored_snapshot_id"] == snap1["snapshot_id"]
        assert res["pre_restore_snapshot_id"].startswith("snap_")
        assert char_name not in pm._personas
        assert char_name not in fs._facts

        restored_persona = json.loads((char_dir / "persona.json").read_text(encoding="utf-8"))
        assert restored_persona["master"]["facts"][0]["text"] == "初始版本：喜欢冰美式"

        # Verify pre_restore backup exists
        after_list = await alist_snapshots(char_name, memory_dir=cm.memory_dir)
        assert any(s["reason"] == "pre_restore" for s in after_list)


@pytest.mark.asyncio
async def test_phase3_zip_export_keepsake_and_redacted_diagnostics(tmp_path: Path):
    import io
    import zipfile

    from memory.analytics.export import abuild_memory_export_zip

    cm = _FakeConfigManager(tmp_path)
    char_name = "导出猫"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)
    ts = "2026-07-01T10:00:00"

    _write_json(
        char_dir / "persona.json",
        {
            "master": {
                "facts": [
                    {
                        "id": "p1",
                        "text": (
                            "邮箱 alice@example.com，手机 13812345678，密钥 sk-1234567890abcdef1234，"
                            'password=abc!def123 secret="my p@ss! word"'
                        ),
                        "created_at": ts,
                    }
                ]
            }
        },
    )
    _write_json(
        char_dir / "recent.json",
        [
            {"type": "human", "data": {"content": "绝密原始聊天内容不应出现在脱敏诊断包里"}},
        ],
    )

    # 1. Keepsake ZIP
    ks_bytes, ks_name = await abuild_memory_export_zip(
        char_name,
        mode="keepsake",
        memory_dir=cm.memory_dir,
    )
    assert ks_name.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(ks_bytes)) as zf:
        names = set(zf.namelist())
        assert "OUR_MEMORIES.md" in names
        assert "raw/persona.json" in names
        md = zf.read("OUR_MEMORIES.md").decode("utf-8")
        assert char_name in md

    # 2. Redacted Diagnostics ZIP
    dg_bytes, dg_name = await abuild_memory_export_zip(
        char_name,
        mode="diagnostics",
        memory_dir=cm.memory_dir,
    )
    assert "diagnostics_redacted" in dg_name
    with zipfile.ZipFile(io.BytesIO(dg_bytes)) as zf:
        names = set(zf.namelist())
        assert {"manifest.json", "overview.json", "star_map.json", "lineage_graph.json"} <= names
        combined = "\n".join(zf.read(n).decode("utf-8") for n in names)
        assert "alice@example.com" not in combined
        assert "13812345678" not in combined
        assert "sk-1234567890abcdef1234" not in combined
        assert "abc!def123" not in combined
        assert "my p@ss! word" not in combined
        assert "绝密原始聊天内容" not in combined
        assert "[REDACTED_EMAIL]" in combined
        assert "[REDACTED_PHONE]" in combined
        assert "[REDACTED_API_KEY]" in combined
        assert "[REDACTED]" in combined


@pytest.mark.asyncio
async def test_phase3_full_lineage_dag_and_deep_conflict_llm_compliance(tmp_path: Path):
    from memory.analytics.deep_conflicts import arun_deep_conflict_check
    from memory.analytics.lineage import abuild_full_lineage_graph

    cm = _FakeConfigManager(tmp_path)
    char_name = "因果猫"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)
    ts = "2026-08-12T12:00:00"

    _write_json(
        char_dir / "facts.json",
        [{"id": "f1", "entity": "master", "text": "每天早上都会喝一杯燕麦拿铁", "created_at": ts}],
    )
    _write_json(
        char_dir / "reflections.json",
        [
            {
                "id": "r1",
                "entity": "master",
                "text": "早上固定喝燕麦拿铁",
                "status": "promoted",
                "source_fact_ids": ["f1"],
                "created_at": ts,
            }
        ],
    )
    _write_json(
        char_dir / "persona.json",
        {
            "master": {
                "facts": [
                    {
                        "id": "p1",
                        "text": "每天早上固定喝燕麦拿铁提神",
                        "source": "reflection",
                        "source_id": "r1",
                        "created_at": ts,
                    },
                    {
                        "id": "p2",
                        "text": "每天早上完全不喝任何燕麦拿铁或咖啡",
                        "source": "reflection",
                        "created_at": ts,
                    },
                ]
            }
        },
    )

    graph = await abuild_full_lineage_graph(char_name, memory_dir=cm.memory_dir)
    assert graph["lanes"] == ["message", "recent_memo", "fact", "reflection", "persona_entry"]
    assert any(e["source"] == "fact:f1" and e["target"] == "reflection:r1" for e in graph["edges"])
    assert any(e["source"] == "reflection:r1" and e["target"] == "persona:p1" for e in graph["edges"])

    fake_resp = MagicMock()
    fake_resp.content = json.dumps(
        [
            {
                "pair_id": "P1",
                "verdict": "contradiction",
                "reason": "一条记录喝燕麦拿铁，另一条记录完全不喝。",
                "suggestion": "建议确认当前咖啡习惯并归档旧印象。",
            }
        ],
        ensure_ascii=False,
    )
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=fake_resp)
    fake_llm.aclose = AsyncMock()
    create_mock = AsyncMock(return_value=fake_llm)

    with patch("memory.analytics.deep_conflicts.create_chat_llm_async", create_mock):
        audit = await arun_deep_conflict_check(
            char_name,
            language="zh-CN",
            config_manager=cm,
            memory_dir=cm.memory_dir,
        )

    assert audit["checked_pairs"] >= 1
    assert audit["summary"]["contradiction"] == 1
    sent_prompt = fake_llm.ainvoke.call_args[0][0]
    assert "{PAIRS}" not in sent_prompt
    assert "燕麦拿铁" in sent_prompt
    _, kwargs = create_mock.call_args
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] > 0
    assert kwargs["timeout"] > 0


@pytest.mark.asyncio
async def test_greptile_regression_guards_replay_stale_sha_and_correction_id(tmp_path: Path):
    """Regression tests for EventLog text replay, embedding_text_sha256 staleness,
    correction_id conflict safety, and manual-forget retirement markers."""
    from memory.evidence_handlers import make_persona_entry_handler
    from memory.facts import FactStore
    from memory.persona.manager import PersonaManager

    cm = _FakeConfigManager(tmp_path)
    char_name = "回归猫"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    # 1. Stale embedding_text_sha256 detection in overview health
    import base64
    import numpy as np

    vec_b64 = base64.b64encode(np.ones(8, dtype=np.float16).tobytes()).decode("ascii")
    _write_json(
        char_dir / "persona.json",
        {
            "master": {
                "facts": [
                    {
                        "id": "p_stale",
                        "text": "现在改喝乌龙茶",
                        "embedding": vec_b64,
                        "embedding_text_sha256": "0" * 64,
                        "source": "reflection",
                    }
                ]
            }
        },
    )
    overview = build_memory_overview(char_name, memory_dir=cm.memory_dir)
    emb_dim = next(d for d in overview["health"]["dimensions"] if d["key"] == "embedding")
    assert emb_dim["metrics"]["stale"] == 1

    # 2. EVT_PERSONA_ENTRY_UPDATED uses RFC §3.3.6 rewrite_text_sha256 (no plaintext in log)
    #    and raises RuntimeError when the view text drifted from the recorded sha256.
    with patch("memory.persona.manager.get_config_manager", return_value=cm):
        pm = PersonaManager()
        pm._config_manager = cm
        handler = make_persona_entry_handler(pm)
        matching_sha = hashlib.sha256("现在改喝乌龙茶".encode("utf-8")).hexdigest()
        assert handler(
            char_name,
            {
                "entity_key": "master",
                "entry_id": "p_stale",
                "rewrite_text_sha256": matching_sha,
            },
        ) is False
        with pytest.raises(RuntimeError):
            handler(
                char_name,
                {
                    "entity_key": "master",
                    "entry_id": "p_stale",
                    "rewrite_text_sha256": "f" * 64,
                },
            )

    # 3. Stale correction_id raises LookupError instead of mutating another queued item
    _write_json(
        char_dir / "persona_corrections.json",
        [{"correction_id": "cid_live", "entity": "master", "old_text": "A", "new_text": "B"}],
    )
    with (
        patch("memory.persona.manager.get_config_manager", return_value=cm),
        patch("memory.persona.corrections.assert_cloudsave_writable"),
    ):
        pm2 = PersonaManager()
        pm2._config_manager = cm
        with pytest.raises(LookupError):
            await pm2.aresolve_correction_manual(
                char_name, 0, "keep_new", correction_id="cid_already_gone",
            )

    # 4. Cold-cache manual fact forget does not deadlock & stamps arbitration_archived_at
    _write_json(char_dir / "facts.json", [{"id": "f_cold", "entity": "master", "text": "旧事实"}])
    with (
        patch("memory.facts.get_config_manager", return_value=cm),
        patch("memory.facts.assert_cloudsave_writable"),
    ):
        fs = FactStore()
        fs._config_manager = cm
        assert char_name not in fs._facts  # cold cache!
        edited = await fs.aedit_fact_manual(char_name, "f_cold", "新事实")
        assert edited is not None and edited["text"] == "新事实"
        assert await fs.aarchive_fact_manual(char_name, "f_cold") is True
        archived = json.loads((char_dir / "facts_archive.json").read_text(encoding="utf-8"))
        assert archived and archived[0].get("arbitration_archived_at")
        assert archived[0].get("arbitration_reason") == "manual_forget"


@pytest.mark.asyncio
async def test_round2_merges_snapshot_archives_directives_and_redaction(tmp_path: Path):
    """Cover Round-2 fixes: fact/persona/reflection merge atomicity, archive snapshots,
    permanent suppression metadata, keepsake cached_note, diagnostics redaction,
    directive TTL renewal, external_import hash+FTS, correction keep_new, and 400 validation."""
    import io
    import zipfile

    from main_routers.memory_router import _validate_browser_character_or_400
    from memory.analytics.export import build_memory_export_zip, redact_obj
    from memory.analytics.snapshots import acreate_snapshot, arestore_snapshot

    cm = _FakeConfigManager(tmp_path)
    char_name = "小橘"
    char_dir = cm.memory_dir / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    # 1. Invalid character name returns HTTP 400 (not AttributeError 500)
    bad_resp = _validate_browser_character_or_400("bad/name")
    assert bad_resp is not None and bad_resp.status_code == 400
    empty_resp = _validate_browser_character_or_400("")
    assert empty_resp is not None and empty_resp.status_code == 400

    # 2. Snapshot create -> forget fact + archive persona -> restore rolls back BOTH live & archive
    _write_json(
        char_dir / "persona.json",
        {
            "master": {
                "facts": [
                    {"id": "p_keep", "text": "喜欢听爵士乐", "source": "reflection"},
                    {"id": "p_drop", "text": "很喜欢听爵士音乐", "source": "reflection"},
                    {
                        "id": "p_retired",
                        "text": "内省已退役的旧印象",
                        "source": "reflection",
                        "permanent_suppression": {"at": "2026-01-01T00:00:00", "reason": "redundant"},
                    },
                ]
            }
        },
    )
    _write_json(
        char_dir / "reflections.json",
        [
            {"id": "r_keep", "entity": "master", "text": "爱去海边", "status": "promoted"},
            {"id": "r_drop", "entity": "master", "text": "喜欢去海边散步", "status": "promoted"},
        ],
    )
    _write_json(
        char_dir / "facts.json",
        [
            {
                "id": "f_keep",
                "entity": "master",
                "text": "周六买了咖啡豆",
                "external_import": {"section": "daily", "event_date": "2026-05-01"},
            },
            {"id": "f_drop", "entity": "master", "text": "周六买了一包咖啡豆"},
        ],
    )
    _write_json(
        char_dir / "persona_corrections.json",
        [
            {"correction_id": "c_1", "entity": "master", "old_text": "喜欢听爵士乐", "new_text": "现在更喜欢古典乐"},
            {"old_text": "旧无实体条目", "new_text": "新无实体条目"},
        ],
    )

    # Permanent suppression must stay distinct from temporary suppress in overview
    ov = build_memory_overview(char_name, memory_dir=cm.memory_dir)
    retired_cards = [c for c in ov["memories"]["suppressed"] if c["id"] == "p_retired"]
    assert len(retired_cards) == 1
    assert retired_cards[0]["permanent_suppression"] is True
    assert retired_cards[0]["suppress"] is False

    fake_timeindex = MagicMock()
    fake_timeindex.aindex_fact = AsyncMock(return_value=True)
    fake_timeindex.adelete_fact_from_index = AsyncMock()
    fake_timeindex.arebuild_fact_index = AsyncMock(return_value=2)

    with (
        patch("memory.event_log.get_config_manager", return_value=cm),
        patch("memory.persona.manager.get_config_manager", return_value=cm),
        patch("memory.persona.persistence.assert_cloudsave_writable"),
        patch("memory.persona.facts.assert_cloudsave_writable"),
        patch("memory.persona.corrections.assert_cloudsave_writable"),
        patch("memory.reflection.persistence.assert_cloudsave_writable"),
        patch("memory.reflection.evidence_flow.assert_cloudsave_writable"),
        patch("memory.facts.get_config_manager", return_value=cm),
        patch("memory.facts.assert_cloudsave_writable"),
        patch("memory.analytics.snapshots.assert_cloudsave_writable"),
        patch("memory.user_directives.get_config_manager", return_value=cm),
    ):
        ev_log = EventLog()
        pm = PersonaManager(event_log=ev_log)
        pm._config_manager = cm
        fs = FactStore(time_indexed_memory=fake_timeindex)
        fs._config_manager = cm
        re_eng = ReflectionEngine(fact_store=fs, persona_manager=pm, event_log=ev_log)
        re_eng._config_manager = cm

        # Create safety checkpoint BEFORE mutations (passing managers to exercise lock path)
        snap = await acreate_snapshot(
            char_name,
            label="pre-ops",
            config_manager=cm,
            persona_manager=pm,
            fact_store=fs,
            reflection_engine=re_eng,
        )

        # Oversized merged_text (>1000 chars) fails WITHOUT archiving drop_id for all 3 kinds
        too_long = "长" * 1005
        with pytest.raises(ValueError):
            await pm.amerge_persona_entries(char_name, "master", "p_keep", "p_drop", too_long)
        with pytest.raises(ValueError):
            await re_eng.amerge_reflections(char_name, "r_keep", "r_drop", too_long)
        with pytest.raises(ValueError):
            await fs.amerge_facts_manual(char_name, "f_keep", "f_drop", too_long)
        assert len((await pm.aensure_persona(char_name))["master"]["facts"]) == 3
        assert len(await re_eng._aload_reflections_full(char_name)) == 2
        assert len(await fs.aload_facts(char_name)) == 2

        # Cross-subject fact merge is rejected before any write
        cur_facts = await fs.aload_facts(char_name)
        cur_facts.append({
            "id": "f_other_subj",
            "entity": "master",
            "text": "周六买了咖啡豆",
            "subject_kind": "person",
            "subject_id": "alice",
            "scope": "group:1",
        })
        fs.save_facts(char_name)
        with pytest.raises(ValueError):
            await fs.amerge_facts_manual(char_name, "f_keep", "f_other_subj", "不应跨主体合并")
        cur_facts.pop()
        fs.save_facts(char_name)

        # Shard preparation failure leaves keep_entry untouched in amerge_persona_entries
        with patch("memory.archive_shards.apick_today_shard_path", AsyncMock(side_effect=OSError("disk full"))):
            with pytest.raises(OSError):
                await pm.amerge_persona_entries(char_name, "master", "p_keep", "p_drop", "不应提前改写")
        persona_after_fail = (await pm.aensure_persona(char_name))["master"]["facts"]
        assert next(e for e in persona_after_fail if e["id"] == "p_keep")["text"] == "喜欢听爵士乐"
        assert any(e["id"] == "p_drop" for e in persona_after_fail)

        # Daily external_import fact edit preserves date-salted hash & refreshes FTS with replace=True
        edited_daily = await fs.aedit_fact_manual(char_name, "f_keep", "周六买了埃塞俄比亚豆")
        expected_daily_hash = hashlib.sha256("2026-05-01\n周六买了埃塞俄比亚豆".encode()).hexdigest()[:16]
        assert edited_daily is not None and edited_daily["hash"] == expected_daily_hash
        fake_timeindex.aindex_fact.assert_awaited_with(char_name, "f_keep", "周六买了埃塞俄比亚豆", replace=True)

        # Valid merges across fact, persona, and promoted reflection all succeed with item-level archive assertions
        merged_fact = await fs.amerge_facts_manual(char_name, "f_keep", "f_drop", "周六买了精品咖啡豆")
        assert merged_fact is not None and merged_fact["text"] == "周六买了精品咖啡豆"
        assert [f["id"] for f in await fs.aload_facts(char_name)] == ["f_keep"]
        assert (char_dir / "facts_archive.json").is_file()

        merged_persona = await pm.amerge_persona_entries(char_name, "master", "p_keep", "p_drop", "很喜欢听爵士乐")
        assert merged_persona["text"] == "很喜欢听爵士乐"
        active_persona_ids = [e["id"] for e in (await pm.aensure_persona(char_name))["master"]["facts"]]
        assert "p_keep" in active_persona_ids and "p_drop" not in active_persona_ids
        p_shards = list((char_dir / "persona_archive").glob("*.json"))
        assert p_shards
        p_archived = json.loads(p_shards[0].read_text(encoding="utf-8"))
        assert any(e.get("id") == "p_drop" and e.get("merged_into") == "p_keep" for e in p_archived)

        merged_refl = await re_eng.amerge_reflections(char_name, "r_keep", "r_drop", "喜欢去海边散步")
        assert merged_refl["text"] == "喜欢去海边散步"
        active_refl_ids = [r["id"] for r in await re_eng._aload_reflections_full(char_name)]
        assert "r_keep" in active_refl_ids and "r_drop" not in active_refl_ids
        r_shards = list((char_dir / "reflection_archive").glob("*.json"))
        assert r_shards
        r_archived = json.loads(r_shards[0].read_text(encoding="utf-8"))
        assert any(r.get("id") == "r_drop" and r.get("merged_into") == "r_keep" for r in r_archived)

        # Automatic archive sweep skips pinned entries under persona lock
        await pm.aset_persona_flags(char_name, "master", "p_keep", user_pinned=True)
        assert await pm.aarchive_persona_entry(char_name, "master", "p_keep", skip_pinned=True) is False
        assert any(e["id"] == "p_keep" for e in (await pm.aensure_persona(char_name))["master"]["facts"])

        # Restore checkpoint: forgotten/merged facts & persona/reflection shards must NOT remain duplicated
        # AND FTS index must be rebuilt from restored facts.json
        await arestore_snapshot(
            char_name,
            snap["snapshot_id"],
            config_manager=cm,
            persona_manager=pm,
            fact_store=fs,
            reflection_engine=re_eng,
        )
        restored_facts = await fs.aload_facts(char_name)
        assert {f["id"] for f in restored_facts} == {"f_keep", "f_drop"}
        assert not (char_dir / "facts_archive.json").exists()
        assert not list((char_dir / "persona_archive").glob("*.json"))
        assert not list((char_dir / "reflection_archive").glob("*.json"))
        fake_timeindex.arebuild_fact_index.assert_awaited_once()

        # Snapshot manifest missing `files` list is rejected without deleting live files
        bad_snap_dir = char_dir / "browser_snapshots" / "snap_bad_manifest"
        bad_snap_dir.mkdir(parents=True, exist_ok=True)
        _write_json(bad_snap_dir / "meta.json", {"snapshot_id": "snap_bad_manifest", "label": "corrupt"})
        with pytest.raises(ValueError):
            await arestore_snapshot(
                char_name, "snap_bad_manifest", config_manager=cm, fact_store=fs,
            )
        assert (char_dir / "facts.json").is_file()

        # Direct manual correction resolve (keep_new) + legacy entry without entity
        r1 = await pm.aresolve_correction_manual(char_name, 0, "keep_new", correction_id="c_1")
        assert r1["remaining_corrections"] == 1
        r2 = await pm.aresolve_correction_manual(char_name, 0, "keep_new")
        assert r2["remaining_corrections"] == 0

        # UserDirectives renewal preserves extended TTL (> base 3-day TTL)
        dm = UserDirectivesManager()
        base_ts = 1_700_000_000.0
        for step in range(4):
            dm.record(char_name, locale="zh", kind="ban_topic", term="考试", now=base_ts + step)
        before_exp = dm.get_active(char_name, now=base_ts + 5)[0]["expire_at"]
        renewed = await dm.arecord(char_name, locale="zh", kind="ban_topic", term="考试", now=base_ts + 10)
        assert renewed["hit_count"] == 5
        assert renewed["expire_at"] >= before_exp

    # 3. Keepsake OUR_MEMORIES.md renders cached Relationship Note & remains unredacted;
    #    Diagnostics ZIP scrubs all credential variants + uses non-PII filename
    phone_char = "13812345678"
    p_dir = cm.memory_dir / phone_char
    p_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        p_dir / "imprint_notes_cache.json",
        {
            "summary": "你们之间有着默契的陪伴。",
            "commentary": "从咖啡聊到音乐，记忆越来越深。",
            "blessing": "愿你们继续同行。",
            "generated_at": "2026-05-02T10:00:00",
            "language": "zh-CN",
        },
    )
    _write_json(
        p_dir / "persona.json",
        {
            "master": {
                "facts": [
                    {
                        "id": "p_sec",
                        "text": (
                            'Remember "token": "opaque_quoted_tok_111", '
                            'token="opaque_unquoted_tok_222", '
                            'access_token=ghp_access_secret_333, '
                            'refresh_token=rf_secret_444, '
                            'csrf_token=csrf_secret_555, '
                            'openai_api_key=oai_secret_666, '
                            '"access_token": "quoted_compound_777", '
                            'Authorization: Basic dXNlcjpwYXNzODg4'
                        ),
                        "source": "reflection",
                    }
                ]
            }
        },
    )

    ks_bytes, ks_name = build_memory_export_zip(phone_char, mode="keepsake", memory_dir=cm.memory_dir)
    assert phone_char in ks_name
    with zipfile.ZipFile(io.BytesIO(ks_bytes)) as zf:
        md = zf.read("OUR_MEMORIES.md").decode("utf-8")
        assert "## Relationship Note" in md
        assert "你们之间有着默契的陪伴。" in md
        assert "愿你们继续同行。" in md
        assert "opaque_quoted_tok_111" in md  # keepsake intentionally unredacted

    diag_bytes, diag_name = build_memory_export_zip(phone_char, mode="diagnostics", memory_dir=cm.memory_dir)
    assert phone_char not in diag_name
    with zipfile.ZipFile(io.BytesIO(diag_bytes)) as zf:
        all_diag_text = "\n".join(zf.read(n).decode("utf-8", errors="replace") for n in zf.namelist())
        for secret_val in (
            "opaque_quoted_tok_111",
            "opaque_unquoted_tok_222",
            "ghp_access_secret_333",
            "rf_secret_444",
            "csrf_secret_555",
            "oai_secret_666",
            "quoted_compound_777",
            "dXNlcjpwYXNzODg4",
            phone_char,
        ):
            assert secret_val not in all_diag_text

    # Structured credential fields in nested dicts are also redacted while preserving token counts
    redacted_struct = redact_obj({
        "access_token": "nested_secret_999",
        "nested": {"api_key": "nested_key_000", "token_count": 42, "label": "normal text"},
    })
    assert redacted_struct["access_token"] == "[REDACTED]"
    assert redacted_struct["nested"]["api_key"] == "[REDACTED]"
    assert redacted_struct["nested"]["token_count"] == 42
    assert redacted_struct["nested"]["label"] == "normal text"
