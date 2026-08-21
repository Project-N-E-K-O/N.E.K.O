"""Table-driven contract for utils.http.url endpoint identity.

Two callers depend on this predicate to decide whether one provider's
credential may travel with a given URL:

* the vision slot, deciding whether it may inherit the chat credential;
* the config layer, deciding whether a key-book secret matches a slot URL.

Both need it wrong in the safe direction, so the contract is enumerated by
CLASS here rather than grown one reported edge case at a time — the earlier
per-edge approach is exactly what let the same mistakes reappear in a second
hand-rolled copy.
"""

import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from utils.http.url import endpoint_identity, same_endpoint  # noqa: E402


# (标签, a, b) —— 这些写法差异不改变端点身份
SAME = [
    ('scheme 大小写', 'HTTPS://api.example.com/v1', 'https://api.example.com/v1'),
    ('host 大小写', 'https://API.Example.COM/v1', 'https://api.example.com/v1'),
    ('单个尾斜杠', 'https://api.example.com/v1/', 'https://api.example.com/v1'),
    ('https 默认端口', 'https://api.example.com:443/v1', 'https://api.example.com/v1'),
    ('http 默认端口', 'http://api.example.com:80/v1', 'http://api.example.com/v1'),
    ('wss 默认端口', 'wss://api.example.com:443/rt', 'wss://api.example.com/rt'),
    ('端口前导零', 'https://api.example.com:0443/v1', 'https://api.example.com/v1'),
    ('非默认端口前导零', 'https://api.example.com:08443/v1', 'https://api.example.com:8443/v1'),
    ('IPv6 压缩与展开', 'http://[2001:0db8:0:0:0:0:0:1]:8080/v1', 'http://[2001:db8::1]:8080/v1'),
    ('IPv6 大小写', 'http://[2001:DB8::1]/v1', 'http://[2001:db8::1]/v1'),
    ('scoped IPv6 地址大小写', 'https://[FE80::1%eth0]/v1', 'https://[fe80::1%eth0]/v1'),
    ('scoped IPv6 地址展开', 'https://[fe80:0:0:0:0:0:0:1%eth0]/v1', 'https://[fe80::1%eth0]/v1'),
    ('host 尾点(DNS 根)', 'https://api.example.com./v1', 'https://api.example.com/v1'),
    ('尾点+大小写', 'https://API.Example.COM./v1', 'https://api.example.com/v1'),
    ('同样写坏的 URL', 'http://[::1/v1', 'http://[::1/v1'),
]

# (标签, a, b) —— 这些差异**必须**判为不同端点，否则凭证会跨边界
DIFFERENT = [
    ('path 大小写', 'https://api.example.com/v1/TenantA', 'https://api.example.com/v1/tenanta'),
    ('query 大小写', 'https://api.example.com/v1?Key=A', 'https://api.example.com/v1?key=a'),
    ('userinfo 大小写', 'https://User:PASS@api.example.com/v1', 'https://user:pass@api.example.com/v1'),
    ('重复尾斜杠', 'https://api.example.com/v1/', 'https://api.example.com/v1//'),
    ('非默认端口', 'https://api.example.com:8443/v1', 'https://api.example.com/v1'),
    ('不同端口', 'https://api.example.com:8443/v1', 'https://api.example.com:9443/v1'),
    ('不同 host', 'https://api.example.com/v1', 'https://api.other.com/v1'),
    ('不同 scheme', 'https://api.example.com/v1', 'http://api.example.com/v1'),
    ('不同 path', 'https://api.example.com/v1', 'https://api.example.com/v2'),
    ('不同 IPv6', 'http://[2001:db8::1]/v1', 'http://[2001:db8::2]/v1'),
    ('IPv6 zone id 大小写', 'https://[fe80::1%eth0]/v1', 'https://[fe80::1%ETH0]/v1'),
    ('IPv6 不同 zone', 'https://[fe80::1%eth0]/v1', 'https://[fe80::1%eth1]/v1'),
    ('host 双尾点', 'https://api.example.com../v1', 'https://api.example.com/v1'),
    ('写坏但不同', 'http://[::1/v1', 'http://[::2/v1'),
]


@pytest.mark.unit
@pytest.mark.parametrize('label,a,b', SAME, ids=[c[0] for c in SAME])
def test_cosmetic_differences_keep_one_identity(label, a, b):
    assert same_endpoint(a, b) is True, f'{label}: {a!r} 应与 {b!r} 同源'


@pytest.mark.unit
@pytest.mark.parametrize('label,a,b', DIFFERENT, ids=[c[0] for c in DIFFERENT])
def test_significant_differences_split_identity(label, a, b):
    assert same_endpoint(a, b) is False, f'{label}: {a!r} 不应与 {b!r} 同源'


@pytest.mark.unit
@pytest.mark.parametrize('blank', ['', '   ', None])
def test_blank_never_matches_anything(blank):
    """A blank URL has no identity, so it matches nothing — including itself."""
    assert endpoint_identity(blank) is None
    assert same_endpoint(blank, 'https://api.example.com/v1') is False
    assert same_endpoint(blank, blank) is False


@pytest.mark.unit
@pytest.mark.parametrize('bad', [
    'http://[::1',            # 未闭合 IPv6 —— urlsplit 自己会抛
    'http://[::1/v1',
    'http://h:not-a-port/v1',  # 非数字端口 —— 读 .port 会抛
    'http://h:99999999/v1',    # 越界端口
    '::::',
    'not a url at all',
])
def test_predicate_is_total(bad):
    """It runs inside constructors and config reads; raising means nothing works."""
    endpoint_identity(bad)
    same_endpoint(bad, 'https://api.example.com/v1')
    same_endpoint('https://api.example.com/v1', bad)


@pytest.mark.unit
def test_identity_tuples_have_a_uniform_shape():
    """Parsed and unparsed branches must not rely on differing tuple lengths."""
    ok = endpoint_identity('https://api.example.com/v1')
    bad = endpoint_identity('http://[::1')
    assert len(ok) == len(bad), f'{len(ok)} vs {len(bad)}'
    assert ok[0] is True and bad[0] is False
