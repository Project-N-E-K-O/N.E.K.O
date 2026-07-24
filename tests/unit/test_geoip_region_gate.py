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
import asyncio
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


def _async_return(value):
    async def _coro(*a, **kw):
        return value
    return _coro


class _JsonResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload.encode()


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
        ('_ip_probe_thread', None),
    ):
        monkeypatch.setattr(ConfigManager, name, value)
    yield
    # 探测线程必须在本用例结束前退出。join 超时本身不会让测试失败，而漏掉的线程
    # 会在 monkeypatch 还原后继续跑，用真实网络污染后续用例——本文件已经被一次
    # 顺序依赖坑过（见 test_session_start_logs_when_the_wait_expires 的注释）。
    # 本 fixture 声明了 monkeypatch，故先于它 teardown：断言时打的桩仍然在位。
    thread = ConfigManager._ip_probe_thread
    if thread is not None:
        thread.join(5)
        assert not thread.is_alive(), '探测线程泄漏，会污染后续用例'


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
def test_steam_fallback_never_latches(steam):
    """Latching Steam would freeze out the IP retries — at any failure count."""
    assert _probe(ip=None, steam=steam)._check_non_mainland() is steam
    assert ConfigManager._region_cache is None
    # IP 退避重试拿到结论后立刻接管，即使方向与 Steam 相反
    assert _probe(ip=not steam, steam=steam)._check_non_mainland() is (not steam)
    assert ConfigManager._region_cache is (not steam)


@pytest.mark.unit
@pytest.mark.parametrize('failures', [1, 6, 7, 100])
def test_steam_fallback_never_latches_however_long_the_probe_has_failed(monkeypatch, failures):
    """No failure count may promote the fallback: connectivity can still recover, and
    _ip_check_attempts counts probes *started*, so a threshold would fire mid-flight."""
    monkeypatch.setattr(ConfigManager, '_ip_check_attempts', failures)
    assert _probe(ip=None, steam=True)._check_non_mainland() is True
    assert ConfigManager._region_cache is None
    # 那次「飞行中」的探测最终成功时，它的结论仍然能接管
    assert _probe(ip=False, steam=True)._check_non_mainland() is False
    assert ConfigManager._region_cache is False


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


def _probe_once(expect_started=True):
    """Kick the probe and wait for the background thread, so the test sees its result.

    The probe never runs on the caller's thread (that would freeze the event loop),
    so every assertion about its outcome has to join it first.
    """
    before = ConfigManager._ip_probe_thread
    result = ConfigManager._check_ip_non_mainland_http()
    thread = ConfigManager._ip_probe_thread
    if expect_started:
        assert thread is not None and thread is not before, '本次调用应当发起一次探测'
        thread.join(5)
        assert not thread.is_alive(), '探测线程未在超时内结束'
    else:
        assert thread is before, '本次调用不应发起新探测'
    return result


@pytest.mark.unit
def test_ip_probe_retries_after_cold_boot_failure(monkeypatch):
    """A cold-boot timeout is retried once the network stack is up."""
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))
    calls = _patch_probe(monkeypatch, [OSError('network unreachable'), 'US'])

    assert _probe_once() is None
    assert ConfigManager._ip_check_cache is None, '失败不得写死结论'

    # 退避窗口内不重复发起探测
    assert _probe_once(expect_started=False) is None
    assert calls['n'] == 1

    clock.now += ConfigManager._IP_CHECK_RETRY_BASE_S + 1
    _probe_once()
    assert ConfigManager._ip_check_cache is True
    # 结论落地后，之后的调用直接命中缓存，不再发探测
    assert _probe_once(expect_started=False) is True


@pytest.mark.unit
def test_ip_probe_backs_off_exponentially_and_never_gives_up(monkeypatch):
    """Connectivity may only arrive tens of minutes in; the probe must still be alive."""
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))
    calls = _patch_probe(monkeypatch, [OSError('down')] * 7 + ['JP'])

    expected = [30.0, 60.0, 120.0, 240.0, 480.0, 600.0, 600.0]
    for i, wait in enumerate(expected, start=1):
        assert _probe_once() is None
        assert calls['n'] == i
        assert ConfigManager._ip_check_backoff_s(i) == wait
        # 退避未到不发请求，到点才发下一次
        clock.now += wait - 1
        assert _probe_once(expect_started=False) is None
        assert calls['n'] == i
        clock.now += 2

    # 网络终于就绪：探测仍然活着，没有永久放弃
    clock.now += ConfigManager._IP_CHECK_RETRY_MAX_S + 1
    _probe_once()
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_probe_never_blocks_the_caller(monkeypatch):
    """The probe must never do network IO on the caller's thread.

    ``get_core_config`` fans out to ~40 sync callers living inside ``async def``
    (``get_model_api_config`` in ``_start_session_prepare_runtime`` among them), so a
    3s connect timeout here freezes the shared event loop and stalls every WebSocket
    handshake in the process.
    """
    release = threading.Event()

    class _HangingOpener:
        def open(self, req, timeout=None):
            release.wait(5)
            raise OSError('timed out')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _HangingOpener())

    try:
        started = real_time.monotonic()
        assert ConfigManager._check_ip_non_mainland_http() is None
        elapsed = real_time.monotonic() - started
        assert elapsed < 0.5, f'调用方被阻塞 {elapsed:.2f}s，探测没有真正后台化'
        assert ConfigManager._ip_probe_thread.is_alive(), '探测应当仍在后台跑'
    finally:
        release.set()
        thread = ConfigManager._ip_probe_thread
        if thread is not None:
            thread.join(5)


@pytest.mark.unit
def test_startup_warmup_waits_for_the_verdict(monkeypatch):
    """The first session must not be pinned to the transient mainland fallback.

    A session freezes its route at start_session time, so the verdict has to land
    before the server accepts sessions. Startup is the one place allowed to wait —
    and it waits off the event loop.
    """
    # 探测必须"还在飞"，否则等不等都拿得到结论，用例会退化成假绿
    class _SlowOpener:
        def open(self, req, timeout=None):
            real_time.sleep(0.3)
            return _JsonResp('{"countryCode": "US"}')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _SlowOpener())

    probe = _Probe()
    probe.aget_core_config = _async_return(None)

    # 探测由 aget_core_config 触发；这里直接触发以隔离 warmup 的等待语义
    ConfigManager._check_ip_non_mainland_http()
    assert ConfigManager._ip_check_cache is None, '前置条件：预热开始时结论尚未落地'

    assert asyncio.run(probe.awarmup_region_check(timeout=5)) is True
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_startup_warmup_does_not_block_the_event_loop(monkeypatch):
    """Waiting is allowed at startup, but never on the loop itself."""
    release = threading.Event()

    class _HangingOpener:
        def open(self, req, timeout=None):
            release.wait(5)
            raise OSError('timed out')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _HangingOpener())

    probe = _Probe()
    probe.aget_core_config = _async_return(None)
    ConfigManager._check_ip_non_mainland_http()

    async def _run():
        gaps = []
        stop = asyncio.Event()

        async def _beat():
            last = real_time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.02)
                now = real_time.monotonic()
                gaps.append(now - last)
                last = now

        beat = asyncio.create_task(_beat())
        await asyncio.sleep(0.1)
        release.set()
        await probe.awarmup_region_check(timeout=5)
        stop.set()
        await beat
        return max(gaps)

    worst = asyncio.run(_run())
    assert worst < 0.5, f'预热期间事件循环被占用 {worst:.2f}s'


@pytest.mark.unit
def test_session_start_waits_out_a_probe_that_outlived_startup(monkeypatch):
    """Startup's join can expire in DNS resolution; the session must not pin blindly."""
    class _SlowOpener:
        def open(self, req, timeout=None):
            real_time.sleep(0.3)
            return _JsonResp('{"countryCode": "US"}')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _SlowOpener())

    probe = _Probe()
    ConfigManager._check_ip_non_mainland_http()
    assert ConfigManager._ip_check_cache is None, '前置条件：会话开始时结论尚未落地'

    assert asyncio.run(probe.aensure_region_resolved(timeout=5)) is True
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_session_start_region_wait_is_free_when_already_resolved(monkeypatch):
    """Zero cost on the normal path: no probe in flight, no waiting."""
    monkeypatch.setattr(ConfigManager, '_region_cache', True)

    def _boom(*a, **kw):
        raise AssertionError('已落定时不应等待探测')

    monkeypatch.setattr(ConfigManager, 'join_ip_probe', staticmethod(_boom))
    probe = _Probe()
    started = real_time.monotonic()
    assert asyncio.run(probe.aensure_region_resolved()) is True
    assert real_time.monotonic() - started < 0.2


@pytest.mark.unit
def test_session_start_does_not_wait_when_no_probe_is_running():
    """Nothing in flight means nothing to wait for — never stall the session."""
    probe = _Probe()
    started = real_time.monotonic()
    assert asyncio.run(probe.aensure_region_resolved(timeout=5)) is False
    assert real_time.monotonic() - started < 0.2


@pytest.mark.unit
def test_session_start_logs_when_the_wait_expires(monkeypatch):
    """Waiting forever is not an option, so the give-up must at least be diagnosable.

    Without this line, "an overseas user is occasionally slow for a whole session"
    leaves no trace in the logs at all.

    Records straight off the module logger rather than via ``caplog``: the app's own
    logging setup puts ``propagate=False`` on the ``N.E.K.O`` parent, so caplog's
    root handler sees nothing once any test has pulled that setup in — which made
    the first version of this test pass or fail purely on import order.
    """
    release = threading.Event()

    class _HangingOpener:
        def open(self, req, timeout=None):
            release.wait(5)
            raise OSError('timed out')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _HangingOpener())

    warnings = []
    monkeypatch.setattr(
        core_config_mod.logger,
        'warning',
        lambda msg, *a, **kw: warnings.append(str(msg) % a if a else str(msg)),
    )

    probe = _Probe()
    ConfigManager._check_ip_non_mainland_http()
    try:
        assert asyncio.run(probe.aensure_region_resolved(timeout=0.1)) is False
        assert any('GeoIP' in w for w in warnings), f'放弃等待必须留下日志，实际: {warnings}'
    finally:
        release.set()
        thread = ConfigManager._ip_probe_thread
        if thread is not None:
            thread.join(5)


@pytest.mark.unit
def test_probe_thread_is_daemon(monkeypatch):
    """A probe hung on a 3s connect must never hold up process exit."""
    _patch_probe(monkeypatch, [OSError('down')])
    ConfigManager._check_ip_non_mainland_http()
    thread = ConfigManager._ip_probe_thread
    assert thread is not None and thread.daemon
    thread.join(5)


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
