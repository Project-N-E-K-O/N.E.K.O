"""Focus-hold regression tests: a group that once won focus stays the focus group
as long as its score is at/above the send-hold line (``_focus_send_threshold``,
default 2.0), even below the focus line (``_focus_threshold``, default 4.0).

Background: changing only the gate's step-5 threshold is not enough -- the focus
selection (``_top_candidate_state``) still required score >= 4.0, so after a reply
dropped the focus group to ~2.x it was treated as a non-focus group and blocked
before the send gate. These tests use the real ``QQAttentionService``.
"""

from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService


def _make_service(groups=("A", "B")):
    plugin = SimpleNamespace(
        group_permission_mgr=SimpleNamespace(
            list_groups=lambda: [{"group_id": g} for g in groups]
        ),
        _qq_settings={
            "enable_group_attention": True,
            "group_attention_focus_threshold": 4.0,
            "group_attention_focus_send_threshold": 2.0,
            "group_attention_min_threshold": 1.0,
            "group_attention_max_score": 10.0,
        },
        _emit_log=lambda *a, **k: None,
    )
    svc = QQAttentionService(plugin)
    svc._current_time = lambda: 1000  # 冻结时钟：last_decay_at 对齐，避免相位衰减
    return svc


def _set_score(svc, gid, score, *, focus=False):
    state = svc._load_state(gid)
    state.attention_score = score
    state.last_decay_at = 1000
    state.phase_started_at = 1000
    if focus:
        state.focus_acquired_at = 1000
        state.last_focus_at = 1000
    svc._write_state(state)
    return state


def test_focus_held_after_score_drops_below_focus_line():
    svc = _make_service()
    # A wins focus (5.0 >= focus line 4.0)
    _set_score(svc, "A", 5.0, focus=True)
    assert svc.get_focus_group() == "A"
    # After a reply the score drops to 2.5 (< 4.0 but >= send-hold line 2.0):
    # the group must remain the focus group (focus hold).
    _set_score(svc, "A", 2.5, focus=True)
    assert svc.get_focus_group() == "A"
    # A challenger reaching 4.5 (>= focus line) and higher steals focus.
    _set_score(svc, "B", 4.5, focus=True)
    assert svc.get_focus_group() == "B"


def test_held_focus_not_displaced_by_low_challenger():
    svc = _make_service()
    _set_score(svc, "A", 5.0, focus=True)
    _set_score(svc, "A", 3.0, focus=True)
    # Challenger B at 3.5 (< focus line 4.0) must NOT displace the held focus A.
    _set_score(svc, "B", 3.5)
    assert svc.get_focus_group() == "A"


def test_focus_lost_below_send_threshold_is_displaced():
    svc = _make_service()
    _set_score(svc, "A", 5.0, focus=True)
    # A drops to 1.5 (< send-hold line 2.0) -> loses its hold; B at 2.5
    # (< focus line 4.0 but higher) becomes the focus via the top-group fallback.
    _set_score(svc, "A", 1.5, focus=True)
    _set_score(svc, "B", 2.5)
    assert svc.get_focus_group() == "B"


def test_choose_focus_falls_back_to_top_group_when_no_hold():
    """With no held focus and no group at/above the focus line, ``_choose_focus_state``
    must return the highest-scoring group (not None) so the gate can still grant it
    focus instead of blocking it as non_focus."""
    svc = _make_service()
    _set_score(svc, "A", 1.5, focus=True)   # was focus but dropped below send line
    _set_score(svc, "B", 2.5)               # never acquired focus
    states = [svc._load_state(g) for g in ("A", "B")]
    chosen = svc._choose_focus_state(states, 1000)
    assert chosen is not None
    assert chosen.group_id == "B"


def test_held_focus_not_displaced_by_below_focus_line_group():
    # The core focus-hold regression: held focus A at 2.5 (< 4.0) must NOT be
    # displaced by B at 3.5 (< 4.0 but higher). Without the hold logic, B (the
    # top group) would become focus and A would be blocked as non-focus.
    svc = _make_service()
    _set_score(svc, "A", 5.0, focus=True)
    _set_score(svc, "A", 2.5, focus=True)
    _set_score(svc, "B", 3.5)
    assert svc.get_focus_group() == "A"
