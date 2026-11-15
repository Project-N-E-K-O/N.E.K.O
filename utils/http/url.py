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


# The scheme's optional default port: specifying or omitting it identifies the same endpoint.
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
        # DNS names are case-insensitive; the trailing dot denotes the DNS root, so `api.example.com.` and
        # `api.example.com` are the same host. Remove only one trailing dot, matching the rule for trailing path slashes:
        # `example.com..` is invalid and must remain distinct from its single-dot form. Prefer treating them as
        # different endpoints to over-normalizing beyond our authority.
        return host.lower().removesuffix('.')
    import ipaddress

    # A scoped IPv6 zone ID (%eth0) is an **interface name**, case-sensitive on Unix,
    # and must not be case-folded with the address; that could merge distinct interfaces and send credentials to the wrong endpoint.
    inner = host[1:-1]
    addr, sep, zone = inner.partition('%')
    try:
        canonical = ipaddress.IPv6Address(addr).compressed
    except ValueError:
        # Preserve unparsable input verbatim, including case, keeping this criterion total and treating differently written
        # invalid addresses as distinct endpoints.
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
        # Both branches return tuples of **equal length**, beginning with parsing success: unequal-length
        # tuples also always compare unequal, but only coincidentally, rather than by the intended criterion.
        return (False, text, '', '', '', '', '')

    # Lowercasing all of netloc is incorrect because it contains userinfo (user:pass@). Separate userinfo
    # from host:port and case-fold only the latter. Intentionally avoid parts.port, which raises on invalid ports.
    userinfo, _, hostinfo = (parts.netloc or '').rpartition('@')
    scheme = (parts.scheme or '').lower()
    # Do not lowercase all hostinfo here: it may include an IPv6 zone ID, a case-sensitive interface name.
    # Let _canonical_host normalize case component by component.
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
