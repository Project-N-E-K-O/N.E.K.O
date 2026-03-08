import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from utils.aiohttp_proxy_utils import aiohttp_session_kwargs_for_url, should_bypass_proxy_for_url


@pytest.mark.unit
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:48911/api", True),
        ("https://127.0.0.1:8443/health", True),
        ("http://localhost:48911/api", True),
        ("http://[::1]:48911/api", True),
        ("https://example.com/api", False),
        ("not-a-url", False),
    ],
)
def test_should_bypass_proxy_for_url(url, expected):
    assert should_bypass_proxy_for_url(url) is expected


@pytest.mark.unit
def test_aiohttp_session_kwargs_for_loopback_url_disables_trust_env():
    assert aiohttp_session_kwargs_for_url("http://127.0.0.1:48911/api") == {"trust_env": False}


@pytest.mark.unit
def test_aiohttp_session_kwargs_for_remote_url_keeps_default_behavior():
    assert aiohttp_session_kwargs_for_url("https://example.com/api") == {"trust_env": True}
    assert aiohttp_session_kwargs_for_url("https://example.com/api", default_trust_env=False) == {
        "trust_env": False
    }
