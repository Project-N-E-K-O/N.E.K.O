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

from urllib.parse import quote, unquote


def encode_url_path(path: str) -> str:
    """
    Safely encode URL path segments, avoiding spaces/special characters breaking static resource loading.
    Encodes only the path segments themselves, preserving the '/' separator structure.
    """
    if not path:
        return path

    parts = path.split('/')
    encoded_parts = [quote(unquote(part), safe='') for part in parts]
    return '/'.join(encoded_parts)


# 该 scheme 下可以省略的默认端口：写与不写是同一个端点。
_DEFAULT_PORTS = {'http': '80', 'https': '443', 'ws': '80', 'wss': '443'}


def _split_hostinfo(hostinfo: str) -> tuple[str, str]:
    """Split ``host[:port]`` without tripping over IPv6 brackets."""
    idx = hostinfo.rfind(':')
    if idx == -1 or hostinfo.rfind(']') > idx:
        return hostinfo, ''
    return hostinfo[:idx], hostinfo[idx + 1:]


def _canonical_host(host: str) -> str:
    """Collapse an IPv6 literal to its canonical form; pass anything else through.

    ``[2001:0db8:0:0:0:0:0:1]`` and ``[2001:db8::1]`` are the same address, so
    they must yield the same identity. A literal that does not parse keeps its
    original text — callers need this to stay total, and an unparseable host
    still compares equal to an identically-written one.
    """
    if not (host.startswith('[') and host.endswith(']')):
        # DNS 名大小写无关；尾点是 DNS 根，`api.example.com.` 与
        # `api.example.com` 是同一台主机。只去一个尾点，与 path 尾斜杠同一条
        # 规则：`example.com..` 不是合法域名，保持它与单点形式不同——宁可判成
        # 不同端点，也不越权归一。
        return host.lower().removesuffix('.')
    import ipaddress

    # scoped IPv6 的 zone id（%eth0）是**接口名**，在 Unix 上大小写敏感，
    # 不能跟着地址一起折叠——那会把两个不同接口判成同一个端点，凭证就送错了。
    inner = host[1:-1]
    addr, sep, zone = inner.partition('%')
    try:
        canonical = ipaddress.IPv6Address(addr).compressed
    except ValueError:
        # 解析不了就原样保留（含大小写）：判据保持 total，且两个写法不同的
        # 坏地址仍算不同端点。
        return host
    return f'[{canonical}{sep}{zone}]'


def endpoint_identity(raw: str | None):
    """A comparable identity for a configured base URL, or None when blank.

    Folds only what RFC 3986 says is insignificant, because callers use this to
    decide whether one provider's credential may be paired with a given URL —
    so it has to be wrong in the safe direction:

    * scheme and host are case-insensitive; userinfo, path and query are NOT
      (two tenants or routes can differ by case alone),
    * ports compare by value, and a scheme's default port is dropped,
    * exactly one trailing slash is cosmetic — ``/v1/`` equals ``/v1`` but not
      ``/v1//``, which is a different HTTP path.

    Total by construction: a URL that ``urlsplit`` rejects (unclosed IPv6) and
    one with a malformed port both yield an identity rather than raising, since
    callers run inside constructors and config reads where an exception means
    "nothing works" rather than "this endpoint is invalid".
    """
    from urllib.parse import urlsplit

    text = (raw or '').strip()
    if not text:
        return None
    if '://' not in text:
        text = f'//{text}'
    try:
        parts = urlsplit(text)
    except ValueError:
        # 两个分支返回**同样长度**的元组，首位是「解析成功与否」：长度不同的
        # 元组比起来虽然也恒不相等，但那是靠巧合而不是靠判据。
        return (False, text, '', '', '', '', '')

    # netloc 整体转小写是错的：它还装着 userinfo（user:pass@）。拆开 userinfo
    # 与 host:port，只折叠后者。刻意不碰 parts.port —— 它对非法端口会抛。
    userinfo, _, hostinfo = (parts.netloc or '').rpartition('@')
    scheme = (parts.scheme or '').lower()
    # 不在这里整体转小写：hostinfo 里可能带 scoped IPv6 的 zone id，那是接口名、
    # 大小写敏感。大小写归一交给 _canonical_host 分部位处理。
    host, port = _split_hostinfo(hostinfo)
    host = _canonical_host(host)
    if port.isdigit():
        port = str(int(port))
    if port and port == _DEFAULT_PORTS.get(scheme):
        port = ''
    return (
        True,
        scheme,
        userinfo,
        host,
        port,
        (parts.path or '').removesuffix('/'),
        parts.query,
    )


def same_endpoint(a: str | None, b: str | None) -> bool:
    """Whether two configured base URLs address the same endpoint."""
    key_a = endpoint_identity(a)
    return key_a is not None and key_a == endpoint_identity(b)
