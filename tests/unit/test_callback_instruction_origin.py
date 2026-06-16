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
- Missing origin silently falls back to "event" (pre-migration compat).
- Explicitly unknown origin values fall back to "event" + warn (signals
  a typo or a producer using an unsupported value, worth surfacing).

Also covers the voice-mode hot-swap renderer
(``_render_pending_extra_replies_by_origin``): the swap path bypasses
``_build_callback_instruction`` entirely and uses CONTEXT_SUMMARY_*
wrappers, so the origin routing must be re-asserted there.
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


def test_topic_hook_source_uses_neutral_descriptor():
    out = _build(
        [
            {
                "origin": "event",
                "status": "completed",
                "source_kind": "topic",
                "source_name": "deep_topic_hook",
                "summary": "聊聊最近想听的城市流行",
                "detail": "请只生成一句自然开场",
                "delivery_mode": "proactive",
            }
        ],
    )

    assert "话题提示" in out
    assert "deep_topic_hook" not in out
    assert "聊聊最近想听的城市流行" in out


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

    Missing-key path stays **silent** (no warning) because it's the
    legitimate pre-migration fallback. An explicit but unknown value, by
    contrast, does warn (see test_unknown_origin_value_warns_and_falls_back).

    Pins both halves of that contract: the fallback behavior AND the
    no-warning invariant. Without the second assertion a future drift that
    started warning on missing-key (turning every legacy callsite noisy)
    would silently slip in.
    """
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
    finally:
        target_logger.removeHandler(handler)
        target_logger.setLevel(prior_level)

    assert "事件 A" in out
    # Should render the EVENT wrapper, not TASK.
    assert "新消息" in out
    assert "任务" not in out
    # Critically: NO "unknown origin" warning (pin the silent contract).
    assert not any("unknown origin" in r.getMessage() for r in records), [
        r.getMessage() for r in records
    ]


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


# ---------------------------------------------------------------------------
# Voice-mode hot-swap renderer
# ---------------------------------------------------------------------------
#
# In voice mode, callbacks are not drained through ``_build_callback_instruction``
# — they sit in ``pending_extra_replies`` until the next session hot-swap, then
# get rendered into ``prime_context`` via ``_render_pending_extra_replies_by_origin``.
# This is a SECOND rendering path that has to honor the same origin distinction;
# otherwise event-stream pushes get the "汇报先前执行的任务的结果" framing.


def _render_swap(entries):
    from main_logic.core import _render_pending_extra_replies_by_origin

    return _render_pending_extra_replies_by_origin(
        entries,
        lang="zh",
        lanlan_name="兰兰",
        master_name="主人",
    )


def _voice_entry(origin, *, summary="", detail="", status="completed",
                 source_kind="plugin", source_name="", error_message=""):
    """Helper: build a voice swap entry matching the shape enqueue_agent_callback
    produces. Lets each test focus on the dimensions it cares about."""
    return {
        "origin": origin,
        "summary": summary,
        "detail": detail,
        "status": status,
        "source_kind": source_kind,
        "source_name": source_name,
        "error_message": error_message,
    }


def test_voice_swap_event_entries_use_event_wrapper():
    """The bilidanmu voice-mode fix anchor. push_message events queued via
    ``enqueue_agent_callback`` end up in ``pending_extra_replies`` with
    ``origin="event"``. The hot-swap renderer must wrap them with
    CONTEXT_SUMMARY_EVENT_HEADER/FOOTER — NOT the task wrapper.
    """
    out = _render_swap(
        [
            _voice_entry("event", summary="💬 [大佬]观众A: 好可爱", source_name="bilibili_danmaku"),
            _voice_entry("event", summary="🎁 观众B 送了 1个 小心心", source_name="bilibili_danmaku"),
        ]
    )
    # Event wrapper: "请...根据下方新消息回应..."
    assert "新消息" in out
    assert "回应" in out
    # Critical: NO "汇报先前执行的任务" framing.
    assert "汇报" not in out
    assert "先前执行的任务" not in out
    # Content carried.
    assert "好可爱" in out
    assert "小心心" in out


def test_voice_swap_task_entries_use_task_wrapper():
    """Real task completions (e.g. finish(delivery="proactive") from
    pomodoro / sts2) keep the "汇报" framing — this is the pre-existing
    behavior the refactor must preserve."""
    out = _render_swap(
        [
            _voice_entry("task_result", summary="番茄钟到点了", source_name="pomodoro"),
        ]
    )
    assert "汇报" in out
    assert "番茄钟到点了" in out
    # Should not pick up the event wrapper.
    assert "新消息" not in out


def test_voice_swap_mixed_origins_render_separate_blocks():
    """Task and event entries in the same drain produce TWO blocks, each
    with its own wrapper — they do not collapse into one.
    """
    out = _render_swap(
        [
            _voice_entry("task_result", summary="搜索完成", source_name="search"),
            _voice_entry("event", summary="弹幕来了", source_name="bilibili_danmaku"),
            _voice_entry("task_result", summary="音乐播放完毕", source_name="music"),
        ]
    )
    # Both wrappers present.
    assert "汇报" in out  # TASK_HEADER
    assert "回应" in out  # EVENT_HEADER
    # All content carried.
    assert "搜索完成" in out
    assert "弹幕来了" in out
    assert "音乐播放完毕" in out
    # Task block precedes event block (matches helper docstring).
    assert out.index("搜索完成") < out.index("弹幕来了")


def test_voice_swap_failure_callback_without_body_still_surfaces():
    """Header-only failure callback: summary/detail empty but status="failed"
    and error_message carries the diagnostic. The v1 voice fix flattened
    these to "" and dropped them entirely; v2 must synthesize a status+source
    placeholder so the failure doesn't disappear silently before the next
    hot-swap injects context into the new session.
    """
    out = _render_swap(
        [
            {
                "origin": "task_result",
                "summary": "",
                "detail": "",
                "status": "failed",
                "source_kind": "plugin",
                "source_name": "pomodoro",
                "error_message": "Connection refused",
            }
        ]
    )
    # Wrapper: TASK (this is a real task failure).
    assert "汇报" in out
    # Status emoji + source phrase + error message all in the placeholder line.
    assert "❌" in out
    assert "pomodoro" in out
    assert "执行失败" in out
    assert "Connection refused" in out


def test_voice_swap_whitespace_summary_does_not_shadow_detail():
    """``summary = "   "`` must NOT block the renderer from picking up
    ``detail``. The legacy ``summary or detail`` chain treated whitespace
    summary as truthy and dropped detail — fixed in this refactor by
    stripping each independently before falling through.
    """
    out = _render_swap(
        [
            _voice_entry(
                "event",
                summary="   ",
                detail="真正的内容在 detail 里",
                source_name="ambient",
            ),
        ]
    )
    assert "真正的内容在 detail 里" in out


def test_voice_swap_legacy_string_entries_treated_as_event():
    """Defensive: if any old code path enqueues a plain string (pre-
    migration shape), treat it as event — the safer fallback, since
    fabricating "我刚才执行了任务" for what may actually be a push event
    is exactly the bug this PR fixes.
    """
    out = _render_swap(["遗留字符串条目"])
    assert "新消息" in out
    assert "汇报" not in out
    assert "遗留字符串条目" in out


def test_voice_swap_missing_origin_falls_back_to_event():
    """Dict entries without ``origin`` key — same fail-safe as
    ``_build_callback_instruction``: render as event."""
    out = _render_swap([{"summary": "无 origin 条目", "source_kind": "plugin", "source_name": "x"}])
    assert "新消息" in out
    assert "汇报" not in out
    assert "无 origin 条目" in out


def test_voice_swap_empty_input_returns_empty_string():
    """Both genuinely empty input and entries that filter to nothing
    (completed status with no body, no error, no source) collapse to ""."""
    assert _render_swap([]) == ""
    assert _render_swap([_voice_entry("event")]) == ""  # all blank
    assert _render_swap([_voice_entry("event", summary="   ", detail="   ")]) == ""
