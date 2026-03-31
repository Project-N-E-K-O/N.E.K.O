# -*- coding: utf-8 -*-
"""
Mock test: full reflection promotion lifecycle for 小天 (tian).

Focus: what happens when reflections go through
  pending → confirmed → promoted (auto_promote_stale)
and land in persona via add_fact? Does contradiction detection block them?

Usage:
    python -m tests.test_persona_mock
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Character card data (from characters.json) ──────────────────────
CHARACTER_DATA = {
    "主人": {"档案名": "碳基生物", "性别": "男", "昵称": "人类"},
    "猫娘": {
        "小天": {
            "昵称": "天酱", "性别": "女", "年龄": "15",
            "性格原型": "远坂凛", "种族": "猫娘", "自称": "本喵",
            "核心特质": ["理智可靠", "表面傲娇", "内心其实温柔"],
            "行为特点": ["喜欢待在碳基生物身边", "外表装成熟，实则内心柔软", "有猫娘的好奇心，喜欢观察周围"],
            "厌恶": ["被忽视或冷落", "重复说之前说过的话", "突如其来的变故或混乱"],
            "一句话台词": "能对碳基生物做这样那样的事情的，只有本喵一只猫娘喵~",
            "live2d": "neko", "voice_id": "voice-tone-OdVwrbG3No",
            "_reserved": {"avatar": {"vrm": {"model_path": ""}}},
        }
    },
    "当前猫娘": "小天",
}

now = datetime.now()

MOCK_REFLECTIONS = [
    # ① pending, 5 days → auto-confirm
    {
        "id": "ref_A_pending_old",
        "text": "碳基生物经常在深夜和小天聊天，可能是个夜猫子",
        "entity": "master",
        "status": "pending",
        "source_fact_ids": ["f1", "f2"],
        "created_at": (now - timedelta(days=5)).isoformat(),
        "feedback": None,
    },
    # ② pending, 1 day → stay pending
    {
        "id": "ref_B_pending_fresh",
        "text": "碳基生物和小天聊天时偶尔会用日语词汇",
        "entity": "master",
        "status": "pending",
        "source_fact_ids": ["f3"],
        "created_at": (now - timedelta(days=1)).isoformat(),
        "feedback": None,
    },
    # ③ confirmed 5d → promote to master (was blocked before fix)
    {
        "id": "ref_C_confirmed_old",
        "text": "碳基生物似乎对编程很感兴趣，经常讨论技术话题",
        "entity": "master",
        "status": "confirmed",
        "source_fact_ids": ["f4", "f5"],
        "created_at": (now - timedelta(days=10)).isoformat(),
        "confirmed_at": (now - timedelta(days=5)).isoformat(),
        "feedback": "confirmed",
    },
    # ④ confirmed 5d → promote to relationship
    {
        "id": "ref_D_confirmed_relationship",
        "text": "两人的互动越来越自然，从最初的拘谨变得随意，信任感在增强",
        "entity": "relationship",
        "status": "confirmed",
        "source_fact_ids": ["f6", "f7"],
        "created_at": (now - timedelta(days=9)).isoformat(),
        "confirmed_at": (now - timedelta(days=5)).isoformat(),
        "feedback": "confirmed",
    },
    # ⑤ confirmed 4d → promote to neko
    {
        "id": "ref_E_confirmed_neko",
        "text": "小天发现自己越来越习惯碳基生物的作息节奏了",
        "entity": "neko",
        "status": "confirmed",
        "source_fact_ids": ["f8"],
        "created_at": (now - timedelta(days=8)).isoformat(),
        "confirmed_at": (now - timedelta(days=4)).isoformat(),
        "feedback": "confirmed",
    },
    # ⑥ confirmed 1d → stay confirmed
    {
        "id": "ref_F_confirmed_fresh",
        "text": "碳基生物最近提到自己喜欢喝咖啡",
        "entity": "master",
        "status": "confirmed",
        "source_fact_ids": ["f9"],
        "created_at": (now - timedelta(days=5)).isoformat(),
        "confirmed_at": (now - timedelta(days=1)).isoformat(),
        "feedback": "confirmed",
    },
    # ⑦ deliberately contradicts character_card "性别: 女"
    {
        "id": "ref_H_contradicts_card",
        "text": "小天其实性别是男孩子",
        "entity": "neko",
        "status": "confirmed",
        "source_fact_ids": ["f11"],
        "created_at": (now - timedelta(days=7)).isoformat(),
        "confirmed_at": (now - timedelta(days=4)).isoformat(),
        "feedback": "confirmed",
    },
    # ⑧ archived
    {
        "id": "ref_G_already_promoted",
        "text": "碳基生物是个程序员",
        "entity": "master",
        "status": "promoted",
        "source_fact_ids": ["f10"],
        "created_at": (now - timedelta(days=20)).isoformat(),
        "confirmed_at": (now - timedelta(days=17)).isoformat(),
        "promoted_at": (now - timedelta(days=14)).isoformat(),
    },
]


def build_mock_config_manager(tmpdir: str):
    mock = MagicMock()
    mock.memory_dir = tmpdir
    mock.get_character_data.return_value = (
        "碳基生物", "小天",
        CHARACTER_DATA["主人"],
        CHARACTER_DATA["猫娘"],
        {"human": "碳基生物", "system": "SYSTEM_MESSAGE"},
        {}, {}, {}, {},
    )
    return mock


def run_test():
    with tempfile.TemporaryDirectory(prefix="persona_test_") as tmpdir:
        mock_cm = build_mock_config_manager(tmpdir)

        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):

            from memory.persona import PersonaManager, _extract_keywords
            from memory.reflection import ReflectionEngine
            from memory.facts import FactStore
            from utils.file_utils import atomic_write_json

            pm = PersonaManager()
            pm._config_manager = mock_cm

            fs = FactStore()
            fs._config_manager = mock_cm

            re = ReflectionEngine(fs, pm)
            re._config_manager = mock_cm

            # ── Step 0: init persona ────────────────────────────────
            print("=" * 60)
            print("  STEP 0: ensure_persona → character card sync")
            print("=" * 60)
            persona = pm.ensure_persona("小天")
            for ek, sec in persona.items():
                facts = sec.get("facts", []) if isinstance(sec, dict) else []
                print(f"  [{ek}] {len(facts)} facts")

            # ── Step 1: write mock reflections ──────────────────────
            print("\n" + "=" * 60)
            print("  STEP 1: write 8 mock reflections")
            print("=" * 60)
            refl_path = re._reflections_path("小天")
            atomic_write_json(refl_path, MOCK_REFLECTIONS, indent=2, ensure_ascii=False)
            for r in MOCK_REFLECTIONS:
                print(f"  [{r['status']:10s}] {r['entity']:12s} | {r['id']:30s} | {r['text'][:40]}")

            # ── Step 2: auto_promote_stale ──────────────────────────
            print("\n" + "=" * 60)
            print("  STEP 2: auto_promote_stale()")
            print("=" * 60)
            transitions = re.auto_promote_stale("小天")
            print(f"\n  Total transitions: {transitions}")

            # ── Step 3: reflection status after ──────────────────────
            print("\n" + "=" * 60)
            print("  STEP 3: reflection status AFTER")
            print("=" * 60)
            after = re.load_reflections("小天", include_archived=True)
            for r in after:
                extra = ""
                if r.get("auto_confirmed"):
                    extra = " [AUTO-CONFIRMED]"
                if r["status"] == "promoted":
                    extra = f" [PROMOTED]"
                print(f"  [{r['status']:10s}] {r['entity']:12s} | {r['text'][:45]}{extra}")

            # ── Step 4: persona facts ────────────────────────────────
            print("\n" + "=" * 60)
            print("  STEP 4: persona facts AFTER promotion")
            print("=" * 60)
            persona_after = pm.get_persona("小天")
            for ek, sec in persona_after.items():
                if not isinstance(sec, dict):
                    continue
                facts = sec.get("facts", [])
                print(f"\n  [{ek}] — {len(facts)} facts:")
                for f in facts:
                    src = f.get("source", "?")
                    prot = " [CARD]" if f.get("source") == "character_card" else ""
                    promoted = " ★NEW" if f.get("source") == "reflection" else ""
                    print(f"    {src:15s} | {f.get('text','')[:55]}{prot}{promoted}")

            # ── Step 5: correction queue ─────────────────────────────
            print("\n" + "=" * 60)
            print("  STEP 5: correction queue (contradictions with non-card facts)")
            print("=" * 60)
            corrections = pm.load_pending_corrections("小天")
            if corrections:
                print(f"  {len(corrections)} items:")
                for c in corrections:
                    print(f"    [{c.get('entity')}] old: {c.get('old_text','')[:40]}")
                    print(f"    {'':12s} new: {c.get('new_text','')[:40]}")
            else:
                print("  (empty)")

            # ── Step 6: n-gram diagnosis for the key pairs ───────────
            print("\n" + "=" * 60)
            print("  STEP 6: n-gram diagnosis (with stop_names=['碳基生物','小天'])")
            print("=" * 60)
            stop_names = ["碳基生物", "小天"]
            stop_kw = set()
            for sn in stop_names:
                stop_kw |= _extract_keywords(sn)
            print(f"  stop keywords: {stop_kw}\n")

            diag_pairs = [
                ("档案名: 碳基生物", "碳基生物似乎对编程很感兴趣，经常讨论技术话题", "master"),
                ("性别: 女", "小天其实性别是男孩子", "neko"),
                ("核心特质: 理智可靠、表面傲娇、内心其实温柔", "小天发现自己越来越习惯碳基生物的作息节奏了", "neko"),
            ]
            for old, new, ent in diag_pairs:
                old_kw = _extract_keywords(old) - stop_kw
                new_kw = _extract_keywords(new) - stop_kw
                overlap = old_kw & new_kw
                ratio = len(overlap) / min(len(old_kw), len(new_kw)) if old_kw and new_kw else 0.0
                flag = "⚠ CONTRA" if ratio >= 0.4 else "✓ OK"
                print(f"  {flag} [{ent}]  ratio={ratio:.2f}")
                print(f"    old: {old[:50]}")
                print(f"    new: {new[:50]}")
                print(f"    old_kw(stripped): {old_kw}")
                print(f"    new_kw(stripped): {new_kw}")
                print(f"    overlap: {overlap}\n")

            # ── Step 7: final markdown ───────────────────────────────
            print("=" * 60)
            print("  STEP 7: final render_persona_markdown")
            print("=" * 60)
            still_pending = re.get_pending_reflections("小天")
            still_confirmed = re.get_confirmed_reflections("小天")
            md = pm.render_persona_markdown("小天",
                                            pending_reflections=still_pending,
                                            confirmed_reflections=still_confirmed)
            print(md if md else "(empty)")


if __name__ == "__main__":
    run_test()
