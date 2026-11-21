# -*- coding: utf-8 -*-
"""Unit tests for Memory Browser Phase 1: analytics imprint, 6-axis diagnostics, and manual corrections."""
from __future__ import annotations

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
        re_eng = ReflectionEngine(event_log=ev_log)
        re_eng._config_manager = cm
        fs = FactStore()
        fs._config_manager = cm

        # 1. Protected entry cannot be edited
        with pytest.raises(PermissionError):
            await pm.aedit_persona_entry(char_name, "master", "card_1", "非法修改")

        # 2. Edit persona pushes version_history & clears embedding cache
        edited = await pm.aedit_persona_entry(char_name, "master", "prom_1", "主人喜欢喝拿铁")
        assert edited is not None
        assert edited["text"] == "主人喜欢喝拿铁"
        assert "embedding" not in edited
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

    _seed_timeindex(char_dir / "time_indexed.db", [ts])
    conn = sqlite3.connect(str(char_dir / "time_indexed.db"))
    try:
        conn.execute(
            "INSERT INTO time_indexed_original (timestamp, role, content) VALUES (?, ?, ?)",
            (ts, "human", "我最近早上都改喝燕麦拿铁啦，冰美式有点伤胃"),
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
        res = await arestore_snapshot(
            char_name,
            snap1["snapshot_id"],
            config_manager=cm,
            persona_manager=pm,
            fact_store=fs,
            max_keep=10,
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
                        "text": "邮箱 alice@example.com，手机 13812345678，密钥 sk-1234567890abcdef1234",
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
        assert "绝密原始聊天内容" not in combined
        assert "[REDACTED_EMAIL]" in combined
        assert "[REDACTED_PHONE]" in combined
        assert "[REDACTED_API_KEY]" in combined


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
    _, kwargs = create_mock.call_args
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] > 0
    assert kwargs["timeout"] > 0
