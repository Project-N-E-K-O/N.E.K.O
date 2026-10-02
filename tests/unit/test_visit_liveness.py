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
