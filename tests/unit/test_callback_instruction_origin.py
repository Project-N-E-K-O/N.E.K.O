"""Unit tests for _build_callback_instruction's (origin × passive) routing.

The host derives ``origin`` from upstream ``event_type``:
- ``event_type == "task_result"`` (agent_server._emit_task_result):
  real task completion → ``origin="task_result"`` → TASK_* templates
  ("任务已完成，请汇报" semantics).
- ``event_type == "proactive_message"`` (proactive_bridge from
  plugin push_message): event stream → ``origin="event"`` → EVENT_*
  templates ("新消息，请回应" semantics; **no** "任务"/"汇报" wording).

Plugin authors cannot influence ``origin``; it is a structural fact of
which SDK method they called (``finish()`` vs ``push_message()``) and
which host path the event flowed through.

These tests pin both the active/passive split and the cross-axis
guarantees:
- TASK ACTIVE renders status_phrase + action_phrase ("已完成 / 汇报").
- EVENT ACTIVE renders neutral wording with no "任务"/"汇报".
- Missing origin falls back to "event" + emits a warning (fail-safe:
  better to omit the "汇报" framing than fabricate "I did a task").
"""
from __future__ import annotations

import logging


def _build(callbacks, *, passive: bool = False):
    from main_logic.core import _build_callback_instruction

    return _build_callback_instruction(
        callbacks,
        lang="zh",
        lanlan_name="兰兰",
        master_name="主人",
        passive=passive,
    )


# ---------------------------------------------------------------------------
# origin × passive matrix
# ---------------------------------------------------------------------------


def test_task_active_renders_task_report_wrapper():
    out = _build(
        [
            {
                "origin": "task_result",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "pomodoro",
                "summary": "番茄钟到点了",
                "detail": "番茄钟到点了",
                "delivery_mode": "proactive",
            }
        ],
    )
    # Task wrapper requires "任务" + "汇报" (and status_phrase "已完成").
    assert "任务" in out
    assert "已完成" in out
    assert "汇报" in out
    assert "番茄钟到点了" in out
    # Event wrapper marker MUST NOT appear.
    assert "新消息" not in out


def test_task_passive_renders_neutral_task_result_wrapper():
    out = _build(
        [
            {
                "origin": "task_result",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "search_plugin",
                "summary": "搜索完成",
                "detail": "找到 5 条结果",
                "delivery_mode": "passive",
            }
        ],
    )
    # Passive task wrapper says "任务结果" — neutral, no "汇报" verb.
    assert "任务结果" in out
    assert "汇报" not in out
    assert "找到 5 条结果" in out


def test_event_active_omits_task_and_report_wording():
    """The bilidanmu fix anchor: a plugin push_message → origin=event →
    EVENT_ACTIVE wrapper. Must NOT include "任务" or "汇报" — those
    framings caused 兰兰 to narrate "我刚才处理了一下弹幕" instead of
    actually reacting to the danmaku content.
    """
    out = _build(
        [
            {
                "origin": "event",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "bilibili_danmaku",
                "summary": "弹幕 3 条",
                "detail": "💬 [大佬]观众A: 好可爱",
                "delivery_mode": "proactive",
            }
        ],
    )
    # Event ACTIVE template — should prompt the AI to respond to content.
    assert "新消息" in out
    assert "回应" in out
    # Critical: no task / report framing.
    assert "任务" not in out
    assert "汇报" not in out
    assert "已完成" not in out
    # Content still gets carried in.
    assert "好可爱" in out


def test_event_passive_renders_minimal_neutral_wrapper():
    out = _build(
        [
            {
                "origin": "event",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "ambient_notifier",
                "summary": "环境提示",
                "detail": "外面下雨了",
                "delivery_mode": "passive",
            }
        ],
    )
    assert "消息" in out
    assert "任务" not in out
    assert "回应" not in out  # passive doesn't push a turn
    assert "外面下雨了" in out


# ---------------------------------------------------------------------------
# Fail-safe behavior for missing / unknown origin
# ---------------------------------------------------------------------------


def test_missing_origin_falls_back_to_event_silently():
    """If a callback arrives without origin (older callsite / test stub /
    pre-migration code), we default to the EVENT template — never the TASK
    one. Rationale: we'd rather have the AI react naturally than fabricate
    "我做完了一个任务" for an event that wasn't actually a task.

    Missing-key path stays silent (no warning) because it's the legitimate
    pre-migration fallback. An explicit but unknown value, by contrast,
    does warn (see test_unknown_origin_value_warns_and_falls_back).
    """
    out = _build(
        [
            {
                # no origin key
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "unknown_emitter",
                "summary": "事件 A",
                "detail": "事件 A",
                "delivery_mode": "proactive",
            }
        ],
    )
    assert "事件 A" in out
    # Should render the EVENT wrapper, not TASK.
    assert "新消息" in out
    assert "任务" not in out


def test_unknown_origin_value_warns_and_falls_back():
    """An explicit but unrecognized origin value should warn and fall
    back to 'event'. Distinct from the missing-key path: this signals a
    typo or a producer using an unsupported value.
    """
    # Trigger module import so the logger exists; then attach a handler
    # directly to the project-prefixed logger name (the project sets
    # ``propagate=False`` on its logger hierarchy in some paths, so
    # ``caplog`` cannot reliably observe these records — it depends on
    # whether ``_ensure_shared_parent_logger`` has run, which is
    # order-dependent across tests).
    from main_logic.core import _build_callback_instruction  # noqa: F401

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.setLevel(logging.WARNING)
    handler.emit = lambda r: records.append(r)
    target_logger = logging.getLogger("N.E.K.O.Main.main_logic.core")
    prior_level = target_logger.level
    target_logger.addHandler(handler)
    if prior_level > logging.WARNING or prior_level == logging.NOTSET:
        target_logger.setLevel(logging.WARNING)
    try:
        out = _build(
            [
                {
                    "origin": "nonsense_kind",
                    "status": "completed",
                    "source_kind": "plugin",
                    "source_name": "buggy_plugin",
                    "summary": "x",
                    "detail": "x",
                    "delivery_mode": "proactive",
                }
            ],
        )
    finally:
        target_logger.removeHandler(handler)
        target_logger.setLevel(prior_level)

    # Should fall back to EVENT wrapper.
    assert "新消息" in out
    assert "任务" not in out
    # And should warn about the unrecognized origin (carrying the bad value
    # so triage can find the producer).
    assert any(
        "unknown origin" in r.getMessage() and "nonsense_kind" in r.getMessage()
        for r in records
    ), [r.getMessage() for r in records]


# ---------------------------------------------------------------------------
# Grouping behavior is preserved across origins
# ---------------------------------------------------------------------------


def test_mixed_origins_render_separate_blocks():
    """Same source_name but different origin must NOT collapse into one
    group — the wrappers are semantically different.
    """
    out = _build(
        [
            {
                "origin": "task_result",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "demo",
                "summary": "完成搜索",
                "detail": "完成搜索",
                "delivery_mode": "proactive",
            },
            {
                "origin": "event",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "demo",
                "summary": "新事件",
                "detail": "新事件",
                "delivery_mode": "proactive",
            },
        ],
    )
    # Both wrappers should appear.
    assert "任务" in out and "汇报" in out  # TASK_ACTIVE
    assert "新消息" in out and "回应" in out  # EVENT_ACTIVE
    assert "完成搜索" in out
    assert "新事件" in out


def test_passive_drain_path_forces_passive_for_all_origins():
    """Calling with passive=True (the drain-on-next-user-turn path) must
    select the PASSIVE wrapper for every origin, regardless of each
    callback's own delivery_mode.
    """
    out = _build(
        [
            {
                "origin": "task_result",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "search",
                "summary": "结果 1",
                "detail": "结果 1",
                "delivery_mode": "proactive",  # would normally pick ACTIVE
            },
            {
                "origin": "event",
                "status": "completed",
                "source_kind": "plugin",
                "source_name": "danmaku",
                "summary": "弹幕",
                "detail": "弹幕",
                "delivery_mode": "proactive",
            },
        ],
        passive=True,
    )
    # No "请...汇报"/"请...回应" verbs in passive mode.
    assert "汇报" not in out
    assert "回应" not in out
    # But each origin still picks its own passive wrapper.
    assert "任务结果" in out
    assert "消息" in out
