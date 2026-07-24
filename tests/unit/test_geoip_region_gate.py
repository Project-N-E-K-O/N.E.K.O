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


@pytest.fixture()
def config_manager(clean_user_data_dir):
    """Real ConfigManager on a temp config dir (mirrors test_api_config_manager)."""
    from utils.config_manager import get_config_manager
    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    return cm


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
        ('_ip_probe_started_monotonic', None),
        ('_ip_probe_generation', 0),
        ('_wedged_probes', []),
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
    """Nothing in flight and nothing due means nothing to wait for."""
    probe = _Probe()
    # 实例属性覆盖：aensure_region_resolved 走的是 self._check_...，打在
    # ConfigManager 上对 _Probe 无效（会穿透去打真实网络）
    probe._check_ip_non_mainland_http = lambda: None
    started = real_time.monotonic()
    assert asyncio.run(probe.aensure_region_resolved(timeout=5)) is False
    assert real_time.monotonic() - started < 0.2


@pytest.mark.unit
def test_session_start_kicks_a_due_probe_instead_of_giving_up(monkeypatch):
    """Backoff expired with no probe in flight: start one rather than open a whole
    session on the fallback route. The probe get_core_config would start moments
    later cannot catch this session — its route is already frozen."""
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))
    calls = _patch_probe(monkeypatch, [OSError('cold boot'), 'JP'])

    # 启动阶段那次探测失败，退避随后到期
    _probe_once()
    assert ConfigManager._ip_check_cache is None
    clock.now += ConfigManager._IP_CHECK_RETRY_BASE_S + 1

    probe = _Probe()
    # aensure 靠 aget_core_config 补发探测——免费路由门就在那里面。这里模拟一个
    # 免费路由用户：读配置会触发区域判定，进而发起探测。
    async def _free_route_config():
        probe._check_non_mainland()
        return {}
    probe.aget_core_config = _free_route_config
    assert asyncio.run(probe.aensure_region_resolved(timeout=5)) is True
    assert calls['n'] == 2, '会话开始时应当补发一次探测'
    assert ConfigManager._ip_check_cache is True


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


# ---------------------------------------------------------------------------
# Steam country write-back (/api/config/steam_language)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize('country, expect_cache', [
    ('US', True),
    ('CN', False),
    ('', None),      # 拿不到国家码 = 暂时不知道，不是"海外"
    (None, None),
])
def test_steam_country_writeback_only_on_real_data(monkeypatch, country, expect_cache):
    """An empty GetIPCountry() means "no answer yet", never "overseas".

    Steam reports an empty country while it is still connecting; writing
    ``not is_mainland_china`` unconditionally turns that blank into a positive
    non-mainland verdict and pushes the route overseas on no evidence.
    """
    from main_routers.config_router import language as lang_mod

    monkeypatch.setattr(
        lang_mod, 'ensure_steamworks',
        lambda: SimpleNamespace(
            Apps=SimpleNamespace(GetCurrentGameLanguage=lambda: 'english'),
            Utils=SimpleNamespace(GetIPCountry=lambda: country),
        ),
    )
    monkeypatch.setattr(lang_mod, 'aload_ui_language_override', _async_return(None))
    monkeypatch.setattr(lang_mod.get_steam_language, '_logged', True, raising=False)

    result = asyncio.run(lang_mod.get_steam_language())

    assert result['success'] is True
    assert ConfigManager._steam_check_cache is expect_cache


# ---------------------------------------------------------------------------
# Structural: every session-preparation path must settle the region first
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_every_session_preparation_path_settles_the_region():
    """A session freezes its base URL, so each path that builds one must settle first.

    Structural rather than behavioural on purpose: the risk is a *third* preparation
    path being added later and silently skipping the settle step, which no
    behavioural test of the existing two would notice.
    """
    import ast
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[2] / 'main_logic' / 'core' / 'lifecycle.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))

    missing = []
    checked = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = {getattr(c.func, 'attr', None) for c in ast.walk(node) if isinstance(c, ast.Call)}
        if 'aget_core_config' not in calls:
            continue
        checked.append(node.name)
        if 'aensure_region_resolved' not in calls:
            missing.append(f'{node.name} (line {node.lineno})')

    assert checked, '未找到任何会话准备路径，断言失效'
    assert not missing, f'这些路径会冻结会话线路却未先落定区域判定: {missing}'


@pytest.mark.unit
def test_session_time_resolution_keeps_the_free_route_privacy_gate(monkeypatch):
    """Only free-route users may reach ip-api.com.

    ``aensure_region_resolved`` must kick a due probe through ``aget_core_config``,
    never by poking the probe directly: URL rewriting only consults the region for
    ``lanlan.tech`` routes, so that read is the natural gate. Poking directly would
    hand the public IP of custom-endpoint and livestream users to a third party
    whose verdict their route never uses.
    """
    fired = []

    class _Spy:
        def open(self, req, timeout=None):
            fired.append(1)
            raise OSError('should not fire')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _Spy())

    probe = _Probe()
    # 自配 API 用户：配置里没有 lanlan.tech，读配置不触发区域判定
    probe.aget_core_config = _async_return({'CORE_URL': 'https://api.openai.com/v1'})

    assert asyncio.run(probe.aensure_region_resolved(timeout=0.1)) is False
    assert not fired, '自配 API 用户不应向第三方地理服务暴露 IP'


@pytest.mark.unit
def test_stuck_probe_is_replaced_instead_of_blocking_forever(monkeypatch):
    """A DNS-wedged thread must not veto every future probe.

    ``getaddrinfo`` ignores the socket timeout, so a probe can stay alive for an
    unbounded time. Gating new probes purely on ``is_alive()`` would let one wedged
    thread cancel the exponential retry for the rest of the process — connectivity
    could come back and the route would never recover.
    """
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))

    release = threading.Event()
    entered = threading.Event()
    started = []

    class _WedgedThenOk:
        def open(self, req, timeout=None):
            started.append(1)
            if len(started) == 1:
                entered.set()             # 已真正进入 open()，避免 is_alive() 竞态
                release.wait(10)          # 第一个探测卡在 DNS 上
                raise OSError('unwedged')
            return _JsonResp('{"countryCode": "US"}')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _WedgedThenOk())

    stuck = None
    try:
        assert ConfigManager._check_ip_non_mainland_http() is None
        stuck = ConfigManager._ip_probe_thread
        assert entered.wait(5), '第一个探测未进入 open()'

        # 还没超龄：不顶替
        clock.now += ConfigManager._IP_PROBE_STALE_AFTER_S - 1
        assert ConfigManager._check_ip_non_mainland_http() is None
        assert ConfigManager._ip_probe_thread is stuck
        assert len(started) == 1

        # 超龄：另起一个，卡死的那个不再有否决权
        clock.now += 2
        _probe_once()
        assert ConfigManager._ip_probe_thread is not stuck
        assert ConfigManager._ip_check_cache is True
    finally:
        release.set()
        if stuck is not None:
            stuck.join(10)


@pytest.mark.unit
def test_game_session_pool_settles_the_region():
    """The game pool caches an OmniOfflineClient with its base_url — same freeze."""
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2]
              / 'main_routers' / 'game_router' / 'session_pool.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == '_get_or_create_session':
            calls = {getattr(c.func, 'attr', None) for c in ast.walk(node) if isinstance(c, ast.Call)}
            assert 'aensure_region_resolved' in calls, \
                '游戏会话池会缓存 base_url，必须先落定区域判定'
            break
    else:
        pytest.fail('未找到 _get_or_create_session，断言失效')


@pytest.mark.unit
def test_superseded_probe_cannot_overwrite_a_newer_verdict(monkeypatch):
    """A wedged probe that surfaces late must not clobber its replacement.

    Its answer was taken before the network exit changed (WiFi back, VPN toggled),
    so publishing it would pin later sessions to the wrong endpoint.
    """
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))

    release = threading.Event()
    entered = threading.Event()
    started = []

    class _OldSaysCN_NewSaysJP:
        def open(self, req, timeout=None):
            started.append(1)
            if len(started) == 1:
                entered.set()
                release.wait(10)
                return _JsonResp('{"countryCode": "CN"}')     # 换网前的旧答案
            return _JsonResp('{"countryCode": "JP"}')         # 顶替者的新答案

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _OldSaysCN_NewSaysJP())

    stuck = None
    try:
        ConfigManager._check_ip_non_mainland_http()
        stuck = ConfigManager._ip_probe_thread
        assert entered.wait(5)

        clock.now += ConfigManager._IP_PROBE_STALE_AFTER_S + 1
        _probe_once()
        assert ConfigManager._ip_check_cache is True, '顶替者应当先写入 JP'

        # 旧探测这时才带着 CN 归来
        release.set()
        stuck.join(10)
        assert not stuck.is_alive()
        assert ConfigManager._ip_check_cache is True, '过期探测不得覆盖更新的结论'
    finally:
        release.set()
        if stuck is not None:
            stuck.join(10)


@pytest.mark.unit
def test_wedged_probe_replacements_are_rate_limited_but_never_stop(monkeypatch):
    """Permanently blocked DNS: bound the thread growth without ever giving up.

    Two failure modes to avoid at once. Spawning one replacement per backoff cycle
    leaks roughly six unjoinable daemons per hour for the life of the process. But
    refusing outright once the cap is reached recreates the very deadlock this PR
    removed — nobody probes again, so an overseas user stays on the mainland route
    until restart even after the network comes back.
    """
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))

    release = threading.Event()

    class _AlwaysWedged:
        def open(self, req, timeout=None):
            release.wait(20)
            raise OSError('never resolves')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _AlwaysWedged())

    threads = []
    step = ConfigManager._IP_PROBE_STALE_AFTER_S + ConfigManager._IP_CHECK_RETRY_MAX_S + 1
    rounds = 24
    try:
        for _ in range(rounds):
            ConfigManager._check_ip_non_mainland_http()
            t = ConfigManager._ip_probe_thread
            if t is not None and t not in threads:
                threads.append(t)
            clock.now += step

        # 有界：远少于「每轮一个」
        assert len(threads) < rounds, f'{rounds} 轮产生了 {len(threads)} 个线程，没有限速'
        # 但不停手：到顶之后仍然继续以兜底节奏尝试，否则网络恢复永远发现不了
        cap = ConfigManager._IP_PROBE_MAX_WEDGED
        # 硬停手时线程数恰好停在 cap+1（到顶前的正常顶替），所以必须严格大于它，
        # 否则这条断言会把「永久停手」放过去。
        assert len(threads) > cap + 1, '到达上限后就再不尝试 = 回到本 PR 删掉的死局'
        # 增速不超过兜底节奏（放一格余量给上限前的正常顶替）
        elapsed = rounds * step
        budget = cap + 1 + elapsed / ConfigManager._IP_PROBE_DESPERATE_INTERVAL_S + 1
        assert len(threads) <= budget, f'线程增速 {len(threads)} 超出预算 {budget:.0f}'
    finally:
        release.set()
        for t in threads:
            t.join(20)


@pytest.mark.unit
def test_steam_fallback_yields_to_a_verdict_that_lands_mid_call():
    """The probe runs in the background and can land between the two sub-checks.

    Taking the Steam fallback anyway would let one core_config snapshot rewrite some
    URLs by the IP verdict and others by Steam's — self-contradictory on a proxy,
    where the two disagree.
    """
    probe = _Probe()
    probe._check_ip_non_mainland_http = lambda: None

    def _steam_then_verdict_lands():
        ConfigManager._ip_check_cache = True     # 探测恰在此刻落地
        return False                             # Steam 说大陆（代理出口）

    probe._check_steam_non_mainland = _steam_then_verdict_lands
    assert probe._check_non_mainland() is True, 'IP 权威结论应当压过 Steam 兜底票'
    assert ConfigManager._region_cache is True


@pytest.mark.unit
def test_one_config_snapshot_uses_one_region_verdict(config_manager, monkeypatch):
    """All URLs in a snapshot must agree on the region.

    The verdict is deliberately not cached while provisional, so resolving it per
    URL lets Steam initialising mid-loop leave earlier URLs on lanlan.tech and later
    ones on lanlan.app — one config pointing at two regions. Asserted on the real
    ``get_core_config`` loop: an earlier version of this test passed
    ``non_mainland=`` by hand and therefore never exercised the call site at all.
    """
    # 必须真的落在免费路由上：URL 里没有 lanlan.tech 时 _adjust_free_api_url
    # 第一行就早退，根本不会查区域，用例会退化成永远绿。
    import json as _json
    path = config_manager.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as fh:
        _json.dump({'coreApi': 'free'}, fh)
    config_manager._core_config_cache = None

    calls = {'n': 0}
    flips = iter([False] + [True] * 50)

    def _flipping():
        calls['n'] += 1
        return next(flips)

    monkeypatch.setattr(type(config_manager), '_check_non_mainland', lambda self: _flipping())
    config_manager._core_config_cache = None
    cfg = config_manager.get_core_config()

    assert calls['n'] == 1, f'一次快照内判定了 {calls["n"]} 次，各 URL 可能不一致'
    lanlan = [v for k, v in cfg.items()
              if k.endswith('_URL') and isinstance(v, str) and 'lanlan.' in v]
    assert lanlan, '前置条件：配置必须处于免费路由，否则区域判定根本不会被查'
    hosts = {'lanlan.app' if 'lanlan.app' in v else 'lanlan.tech' for v in lanlan}
    assert len(hosts) <= 1, f'同一份快照指向了两个区域: {lanlan}'


@pytest.mark.unit
def test_steam_users_do_not_pay_for_the_ip_wait(monkeypatch):
    """Having Steam's answer already is enough to pick a route — do not wait for IP.

    The wait exists to avoid routing on *no* information. Making users who already
    have an answer sit through a probe timeout is pure added first-session latency,
    and it buys nothing: the Steam verdict is never latched, so the probe still takes
    over for later sessions once it lands.
    """
    release = threading.Event()

    class _Hanging:
        def open(self, req, timeout=None):
            release.wait(10)
            raise OSError('timed out')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _Hanging())

    try:
        ConfigManager._check_ip_non_mainland_http()          # 探测挂在网络上
        monkeypatch.setattr(ConfigManager, '_steam_check_cache', True)

        started = real_time.monotonic()
        assert ConfigManager.join_ip_probe(timeout=5) is True
        waited = real_time.monotonic() - started
        assert waited < 0.5, f'Steam 已有结论却仍等了 {waited:.2f}s'
    finally:
        release.set()
        thread = ConfigManager._ip_probe_thread
        if thread is not None:
            thread.join(10)


@pytest.mark.unit
def test_skipping_the_wait_does_not_promote_steam():
    """Not waiting is a latency call, not a correctness one — Steam still must not latch.

    Exercised through ``aensure_region_resolved`` specifically: that is where the
    skip lives, so asserting it on ``_check_non_mainland`` would leave the shortcut
    itself untested.
    """
    probe = _Probe()
    probe._check_steam_non_mainland = lambda: True
    probe.aget_core_config = _async_return(None)

    assert asyncio.run(probe.aensure_region_resolved(timeout=5)) is True
    assert ConfigManager._region_cache is None, 'Steam 票不得因为跳过等待而落定'

    # IP 稍后落地并给出相反结论时，照样接管
    assert _probe(ip=False, steam=True)._check_non_mainland() is False
    assert ConfigManager._region_cache is False
