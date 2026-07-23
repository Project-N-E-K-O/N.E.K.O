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
are pinned to the mainland route": Steam is a veto, not a required yes vote,
and the HTTP probe retries instead of giving up after one cold-boot timeout.
"""
import os
import sys
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
        ('_geo_ip_only_logged', False),
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
# Steam as veto, not as a required yes vote
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_steam_silent_overseas_ip_routes_overseas():
    """Main case: overseas users on a non-Steam build, or who never launched the
    Steam client, are no longer pinned to the mainland route."""
    assert _probe(ip=True, steam=None)._check_non_mainland() is True


@pytest.mark.unit
def test_steam_silent_result_is_not_cached():
    """The IP-only verdict must not be cached, so a late Steam can still overturn it."""
    assert _probe(ip=True, steam=None)._check_non_mainland() is True
    assert ConfigManager._region_cache is None
    # 同一进程内 Steam 随后发声说大陆 → 立刻否决回国内线路
    assert _probe(ip=True, steam=False)._check_non_mainland() is False
    assert ConfigManager._region_cache is False


@pytest.mark.unit
def test_steam_veto_beats_overseas_ip():
    """A proxy can make the IP look non-CN; Steam saying CN still wins."""
    assert _probe(ip=True, steam=False)._check_non_mainland() is False


@pytest.mark.unit
def test_mainland_ip_routes_mainland():
    assert _probe(ip=False, steam=None)._check_non_mainland() is False
    assert ConfigManager._region_cache is False


@pytest.mark.unit
def test_dual_pass_is_cached():
    assert _probe(ip=True, steam=True)._check_non_mainland() is True
    assert ConfigManager._region_cache is True


@pytest.mark.unit
def test_both_indeterminate_defaults_mainland_without_caching():
    assert _probe(ip=None, steam=None)._check_non_mainland() is False
    assert ConfigManager._region_cache is None
    # 网络稍后就绪 → 无需重启即可翻成海外
    assert _probe(ip=True, steam=None)._check_non_mainland() is True


@pytest.mark.unit
def test_steam_only_yes_without_ip_stays_mainland():
    """Steam alone is not accepted while the IP probe has no verdict: unchanged behaviour."""
    assert _probe(ip=None, steam=True)._check_non_mainland() is False
    assert ConfigManager._region_cache is None


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
    assert ConfigManager._ip_check_cache is None, '首次失败不得写永久哨兵'

    # 退避窗口内不重复付超时
    assert ConfigManager._check_ip_non_mainland_http() is None
    assert calls['n'] == 1

    clock.now += ConfigManager._IP_CHECK_RETRY_INTERVAL_S + 1
    assert ConfigManager._check_ip_non_mainland_http() is True
    assert ConfigManager._ip_check_cache is True


@pytest.mark.unit
def test_ip_probe_gives_up_after_max_attempts(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(core_config_mod, 'time', SimpleNamespace(monotonic=clock))
    calls = _patch_probe(monkeypatch, [OSError('down')])

    for _ in range(ConfigManager._IP_CHECK_MAX_ATTEMPTS):
        assert ConfigManager._check_ip_non_mainland_http() is None
        clock.now += ConfigManager._IP_CHECK_RETRY_INTERVAL_S + 1

    assert calls['n'] == ConfigManager._IP_CHECK_MAX_ATTEMPTS
    assert ConfigManager._ip_check_cache is ConfigManager._GEO_INDETERMINATE
    # 哨兵落定后不再发起网络请求
    assert ConfigManager._check_ip_non_mainland_http() is None
    assert calls['n'] == ConfigManager._IP_CHECK_MAX_ATTEMPTS
