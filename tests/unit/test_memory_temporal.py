# -*- coding: utf-8 -*-
"""Unit tests for schema v2 temporal helpers + past-derivation render
+ weighted followup sampling.

Covered:
- ``memory.temporal``: normalize_event_when / compute_event_timestamps /
  is_past_for_render / time_since_label / weighted_sample_no_replace
- ``persona._compose_markdown_from_trimmed``: outdated block with
  六等号 delimiters, time labels, mixed active + past
- ``reflection._filter_followup_candidates``: weighted sampling by
  evidence_score
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ── cooldown_elapsed (dead-letter 时间自愈) ──────────────────────────


def test_cooldown_elapsed_true_after_window():
    from memory.temporal import cooldown_elapsed
    last = (datetime.now() - timedelta(hours=6)).isoformat()
    assert cooldown_elapsed(last, 5 * 3600) is True


def test_cooldown_elapsed_false_within_window():
    from memory.temporal import cooldown_elapsed
    last = (datetime.now() - timedelta(hours=1)).isoformat()
    assert cooldown_elapsed(last, 5 * 3600) is False


def test_cooldown_elapsed_missing_or_garbage_is_eligible():
    """无时间戳 / 无法解析 → True（给一次 probe，比永久冻死安全）。"""
    from memory.temporal import cooldown_elapsed
    assert cooldown_elapsed(None, 5 * 3600) is True
    assert cooldown_elapsed("", 5 * 3600) is True
    assert cooldown_elapsed("not-a-date", 5 * 3600) is True


def test_cooldown_elapsed_respects_now_arg():
    from memory.temporal import cooldown_elapsed
    base = datetime(2026, 5, 24, 12, 0, 0)
    last = (base - timedelta(hours=4)).isoformat()
    # 4h < 5h 窗口
    assert cooldown_elapsed(last, 5 * 3600, now=base) is False
    # 同一时间戳，6h 窗口外
    assert cooldown_elapsed(last, 3 * 3600, now=base) is True


def test_cooldown_elapsed_handles_aware_timestamp():
    """aware ISO（+00:00 / Z）不应让相减抛 TypeError（迁移/import 数据防御，
    CodeRabbit）。aware 与 naive now 经 to_naive_local 归一后正常比较。"""
    from datetime import timezone
    from memory.temporal import cooldown_elapsed
    last_aware_old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    assert cooldown_elapsed(last_aware_old, 5 * 3600) is True
    last_aware_recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert cooldown_elapsed(last_aware_recent, 5 * 3600) is False


# ── memory.temporal helpers ─────────────────────────────────────────


def test_normalize_event_when_accepts_valid_spec():
    from memory.temporal import normalize_event_when
    raw = {'start': {'offset': -3, 'unit': 'day'}, 'end': {'offset': 0, 'unit': 'day'}}
    out = normalize_event_when(raw)
    assert out == raw


def test_normalize_event_when_rejects_garbage():
    from memory.temporal import normalize_event_when
    assert normalize_event_when("nope") is None
    assert normalize_event_when({'start': 'bad'}) is None
    # 单边坏掉但另一边 OK 时仍返回（半结构容忍）
    out = normalize_event_when({
        'start': {'offset': 'x', 'unit': 'day'},
        'end': {'offset': 0, 'unit': 'day'},
    })
    assert out is not None
    assert out['start'] is None
    assert out['end'] == {'offset': 0, 'unit': 'day'}


def test_normalize_event_when_rejects_unknown_unit():
    from memory.temporal import normalize_event_when
    out = normalize_event_when({'start': {'offset': -1, 'unit': 'banana'}, 'end': None})
    assert out is None


def test_compute_event_timestamps_pattern_no_end():
    """pattern: fallback_start=True, fallback_end=False → end stays None."""
    from memory.temporal import compute_event_timestamps
    anchor = "2026-05-12T00:00:00"
    s, e = compute_event_timestamps(None, anchor, fallback_start=True, fallback_end=False)
    assert s == anchor
    assert e is None


def test_compute_event_timestamps_state_falls_back_to_added():
    """state / episode: both fall back to added_at so TTL anchor exists."""
    from memory.temporal import compute_event_timestamps
    anchor = "2026-05-12T00:00:00"
    s, e = compute_event_timestamps(None, anchor, fallback_start=True, fallback_end=True)
    assert s == anchor
    assert e == anchor


def test_compute_event_timestamps_applies_offset():
    from memory.temporal import compute_event_timestamps
    anchor = "2026-05-12T00:00:00"
    raw = {'start': {'offset': -3, 'unit': 'day'}, 'end': {'offset': -1, 'unit': 'day'}}
    s, e = compute_event_timestamps(raw, anchor, fallback_start=True, fallback_end=True)
    assert s.startswith("2026-05-09")  # -3 day
    assert e.startswith("2026-05-11")  # -1 day


# ── is_past_for_render ─────────────────────────────────────────────


def _entry(scope, **overrides):
    base = {'temporal_scope': scope, 'created_at': '2026-05-01T00:00:00'}
    base.update(overrides)
    return base


def test_pattern_never_past():
    from memory.temporal import is_past_for_render
    now = datetime(2027, 1, 1)  # 一年后
    assert is_past_for_render(_entry('pattern'), now) is False


def test_state_past_after_7_days():
    from memory.temporal import is_past_for_render
    now = datetime(2026, 5, 20)
    fresh = _entry('state', event_end_at='2026-05-17T00:00:00')  # 3 天前
    old = _entry('state', event_end_at='2026-05-10T00:00:00')    # 10 天前
    assert is_past_for_render(fresh, now) is False
    assert is_past_for_render(old, now) is True


def test_episode_past_after_3_days():
    from memory.temporal import is_past_for_render
    now = datetime(2026, 5, 20)
    fresh = _entry('episode', event_end_at='2026-05-18T00:00:00')  # 2 天前
    old = _entry('episode', event_end_at='2026-05-15T00:00:00')    # 5 天前
    assert is_past_for_render(fresh, now) is False
    assert is_past_for_render(old, now) is True


def test_stored_past_always_past():
    from memory.temporal import is_past_for_render
    # 即使 event_end_at 是今天，stored 'past' 也立刻进过时 block
    e = _entry('past', event_end_at='2026-05-20T00:00:00')
    assert is_past_for_render(e, datetime(2026, 5, 20)) is True


def test_legacy_scopes_never_past():
    """v1 legacy: current / ongoing / None 不淡出（等慢速重判循环修正）。"""
    from memory.temporal import is_past_for_render
    now = datetime(2026, 5, 20)
    for legacy in ('current', 'ongoing', None):
        old = _entry(legacy, event_end_at='2026-04-01T00:00:00')
        assert is_past_for_render(old, now) is False, f"{legacy!r} should not be past"


def test_past_anchor_prefers_event_end():
    """anchor 优先级 end > start > added > created（与 time_since 一致）。"""
    from memory.temporal import is_past_for_render
    now = datetime(2026, 5, 20)
    # episode TTL=3d；end 较远（10d 前）→ past；start/created 都是今天但应被忽略
    e = {
        'temporal_scope': 'episode',
        'event_end_at': '2026-05-10T00:00:00',
        'event_start_at': '2026-05-20T00:00:00',
        'created_at': '2026-05-20T00:00:00',
    }
    assert is_past_for_render(e, now) is True


def test_is_past_tz_aware_anchor_does_not_crash():
    """aware anchor（import/迁移的 +00:00）和 naive now 相减不能 TypeError
    把过时判定打断（CodeRabbit）。"""
    from memory.temporal import is_past_for_render
    now = datetime(2026, 5, 20)
    old = _entry('episode', event_end_at='2026-05-10T00:00:00+00:00')  # 10d 前 aware
    assert is_past_for_render(old, now) is True


# ── time_since_label：Q-α 0-6d 天 / 7-29d 周 / 30d+ 月 ────────────────


def test_time_since_zero_days():
    from memory.temporal import time_since_label
    now = datetime(2026, 5, 20)
    assert time_since_label('2026-05-20T00:00:00', now=now, lang='zh') == '当下'


@pytest.mark.parametrize("days,label", [
    (1, '1 天前'), (3, '3 天前'), (6, '6 天前'),
    (7, '1 周前'), (14, '2 周前'), (29, '4 周前'),
    (30, '1 月前'), (60, '2 月前'), (365, '12 月前'),
])
def test_time_since_buckets_zh(days, label):
    from memory.temporal import time_since_label
    now = datetime(2026, 5, 20)
    anchor = (now - timedelta(days=days)).isoformat()
    assert time_since_label(anchor, now=now, lang='zh') == label


def test_time_since_unknown_lang_falls_back_zh():
    """Unknown lang code shouldn't crash — falls back to zh table."""
    from memory.temporal import time_since_label
    now = datetime(2026, 5, 20)
    anchor = (now - timedelta(days=3)).isoformat()
    out = time_since_label(anchor, now=now, lang='xx')
    assert out == '3 天前'


def test_to_naive_local_converts_then_strips():
    """aware → 转本地再剥 tz（保留瞬时，不是直接 replace 丢墙钟）；naive /
    None 原样返回。"""
    from datetime import datetime, timezone, timedelta
    from memory.temporal import to_naive_local
    aware = datetime(2026, 5, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    out = to_naive_local(aware)
    assert out.tzinfo is None
    # 等价于先 astimezone 本地再剥 tz —— 即保留的是同一瞬时的本地墙钟
    assert out == aware.astimezone().replace(tzinfo=None)
    # naive / None 不动
    naive = datetime(2026, 5, 1, 8, 0, 0)
    assert to_naive_local(naive) == naive
    assert to_naive_local(None) is None


def test_parse_time_window_hour_granularity():
    """整点小时 token → 1 小时窗口 [HH:00, HH+1:00)；T 和空格分隔都认。"""
    from datetime import datetime
    from memory.temporal import parse_time_window
    for tok in ('2026-05-01T14', '2026-05-01 14'):
        assert parse_time_window(tok) == (
            datetime(2026, 5, 1, 14), datetime(2026, 5, 1, 15)), tok


def test_parse_time_window_iso_with_minutes_floors_to_hour():
    """带分秒的 ISO 向下取整到所在那一小时（精度到小时）。"""
    from datetime import datetime
    from memory.temporal import parse_time_window
    assert parse_time_window('2026-05-01T14:37:12') == (
        datetime(2026, 5, 1, 14), datetime(2026, 5, 1, 15))


def test_parse_time_window_hour_range_union():
    """小时区间取两端并集：当天 9 点到 19 点。"""
    from datetime import datetime
    from memory.temporal import parse_time_window
    assert parse_time_window('2026-05-01T09/2026-05-01T18') == (
        datetime(2026, 5, 1, 9), datetime(2026, 5, 1, 19))


def test_parse_time_window_date_still_whole_day():
    """纯日期不带时间仍是整日窗口（不被小时分支误抢）。"""
    from datetime import datetime
    from memory.temporal import parse_time_window
    assert parse_time_window('2026-05-01') == (
        datetime(2026, 5, 1), datetime(2026, 5, 2))


def test_parse_time_window_boundary_overflow_returns_none():
    """边界输入让窗口右界越过 datetime.max（+1 天 / 年月进位）时返回 None，
    不冒 OverflowError/ValueError 到上层（Codex）。"""
    from memory.temporal import parse_time_window
    for tok in ('9999-12-31', '9999-12', '9999', '9999-12-31T23:59:59'):
        assert parse_time_window(tok) is None, tok


def test_to_naive_local_boundary_overflow_falls_back_to_strip():
    """边界 aware 值 astimezone 加减 offset 会越过 datetime.min/max 抛
    OverflowError；to_naive_local 退回直接剥 tz（保墙钟）而非崩（Codex）。"""
    from datetime import datetime, timezone, timedelta
    from memory.temporal import to_naive_local
    # min 附近 + 正 offset → astimezone 转本地（机器 tz）可能下溢
    near_min = datetime(1, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=14)))
    out = to_naive_local(near_min)
    assert out is not None and out.tzinfo is None
    near_max = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=-14)))
    out2 = to_naive_local(near_max)
    assert out2 is not None and out2.tzinfo is None


def test_parse_time_window_tz_aware_token_returns_naive():
    """带 tz 的 ISO time token 不该崩，且返回 naive 区间。带分秒 → 精度到
    小时（1 小时窗口），tz 先转本地再 floor。"""
    from memory.temporal import parse_time_window
    win = parse_time_window('2026-05-01T23:30:00+00:00')
    assert win is not None
    start, end = win
    assert start.tzinfo is None and end.tzinfo is None
    assert (end - start).total_seconds() == 3600  # 精度到小时


def test_time_since_tz_aware_anchor_does_not_crash():
    """tz-aware anchor（import/迁移路径会写 +00:00 / Z）不能因为和 naive
    now 相减而 TypeError —— days_since 内部把 aware 转本地剥 tz（Codex）。"""
    from memory.temporal import days_since, time_since_label
    now = datetime(2026, 5, 20)
    # 3 天前的 UTC aware 时间戳；只要不抛异常且落进合理桶即可
    assert days_since('2026-05-17T00:00:00+00:00', now=now) is not None
    assert time_since_label('2026-05-17T00:00:00+00:00', now=now, lang='zh').endswith('天前')


# ── weighted sampling ──────────────────────────────────────────────


def test_weighted_sample_empty():
    from memory.temporal import weighted_sample_no_replace
    assert weighted_sample_no_replace([], [], 3) == []


def test_weighted_sample_all_zero_weights():
    """全 0 权重应返回 [] 而非 ZeroDivision。"""
    from memory.temporal import weighted_sample_no_replace
    assert weighted_sample_no_replace([1, 2, 3], [0, 0, 0], 2) == []


def test_weighted_sample_k_exceeds_n():
    from memory.temporal import weighted_sample_no_replace
    out = weighted_sample_no_replace([1, 2], [1, 1], 5, rng=random.Random(0))
    assert sorted(out) == [1, 2]


def test_weighted_sample_high_weight_dominates():
    """高权重条目应在多次采样中显著多次被选中。"""
    from memory.temporal import weighted_sample_no_replace
    items = list(range(10))
    # idx 1, 4, 7 权重 5；其余 1
    weights = [1, 5, 1, 1, 5, 1, 1, 5, 1, 1]
    counts = {i: 0 for i in items}
    for seed in range(200):
        picked = weighted_sample_no_replace(items, weights, 3, rng=random.Random(seed))
        for p in picked:
            counts[p] += 1
    # 高权重三个 (1, 4, 7) 累计计数应 > 普通三个 (2, 3, 5) 累计计数
    high = counts[1] + counts[4] + counts[7]
    low = counts[2] + counts[3] + counts[5]
    assert high > low, f"high={high} not > low={low}"


# ── render: outdated block layout ──────────────────────────────────


def _mock_cm(tmpdir: str):
    from unittest.mock import AsyncMock
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    return cm


def _persona_manager(tmpdir: str):
    from memory.event_log import EventLog
    from memory.persona import PersonaManager
    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm):
        evl = EventLog()
        evl._config_manager = cm
        pm = PersonaManager(event_log=evl)
        pm._config_manager = cm
    return pm, cm


def _refl(text, scope, event_end_at=None, status='confirmed'):
    return {
        'id': f'r-{text[:8]}',
        'text': text,
        'entity': 'master',
        'status': status,
        'temporal_scope': scope,
        'event_end_at': event_end_at,
        'created_at': event_end_at or '2026-05-01T00:00:00',
    }


def test_render_outdated_block_uses_six_equals(tmp_path):
    """过时 block 必须用六个等号包裹 below/above 对偶分隔符。

    显式 pin lang='zh' 验证 zh locale 行为；其他 locale 由
    test_render_past_block_localizes_to_active_language 覆盖。
    """
    pm, _ = _persona_manager(str(tmp_path))
    old_iso = (datetime.now() - timedelta(days=10)).isoformat()
    with patch('utils.language_utils.get_global_language', return_value='zh'):
        md = pm._compose_markdown_from_trimmed(
            name='小天',
            persona={'master': {'facts': []}, 'neko': {'facts': []}, 'relationship': {'facts': []}},
            name_mapping={'human': '主人'},
            protected_entries=[],
            trimmed_non_protected=[],
            non_protected_entity_index={},
            trimmed_pending_reflections=[],
            trimmed_confirmed_reflections=[
                _refl('当下持续模式', 'pattern'),
                _refl('过时状态', 'state', event_end_at=old_iso),
            ],
        )
    # active confirmed section stays
    assert '当下持续模式' in md
    assert '比较确定的印象' in md
    # past block uses six-equals below/above pair
    assert '======以下为较久前的记忆======' in md
    assert '======以上为较久前的记忆======' in md
    # past entry rendered with time label
    assert '过时状态' in md
    assert '[1 周前]' in md or '[10 天前]' in md  # 10d falls in 7-29d → "1 周前"


def test_render_omits_past_block_when_all_active(tmp_path):
    """没有任何过时条目时不应出现过时 block 分隔符。"""
    pm, _ = _persona_manager(str(tmp_path))
    md = pm._compose_markdown_from_trimmed(
        name='小天',
        persona={'master': {'facts': []}, 'neko': {'facts': []}, 'relationship': {'facts': []}},
        name_mapping={'human': '主人'},
        protected_entries=[],
        trimmed_non_protected=[],
        non_protected_entity_index={},
        trimmed_pending_reflections=[],
        trimmed_confirmed_reflections=[
            _refl('当下持续模式 A', 'pattern'),
            _refl('当下持续模式 B', 'pattern'),
        ],
    )
    assert '比较确定的印象' in md
    assert '以下为较久前的记忆' not in md
    assert '以上为较久前的记忆' not in md


def test_render_legacy_temporal_scope_not_past(tmp_path):
    """legacy 'current' / 'ongoing' / None 应当 fall back into active section,
    not the past block (保守不淡出，等慢速重判修正)。"""
    pm, _ = _persona_manager(str(tmp_path))
    old_iso = (datetime.now() - timedelta(days=60)).isoformat()  # 60 days old
    md = pm._compose_markdown_from_trimmed(
        name='小天',
        persona={'master': {'facts': []}, 'neko': {'facts': []}, 'relationship': {'facts': []}},
        name_mapping={'human': '主人'},
        protected_entries=[],
        trimmed_non_protected=[],
        non_protected_entity_index={},
        trimmed_pending_reflections=[],
        trimmed_confirmed_reflections=[
            _refl('legacy current 条目', 'current', event_end_at=old_iso),
            _refl('legacy ongoing 条目', 'ongoing', event_end_at=old_iso),
            _refl('legacy None 条目', None, event_end_at=old_iso),
        ],
    )
    assert '比较确定的印象' in md
    assert '以下为较久前的记忆' not in md  # legacy 不算 past
    assert 'legacy current 条目' in md
    assert 'legacy ongoing 条目' in md
    # None 分支必须显式 assert——否则误把 None 当 past 时此用例仍会通过
    # (CodeRabbit review on PR #1316 catch)
    assert 'legacy None 条目' in md


def test_render_past_block_localizes_to_active_language(tmp_path):
    """Past block 内容 + 时间标签必须跟随 get_global_language()。

    Codex review on PR #1316 P2 regression guard：之前硬编码 zh，非 zh
    locale 看到中文时间标签 + 中文 below/above 分隔符。
    """
    pm, _ = _persona_manager(str(tmp_path))
    old_iso = (datetime.now() - timedelta(days=10)).isoformat()
    with patch(
        'utils.language_utils.get_global_language', return_value='en',
    ), patch(
        'utils.language_utils.get_global_language', return_value='en',
    ):
        md = pm._compose_markdown_from_trimmed(
            name='Mio',
            persona={'master': {'facts': []}, 'neko': {'facts': []}, 'relationship': {'facts': []}},
            name_mapping={'human': 'Master'},
            protected_entries=[],
            trimmed_non_protected=[],
            non_protected_entity_index={},
            trimmed_pending_reflections=[],
            trimmed_confirmed_reflections=[
                _refl('an outdated state', 'state', event_end_at=old_iso),
            ],
        )
    # English below/above pair, not 中文
    assert '======Below is older memory======' in md
    assert '======Above is older memory======' in md
    assert '以下为较久前的记忆' not in md
    # Time label localized to en (10 天前 → "1w ago" via 7-29d 周 bucket)
    assert '[1w ago]' in md
    assert '天前' not in md and '周前' not in md


def test_render_past_block_no_temporal_scope_label(tmp_path):
    """过时 block 内不出现 temporal_scope 字面值（用户原话："不需要进任何 block"）。

    用户的要求是 render 时不要把 pattern/state/episode 这种类型标签也塞到
    每条前缀里（仅时间标签 + 全局过时提醒就够）。我们检查 prefix 形式：
    bullet 行只有 "- [时间标签] 内容"，不会出现 "- [state] xxx" 之类。
    """
    pm, _ = _persona_manager(str(tmp_path))
    old_iso = (datetime.now() - timedelta(days=10)).isoformat()
    with patch('utils.language_utils.get_global_language', return_value='zh'):
        md = pm._compose_markdown_from_trimmed(
            name='小天',
            persona={'master': {'facts': []}, 'neko': {'facts': []}, 'relationship': {'facts': []}},
            name_mapping={'human': '主人'},
            protected_entries=[],
            trimmed_non_protected=[],
            non_protected_entity_index={},
            trimmed_pending_reflections=[],
            trimmed_confirmed_reflections=[
                _refl('一条过时印象', 'state', event_end_at=old_iso),
            ],
        )
    start = md.find('======以下为较久前的记忆======')
    end = md.find('======以上为较久前的记忆======')
    # 显式断言分隔符存在——否则 find 返回 -1 → block 为空字符串 →
    # 正则循环零次执行 → 测试"空块假通过" (CodeRabbit nit on PR #1316)
    assert start >= 0, "past block opening delimiter missing"
    assert end > start, "past block closing delimiter missing or out of order"
    block = md[start:end]
    # bullet 前缀只允许 [时间标签]，不允许任何形式的 [pattern/state/episode/...]
    import re as _re
    prefixes = _re.findall(r'^- \[([^\]]*)\]', block, _re.MULTILINE)
    assert prefixes, "past block has no bulleted entries to validate"
    for prefix in prefixes:
        for word in ('state', 'episode', 'pattern', 'past', 'temporal_scope'):
            assert word not in prefix, (
                f"unexpected temporal label {word!r} in bullet prefix {prefix!r}"
            )


# ── weighted followup sampling integration ─────────────────────────


@pytest.mark.asyncio
async def test_followup_weighted_disabled_uses_list_order(tmp_path):
    """REFLECTION_FOLLOWUP_WEIGHTED=False 时回退旧行为（list 顺序）。"""
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine
    cm = _mock_cm(str(tmp_path))
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm), \
         patch("memory.reflection.manager.get_config_manager", return_value=cm):
        evl = EventLog(); evl._config_manager = cm
        fs = FactStore(); fs._config_manager = cm
        pm = PersonaManager(event_log=evl); pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=evl); re._config_manager = cm

    from datetime import datetime
    now_iso = datetime.now().isoformat()
    candidates = [
        {'id': f'r{i}', 'status': 'pending', 'text': f'topic {i}',
         'reinforcement': 0.5, 'disputation': 0.0,
         'rein_last_signal_at': now_iso, 'disp_last_signal_at': None,
         'next_eligible_at': now_iso, 'created_at': now_iso}
        for i in range(5)
    ]
    await re.asave_reflections('小天', candidates)
    with patch('config.REFLECTION_FOLLOWUP_WEIGHTED', False):
        # 两次调用应该返回完全相同的前 K 条（list 顺序）
        a = await re.aget_followup_topics('小天')
        b = await re.aget_followup_topics('小天')
    assert [r['id'] for r in a] == [r['id'] for r in b]
    assert len(a) == 3  # REFLECTION_SURFACE_TOP_K default


@pytest.mark.asyncio
async def test_arecheck_one_legacy_fact_no_self_deadlock(tmp_path):
    """Regression for Codex review on PR #1316 inline P1: `_apply_update`
    must not hold `_get_lock` while calling `save_facts` (threading.Lock is
    non-reentrant → would deadlock on same-thread re-acquire).

    We assert the call returns within a short timeout against a mocked LLM
    response — a deadlock would manifest as the test hanging until pytest
    kills the loop.
    """
    import asyncio as _asyncio
    import json
    from memory.facts import FactStore
    cm = _mock_cm(str(tmp_path))
    with patch("memory.facts.get_config_manager", return_value=cm):
        fs = FactStore()
        fs._config_manager = cm

    # seed one v1 fact (schema_version missing → defaults to 1)
    fs._facts.setdefault("小天", []).append({
        "id": "f-legacy",
        "text": "用户上周一去爬山了",
        "importance": 6,
        "entity": "master",
        "tags": [],
        "hash": "h-legacy",
        "created_at": "2026-05-01T00:00:00",
        "absorbed": False,
    })
    await fs.asave_facts("小天")

    resp = MagicMock()
    resp.content = json.dumps({
        "event_when": {"start": {"offset": -1, "unit": "week"}, "end": None},
    })

    async def _ainvoke(*_a, **_k):
        return resp

    async def _aclose():
        return None

    fake_llm = MagicMock()
    fake_llm.ainvoke = _ainvoke
    fake_llm.aclose = _aclose

    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        # If _apply_update held _get_lock around save_facts, this would
        # deadlock on the inner self.save_facts(name)'s `with self._get_lock`.
        result = await _asyncio.wait_for(
            fs.arecheck_one_legacy_fact("小天"),
            timeout=5.0,
        )
    assert result is True
    # Verify fields were actually persisted, not just "no deadlock"
    facts = await fs.aload_facts("小天")
    assert facts[0]['schema_version'] == 2
    assert facts[0]['event_when_raw'] == {
        "start": {"offset": -1, "unit": "week"},
        "end": None,
    }
    assert facts[0]['event_start_at'].startswith("2026-04-24")  # -1 week


@pytest.mark.asyncio
async def test_arecheck_one_legacy_fact_skips_malformed_head(tmp_path):
    """Regression for Codex review on PR #1316 P2: malformed head must
    not starve later valid v1 entries. Seed [malformed, valid] in that
    order — recheck must skip malformed and migrate valid.
    """
    import json
    from memory.facts import FactStore
    cm = _mock_cm(str(tmp_path))
    with patch("memory.facts.get_config_manager", return_value=cm):
        fs = FactStore()
        fs._config_manager = cm

    fs._facts.setdefault("小天", []).extend([
        # malformed: missing created_at + id present but empty string
        {"id": "", "text": "破损条目", "importance": 5, "entity": "master",
         "tags": [], "hash": "h-bad", "created_at": "", "absorbed": False},
        # valid v1 (FIFO order would normally pick this second)
        {"id": "f-good", "text": "用户喜欢咖啡", "importance": 6, "entity": "master",
         "tags": [], "hash": "h-good", "created_at": "2026-05-01T00:00:00",
         "absorbed": False},
    ])
    await fs.asave_facts("小天")

    resp = MagicMock()
    resp.content = json.dumps({"event_when": None})
    async def _ainvoke(*_a, **_k):
        return resp
    async def _aclose():
        return None
    fake_llm = MagicMock()
    fake_llm.ainvoke = _ainvoke
    fake_llm.aclose = _aclose

    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        result = await fs.arecheck_one_legacy_fact("小天")
    assert result is True
    facts = await fs.aload_facts("小天")
    # malformed entry left untouched (schema_version not set)
    bad = next(f for f in facts if f.get('hash') == 'h-bad')
    good = next(f for f in facts if f.get('hash') == 'h-good')
    assert (bad.get('schema_version') or 1) == 1
    assert good.get('schema_version') == 2


@pytest.mark.asyncio
async def test_arecheck_one_legacy_reflection_skips_malformed_head(tmp_path):
    """Same starvation guard for reflection.arecheck_one_legacy_reflection."""
    import json
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine
    cm = _mock_cm(str(tmp_path))
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm), \
         patch("memory.reflection.manager.get_config_manager", return_value=cm):
        evl = EventLog(); evl._config_manager = cm
        fs = FactStore(); fs._config_manager = cm
        pm = PersonaManager(event_log=evl); pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=evl); re._config_manager = cm

    bad = {
        "id": "", "text": "破损 reflection", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": "",  # malformed
        "feedback": None, "reinforcement": 1.0, "disputation": 0.0,
    }
    good = {
        "id": "r-good", "text": "用户喜欢咖啡 (v1)", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": "2026-05-01T00:00:00",
        "feedback": None, "reinforcement": 1.0, "disputation": 0.0,
    }
    await re.asave_reflections("小天", [bad, good])

    resp = MagicMock()
    resp.content = json.dumps({"temporal_scope": "pattern", "event_when": None})
    async def _ainvoke(*_a, **_k):
        return resp
    async def _aclose():
        return None
    fake_llm = MagicMock()
    fake_llm.ainvoke = _ainvoke
    fake_llm.aclose = _aclose

    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        result = await re.arecheck_one_legacy_reflection("小天")
    assert result is True
    reflections = await re.aload_reflections("小天", include_archived=True)
    g = next(r for r in reflections if r.get('id') == 'r-good')
    assert g.get('schema_version') == 2
    assert g.get('temporal_scope') == 'pattern'
    # 同时断言 malformed 条目保持原状（schema_version 未升 v2、未被改写
    # temporal_scope）—— 防止未来回归把"洗白未验证条目"的反模式引回来
    # (CodeRabbit nit on PR #1316)
    b = next(r for r in reflections if r.get('text') == '破损 reflection')
    assert (b.get('schema_version') or 1) == 1
    assert b.get('event_when_raw') is None


@pytest.mark.asyncio
async def test_arecheck_invalid_scope_bumps_attempts_and_eventually_skips(tmp_path):
    """Regression for Codex review on PR #1316 P2: persistently-invalid LLM
    scope must not block migration of other v1 reflections.

    Seed [bad_then_good (always-invalid LLM), other_good]. After MAX attempts
    on bad_then_good, candidates filter should exclude it and other_good
    becomes reachable. We patch MAX_ATTEMPTS=2 to keep the test fast.
    """
    import json
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine
    cm = _mock_cm(str(tmp_path))
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm), \
         patch("memory.reflection.manager.get_config_manager", return_value=cm):
        evl = EventLog(); evl._config_manager = cm
        fs = FactStore(); fs._config_manager = cm
        pm = PersonaManager(event_log=evl); pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=evl); re._config_manager = cm

    # bad_then_good will always get invalid scope; other_good gets valid scope
    bad = {
        "id": "r-bad", "text": "总是被误判的 reflection", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": "2026-04-01T00:00:00",  # 更老 → FIFO 优先
        "feedback": None, "reinforcement": 1.0, "disputation": 0.0,
    }
    other = {
        "id": "r-other", "text": "另一条 v1 reflection", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": "2026-05-01T00:00:00",
        "feedback": None, "reinforcement": 1.0, "disputation": 0.0,
    }
    await re.asave_reflections("小天", [bad, other])

    # 控制 LLM 响应：r-bad 永远返回非法 scope 'banana'；r-other 返回 valid 'pattern'
    call_count = {"n": 0}
    bad_resp = MagicMock()
    bad_resp.content = json.dumps({"temporal_scope": "banana", "event_when": None})
    good_resp = MagicMock()
    good_resp.content = json.dumps({"temporal_scope": "pattern", "event_when": None})

    async def _ainvoke(prompt, *_a, **_k):
        call_count["n"] += 1
        # prompt 中包含 reflection text，借此区分
        return bad_resp if "总是被误判" in prompt else good_resp

    async def _aclose():
        return None

    fake_llm = MagicMock()
    fake_llm.ainvoke = _ainvoke
    fake_llm.aclose = _aclose

    # MAX=2：bad 经过 2 次 invalid scope 后被排除，第 3 次调用应命中 other
    with patch("memory.reflection.create_chat_llm", return_value=fake_llm, create=True), \
         patch("utils.llm_client.create_chat_llm", return_value=fake_llm), \
         patch("config.MEMORY_RECHECK_MAX_ATTEMPTS", 2):
        r1 = await re.arecheck_one_legacy_reflection("小天")  # bad: invalid → bump 1, return False
        r2 = await re.arecheck_one_legacy_reflection("小天")  # bad: invalid → bump 2, return False (now excluded)
        r3 = await re.arecheck_one_legacy_reflection("小天")  # other: valid → migrate, return True

    assert r1 is False
    assert r2 is False
    assert r3 is True
    reflections = await re.aload_reflections("小天", include_archived=True)
    bad_after = next(r for r in reflections if r.get('id') == 'r-bad')
    other_after = next(r for r in reflections if r.get('id') == 'r-other')
    assert bad_after.get('recheck_attempts') == 2
    assert (bad_after.get('schema_version') or 1) == 1  # 不洗白
    assert other_after.get('schema_version') == 2
    assert other_after.get('temporal_scope') == 'pattern'


@pytest.mark.asyncio
async def test_followup_weighted_enabled_varies_picks(tmp_path):
    """REFLECTION_FOLLOWUP_WEIGHTED=True + 候选 > TOP_K → 多轮采样应出现
    不同组合（不再雷同）。"""
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine
    cm = _mock_cm(str(tmp_path))
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm), \
         patch("memory.reflection.manager.get_config_manager", return_value=cm):
        evl = EventLog(); evl._config_manager = cm
        fs = FactStore(); fs._config_manager = cm
        pm = PersonaManager(event_log=evl); pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=evl); re._config_manager = cm

    from datetime import datetime
    now_iso = datetime.now().isoformat()
    # 8 候选 + TOP_K=3 → 加权采样至少应在 30 次中产生 ≥ 2 种 picks
    candidates = [
        {'id': f'r{i}', 'status': 'pending', 'text': f'topic {i}',
         'reinforcement': 0.5, 'disputation': 0.0,
         'rein_last_signal_at': now_iso, 'disp_last_signal_at': None,
         'next_eligible_at': now_iso, 'created_at': now_iso}
        for i in range(8)
    ]
    await re.asave_reflections('小天', candidates)
    # 显式 patch REFLECTION_FOLLOWUP_WEIGHTED=True，不依赖全局默认——如果
    # 未来 config 默认值翻成 False，这个用例就会"沉默通过"成 list-order
    # 行为而不报错 (CodeRabbit nit on PR #1316)。
    with patch('config.REFLECTION_FOLLOWUP_WEIGHTED', True):
        seen = set()
        for _ in range(30):
            picks = await re.aget_followup_topics('小天')
            seen.add(tuple(sorted(r['id'] for r in picks)))
    assert len(seen) >= 2, f"weighted sampling produced only {len(seen)} unique combos"
