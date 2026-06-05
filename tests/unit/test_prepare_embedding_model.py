# -*- coding: utf-8 -*-
"""Unit tests for scripts/prepare_embedding_model.py — the build-time embedding
model downloader.

Focus is the mirror-fallback layer added to survive huggingface.co's per-IP
HTTP 429 throttling of shared CI egress IPs: each file is tried against every
endpoint in order (huggingface.co -> hf-mirror.com by default), each endpoint
getting its own bounded backoff retry, and only an all-source failure raises.

Pure stdlib + mock: no network, no numpy/onnxruntime, so this runs on every
workstation regardless of the embedding bundle state.
"""
from __future__ import annotations

import importlib.util
import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prepare_embedding_model.py"


@pytest.fixture
def prep(monkeypatch):
    """Load the script as a module and neutralize real backoff sleeps."""
    spec = importlib.util.spec_from_file_location("prepare_embedding_model", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    # Default env: tests opt into HF_ENDPOINT(S) explicitly where relevant.
    monkeypatch.delenv("HF_ENDPOINTS", raising=False)
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    return module


class _FakeResp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "err", {}, None)


def _install_urlopen(monkeypatch, prep, behavior, *, record=None):
    """Patch the module's urlopen with a callable mapping url -> bytes | Exception."""

    def fake_urlopen(request, timeout=120):
        url = request.full_url if hasattr(request, "full_url") else request
        if record is not None:
            record.append(url)
        result = behavior(url)
        if isinstance(result, Exception):
            raise result
        return _FakeResp(result)

    monkeypatch.setattr(prep.urllib.request, "urlopen", fake_urlopen)


# --- endpoint resolution ---------------------------------------------------

def test_endpoints_default(prep):
    assert prep._endpoints() == ["https://huggingface.co", "https://hf-mirror.com"]


def test_hf_endpoint_pins_single_mirror(prep, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")
    assert prep._endpoints() == ["https://hf-mirror.com"]


def test_hf_endpoints_list_takes_precedence_and_is_cleaned(prep, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "https://ignored.example")
    monkeypatch.setenv("HF_ENDPOINTS", "https://a.example/ , https://b.example ,, ")
    assert prep._endpoints() == ["https://a.example", "https://b.example"]


# --- download fallback -----------------------------------------------------

def test_falls_back_to_mirror_on_persistent_429(prep, monkeypatch, tmp_path):
    def behavior(url):
        if "huggingface.co" in url:
            return _http_error(url, 429)
        return b"FROM-MIRROR"

    _install_urlopen(monkeypatch, prep, behavior)
    dest = tmp_path / "tokenizer.json"
    prep._download(
        "tokenizer.json", dest, repo="r/m", revision="abc",
        endpoints=["https://huggingface.co", "https://hf-mirror.com"], force=False,
    )
    assert dest.read_bytes() == b"FROM-MIRROR"


def test_non_retryable_404_on_first_source_still_falls_through(prep, monkeypatch, tmp_path):
    def behavior(url):
        if "huggingface.co" in url:
            return _http_error(url, 404)
        return b"OK2"

    _install_urlopen(monkeypatch, prep, behavior)
    dest = tmp_path / "x"
    prep._download(
        "x", dest, repo="r/m", revision="abc",
        endpoints=["https://huggingface.co", "https://hf-mirror.com"], force=False,
    )
    assert dest.read_bytes() == b"OK2"


def test_all_sources_failing_raises_listing_every_source(prep, monkeypatch, tmp_path):
    _install_urlopen(monkeypatch, prep, lambda url: _http_error(url, 429))
    with pytest.raises(RuntimeError) as excinfo:
        prep._download(
            "f.bin", tmp_path / "f.bin", repo="r/m", revision="abc",
            endpoints=["https://huggingface.co", "https://hf-mirror.com"], force=False,
        )
    message = str(excinfo.value)
    assert "all 2 source(s)" in message
    assert "huggingface.co" in message and "hf-mirror.com" in message


def test_primary_success_does_not_contact_mirror(prep, monkeypatch, tmp_path):
    seen: list[str] = []
    _install_urlopen(monkeypatch, prep, lambda url: b"PRIMARY", record=seen)
    dest = tmp_path / "x"
    prep._download(
        "x", dest, repo="r/m", revision="abc",
        endpoints=["https://huggingface.co", "https://hf-mirror.com"], force=False,
    )
    assert dest.read_bytes() == b"PRIMARY"
    assert len(seen) == 1 and "huggingface.co" in seen[0]


def test_existing_nonempty_file_is_kept_without_any_request(prep, monkeypatch, tmp_path):
    dest = tmp_path / "x"
    dest.write_bytes(b"already-here")

    def explode(url):
        raise AssertionError("should not hit the network when file already exists")

    _install_urlopen(monkeypatch, prep, explode)
    prep._download(
        "x", dest, repo="r/m", revision="abc",
        endpoints=["https://huggingface.co", "https://hf-mirror.com"], force=False,
    )
    assert dest.read_bytes() == b"already-here"


def test_sends_stable_user_agent(prep, monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    def fake_urlopen(request, timeout=120):
        captured["ua"] = request.get_header("User-agent")
        return _FakeResp(b"data")

    monkeypatch.setattr(prep.urllib.request, "urlopen", fake_urlopen)
    prep._download_one("https://huggingface.co/r/m/resolve/abc/x", tmp_path / "x")
    assert captured["ua"] == prep._USER_AGENT
