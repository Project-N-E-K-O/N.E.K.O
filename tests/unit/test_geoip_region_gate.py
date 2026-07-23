# -*- coding: utf-8 -*-
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

"""Region gate (_check_non_mainland) regression tests.

Covers the fix for "overseas users who only auto-start N.E.K.O. (not Steam)
are pinned to the mainland route": the IP probe decides whenever it has an
answer and Steam is only a fallback, and the probe retries instead of giving
up after one cold-boot timeout.
"""
import os
import sys
import threading
import time as real_time
from types import SimpleNamespace

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from utils.config_manager import ConfigManager  # noqa: E402
from utils.config_manager import core_config as core_config_mod  # noqa: E402


class _Probe(core_config_mod.CoreConfigMixin):
    """Bare mixin carrier: _check_non_mainland only needs the two sub-checks."""


@pytest.fixture(autouse=True)
def reset_geo_caches(monkeypatch):
    monkeypatch.setattr(core_config_mod, 'GEOIP_FORCE_NON_MAINLAND', None)
    for name, value in (
        ('_region_cache', None),
        ('_ip_check_cache', None),
        ('_steam_check_cache', None),
        ('_geo_indeterminate_logged', False),
        ('_geo_steam_fallback_logged', False),
        ('_ip_check_attempts', 0),
        ('_ip_check_last_attempt_monotonic', None),
    ):
        monkeypatch.setattr(ConfigManager, name, value)
    yield


def _probe(ip, steam):
    p = _Probe()
    # 实例属性 → 无描述符协议，调用时不会多传 self
    p._check_ip_non_mainland_http = lambda: ip
    p._check_steam_non_mainland = lambda: steam
    return p


# ---------------------------------------------------------------------------
# IP decides; Steam is only the fallback
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_steam_silent_overseas_ip_routes_overseas():
    """Main case: overseas users on a non-Steam build, or who never launched the
    Steam client, are no longer pinned to the mainland route."""
    assert _probe(ip=True, steam=None)._check_non_mainland() is True
    assert ConfigManager._region_cache is True


@pytest.mark.unit
@pytest.mark.parametrize('steam', [True, False, None])
def test_ip_outranks_steam(steam):
    """The probe bypasses proxies, so it geolocates better than Steam's exit IP."""
    assert _probe(ip=True, steam=steam)._check_non_mainland() is True
    ConfigManager._region_cache = None
    assert _probe(ip=False, steam=steam)._check_non_mainland() is False


@pytest.mark.unit
def test_mainland_ip_routes_mainland():
    assert _probe(ip=False, steam=None)._check_non_mainland() is False
    assert ConfigManager._region_cache is False


@pytest.mark.unit
@pytest.mark.parametrize('steam, expected', [(True, True), (False, False)])
def test_steam_breaks_the_tie_when_ip_is_silent(steam, expected):
    assert _probe(ip=None, steam=steam)._check_non_mainland() is expected


@pytest.mark.unit
@pytest.mark.parametrize('steam', [True, False])
def test_steam_fallback_does_not_latch_during_cold_boot(steam):
    """Latching Steam during the cold-boot window would freeze out the IP retries."""
    assert _probe(ip=None, steam=steam)._check_non_mainland() is steam
    assert ConfigManager._region_cache is None
    # IP 退避重试拿到结论后立刻接管，即使方向与 Steam 相反
    assert _probe(ip=not steam, steam=steam)._check_non_mainland() is (not steam)
    assert ConfigManager._region_cache is (not steam)


@pytest.mark.unit
@pytest.mark.parametrize('steam', [True, False])
def test_steam_fallback_settles_once_the_probe_is_hopeless(monkeypatch, steam):
    """A permanently unreachable probe must not keep costing a 3s timeout every cycle."""
    monkeypatch.setattr(
        ConfigManager, '_ip_check_attempts', ConfigManager._IP_CHECK_SETTLE_AFTER_FAILURES,
    )
    assert _probe(ip=None, steam=steam)._check_non_mainland() is steam
    assert ConfigManager._region_cache is steam


@pytest.mark.unit
def test_steam_fallback_settle_threshold_is_at_the_backoff_ceiling():
    """The settle point should be where backoff has stopped growing, not earlier."""
    n = ConfigManager._IP_CHECK_SETTLE_AFTER_FAILURES
    assert ConfigManager._ip_check_backoff_s(n) == ConfigManager._IP_CHECK_RETRY_MAX_S
    assert ConfigManager._ip_check_backoff_s(n - 1) < ConfigManager._IP_CHECK_RETRY_MAX_S


@pytest.mark.unit
def test_both_indeterminate_defaults_mainland_without_caching():
    assert _probe(ip=None, steam=None)._check_non_mainland() is False
    assert ConfigManager._region_cache is None
    # 网络稍后就绪 → 无需重启即可翻成海外
    assert _probe(ip=True, steam=None)._check_non_mainland() is True


# ---------------------------------------------------------------------------
# HTTP probe retry budget
# ---------------------------------------------------------------------------

class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _patch_probe(monkeypatch, responses):
    """Make the HTTP probe consume `responses` (Exception → failure, str → countryCode)."""
    calls = {'n': 0}

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._payload.encode()

    class _Opener:
        def open(self, req, timeout=None):
            i = calls['n']
            calls['n'] += 1
            outcome = responses[i] if i < len(responses) else responses[-1]
            if isinstance(outcome, Exception):
                raise outcome
            return _Resp('{"countryCode": "%s"}' % outcome)

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _Opener())
    return calls


@pytest.mark.unit
def test_ip_probe_retries_after_cold_boot_failure(monkeypatch):
    """A cold-boot timeout is retried once the network stack is up."""
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))
    calls = _patch_probe(monkeypatch, [OSError('network unreachable'), 'US'])

    assert ConfigManager._check_ip_non_mainland_http() is None
    assert ConfigManager._ip_check_cache is None, '失败不得写死结论'

    # 退避窗口内不重复付超时
    assert ConfigManager._check_ip_non_mainland_http() is None
    assert calls['n'] == 1

    clock.now += ConfigManager._IP_CHECK_RETRY_BASE_S + 1
    assert ConfigManager._check_ip_non_mainland_http() is True
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_ip_probe_backs_off_exponentially_and_never_gives_up(monkeypatch):
    """Connectivity may only arrive tens of minutes in; the probe must still be alive."""
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))
    calls = _patch_probe(monkeypatch, [OSError('down')] * 7 + ['JP'])

    expected = [30.0, 60.0, 120.0, 240.0, 480.0, 600.0, 600.0]
    for i, wait in enumerate(expected, start=1):
        assert ConfigManager._check_ip_non_mainland_http() is None
        assert calls['n'] == i
        assert ConfigManager._ip_check_backoff_s(i) == wait
        # 退避未到不发请求，到点才发下一次
        clock.now += wait - 1
        assert ConfigManager._check_ip_non_mainland_http() is None
        assert calls['n'] == i
        clock.now += 2

    # 网络终于就绪：探测仍然活着，没有永久放弃
    clock.now += ConfigManager._IP_CHECK_RETRY_MAX_S + 1
    assert ConfigManager._check_ip_non_mainland_http() is True


@pytest.mark.unit
@pytest.mark.parametrize('failures', [0, 1, 2, 33, 1025, 10 ** 6])
def test_backoff_stays_finite_for_any_failure_count(failures):
    """A machine offline for days keeps accumulating failures; 2 ** huge would raise."""
    wait = ConfigManager._ip_check_backoff_s(failures)
    assert isinstance(wait, float)
    assert 0.0 <= wait <= ConfigManager._IP_CHECK_RETRY_MAX_S


@pytest.mark.unit
def test_concurrent_probes_do_not_burn_the_backoff(monkeypatch):
    """aget_core_config offloads to threads; a burst must not spend several attempts at once.

    The backoff lookup is slowed down so the check-and-set window is wide enough for
    threads to actually interleave — without it the critical section is short enough
    that the GIL hides an unsynchronized ledger and this test passes either way.
    """
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))
    calls = _patch_probe(monkeypatch, [OSError('down')])

    def _slow_backoff(failures):
        real_time.sleep(0.02)
        return 30.0

    monkeypatch.setattr(ConfigManager, '_ip_check_backoff_s', staticmethod(_slow_backoff))
    # 退避窗口刚好到期：8 个 worker 同时醒来抢这一次探测配额
    monkeypatch.setattr(ConfigManager, '_ip_check_last_attempt_monotonic', 0.0)
    monkeypatch.setattr(ConfigManager, '_ip_check_attempts', 1)
    clock.now = 10_000.0

    start = threading.Barrier(8)

    def _run():
        start.wait()
        ConfigManager._check_ip_non_mainland_http()

    threads = [threading.Thread(target=_run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls['n'] == 1, '并发爆发只应消耗一次探测配额'
    assert ConfigManager._ip_check_attempts == 2
