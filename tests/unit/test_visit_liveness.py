# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests of the pure visit liveness timers (``main_logic/visit/liveness.py``).

Follows the ``test_visit_liveness.py`` list of PR-06 in the main design
document; everything runs on a virtual clock.
"""
from __future__ import annotations

from main_logic.visit.liveness import READY_WAIT_S, VisitLiveness


def verified(side: str = "host", t: float = 0.0) -> VisitLiveness:
    lv = VisitLiveness(side, t)  # type: ignore[arg-type]
    lv.on_peer_verified(t)
    return lv


def feed(lv: VisitLiveness, start: float, end: float, step: float = 5.0) -> None:
    """Peer heartbeats every ``step`` seconds in ``[start, end]``."""
    t = start
    while t <= end:
        lv.on_peer_message(t)
        t += step


# ── waiting state ──────────────────────────────────────────────────────

def test_host_waits_for_the_invite_not_the_heartbeat_clock():
    lv = VisitLiveness("host", 0.0)
    assert lv.tick(31.0) is None
    assert lv.tick(599.0) is None
    assert lv.tick(600.0) == "invite_expired"


def test_peer_entering_late_extends_the_host_deadline():
    lv = VisitLiveness("host", 0.0)
    lv.on_peer_entered(590.0)
    assert lv.wait_deadline == 650.0
    assert lv.tick(645.0) is None
    lv.on_peer_verified(645.0)
    assert lv.tick(649.0) is None
    assert lv.tick(660.0) is None


def test_peer_entering_early_does_not_shorten_the_host_deadline():
    lv = VisitLiveness("host", 0.0)
    lv.on_peer_entered(10.0)
    assert lv.wait_deadline == 600.0


def test_guest_waits_thirty_seconds_for_the_host_hello():
    lv = VisitLiveness("guest", 0.0)
    # peer messages before verification do not move the wait deadline
    lv.on_peer_message(20.0)
    assert lv.tick(29.0) is None
    assert lv.tick(31.0) == "peer_lost"


def test_guest_ready_wait_counts_from_hello_acked():
    lv = VisitLiveness("guest", 0.0)
    lv.on_peer_verified(1.0)
    lv.on_hello_acked(2.0)
    assert READY_WAIT_S == 85
    feed(lv, 1.0, 100.0)
    assert lv.tick(2.0 + 84.0) is None
    assert lv.tick(2.0 + 86.0) == "declined"


def test_guest_ready_retransmitted_at_76s_still_activates():
    lv = VisitLiveness("guest", 0.0)
    lv.on_peer_verified(0.0)
    lv.on_hello_acked(0.0)
    feed(lv, 0.0, 120.0)
    # host accepts at 59.9 s, init uses 15 s, the first ready is lost, retransmit at 76 s
    assert lv.tick(75.5) is None
    lv.on_ready(76.0)
    for t in (76.0, 86.0, 100.0, 120.0):
        assert lv.tick(t) is None


# ── heartbeat clock ────────────────────────────────────────────────────

def test_after_verification_29s_alive_31s_dead():
    lv = verified("host", 100.0)
    assert lv.tick(129.0) is None
    assert lv.tick(131.0) == "peer_lost"


def test_any_peer_message_refreshes_last_seen():
    lv = verified("guest", 0.0)
    lv.on_peer_message(20.0)  # e.g. a lossy stats message
    assert lv.tick(49.0) is None
    assert lv.tick(51.0) == "peer_lost"


def test_verdict_is_sticky():
    lv = verified("host", 0.0)
    assert lv.tick(31.0) == "peer_lost"
    lv.on_peer_message(32.0)
    assert lv.tick(33.0) == "peer_lost"


# ── own connection and page ────────────────────────────────────────────

def test_self_reconnect_24s_continues_26s_relay_lost():
    lv = verified("host", 0.0)
    feed(lv, 0.0, 100.0)
    lv.on_message_sent(50.0)
    lv.on_self_disconnected(50.0)
    assert lv.tick(74.0) is None
    lv.on_self_connected(74.0)
    assert lv.tick(80.0) is None
    lv.on_message_sent(85.0)
    lv.on_self_disconnected(85.0)
    assert lv.tick(111.0) == "relay_lost"


def test_self_deadline_is_bounded_by_the_last_successful_send():
    lv = verified("host", 0.0)
    feed(lv, 0.0, 100.0)
    lv.on_message_sent(40.0)
    lv.on_self_disconnected(50.0)
    assert lv.self_deadline() == 67.0
    assert lv.tick(66.0) is None
    lv2 = verified("host", 0.0)
    feed(lv2, 0.0, 100.0)
    lv2.on_message_sent(40.0)
    lv2.on_self_disconnected(50.0)
    assert lv2.tick(68.0) == "relay_lost"


def test_page_19s_back_21s_local_page_lost():
    lv = verified("guest", 0.0)
    feed(lv, 0.0, 200.0)
    lv.on_page_lost(10.0)
    assert lv.tick(29.0) is None
    lv.on_page_back(29.0)
    assert lv.tick(40.0) is None
    lv.on_page_lost(50.0)
    assert lv.tick(71.0) == "local_page_lost"


# ── peer leaving ───────────────────────────────────────────────────────

def test_authenticated_leave_without_gap_is_immediate():
    lv = verified("host", 0.0)
    assert lv.on_peer_leave_message(5.0, last_seq=9, contiguous_seq=9) == "peer_left"
    assert lv.tick(5.0) == "peer_left"


def test_leave_with_gap_waits_for_the_fill():
    t0 = 10.0
    lv = verified("host", 0.0)
    feed(lv, 0.0, 30.0)
    assert lv.on_peer_leave_message(t0, last_seq=9, contiguous_seq=8) is None
    assert lv.tick(t0 + 4) is None
    assert lv.on_gap_filled(t0 + 2) == "peer_left"
    assert lv.tick(t0 + 2) == "peer_left"


def test_leave_with_gap_expires_after_five_seconds():
    t0 = 10.0
    lv = verified("host", 0.0)
    feed(lv, 0.0, 30.0)
    lv.on_peer_leave_message(t0, last_seq=9, contiguous_seq=8)
    assert lv.tick(t0 + 4.9) is None
    assert lv.tick(t0 + 5) == "peer_left"


def test_vendor_leave_is_tentative_for_35s():
    t0 = 100.0
    lv = verified("guest", 0.0)
    feed(lv, 0.0, t0)
    lv.on_peer_vendor_left(t0)
    assert lv.tick(t0 + 34) is None
    assert lv.tick(t0 + 36) == "peer_left"


def test_backend_crash_peer_ends_within_rejoin_grace_not_later():
    # our iframe leaves the vendor room the moment its WS drops (~T); the peer
    # must end at T + 35, well before T + 55
    T = 200.0
    lv = verified("host", 0.0)
    feed(lv, 0.0, T)
    lv.on_peer_vendor_left(T)
    verdicts = {t: lv.tick(T + t) for t in (30.0, 34.9, 35.0)}
    assert verdicts[30.0] is None and verdicts[34.9] is None
    assert verdicts[35.0] == "peer_left"


def test_vendor_rejoin_clears_the_grace():
    t0 = 100.0
    lv = verified("host", 0.0)
    feed(lv, 0.0, t0)
    lv.on_peer_vendor_left(t0)
    lv.on_peer_vendor_rejoined(t0 + 8)
    feed(lv, t0 + 9, t0 + 80)
    for t in (t0 + 35, t0 + 50, t0 + 80):
        assert lv.tick(t) is None


def test_grace_is_not_preempted_by_the_heartbeat_clock():
    t0 = 100.0
    lv = verified("host", 0.0)
    feed(lv, 0.0, t0 - 25)
    lv.on_peer_vendor_left(t0)
    assert lv.tick(t0 + 5) is None
    assert lv.tick(t0 + 30) is None
    lv.on_peer_vendor_rejoined(t0 + 32)
    assert lv.tick(t0 + 60) is None
    assert lv.tick(t0 + 63) == "peer_lost"


def test_vendor_timeout_event_does_not_change_the_death_time():
    lv = verified("host", 0.0)
    feed(lv, 0.0, 20.0)
    lv.on_peer_vendor_timeout(21.0)
    assert lv.tick(22.0) is None
    assert lv.tick(50.0) is None
    assert lv.tick(51.0) == "peer_lost"
    assert lv.vendor_timeout_events == 1


# ── heartbeat cadence ──────────────────────────────────────────────────

def test_heartbeat_exactly_once_per_five_seconds():
    lv = VisitLiveness("host", 0.0)
    due = [t / 10 for t in range(0, 301) if lv.heartbeat_due(t / 10)]
    assert due == [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]


def test_heartbeat_resumes_without_burst_after_a_pause():
    lv = VisitLiveness("host", 0.0)
    assert lv.heartbeat_due(5.0)
    assert lv.heartbeat_due(60.0)
    assert not lv.heartbeat_due(60.5)
    assert not lv.heartbeat_due(64.9)
    assert lv.heartbeat_due(65.0)


def test_verification_clears_a_departure_left_over_from_the_wait():
    # 等待期对端进房又走（刷新后换了 vendor 身份重进，不会调 rejoined），之后 hello 才核验通过
    lv = VisitLiveness("host", 0.0)
    lv.on_peer_entered(100.0)
    lv.on_peer_vendor_left(110.0)
    lv.on_peer_verified(150.0)          # > 110 + 35
    feed(lv, 150.0, 170.0)
    assert lv.tick(171.0) is None
    assert lv.tick(199.0) is None
    assert lv.tick(201.0) == "peer_lost"


def test_late_hello_ack_after_ready_does_not_rearm_the_wait():
    # 首个 hello 的 ack 丢了、ready 先到；之后重传 hello 触发的累计 ack 不能再开 85 s 期限
    lv = VisitLiveness("guest", 0.0)
    lv.on_peer_verified(1.0)
    lv.on_ready(5.0)
    lv.on_hello_acked(6.0)
    assert lv.ready_deadline is None
    feed(lv, 6.0, 200.0)
    assert lv.tick(200.0) is None


def test_rejoin_after_a_timeout_disconnect_restarts_the_heartbeat_clock():
    # 超时类断开（没有暂定离开）后对端重进：心跳时钟从重进起算，不在下一次 tick 立刻判死
    lv = verified("host", 0.0)
    feed(lv, 0.0, 20.0)
    lv.on_peer_vendor_timeout(21.0)
    lv.on_peer_vendor_rejoined(49.0)
    assert lv.tick(51.0) is None
    assert lv.tick(79.0) is None
    assert lv.tick(80.0) == "peer_lost"


# ── page reload deadline (design §4.8) ─────────────────────────────────


def test_page_reload_absolute_deadline_takes_the_earlier_term():
    lv = verified("guest", 0.0)
    feed(lv, 0.0, 200.0)  # 对端一直在线：只看本侧页面期限
    lv.on_message_sent(100.0)
    lv.on_page_lost(110.0)
    # 离开 + 35 − 5 = 140；最后发出 + 30 − 3 = 127：取较早的 127，WS 阶段 min(130, 127)
    assert lv.page_reload_deadline() == 127.0
    assert lv.page_deadline == 127.0
    lv.on_page_socket_back(115.0)
    assert lv.page_deadline == 127.0
    assert lv.tick(126.9) is None
    assert lv.tick(127.0) == "local_page_lost"


def test_page_second_drop_counts_its_own_socket_budget():
    lv = verified("guest", 0.0)
    lv.on_page_lost(0.0)
    lv.on_page_socket_back(5.0)
    assert lv.page_deadline == 30.0
    lv.on_page_lost(8.0)
    # 本次断线 + 20 = 28，没超过绝对期限 30
    assert lv.page_deadline == 28.0
    assert lv.page_departed_at == 0.0


def test_page_lost_is_idempotent_within_the_socket_stage():
    lv = verified("guest", 0.0)
    lv.on_page_lost(0.0)
    lv.on_page_lost(15.0)  # 没有连回就再报一次：不能把 20 推到 35
    assert lv.page_deadline == 20.0
    assert lv.tick(20.0) == "local_page_lost"


def test_late_socket_and_late_rejoin_cannot_revive_the_page():
    lv = verified("guest", 0.0)
    lv.on_page_lost(0.0)
    assert lv.page_expired(20.0) and not lv.page_expired(19.9)
    lv.on_page_socket_back(20.2)
    assert lv.page_deadline == 20.0
    lv.on_page_back(20.3)
    assert lv.page_deadline == 20.0
    assert lv.tick(20.5) == "local_page_lost"


def test_page_back_clears_the_reload():
    lv = verified("guest", 0.0)
    lv.on_page_lost(0.0)
    lv.on_page_socket_back(3.0)
    lv.on_page_back(10.0)
    assert (lv.page_departed_at, lv.page_deadline, lv.page_socket_back) == (None, None, False)
    assert lv.page_reload_deadline() is None and not lv.page_expired(100.0)


def test_last_send_term_waits_for_the_peer_hello_exchange():
    # host 发过一条消息后仍在等对端：对端的心跳时钟还没开始算我们，旧的发出时刻不能让期限一设下就过期
    lv = VisitLiveness("host", 0.0)
    lv.on_message_sent(0.0)
    lv.on_page_lost(60.0)
    assert lv.page_deadline == 80.0
    assert lv.tick(60.0) is None
    lv.on_self_disconnected(70.0)
    assert lv.self_deadline() == 95.0
    # 核验通过之后才套上这一项，且从核验时刻算起：不会一核验就落在过去
    lv.on_peer_verified(75.0)
    assert lv.self_deadline() == 95.0
    assert lv.tick(75.0) is None
    lv2 = VisitLiveness("host", 0.0)
    lv2.on_message_sent(0.0)
    lv2.on_self_disconnected(70.0)
    lv2.on_peer_verified(90.0)
    assert lv2.self_deadline() == 95.0  # min(70 + 25, 90 + 27)


def test_page_rejoin_safety_margin_is_injectable():
    lv = VisitLiveness("guest", 0.0, rejoin_grace_s=12.0, page_grace_s=5.0, page_rejoin_safety_s=2.0)
    lv.on_peer_verified(0.0)
    lv.on_page_lost(0.0)
    lv.on_page_socket_back(1.0)
    assert lv.page_deadline == 10.0



def test_waiting_guest_keeps_the_last_send_term():
    # guest 等 host hello 时，host 可能已核验了 guest、正在对它计 30 s：这一项照算
    lv = VisitLiveness("guest", 0.0)
    lv.on_message_sent(10.0)
    lv.on_page_lost(12.0)
    assert lv.page_reload_deadline() == 37.0  # min(12 + 30, 10 + 27)


def test_first_verification_tightens_a_reload_started_while_waiting():
    lv = VisitLiveness("host", 0.0)
    lv.on_page_lost(100.0)
    lv.on_page_socket_back(105.0)
    assert lv.page_deadline == 130.0  # 等待期：只有离开 + 30
    lv.on_message_sent(98.0)
    lv.on_peer_verified(101.0)
    # 核验后最后发出那一项生效（98 夹到核验时刻 101）：min(130, 101 + 27)
    assert lv.page_deadline == 128.0


def test_re_verification_does_not_move_the_last_send():
    lv = verified("host", 0.0)
    lv.on_message_sent(10.0)
    lv.on_peer_verified(50.0)  # 对端重连后同 jti 复验：对端的时钟仍从我们最后一次发出算
    assert lv.last_sent_at == 10.0


def test_injected_durations_must_keep_the_invariants():
    import pytest

    with pytest.raises(ValueError, match="positive"):
        VisitLiveness("guest", 0.0, page_rejoin_safety_s=0.0)
    with pytest.raises(ValueError, match="outlast"):
        VisitLiveness("guest", 0.0, rejoin_grace_s=24.0)  # 24 − 5 不比 20 长
