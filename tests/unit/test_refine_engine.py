# -*- coding: utf-8 -*-
"""Phase A-3 — MemoryRefineEngine static methods.

Covers _cluster_hash / _all_stamped_fresh / _cluster_starvation_key /
_render_cluster — the pure-function backbone of the refine pipeline.
Full integration (cosine cluster + LLM + manager apply) sits in
test_persona_refine_apply.py and test_reflection_refine_apply.py."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _refl(rid, **kw):
    from memory.refine import REFINE_TYPE_KEY, REFINE_ENTITY_KEY
    d = {'id': rid, REFINE_TYPE_KEY: 'reflection', REFINE_ENTITY_KEY: 'master'}
    d.update(kw)
    return d


def _fact(fid, **kw):
    from memory.refine import REFINE_TYPE_KEY, REFINE_ENTITY_KEY
    d = {'id': fid, REFINE_TYPE_KEY: 'fact', REFINE_ENTITY_KEY: 'master'}
    d.update(kw)
    return d


def _persona(pid, **kw):
    from memory.refine import REFINE_TYPE_KEY, REFINE_ENTITY_KEY
    d = {'id': pid, REFINE_TYPE_KEY: 'persona', REFINE_ENTITY_KEY: 'master'}
    d.update(kw)
    return d


# ── _cluster_hash ────────────────────────────────────────────────────


def test_cluster_hash_is_order_independent():
    from memory.refine import MemoryRefineEngine
    a = MemoryRefineEngine._cluster_hash([_refl('r2'), _refl('r1'), _refl('r3')])
    b = MemoryRefineEngine._cluster_hash([_refl('r3'), _refl('r1'), _refl('r2')])
    assert a == b


def test_cluster_hash_excludes_fact_ids():
    """fact 加入/退出 cluster 不应改变 hash —— fact 是只读 info source。"""
    from memory.refine import MemoryRefineEngine
    base = [_refl('r1'), _refl('r2')]
    with_fact = base + [_fact('f1'), _fact('f2')]
    assert MemoryRefineEngine._cluster_hash(base) == MemoryRefineEngine._cluster_hash(with_fact)


def test_cluster_hash_changes_when_refl_member_changes():
    from memory.refine import MemoryRefineEngine
    a = MemoryRefineEngine._cluster_hash([_refl('r1'), _refl('r2')])
    b = MemoryRefineEngine._cluster_hash([_refl('r1'), _refl('r3')])
    assert a != b


# ── _all_stamped_fresh ───────────────────────────────────────────────


def test_all_stamped_fresh_true_when_full_match():
    from memory.refine import MemoryRefineEngine
    now = datetime.now().isoformat()
    h = 'abc12345'
    cluster = [
        _refl('r1', last_refine_cluster_hash=h, last_refine_at=now),
        _refl('r2', last_refine_cluster_hash=h, last_refine_at=now),
    ]
    assert MemoryRefineEngine._all_stamped_fresh(cluster, h) is True


def test_all_stamped_fresh_false_when_any_unstamped():
    from memory.refine import MemoryRefineEngine
    now = datetime.now().isoformat()
    h = 'abc12345'
    cluster = [
        _refl('r1', last_refine_cluster_hash=h, last_refine_at=now),
        _refl('r2'),  # never refined
    ]
    assert MemoryRefineEngine._all_stamped_fresh(cluster, h) is False


def test_all_stamped_fresh_false_when_hash_mismatch():
    from memory.refine import MemoryRefineEngine
    now = datetime.now().isoformat()
    cluster = [
        _refl('r1', last_refine_cluster_hash='old_hash', last_refine_at=now),
        _refl('r2', last_refine_cluster_hash='new_hash', last_refine_at=now),
    ]
    assert MemoryRefineEngine._all_stamped_fresh(cluster, 'new_hash') is False


def test_all_stamped_fresh_false_when_any_stale():
    from memory.refine import MemoryRefineEngine
    from config import MEMORY_REFINE_REVISIT_AFTER_DAYS
    h = 'h'
    fresh = datetime.now().isoformat()
    stale = (datetime.now() - timedelta(days=MEMORY_REFINE_REVISIT_AFTER_DAYS + 5)).isoformat()
    cluster = [
        _refl('r1', last_refine_cluster_hash=h, last_refine_at=fresh),
        _refl('r2', last_refine_cluster_hash=h, last_refine_at=stale),
    ]
    assert MemoryRefineEngine._all_stamped_fresh(cluster, h) is False


def test_all_stamped_fresh_skips_fact_members():
    """fact 没有 last_refine 字段，不应让 skip 判定 fail。"""
    from memory.refine import MemoryRefineEngine
    h = 'h'
    now = datetime.now().isoformat()
    cluster = [
        _refl('r1', last_refine_cluster_hash=h, last_refine_at=now),
        _fact('f1'),  # 没 stamp，但是 fact，应被跳过
    ]
    assert MemoryRefineEngine._all_stamped_fresh(cluster, h) is True


# ── _cluster_starvation_key ──────────────────────────────────────────


def test_starvation_key_returns_min_non_fact_timestamp():
    from memory.refine import MemoryRefineEngine
    cluster = [
        _refl('r1', last_refine_at='2026-04-01T00:00:00'),
        _refl('r2', last_refine_at='2026-01-15T00:00:00'),
        _fact('f1', last_refine_at='2010-01-01T00:00:00'),  # ignored
    ]
    assert MemoryRefineEngine._cluster_starvation_key(cluster) == '2026-01-15T00:00:00'


def test_starvation_key_empty_string_when_any_unstamped():
    """未 stamp 的成员视为最饿（''）→ 整个 cluster 排到队首。"""
    from memory.refine import MemoryRefineEngine
    cluster = [
        _refl('r1', last_refine_at='2026-04-01T00:00:00'),
        _refl('r2'),  # 无 last_refine_at → ''
    ]
    assert MemoryRefineEngine._cluster_starvation_key(cluster) == ''


def test_starvation_key_empty_when_all_facts():
    """全 fact cluster（理论不会发生）→ ''。"""
    from memory.refine import MemoryRefineEngine
    cluster = [_fact('f1'), _fact('f2')]
    assert MemoryRefineEngine._cluster_starvation_key(cluster) == ''


# ── _render_cluster ──────────────────────────────────────────────────


def test_render_cluster_persona_format():
    from memory.refine import MemoryRefineEngine
    out = MemoryRefineEngine._render_cluster([
        _persona('p1', text='主人住在东京'),
    ])
    assert '[0] (persona id=p1) 主人住在东京' in out


def test_render_cluster_reflection_includes_ontology():
    from memory.refine import MemoryRefineEngine
    out = MemoryRefineEngine._render_cluster([
        _refl('r1', text='主人喜欢咖啡', relation_type='preference', temporal_scope='pattern'),
    ])
    assert 'reflection id=r1' in out
    assert 'relation_type=preference' in out
    assert 'temporal_scope=pattern' in out


def test_render_cluster_fact_includes_importance():
    from memory.refine import MemoryRefineEngine
    out = MemoryRefineEngine._render_cluster([
        _fact('f1', text='主人今早喝了三杯咖啡', importance=7),
    ])
    assert 'fact id=f1' in out
    assert 'importance=7' in out


def test_render_cluster_skips_entries_missing_text_or_id():
    from memory.refine import MemoryRefineEngine
    out = MemoryRefineEngine._render_cluster([
        _refl('r1', text='valid'),
        _refl('r2', text=''),       # 无 text，跳
        _refl('', text='no id'),    # 无 id，跳
    ])
    assert out.count('\n') == 0  # 只有 r1 一行
    assert 'valid' in out
    assert 'no id' not in out


# ── refine_pass embedding-disabled fallback ──────────────────────────


@pytest.mark.asyncio
async def test_refine_pass_noop_when_embedding_disabled():
    """embedding 服务关闭 → 整 pass no-op，零 LLM 调用。"""
    from unittest.mock import MagicMock, patch
    from memory.refine import MemoryRefineEngine

    cm = MagicMock()
    with patch('memory.refine.get_embedding_service') as mock_svc:
        svc = MagicMock()
        svc.is_disabled = MagicMock(return_value=True)
        mock_svc.return_value = svc
        engine = MemoryRefineEngine(cm)

        async def _never_call(*args, **kwargs):
            raise AssertionError("apply_fn should not be called when embedding disabled")

        result = await engine.refine_pass(
            candidates_by_entity={'master': [_refl('r1'), _refl('r2')]},
            apply_fn=_never_call,
            scope_label='test',
        )
    assert result == {
        'clusters_seen': 0,
        'clusters_skipped': 0,
        'clusters_resolved': 0,
        'clusters_failed': 0,
    }
