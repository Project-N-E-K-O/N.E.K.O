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

# 只用一种导入形式：既要 monkeypatch 包属性（is_livestream_active），又要拿到
# ConfigManager / core_config，混用 import 与 from-import 会被静态检查判为风格问题。
import utils.config_manager as config_manager_pkg  # noqa: E402

ConfigManager = config_manager_pkg.ConfigManager
core_config_mod = config_manager_pkg.core_config


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
def config_manager(clean_user_data_dir, monkeypatch):
    """Real ConfigManager on a temp config dir.

    ``get_config_manager`` hands back a process-wide singleton, so whichever test
    file ran first leaves it bound to *its* (now deleted) temp dir — these tests then
    read a stale config and fail only when run alongside that file. Rebuild the
    singleton here so the instance actually belongs to this test's directory.
    """
    monkeypatch.setattr(config_manager_pkg, '_config_manager', None, raising=False)
    monkeypatch.setattr(config_manager_pkg, '_config_manager_migrated', False, raising=False)
    cm = config_manager_pkg.get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    cm._core_config_cache = None
    return cm


@pytest.fixture(autouse=True)
def reset_geo_state(monkeypatch):
    monkeypatch.setattr(core_config_mod, 'GEOIP_FORCE_NON_MAINLAND', None)
    monkeypatch.setattr(ConfigManager, '_ip_probe_wake', threading.Event())
    monkeypatch.setattr(ConfigManager, '_ip_probe_in_flight', threading.Event())
    monkeypatch.setattr(ConfigManager, '_ip_probe_stopping', False)
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
        ConfigManager._ip_probe_stopping = True
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
    monkeypatch.setattr(
        config_manager_pkg, 'get_livestream_config',
        lambda: {'server_prefix': 'https://live.example/tok'})
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


def _plugin_files_constructing_offline_clients():
    """Every plugin file that builds an OmniOfflineClient — discovered, not listed.

    A hardcoded list is exactly how bilibili_danmaku and reply_buffer_service were
    missed: two plugins had the same freeze and the test only knew about the other
    two. Discovery makes a newly added plugin fail this test instead of shipping
    an unsettled route.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / 'plugin'
    return sorted(
        p for p in root.rglob('*.py')
        if 'OmniOfflineClient(' in p.read_text(encoding='utf-8')
    )


@pytest.mark.unit
def test_every_plugin_offline_client_settles_the_region():
    """Any plugin building an OmniOfflineClient must settle the region first.

    The client captures base_url at construction, so a route picked before the
    verdict lands is what that client keeps using. Checked per enclosing function
    and by line order: a settle call in some other function, or after the client is
    built, would satisfy a naive count while guaranteeing nothing.
    """
    import ast

    files = _plugin_files_constructing_offline_clients()
    assert files, '未发现任何构造 OmniOfflineClient 的插件文件，本断言已失效'

    problems = []
    for path in files:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            builds, settles = [], []
            for call in ast.walk(func):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, 'attr', None) or getattr(call.func, 'id', None)
                if name == 'OmniOfflineClient':
                    builds.append(call.lineno)
                elif name == 'aensure_region_resolved':
                    settles.append(call.lineno)
            for build_line in builds:
                if not [x for x in settles if x < build_line]:
                    problems.append(
                        f'{path.name}:{build_line} in {func.name}()'
                        f'（该函数内的落定调用: {settles or "无"}）'
                    )

    assert not problems, (
        '这些插件在构造 OmniOfflineClient 前没有先落定区域判定: ' + '; '.join(problems)
    )


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
    cfg = {'CORE_URL': 'wss://www.lanlan.app/core'}
    assert ConfigManager._config_needs_region(
        cfg, ConfigManager._REGION_HOSTS_ADJUSTED) is True


@pytest.mark.unit
def test_raw_config_gate_ignores_an_explicit_lanlan_app_endpoint():
    """Only ``lanlan.tech`` is ever rewritten, so a raw ``lanlan.app`` is a custom route.

    Accepting ``.app`` for raw user config — needed only when the loop inspects its
    own rewritten snapshot — would probe on behalf of someone whose URLs no region
    decision will ever touch. That is a privacy-gate violation, not a wasted request,
    which is why the two questions now take different host sets.
    """
    cfg = {'CORE_URL': 'wss://www.lanlan.app/core'}
    assert ConfigManager._config_needs_region(cfg) is False              # 默认 = RAW
    assert ConfigManager._config_needs_region(
        cfg, ConfigManager._REGION_HOSTS_RAW) is False
    # 免费路由本身不受影响
    assert ConfigManager._config_needs_region(
        {'CORE_URL': 'wss://www.lanlan.tech/core'}) is True


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


@pytest.mark.unit
def test_waiter_stops_when_the_attempt_fails_mid_wait(monkeypatch):
    """The wait tracks the current attempt, not the thread's lifetime.

    Joining the loop thread would block for the whole timeout whenever an attempt
    fails while someone is waiting: the loop stays alive in its 30-600s backoff,
    during which no verdict can possibly arrive. Startup and every session would
    pay the full timeout for nothing.
    """
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_BASE_S', 30.0)
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_MAX_S', 30.0)

    entered = threading.Event()
    fail_now = threading.Event()

    def _once():
        entered.set()
        fail_now.wait(10)          # 等测试发话再失败
        raise OSError('down')

    monkeypatch.setattr(ConfigManager, '_ip_probe_once', staticmethod(_once))

    _Probe()._ensure_ip_probe_started()
    assert entered.wait(5), '探测未进入请求阶段'
    assert ConfigManager._ip_probe_in_flight.is_set()

    # 在等待过程中让本次尝试失败：循环转入长退避但线程仍 alive
    def _fail_soon():
        real_time.sleep(0.15)
        fail_now.set()

    threading.Thread(target=_fail_soon, daemon=True).start()

    started = real_time.monotonic()
    assert ConfigManager.join_ip_probe(timeout=5) is False
    waited = real_time.monotonic() - started
    assert waited < 2.0, f'本次尝试已失败仍等了 {waited:.2f}s（应在转入退避时立刻返回）'
    assert ConfigManager._ip_probe_thread.is_alive(), '循环应仍在退避中（并未结束）'


@pytest.mark.unit
def test_malformed_livestream_prefix_still_needs_the_region(monkeypatch):
    """Excluding a URL is only safe when its livestream derivation actually succeeds.

    ``_derive_livestream_url`` rejects a prefix without scheme/netloc and falls back
    to the regional rewrite. Excluding on ``is_livestream_active()`` alone would then
    say "no region needed", start no probe, and pin an overseas user to lanlan.tech.
    """
    cfg = {'CORE_URL': 'wss://www.lanlan.tech/core'}
    monkeypatch.setattr(config_manager_pkg, 'is_livestream_active', lambda: True)

    # 畸形 prefix（缺 scheme）：派生会失败 → 仍然需要区域判定
    monkeypatch.setattr(
        config_manager_pkg, 'get_livestream_config',
        lambda: {'server_prefix': 'localhost:8080/tok'})
    assert ConfigManager._config_needs_region(cfg) is True

    # 空 prefix 同理
    monkeypatch.setattr(
        config_manager_pkg, 'get_livestream_config', lambda: {'server_prefix': ''})
    assert ConfigManager._config_needs_region(cfg) is True

    # 合法 prefix：派生成功 → 用不到区域判定
    monkeypatch.setattr(
        config_manager_pkg, 'get_livestream_config',
        lambda: {'server_prefix': 'https://live.example/tok'})
    assert ConfigManager._config_needs_region(cfg) is False


@pytest.mark.unit
def test_plugin_geoip_fallback_logging_uses_a_real_facility():
    """The fail-open handler must not raise on its own.

    These handlers exist so a probe error cannot stop a plugin session. Logging
    through an attribute the class does not define turns that inside out: the
    ``except`` raises ``AttributeError``, the original error is lost, and fail-open
    becomes fail-closed. Copying a logging idiom between plugin files is exactly how
    that slipped in twice, so check each site against its own class.
    """
    import ast

    problems = []
    for path in _plugin_files_constructing_offline_clients():
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        lines = source.split('\n')

        for lineno, line in enumerate(lines, 1):
            if 'GeoIP' not in line or ('warning' not in line and '_emit_log' not in line):
                continue
            owner = None
            for node in ast.walk(tree):
                if (isinstance(node, ast.ClassDef)
                        and node.lineno <= lineno <= node.end_lineno):
                    owner = node
            if owner is None:
                problems.append(f'{path.name}:{lineno} 不在任何类内')
                continue

            body = lines[owner.lineno - 1:owner.end_lineno]
            expr = line.strip()
            if 'self.plugin.' in expr:
                attr = expr.split('self.plugin.')[1].split('(')[0]
                # 同类里别处也这么用 → 是该插件的既有惯例
                ok = sum(1 for x in body if f'self.plugin.{attr}' in x) > 1
                what = f'self.plugin.{attr}'
            elif 'self.logger' in expr:
                ok = 'logger' in {
                    t.attr for n in ast.walk(owner) if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name) and t.value.id == 'self'
                }
                what = 'self.logger'
            else:
                ok, what = False, expr[:40]
            if not ok:
                problems.append(f'{path.name}:{lineno} [{owner.name}] 用了 {what}')

    assert not problems, (
        'GeoIP fail-open 处理器用了该类不存在的日志设施（会在 except 里再抛）: '
        + '; '.join(problems)
    )


@pytest.mark.unit
def test_loop_eligibility_reads_the_rewritten_snapshot_correctly(monkeypatch):
    """Exercises the call site, not just the predicate.

    ``_free_route_still_needs_region`` re-reads ``get_core_config()``, whose free URLs
    are already rewritten to ``lanlan.app`` once the region resolves overseas. Passing
    the raw host set there would read "user left the free route" and kill the probe
    after one failure — a mistake a predicate-only test cannot see.
    """
    class _FakeCM:
        @staticmethod
        def get_core_config():
            return {'CORE_URL': 'wss://www.lanlan.app/core', 'coreApi': 'free'}

    # autouse fixture 把这个方法桩成了恒 True（供其它用例用），本用例要测真实实现
    monkeypatch.setattr(
        ConfigManager, '_free_route_still_needs_region',
        core_config_mod.CoreConfigMixin.__dict__['_free_route_still_needs_region'])
    monkeypatch.setattr(config_manager_pkg, 'get_config_manager', lambda *a, **kw: _FakeCM())
    monkeypatch.setattr(config_manager_pkg, 'is_livestream_active', lambda: False)

    assert ConfigManager._free_route_still_needs_region() is True, \
        '海外改写后的快照仍属免费路由，探测不应因此收工'

    # 真正切走免费线路时才该收工
    class _CustomCM:
        @staticmethod
        def get_core_config():
            return {'CORE_URL': 'https://api.openai.com/v1', 'coreApi': 'openai'}

    monkeypatch.setattr(config_manager_pkg, 'get_config_manager', lambda *a, **kw: _CustomCM())
    assert ConfigManager._free_route_still_needs_region() is False

    # 自配端点恰好也在 lanlan.app：只看 host 分不清它和「被改写的免费 URL」，
    # 会让切走免费线路的用户继续被探测。路由选择字段不受改写影响，能区分。
    class _CustomAppCM:
        @staticmethod
        def get_core_config():
            return {'CORE_URL': 'wss://www.lanlan.app/core', 'coreApi': 'openai'}

    monkeypatch.setattr(config_manager_pkg, 'get_config_manager', lambda *a, **kw: _CustomAppCM())
    assert ConfigManager._free_route_still_needs_region() is False,         '显式配在 lanlan.app 的自配线路不应让探测继续'


# ---------------------------------------------------------------------------
# Voice cleanup must not act on a guessed region (it writes to characters.json)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_voice_cleanup_is_skipped_while_the_region_is_provisional(monkeypatch):
    """Clearing a voice is a permanent write; a provisional region is a guess.

    On the transient mainland fallback an overseas-only voice (``yui``, Gemini
    voices) is absent from the mainland catalog, so cleanup would strip it from
    characters.json. The verdict landing a second later fixes the endpoint but
    cannot restore the user's choice — so cleanup waits instead.
    """

    class _CM(config_manager_pkg.voice_storage.VoiceStorageMixin):
        def __init__(self, cfg):
            self._cfg = cfg

        def get_core_config(self):
            return dict(self._cfg)

        def load_characters(self):
            raise AssertionError('区域未落定时不应读取/改写角色数据')

    free_cfg = {'coreApi': 'free', 'CORE_URL': 'wss://www.lanlan.tech/core'}
    cm = _CM(free_cfg)
    cm._config_needs_region = ConfigManager._config_needs_region

    monkeypatch.setattr(ConfigManager, '_region_cache', None)
    assert cm._region_verdict_is_provisional() is True
    assert cm.cleanup_invalid_voice_ids() == (0, []), '未落定时应整体跳过清理'


@pytest.mark.unit
@pytest.mark.parametrize('region, cfg, provisional', [
    # 已落定 → 可清理
    (True, {'coreApi': 'free', 'CORE_URL': 'wss://www.lanlan.tech/core'}, False),
    (False, {'coreApi': 'free', 'CORE_URL': 'wss://www.lanlan.tech/core'}, False),
    # 自配线路与区域无关 → 可清理（否则它们的区域永不落定，清理会被永久禁用）
    (None, {'coreApi': 'openai', 'CORE_URL': 'https://api.openai.com/v1'}, False),
    (None, {'coreApi': 'openai', 'CORE_URL': 'wss://www.lanlan.app/core'}, False),
    # 免费 + 未落定 → 跳过。第二格是关键：Steam 临时判海外时快照已被改写成
    # lanlan.app，按 URL host 判会误判成自配线路而放行清理。
    (None, {'coreApi': 'free', 'CORE_URL': 'wss://www.lanlan.tech/core'}, True),
    (None, {'coreApi': 'free', 'CORE_URL': 'wss://www.lanlan.app/core'}, True),
    # 配置残缺读不到路由选择 → 保守不删
    (None, {'CORE_URL': 'wss://www.lanlan.tech/core'}, True),
])
def test_provisional_region_predicate(monkeypatch, region, cfg, provisional):

    class _CM(config_manager_pkg.voice_storage.VoiceStorageMixin):
        def get_core_config(self):
            return dict(cfg)

    cm = _CM()
    cm._config_needs_region = ConfigManager._config_needs_region
    monkeypatch.setattr(ConfigManager, '_region_cache', region)
    assert cm._region_verdict_is_provisional() is provisional


@pytest.mark.unit
def test_this_file_has_no_duplicate_test_names():
    """A redefined test silently replaces the earlier one — it simply never runs.

    This file has grown by repeated appends and hit that twice already; both times a
    whole block of assertions was quietly dead until a reviewer noticed. Cheap to
    check, and it fails loudly instead.
    """
    import ast
    import collections
    import pathlib

    tree = ast.parse(pathlib.Path(__file__).read_text(encoding='utf-8'))
    names = [
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    dupes = {n: c for n, c in collections.Counter(names).items() if c > 1}
    assert not dupes, f'重名的测试函数（早先定义从未运行）: {dupes}'


@pytest.mark.unit
@pytest.mark.parametrize('cfg, expected', [
    ({'coreApi': 'free'}, True),
    ({'CORE_API_TYPE': 'free'}, True),
    # 付费 core + 免费 assist：assist 的 lanlan.tech URL 同样要区域改写，
    # 只看 core 会让这些用户的探测提前收工、assist 线路停在国内。
    ({'coreApi': 'openai', 'assistApi': 'free'}, True),
    ({'coreApi': 'openai', 'assistApi': 'qwen'}, False),
    ({}, False),
])
def test_free_provider_detection_covers_every_slot(cfg, expected):
    assert ConfigManager._any_free_provider(cfg) is expected


@pytest.mark.unit
def test_livestream_derived_route_is_not_provisional_forever(monkeypatch):
    """Livestream that derives every free endpoint needs no verdict — so never wait.

    Those configs deliberately start no probe, so ``_region_cache`` stays ``None``
    for the life of the process. Judging provisional on the provider slot alone would
    therefore disable voice cleanup and default-voice binding permanently for them.
    """
    monkeypatch.setattr(ConfigManager, '_region_cache', None)
    monkeypatch.setattr(config_manager_pkg, 'is_livestream_active', lambda: True)
    monkeypatch.setattr(
        config_manager_pkg, 'get_livestream_config',
        lambda: {'server_prefix': 'https://live.example/tok'})

    cfg = {'coreApi': 'free', 'CORE_URL': 'wss://www.lanlan.tech/core'}

    class _CM(config_manager_pkg.voice_storage.VoiceStorageMixin):
        def get_core_config(self):
            return dict(cfg)

    cm = _CM()
    cm._config_needs_region = ConfigManager._config_needs_region
    assert cm._region_verdict_is_provisional() is False, \
        'livestream 已派生掉全部免费端点，不该被永远判成未落定'

    # 而 livestream 没接管的路径仍然需要判定
    monkeypatch.setattr(
        config_manager_pkg, 'get_livestream_config', lambda: {'server_prefix': ''})
    assert cm._region_verdict_is_provisional() is True


@pytest.mark.unit
def test_startup_warmup_retries_a_backed_off_probe(monkeypatch):
    """Startup must get a verdict, even if the first attempt already failed.

    Voice cleanup now reads the config (and thus starts the probe) before warmup
    runs, so by warmup time the first attempt has often already failed on a
    not-yet-ready network and entered a 30s backoff. Returning immediately there
    would make warmup a no-op and admit sessions with no verdict.
    """
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_BASE_S', 30.0)
    monkeypatch.setattr(ConfigManager, '_IP_CHECK_RETRY_MAX_S', 30.0)
    calls = _patch_probe_once(monkeypatch, [OSError('network not up'), 'US'])

    probe = _Probe()
    probe.aget_core_config = _async_return(None)

    # 首探失败并进入 30 秒退避（远长于预热愿意等的时间）
    ConfigManager._ensure_ip_probe_started()
    for _ in range(500):
        if calls['n'] >= 1 and not ConfigManager._ip_probe_in_flight.is_set():
            break
        real_time.sleep(0.01)
    assert calls['n'] == 1 and ConfigManager._ip_check_cache is None

    started = real_time.monotonic()
    assert asyncio.run(probe.awarmup_region_check(timeout=5)) is True, \
        '预热应当催重试并拿到结论，而不是因为在退避就立刻返回'
    assert real_time.monotonic() - started < 5, '不应等满 30 秒退避'
    assert calls['n'] == 2


@pytest.mark.unit
def test_warmup_does_not_wait_when_no_probe_is_running():
    """``through_backoff`` means "wait through a backoff", not "always wait".

    A paid/custom provider (or a fully livestream-derived route) never starts a
    probe at all. Startup warmup is the only ``through_backoff=True`` caller, so
    treating "no probe" like "backing off" made every such user pay the full
    timeout on every boot before session admission opened.
    """
    assert ConfigManager._ip_probe_thread is None or not ConfigManager._ip_probe_thread.is_alive()
    assert ConfigManager._ip_check_cache is None and ConfigManager._steam_check_cache is None

    started = real_time.monotonic()
    assert ConfigManager.join_ip_probe(timeout=5, through_backoff=True) is False
    elapsed = real_time.monotonic() - started
    assert elapsed < 1.0, f'没有探测在跑却等了 {elapsed:.2f}s'


@pytest.mark.unit
@pytest.mark.parametrize('settled', [True, False])
def test_game_session_refreshes_the_character_after_the_wait(monkeypatch, settled):
    """The character can change while we wait — regardless of the wait's outcome.

    Re-reading only inside ``if settled:`` covered the route-rewrite reason but
    not this one: on a fail-open timeout the pool would build the client from the
    pre-wait character and cache it under the stale key, so the event runs the
    wrong persona and leaves an entry no later event can hit.
    """
    from main_routers.game_router import session_pool as sp
    from main_routers import shared_state as sp_shared

    sp._game_sessions.clear()
    names = iter(['旧角色', '新角色'])
    monkeypatch.setattr(sp, '_get_character_info', lambda n=None: {'lanlan_name': next(names)})

    class _CM:
        async def aensure_region_resolved(self, timeout=1.5):
            return settled

    monkeypatch.setattr(sp_shared, 'get_config_manager', lambda: _CM())

    built = {}

    async def _fake_build(key, game_type, session_id, char_info, *, postgame_snapshot=None):
        built['key'] = key
        built['name'] = char_info.get('lanlan_name')
        return {'last_activity': 0.0}

    monkeypatch.setattr(sp, '_build_and_register_game_session', _fake_build)

    asyncio.run(sp._get_or_create_session('mc', 'sid'))

    assert built['name'] == '新角色', '等待期间角色已切换，必须用切换后的角色建会话'
    assert '新角色' in built['key'], f'会话被挂到了等待前的 key 上: {built["key"]}'


@pytest.mark.unit
def test_every_voice_cleanup_path_also_retries_the_deferred_binding():
    """A deferred default-voice binding needs a path that comes back for it.

    ``ensure_default_yui_voice_for_free_api`` skips binding while the region is
    provisional, promising "next round". Its only original callers were the
    config-save route and ``clear_voice_ids`` — neither of which runs again on
    its own, so switching to the free API left the default card permanently
    unbound. Session preparation is that next round; discovered automatically
    from the voice-cleanup call sites so a third path cannot silently skip it.
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
        calls = set()
        for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
            # 清理走 to_thread(方法引用)，绑定是直接函数调用——两种形态都要收
            for sub in ast.walk(call):
                if isinstance(sub, ast.Attribute):
                    calls.add(sub.attr)
                elif isinstance(sub, ast.Name):
                    calls.add(sub.id)
        if 'cleanup_invalid_voice_ids' not in calls:
            continue
        checked.append(node.name)
        if 'ensure_default_yui_voice_for_free_api' not in calls:
            missing.append(f'{node.name} (line {node.lineno})')

    assert len(checked) >= 2, f'未找到足够的音色清理路径，断言失效: {checked}'
    assert not missing, f'这些路径清理了音色却没补上被推迟的默认音色绑定: {missing}'


@pytest.mark.unit
@pytest.mark.parametrize('url', [
    'https://www.lanlan.tech/text/v1',
    'https://www.lanlan.app/text/v1',
])
def test_agent_url_is_exempt_from_the_region_rewrite(config_manager, url):
    """``AGENT_MODEL_URL`` deliberately never follows the region switch.

    free-agent-model is pinned to the CN text entry, so ``_normalize_agent_url``
    is an identity function and the Agent route carries no region dependency at
    all. Pinned because the exemption is easy to mistake for a missing rewrite —
    a review already read it that way — and because turning it into a real
    rewrite would silently move every Agent request to a different endpoint.
    """
    assert config_manager._normalize_agent_url(url) == url


@pytest.mark.unit
def test_region_sensitive_voice_endpoints_settle_first():
    """Endpoints serving the voice catalog settle the region before reading it.

    The mainland ``free`` and overseas ``free_intl`` catalogs are disjoint, so a
    response assembled across a landing verdict can offer a voice the runtime
    route then refuses. Discovered from the catalog readers rather than a
    hardcoded endpoint list, so a third endpoint cannot quietly skip it.
    """
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2]
              / 'main_routers' / 'characters_router' / 'voice_preview.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))

    readers = {'get_voices_for_current_api', 'get_active_realtime_native_provider_for_ui'}
    missing = []
    checked = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # 只管 HTTP 端点：helper 由端点调用，落定在端点入口做一次即可
        is_endpoint = any(
            isinstance(d, ast.Call) and getattr(d.func, 'attr', None) in {'get', 'post'}
            and getattr(getattr(d.func, 'value', None), 'id', None) == 'router'
            for d in node.decorator_list
        )
        if not is_endpoint:
            continue
        calls = {getattr(c.func, 'attr', None) or getattr(c.func, 'id', None)
                 for c in ast.walk(node) if isinstance(c, ast.Call)}
        if not (calls & readers):
            continue
        checked.append(node.name)
        if 'aensure_region_resolved' not in calls:
            missing.append(f'{node.name} (line {node.lineno})')

    assert len(checked) >= 2, f'未找到足够的音色目录端点，断言失效: {checked}'
    assert not missing, f'这些端点按区域出音色目录却未先落定: {missing}'
