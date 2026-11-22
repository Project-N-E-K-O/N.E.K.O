"""Security tests for tfLink HTTPS & SSRF allowlist validation (F0002)."""

from __future__ import annotations

import io
import json

import httpx
import pytest

import config
from utils.voice_clone import QwenVoiceCloneClient, QwenVoiceCloneError


def test_config_tflink_defaults_use_https_and_domain_allowlist():
    """F0002: default tfLink upload URL and allowlist must use HTTPS domains, never plaintext raw IP."""
    assert config.TFLINK_UPLOAD_URL == "https://tmpfile.link/api/upload"
    assert config.TFLINK_UPLOAD_URL.startswith("https://")
    assert config.TFLINK_ALLOWED_HOSTS == ["tmpfile.link", "d.tmpfile.link"]
    assert "47.101.214.205" not in config.TFLINK_ALLOWED_HOSTS


async def test_upload_file_rejects_plaintext_or_private_upload_url_before_network(monkeypatch):
    """upload_file must reject non-HTTPS or private/loopback upload URLs before making HTTP calls."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"downloadLink": "https://d.tmpfile.link/public/a.wav"})

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    for bad_url in (
        "http://tmpfile.link/api/upload",
        "https://127.0.0.1/api/upload",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.1/api/upload",
        "https://user:pass@tmpfile.link/api/upload",
        "ftp://tmpfile.link/api/upload",
        "https://tmpfile.link/api/upload\r\n",
        "https://tmpfile.link/api/\tupload",
        "https://tmpfile.link/api/upload\x00",
        "https://tmpfile.link/api/upload\x7f",
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
        "http://d.tmpfile.link/public/a.wav",
        "http://127.0.0.1/secret",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/internal.wav",
        "https://tmpfile.link@evil.example/a.wav",
        "https://evil.example/a.wav",
        "https://sub.evil.example/tmpfile.link/a.wav",
    ],
)
async def test_upload_file_rejects_disallowed_download_link_before_head(monkeypatch, bad_download_link):
    """upload_file must validate the response downloadLink against HTTPS + allowlist before issuing HEAD."""
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


async def test_upload_file_rejects_redirect_to_off_allowlist_host(monkeypatch):
    """HEAD 3xx redirect pointing outside TFLINK_ALLOWED_HOSTS must be rejected (follow_redirects=False)."""
    urls_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls_seen.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(200, json={"downloadLink": "https://d.tmpfile.link/public/a.wav"})
        if request.method == "HEAD":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
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

    assert urls_seen == ["https://tmpfile.link/api/upload", "https://d.tmpfile.link/public/a.wav"]


async def test_upload_file_accepts_allowlisted_https_download_link(monkeypatch):
    """Valid HTTPS upload + allowlisted HTTPS downloadLink passes validation and returns tmp_url."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.method == "POST":
            return httpx.Response(
                200,
                content=json.dumps({"downloadLink": "https://d.tmpfile.link/public/ok.wav"}).encode(),
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
    assert result == "https://d.tmpfile.link/public/ok.wav"
    assert seen == [
        ("POST", "https://tmpfile.link/api/upload"),
        ("HEAD", "https://d.tmpfile.link/public/ok.wav"),
    ]
