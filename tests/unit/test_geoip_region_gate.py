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
