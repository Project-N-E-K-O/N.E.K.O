"""Security tests for tfLink SSRF allowlist validation (F0002)."""

from __future__ import annotations

import io
import json

import httpx
import pytest

import config
from utils.voice_clone import QwenVoiceCloneClient, QwenVoiceCloneError


def test_config_tflink_defaults_preserve_self_hosted_allowlist():
    """F0002: default tfLink upload URL and allowlist keep the self-hosted endpoint."""
    assert config.TFLINK_UPLOAD_URL == "http://47.101.214.205:8000/api/upload"
    assert config.TFLINK_ALLOWED_HOSTS == ["47.101.214.205"]


async def test_upload_file_rejects_disallowed_or_private_upload_url_before_network(monkeypatch):
    """upload_file must reject off-allowlist HTTP or private/loopback upload URLs before making HTTP calls."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"downloadLink": "http://47.101.214.205:8000/public/a.wav"})

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    for bad_url in (
        "http://evil.example/api/upload",
        "https://127.0.0.1/api/upload",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.1/api/upload",
        "https://user:pass@tflink.example/api/upload",
        "ftp://tflink.example/api/upload",
        "https://tflink.example/api/upload\r\n",
        "https://tflink.example/api/\tupload",
        "https://tflink.example/api/upload\x00",
        "https://tflink.example/api/upload\x7f",
    ):
        client = QwenVoiceCloneClient("api-key", bad_url)
        with pytest.raises(QwenVoiceCloneError):
            client._validate_tflink_url(bad_url, require_allowlist=False)
        with pytest.raises(QwenVoiceCloneError):
            await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")
        assert called is False


@pytest.mark.parametrize(
    "bad_download_link",
    [
        "http://evil.example/public/a.wav",
        "http://127.0.0.1/secret",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/internal.wav",
        "https://47.101.214.205@evil.example/a.wav",
        "https://evil.example/a.wav",
        "https://sub.evil.example/47.101.214.205/a.wav",
    ],
)
async def test_upload_file_rejects_disallowed_download_link_before_head(monkeypatch, bad_download_link):
    """upload_file must validate the response downloadLink against allowlist + SSRF rules before issuing HEAD."""
    methods_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods_seen.append(request.method)
        return httpx.Response(200, json={"downloadLink": bad_download_link})

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    client = QwenVoiceCloneClient("api-key", config.TFLINK_UPLOAD_URL)
    with pytest.raises(QwenVoiceCloneError):
        await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")

    assert methods_seen == ["POST"]


@pytest.mark.parametrize(
    "redirect_location",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://evil.example/a.wav",
    ],
)
async def test_upload_file_rejects_redirect_to_off_allowlist_host(monkeypatch, redirect_location):
    """HEAD 3xx redirect pointing outside TFLINK_ALLOWED_HOSTS must be rejected (follow_redirects=False)."""
    urls_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls_seen.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(200, json={"downloadLink": "http://47.101.214.205:8000/public/a.wav"})
        if request.method == "HEAD":
            return httpx.Response(302, headers={"Location": redirect_location})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    client = QwenVoiceCloneClient("api-key", config.TFLINK_UPLOAD_URL)
    with pytest.raises(QwenVoiceCloneError):
        await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")

    assert urls_seen == [
        "http://47.101.214.205:8000/api/upload",
        "http://47.101.214.205:8000/public/a.wav",
    ]


async def test_upload_file_accepts_allowlisted_self_hosted_download_link(monkeypatch):
    """Valid upload + allowlisted self-hosted downloadLink passes validation and returns tmp_url."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.method == "POST":
            return httpx.Response(
                200,
                content=json.dumps({"downloadLink": "http://47.101.214.205:8000/public/ok.wav"}).encode(),
            )
        if request.method == "HEAD":
            return httpx.Response(200)
        raise AssertionError(f"Unexpected method {request.method}")

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    client = QwenVoiceCloneClient("api-key", config.TFLINK_UPLOAD_URL)
    result = await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")
    assert result == "http://47.101.214.205:8000/public/ok.wav"
    assert seen == [
        ("POST", "http://47.101.214.205:8000/api/upload"),
        ("HEAD", "http://47.101.214.205:8000/public/ok.wav"),
    ]


async def test_upload_file_follows_single_allowlisted_https_redirect_and_returns_target(monkeypatch):
    """Allowlisted HTTPS downloadLink that 302-redirects to another allowlisted host returns verified target."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.method == "POST":
            return httpx.Response(
                200,
                content=json.dumps({"downloadLink": "https://tflink.example/public/a.wav"}).encode(),
            )
        if request.method == "HEAD" and str(request.url) == "https://tflink.example/public/a.wav":
            return httpx.Response(302, headers={"Location": "https://cdn.tflink.example/public/a.wav"})
        if request.method == "HEAD" and str(request.url) == "https://cdn.tflink.example/public/a.wav":
            return httpx.Response(200)
        raise AssertionError(f"Unexpected request {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    client = QwenVoiceCloneClient(
        "api-key",
        "https://tflink.example/api/upload",
        allowed_hosts=("tflink.example", "cdn.tflink.example"),
    )
    result = await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")
    assert result == "https://cdn.tflink.example/public/a.wav"
    assert seen == [
        ("POST", "https://tflink.example/api/upload"),
        ("HEAD", "https://tflink.example/public/a.wav"),
        ("HEAD", "https://cdn.tflink.example/public/a.wav"),
    ]


@pytest.mark.parametrize("malformed_url", ["https://[::1/api/upload", "https://tflink.example:badport/api/upload"])
async def test_upload_file_converts_malformed_url_parse_errors_to_domain_error(malformed_url):
    """Malformed upload URLs (e.g., broken IPv6 brackets or port) must raise QwenVoiceCloneError, not ValueError."""
    client = QwenVoiceCloneClient("api-key", malformed_url)
    with pytest.raises(QwenVoiceCloneError):
        await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")


async def test_upload_file_follows_multihop_allowlisted_redirects_and_rejects_cycles_and_excess(monkeypatch):
    """Bounded allowlisted multi-hop redirects succeed, while cycles and excessive hops raise QwenVoiceCloneError."""
    hop_sequence: list[str] = []

    def multi_handler(request: httpx.Request) -> httpx.Response:
        hop_sequence.append(f"{request.method} {request.url}")
        if request.method == "POST":
            return httpx.Response(200, json={"downloadLink": "https://tflink.example/a"})
        url_str = str(request.url)
        if url_str.endswith("/a"):
            return httpx.Response(302, headers={"Location": "https://cdn1.tflink.example/b"})
        if url_str.endswith("/b"):
            return httpx.Response(302, headers={"Location": "https://cdn2.tflink.example/c"})
        if url_str.endswith("/c"):
            return httpx.Response(200)
        raise AssertionError(f"Unexpected request: {url_str}")

    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=httpx.MockTransport(multi_handler), **{k: v for k, v in kw.items() if k != "transport"}),
    )

    client = QwenVoiceCloneClient(
        "api-key",
        "https://tflink.example/api/upload",
        allowed_hosts=("tflink.example", "cdn1.tflink.example", "cdn2.tflink.example"),
    )
    resolved = await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")
    assert resolved == "https://cdn2.tflink.example/c"
    assert hop_sequence == [
        "POST https://tflink.example/api/upload",
        "HEAD https://tflink.example/a",
        "HEAD https://cdn1.tflink.example/b",
        "HEAD https://cdn2.tflink.example/c",
    ]

    # Redirect loop (/a -> /b -> /a) fails closed.
    def cycle_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"downloadLink": "https://tflink.example/a"})
        if str(request.url).endswith("/a"):
            return httpx.Response(302, headers={"Location": "https://tflink.example/b"})
        return httpx.Response(302, headers={"Location": "https://tflink.example/a"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=httpx.MockTransport(cycle_handler), **{k: v for k, v in kw.items() if k != "transport"}),
    )
    with pytest.raises(QwenVoiceCloneError, match="重定向循环"):
        await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")

    # Excessive hops (> 5 redirects) fails closed.
    def chain_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"downloadLink": "https://tflink.example/hop/0"})
        idx = int(str(request.url).rsplit("/", 1)[-1])
        return httpx.Response(302, headers={"Location": f"https://tflink.example/hop/{idx + 1}"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=httpx.MockTransport(chain_handler), **{k: v for k, v in kw.items() if k != "transport"}),
    )
    with pytest.raises(QwenVoiceCloneError, match="重定向次数过多"):
        await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")


def test_explicit_allowlisted_lan_ip_is_permitted_while_other_private_ips_remain_blocked():
    """Operators explicitly allowlisting a self-hosted LAN IP succeed while unallowlisted private IPs fail."""
    client = QwenVoiceCloneClient(
        "api-key",
        "http://192.168.1.10:8000/api/upload",
        allowed_hosts=("192.168.1.10",),
    )
    assert (
        client._validate_tflink_url("http://192.168.1.10:8000/api/upload", require_allowlist=False)
        == "http://192.168.1.10:8000/api/upload"
    )
    assert (
        client._validate_tflink_url("http://192.168.1.10:8000/public/a.wav", require_allowlist=True)
        == "http://192.168.1.10:8000/public/a.wav"
    )
    for blocked in ("http://192.168.1.11:8000/api/upload", "https://127.0.0.1/api/upload", "https://169.254.169.254/latest/meta-data"):
        with pytest.raises(QwenVoiceCloneError):
            client._validate_tflink_url(blocked, require_allowlist=False)


async def test_upload_file_converts_malformed_redirect_location_to_domain_error(monkeypatch):
    """Malformed Location headers (e.g. broken IPv6 brackets or control chars) must raise QwenVoiceCloneError."""
    orig_client = httpx.AsyncClient

    def bad_ipv6_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"downloadLink": "https://tflink.example/a"})
        return httpx.Response(302, headers={"Location": "https://[::1/bad"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=httpx.MockTransport(bad_ipv6_handler), **{k: v for k, v in kw.items() if k != "transport"}),
    )
    client = QwenVoiceCloneClient("api-key", "https://tflink.example/api/upload", allowed_hosts=("tflink.example",))
    with pytest.raises(QwenVoiceCloneError):
        await client.upload_file(io.BytesIO(b"RIFFdemo"), "sample.wav")
