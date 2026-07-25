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

"""Region-resolution regression tests.

Structured around the five invariants in ``core_config``'s module docstring:
a single background probe owns the IP verdict, everyone else reads it; IP
outranks Steam and Steam never latches; only free-route users are probed;
the probe never gives up; and every path that freezes a session route settles
the region first.
"""
import asyncio
import os
import sys
import threading
import time as real_time
from types import SimpleNamespace

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

import utils.config_manager as config_manager_pkg  # noqa: E402
from utils.config_manager import ConfigManager  # noqa: E402
from utils.config_manager import core_config as core_config_mod  # noqa: E402


class _Probe(core_config_mod.CoreConfigMixin):
    """Bare mixin carrier — _check_non_mainland only needs the sub-checks."""


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
def reset_geo_state(monkeypatch):
    monkeypatch.setattr(core_config_mod, 'GEOIP_FORCE_NON_MAINLAND', None)
    monkeypatch.setattr(ConfigManager, '_ip_probe_wake', threading.Event())
    monkeypatch.setattr(ConfigManager, '_ip_probe_in_flight', threading.Event())
    # 默认「仍在免费路由」，否则探测循环每轮都会去读真实配置并提前收工。
    # 专测「切走免费路由」的用例自行覆盖它。
    monkeypatch.setattr(
        ConfigManager, '_free_route_still_needs_region', staticmethod(lambda: True))
    for name, value in (
        ('_region_cache', None),
        ('_ip_check_cache', None),
        ('_steam_check_cache', None),
        ('_geo_indeterminate_logged', False),
        ('_geo_steam_fallback_logged', False),
        ('_ip_probe_thread', None),
    ):
        monkeypatch.setattr(ConfigManager, name, value)
    yield
    # 背景探测线程是无限重试循环（永不放弃），必须主动终止再 join，否则泄漏的线程
    # 会带着真实网络污染后续用例。写 cache 打破 while、set wake 唤醒退避 sleep。
    # 本 fixture 声明了 monkeypatch，故先于它 teardown：断言/桩仍在位。
    thread = ConfigManager._ip_probe_thread
    if thread is not None:
        if ConfigManager._ip_check_cache is None:
            ConfigManager._ip_check_cache = False
        ConfigManager._ip_probe_wake.set()
        thread.join(5)
        assert not thread.is_alive(), '探测线程泄漏，会污染后续用例'


def _probe(ip, steam):
    """A carrier whose sub-checks return fixed values (no real network/Steam)."""
    p = _Probe()
    # 实例属性 → 无描述符协议，调用时不多传 self
    p._ensure_ip_probe_started = lambda: None
    p._check_ip_non_mainland_http = staticmethod(lambda: ip)
    p._check_steam_non_mainland = lambda: steam
    return p


def _patch_probe_once(monkeypatch, responses):
    """Drive ``_ip_probe_once`` off a scripted list (Exception=failure, str=country)."""
    calls = {'n': 0}

    def _once():
        i = calls['n']
        calls['n'] += 1
        outcome = responses[i] if i < len(responses) else responses[-1]
        if isinstance(outcome, Exception):
            raise outcome
        return (outcome != 'CN') if outcome else None

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_once))
    return calls


# ---------------------------------------------------------------------------
# #3 — IP decides; Steam is only the (never-latching) fallback
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_steam_silent_overseas_ip_routes_overseas():
    """Non-Steam / Steam-not-running overseas users are no longer pinned mainland."""
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
    """Latching Steam would freeze out the IP takeover — it must stay provisional."""
    assert _probe(ip=None, steam=steam)._check_non_mainland() is steam
    assert ConfigManager._region_cache is None
    # IP 稍后落地、即便方向相反，也立刻接管
    assert _probe(ip=not steam, steam=steam)._check_non_mainland() is (not steam)
    assert ConfigManager._region_cache is (not steam)


@pytest.mark.unit
def test_both_indeterminate_defaults_mainland_without_caching():
    assert _probe(ip=None, steam=None)._check_non_mainland() is False
    assert ConfigManager._region_cache is None
    # 网络稍后就绪 → 无需重启即可翻成海外
    assert _probe(ip=True, steam=None)._check_non_mainland() is True


# ---------------------------------------------------------------------------
# The single background probe (#1, #4)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_probe_loop_retries_until_it_lands_a_verdict(monkeypatch):
    """Cold-boot failures are retried; the loop is the sole writer of the cache."""
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_BASE_S', 0.0)
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_MAX_S', 0.0)
    calls = _patch_probe_once(monkeypatch, [OSError('cold boot'), OSError('again'), 'US'])

    _Probe()._ensure_ip_probe_started()
    ConfigManager._ip_probe_thread.join(5)

    assert calls['n'] == 3
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_probe_loop_never_gives_up(monkeypatch):
    """Connectivity can arrive tens of minutes in; the loop must still be trying."""
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_BASE_S', 0.0)
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_MAX_S', 0.0)
    # 长时间只失败，然后成功——中途从不写永久放弃标记
    calls = _patch_probe_once(monkeypatch, [OSError('down')] * 50 + ['JP'])

    _Probe()._ensure_ip_probe_started()
    ConfigManager._ip_probe_thread.join(5)

    assert calls['n'] == 51
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_probe_is_idempotent_and_single(monkeypatch):
    """Only ever one probe thread: repeated starts do not stack writers."""
    release = threading.Event()
    entered = threading.Event()

    def _once():
        entered.set()
        release.wait(5)
        raise OSError('slow')

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_once))
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_BASE_S', 0.0)

    first = None
    try:
        _Probe()._ensure_ip_probe_started()
        first = ConfigManager._ip_probe_thread
        assert entered.wait(5)
        for _ in range(5):
            _Probe()._ensure_ip_probe_started()
            assert ConfigManager._ip_probe_thread is first, '不应另起第二个探测线程'
    finally:
        release.set()


@pytest.mark.unit
def test_probe_thread_is_daemon(monkeypatch):
    """A probe hung on a 3s connect must never hold up process exit."""
    release = threading.Event()

    def _once():
        release.wait(5)
        raise OSError('slow')

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_once))
    try:
        _Probe()._ensure_ip_probe_started()
        thread = ConfigManager._ip_probe_thread
        assert thread is not None and thread.daemon
    finally:
        release.set()


@pytest.mark.unit
def test_read_never_blocks_the_caller(monkeypatch):
    """_check_ip_non_mainland_http is a pure read — no network on the caller thread."""
    def _boom():
        raise AssertionError('read path must not probe')

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_boom))
    started = real_time.monotonic()
    assert ConfigManager._check_ip_non_mainland_http() is None
    assert real_time.monotonic() - started < 0.1


@pytest.mark.unit
@pytest.mark.parametrize('failures', [0, 1, 2, 33, 1025, 10 ** 6])
def test_backoff_stays_finite_for_any_failure_count(failures):
    """A machine offline for days keeps failing; 2 ** huge would raise OverflowError."""
    wait = ConfigManager._ip_check_backoff_s(failures)
    assert isinstance(wait, float)
    assert 0.0 <= wait <= ConfigManager._IP_CHECK_RETRY_MAX_S


# ---------------------------------------------------------------------------
# #2 — only free-route users are probed
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_paid_route_config_read_never_probes(config_manager, monkeypatch):
    """Reading config on a paid/custom route must not start the geolocation probe."""
    def _boom():
        raise AssertionError('paid-route read must not probe')

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_boom))

    import json as _json
    path = config_manager.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as fh:
        _json.dump({'coreApi': 'qwen'}, fh)
    config_manager._core_config_cache = None

    cfg = config_manager.get_core_config()
    assert not [v for k, v in cfg.items()
                if k.endswith('_URL') and isinstance(v, str) and 'lanlan.tech' in v], \
        '前置条件：该配置不应处于免费路由'
    assert ConfigManager._ip_probe_thread is None, '自配 API 用户不应启动 GeoIP 探测'


@pytest.mark.unit
def test_free_route_config_read_starts_the_probe(config_manager, monkeypatch):
    """The free route is exactly where probing is allowed."""
    started = threading.Event()

    def _once():
        started.set()
        real_time.sleep(0.3)
        raise OSError('slow')

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_once))

    import json as _json
    path = config_manager.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as fh:
        _json.dump({'coreApi': 'free'}, fh)
    config_manager._core_config_cache = None

    try:
        config_manager.get_core_config()
        assert started.wait(5), '免费路由读配置应当启动探测'
    finally:
        pass
@pytest.mark.unit
def test_one_config_snapshot_uses_one_region_verdict(config_manager, monkeypatch):
    """All URLs in a snapshot must agree on the region.

    Resolving per URL would let Steam initialising mid-loop leave earlier URLs on
    lanlan.tech and later ones on lanlan.app — one config pointing at two regions.
    Asserted on the real ``get_core_config`` loop (an earlier draft passed
    ``non_mainland=`` by hand and never exercised the call site).
    """
    import json as _json
    path = config_manager.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as fh:
        _json.dump({'coreApi': 'free'}, fh)
    config_manager._core_config_cache = None

    calls = {'n': 0}
    flips = iter([False] + [True] * 50)

    def _flipping(self):
        calls['n'] += 1
        return next(flips)

    monkeypatch.setattr(type(config_manager), '_check_non_mainland', _flipping)
    cfg = config_manager.get_core_config()

    assert calls['n'] == 1, f'一次快照内判定了 {calls["n"]} 次，各 URL 可能不一致'
    lanlan = [v for k, v in cfg.items()
              if k.endswith('_URL') and isinstance(v, str) and 'lanlan.' in v]
    assert lanlan, '前置条件：配置必须处于免费路由'
    hosts = {'lanlan.app' if 'lanlan.app' in v else 'lanlan.tech' for v in lanlan}
    assert len(hosts) == 1, f'同一份快照指向了两个区域: {lanlan}'


# ---------------------------------------------------------------------------
# #5 — sessions settle the region before freezing a route
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_startup_warmup_waits_for_the_verdict(monkeypatch):
    """The first session must not be pinned to the transient mainland fallback."""
    class _Slow:
        def open(self, req, timeout=None):
            real_time.sleep(0.3)
            return _JsonResp('{"countryCode": "US"}')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _Slow())

    probe = _Probe()
    probe.aget_core_config = _async_return(None)

    ConfigManager._ensure_ip_probe_started()
    assert ConfigManager._ip_check_cache is None, '前置条件：预热开始时结论尚未落地'

    assert asyncio.run(probe.awarmup_region_check(timeout=5)) is True
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_startup_warmup_does_not_block_the_event_loop(monkeypatch):
    """Waiting is allowed at startup, but never on the loop itself."""
    release = threading.Event()

    class _Hanging:
        def open(self, req, timeout=None):
            release.wait(5)
            raise OSError('timed out')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _Hanging())

    probe = _Probe()
    probe.aget_core_config = _async_return(None)
    ConfigManager._ensure_ip_probe_started()

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

    try:
        worst = asyncio.run(_run())
        assert worst < 0.5, f'预热期间事件循环被占用 {worst:.2f}s'
    finally:
        release.set()


@pytest.mark.unit
def test_session_start_waits_out_a_probe_still_in_flight(monkeypatch):
    """A session freezes its route, so it waits for a still-running probe."""
    class _Slow:
        def open(self, req, timeout=None):
            real_time.sleep(0.3)
            return _JsonResp('{"countryCode": "US"}')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _Slow())

    probe = _Probe()
    probe.aget_core_config = _async_return(None)
    ConfigManager._ensure_ip_probe_started()
    assert ConfigManager._ip_check_cache is None

    assert asyncio.run(probe.aensure_region_resolved(timeout=5)) is True
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_session_start_is_free_when_already_resolved(monkeypatch):
    """Zero cost on the normal path: verdict in hand, no waiting."""
    monkeypatch.setattr(ConfigManager, '_region_cache', True)

    def _boom(*a, **kw):
        raise AssertionError('已落定时不应等待探测')

    monkeypatch.setattr(ConfigManager, 'join_ip_probe', staticmethod(_boom))
    probe = _Probe()
    started = real_time.monotonic()
    assert asyncio.run(probe.aensure_region_resolved()) is True
    assert real_time.monotonic() - started < 0.2


@pytest.mark.unit
def test_session_start_logs_when_the_wait_expires(monkeypatch):
    """Waiting forever is not an option, so the give-up must be diagnosable.

    Records straight off the module logger rather than via ``caplog``: the app's
    logging setup puts ``propagate=False`` on the ``N.E.K.O`` parent, so caplog's
    root handler sees nothing once any test has pulled that setup in.
    """
    release = threading.Event()

    class _Hanging:
        def open(self, req, timeout=None):
            release.wait(5)
            raise OSError('timed out')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _Hanging())

    warnings = []
    monkeypatch.setattr(
        core_config_mod.logger, 'warning',
        lambda msg, *a, **kw: warnings.append(str(msg) % a if a else str(msg)),
    )

    probe = _Probe()
    probe.aget_core_config = _async_return(None)
    ConfigManager._ensure_ip_probe_started()
    try:
        assert asyncio.run(probe.aensure_region_resolved(timeout=0.1)) is False
        assert any('GeoIP' in w for w in warnings), f'放弃等待必须留下日志，实际: {warnings}'
    finally:
        release.set()


@pytest.mark.unit
def test_steam_users_do_not_pay_for_the_ip_wait(monkeypatch):
    """Having Steam's answer is enough to pick a route — do not wait for IP.

    The wait avoids routing on *no* information; Steam's answer is information.
    Making Steam users sit through a probe timeout is pure first-session latency
    and buys nothing — the Steam verdict is never latched, so the probe still
    takes over for later sessions once it lands.
    """
    release = threading.Event()

    class _Hanging:
        def open(self, req, timeout=None):
            release.wait(10)
            raise OSError('timed out')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a, **kw: _Hanging())

    try:
        ConfigManager._ensure_ip_probe_started()
        monkeypatch.setattr(ConfigManager, '_steam_check_cache', True)

        started = real_time.monotonic()
        assert ConfigManager.join_ip_probe(timeout=5) is True
        waited = real_time.monotonic() - started
        assert waited < 0.5, f'Steam 已有结论却仍等了 {waited:.2f}s'
    finally:
        release.set()


@pytest.mark.unit
def test_skipping_the_wait_does_not_promote_steam():
    """Not waiting is a latency call, not a correctness one — Steam must not latch."""
    probe = _Probe()
    probe._check_steam_non_mainland = lambda: True
    probe.aget_core_config = _async_return(None)

    assert asyncio.run(probe.aensure_region_resolved(timeout=5)) is True
    assert ConfigManager._region_cache is None, 'Steam 票不得因跳过等待而落定'
    assert _probe(ip=False, steam=True)._check_non_mainland() is False
    assert ConfigManager._region_cache is False


@pytest.mark.unit
def test_every_session_preparation_path_settles_the_region():
    """Each path that builds a session (and freezes its base URL) settles first.

    Structural, because the real risk is a *new* path added later that a
    behavioural test of the existing two would never notice.
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
    """An empty GetIPCountry() means "no answer yet", never "overseas"."""
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


@pytest.mark.unit
def test_dns_wedged_iteration_recovers_without_a_replacement_thread(monkeypatch):
    """A DNS-wedged iteration must not stall recovery — and must not need a replacement.

    ``getaddrinfo`` ignores the socket timeout, so one iteration can hang far longer
    than 3s. That is survivable precisely because the thread is a *loop*: the wedged
    call eventually raises (OS resolver timeout), the loop backs off and retries.
    Spawning a "replacement" would call the same ``getaddrinfo``, hang identically,
    and only buy multi-writer races plus a thread leak — which is what this design
    exists to remove. Asserted as behaviour so the point is not re-litigated.
    """
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_BASE_S', 0.0)
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_MAX_S', 0.0)

    wedged_entered = threading.Event()
    unwedge = threading.Event()
    calls = {'n': 0}

    def _once():
        calls['n'] += 1
        if calls['n'] == 1:
            wedged_entered.set()
            unwedge.wait(10)          # 模拟卡在 getaddrinfo 里
            raise OSError('resolver timed out')
        return True                    # 网络恢复

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_once))

    _Probe()._ensure_ip_probe_started()
    thread = ConfigManager._ip_probe_thread
    assert wedged_entered.wait(5), '第一次探测未进入卡死状态'

    # 卡死期间反复触发启动：不得另起线程（活着 == 重试计划在跑）
    for _ in range(5):
        _Probe()._ensure_ip_probe_started()
        assert ConfigManager._ip_probe_thread is thread, '卡死期间不应另起替代探测'
    assert ConfigManager._ip_check_cache is None

    # 解析超时返回后，同一个循环自行重试并拿到结论——无需任何外部干预
    unwedge.set()
    thread.join(5)
    assert ConfigManager._ip_check_cache is True, '卡死迭代后循环应自行恢复'
    assert calls['n'] == 2


@pytest.mark.unit
def test_probe_stops_when_user_leaves_the_free_route_mid_backoff(monkeypatch):
    """Switching to a paid/custom provider *while backing off* must stop the probe.

    Two things this must not do, both of which make the test vacuous:
    - fix eligibility to False before the thread starts (only proves "exits after
      the first failure", never exercises the mid-backoff switch);
    - use ``_ip_probe_wake`` to wake the sleeper — that event *also* terminates the
      loop, so the thread would exit even with the eligibility check deleted.
    So: let the backoff expire naturally and assert no second request goes out.
    """
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_BASE_S', 0.3)
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_MAX_S', 0.3)
    eligible = {'v': True}
    monkeypatch.setattr(
        ConfigManager, '_free_route_still_needs_region',
        staticmethod(lambda: eligible['v']))

    probed = threading.Event()
    calls = {'n': 0}

    def _once():
        calls['n'] += 1
        probed.set()
        raise OSError('down')

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_once))

    _Probe()._ensure_ip_probe_started()
    thread = ConfigManager._ip_probe_thread
    assert probed.wait(5), '首次探测未发生'

    # 等它真正进入退避 sleep，再模拟用户切走免费线路
    for _ in range(200):
        if not ConfigManager._ip_probe_in_flight.is_set():
            break
        real_time.sleep(0.005)
    assert not ConfigManager._ip_probe_in_flight.is_set(), '前置条件：应已进入退避'
    assert thread.is_alive(), '前置条件：循环仍在退避中'
    eligible['v'] = False

    # 退避自然到期后循环回到顶部，应当据资格判定收工——而不是再敲一次
    thread.join(5)
    assert not thread.is_alive(), '切走免费线路后循环应收工'
    assert calls['n'] == 1, f'退避到期后不应再探测，实际探了 {calls["n"]} 次'
    assert ConfigManager._ip_check_cache is None


@pytest.mark.unit
def test_waiters_skip_a_probe_that_is_only_backing_off(monkeypatch):
    """Backoff sleep is not in-flight: no verdict can arrive, so do not pay the join.

    The loop stays alive while sleeping 30-600s. Treating that as "in flight" makes
    every session pay the full join timeout for the whole duration of a GeoIP outage.
    """
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_BASE_S', 30.0)
    backing_off = threading.Event()

    def _once():
        backing_off.set()
        raise OSError('down')

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_once))

    _Probe()._ensure_ip_probe_started()
    assert backing_off.wait(5)
    # 等它进入退避 sleep（in_flight 被清掉）
    for _ in range(200):
        if not ConfigManager._ip_probe_in_flight.is_set():
            break
        real_time.sleep(0.01)
    assert not ConfigManager._ip_probe_in_flight.is_set(), '退避期间不应标记为在飞'
    assert ConfigManager._ip_probe_thread.is_alive(), '前置条件：线程仍活着（在退避）'

    started = real_time.monotonic()
    assert ConfigManager.join_ip_probe(timeout=5) is False
    waited = real_time.monotonic() - started
    assert waited < 0.5, f'退避期间不应等待，实际等了 {waited:.2f}s'


@pytest.mark.unit
def test_ip_verdict_landing_during_the_steam_check_still_wins():
    """The probe can publish while ``_check_steam_non_mainland`` is running.

    Returning Steam anyway would let the fallback outrank the authoritative verdict,
    and since ``get_core_config`` decides per URL, one snapshot could mix
    ``lanlan.tech`` and ``lanlan.app`` — they disagree exactly when a proxy is in play.
    """
    probe = _Probe()
    probe._ensure_ip_probe_started = lambda: None
    probe._check_ip_non_mainland_http = staticmethod(
        lambda: ConfigManager._ip_check_cache)

    def _steam_then_verdict_lands():
        ConfigManager._ip_check_cache = True     # 探测恰在此刻落地
        return False                             # Steam 说大陆（代理出口）

    probe._check_steam_non_mainland = _steam_then_verdict_lands
    assert probe._check_non_mainland() is True, 'IP 权威结论应压过 Steam 兜底票'
    assert ConfigManager._region_cache is True


@pytest.mark.unit
def test_livestream_derived_urls_do_not_trigger_the_probe(monkeypatch):
    """Livestream takes those routes over before the region is consulted.

    ``_adjust_free_api_url`` derives /core, /text/v1 and /tts from the livestream
    prefix without asking for a verdict, so a livestream user needs no probe for
    them and must not have their IP sent to ip-api.com on their account.
    """
    cfg = {
        'CORE_URL': 'wss://www.lanlan.tech/core',
        'TTS_URL': 'wss://www.lanlan.tech/tts',
        'ASSIST_URL': 'https://www.lanlan.tech/text/v1',
    }
    monkeypatch.setattr(config_manager_pkg, 'is_livestream_active', lambda: True)
    assert ConfigManager._config_needs_region(cfg) is False

    # 非派生路径仍然需要判定（livestream 只接管那三个端点）
    cfg['OTHER_URL'] = 'https://www.lanlan.tech/something-else'
    assert ConfigManager._config_needs_region(cfg) is True

    monkeypatch.setattr(config_manager_pkg, 'is_livestream_active', lambda: False)
    assert ConfigManager._config_needs_region(
        {'CORE_URL': 'wss://www.lanlan.tech/core'}) is True


@pytest.mark.unit
def test_startup_warmup_runs_after_runtime_config_is_finalized():
    """Warmup must sit after the Cloud Save import / Steamworks init.

    Reading config before that can see the pre-import values, conclude "no region
    needed", and never start the probe — leaving the first session on the fallback.
    Structural because the ordering, not the call itself, is the invariant.
    """
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2]
              / 'app' / 'main_server' / '__init__.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))

    for node in ast.walk(tree):
        if (isinstance(node, ast.AsyncFunctionDef)
                and node.name == '_ensure_main_server_runtime_initialized'):
            break
    else:
        pytest.fail('未找到 _ensure_main_server_runtime_initialized，断言失效')

    # 只断言「在这个函数里」是不够的：预热被挪到 Cloud Save 导入或 Steamworks
    # 初始化之前时那样仍会通过，而那正是本 PR 要防的时序回归。比行号。
    seen = {}
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        name = getattr(call.func, 'attr', None) or getattr(call.func, 'id', None)
        if name in ('awarmup_region_check', 'initialize_steamworks',
                    '_sync_memory_server_after_startup_import',
                    '_disable_main_storage_limited_mode'):
            seen.setdefault(name, call.lineno)

    assert 'awarmup_region_check' in seen, 'GeoIP 预热必须在本函数内执行'
    for anchor, what in (('_sync_memory_server_after_startup_import', 'Cloud Save 导入'),
                         ('initialize_steamworks', 'Steamworks 初始化')):
        assert anchor in seen, f'锚点 {anchor} 不见了，本断言已失效'
        assert seen['awarmup_region_check'] > seen[anchor], \
            f'GeoIP 预热必须晚于{what}，否则可能读到成型前的配置'

    # 另一侧的边界：也不能晚于「放开会话准入」。那之后请求就能进来，若预热尚未
    # 落地，首个会话会整场钉在兜底线路——预热必须夹在「配置成型」与「准入」之间。
    assert '_disable_main_storage_limited_mode' in seen, '准入锚点不见了，本断言已失效'
    assert seen['awarmup_region_check'] < seen['_disable_main_storage_limited_mode'], \
        'GeoIP 预热必须早于解除 limited mode，否则会话可在区域未落定时进来'


@pytest.mark.unit
def test_custom_url_merely_containing_the_brand_string_is_not_a_free_route():
    """Eligibility keys on hostname, not substring.

    A custom endpoint like ``https://custom.example/v1/lanlan.tech`` is not the
    official free route; treating it as one starts the probe and discloses the
    user's IP for nothing.
    """
    assert ConfigManager._config_needs_region(
        {'CORE_URL': 'https://custom.example/v1/lanlan.tech'}) is False
    assert ConfigManager._config_needs_region(
        {'CORE_URL': 'https://lanlan.tech.evil.example/core'}) is False
    assert ConfigManager._config_needs_region(
        {'CORE_URL': 'wss://www.lanlan.tech/core'}) is True


@pytest.mark.unit
def test_eligibility_recheck_survives_the_overseas_rewrite():
    """The loop re-checks against an *already adjusted* snapshot.

    Once the region resolves overseas, ``get_core_config`` hands back ``lanlan.app``
    URLs. If eligibility only recognised ``lanlan.tech``, a Steam-overseas user with
    the IP probe still unresolved would look like "no longer on the free route" and
    the probe would quit after its first failure.
    """
    assert ConfigManager._config_needs_region(
        {'CORE_URL': 'wss://www.lanlan.app/core'}) is True


@pytest.mark.unit
@pytest.mark.parametrize('rel_path', [
    'plugin/plugins/qq_auto_reply/session_bootstrap_service.py',
    'plugin/plugins/bilibili_dm/__init__.py',
])
def test_plugin_session_paths_settle_the_region(rel_path):
    """Plugin sessions cache an OmniOfflineClient too — same base-URL freeze.

    Both plugins keep the client in a session-keyed dict, so a route picked before
    the verdict lands sticks for the life of that session.

    Checked per enclosing function and by line order, not by whole-file counts: a
    settle call sitting in some unrelated function, or after the config read it is
    supposed to guard, would satisfy a count-based assertion while guaranteeing
    nothing.
    """
    import ast
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[2] / rel_path
    tree = ast.parse(source.read_text(encoding='utf-8'))

    def _named(call):
        return getattr(call.func, 'attr', None) or getattr(call.func, 'id', None)

    checked = 0
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reads, settles = [], []
        for call in ast.walk(func):
            if not isinstance(call, ast.Call):
                continue
            name = _named(call)
            if (name == 'get_model_api_config' and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == 'conversation'):
                reads.append(call.lineno)
            elif name == 'aensure_region_resolved':
                settles.append(call.lineno)
        for read_line in reads:
            checked += 1
            earlier = [s for s in settles if s < read_line]
            assert earlier, (
                f'{rel_path}:{read_line} 在 {func.name}() 里冻结会话线路前没有先落定区域'
                f'（该函数内的落定调用: {settles or "无"}）'
            )

    assert checked, f'{rel_path} 里没找到 conversation 配置读取，本断言已失效'
