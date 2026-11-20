"""Behaviour tests for scripts/check_wake_word_runtime_url.py.

All HTTP traffic goes to a local ``http.server`` on 127.0.0.1; nothing here
touches the real release asset.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "check_wake_word_runtime_url", ROOT / "scripts" / "check_wake_word_runtime_url.py"
)
assert _SPEC is not None and _SPEC.loader is not None
checker = importlib.util.module_from_spec(_SPEC)
# dataclasses resolves annotations through sys.modules.
sys.modules.setdefault(_SPEC.name, checker)
_SPEC.loader.exec_module(checker)

PAYLOAD = b"neko-wheel-" * 1000
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


class _Handler(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict[str, str]]]

    def log_message(self, *args):  # keep pytest output quiet
        pass

    def _record(self):
        self.server.requests.append((self.command, self.path, dict(self.headers.items())))

    def _send_wheel(self, with_body: bool):
        body = self.server.payload
        if self.headers.get("Range") == "bytes=0-0" and not self.server.ignore_range:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes 0-0/{len(body)}")
            self.send_header("Content-Length", "1")
            self.end_headers()
            if with_body:
                self.wfile.write(body[:1])
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _route(self, with_body: bool):
        self._record()
        path = self.path
        if path in ("/redirect", "/redirect-to-head403"):
            self.send_response(302)
            self.send_header("Location", "/wheel" if path == "/redirect" else "/head403")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/wheel" or (path == "/head403" and self.command == "GET"):
            self._send_wheel(with_body)
        elif path in ("/head403", "/head403-get404") and self.command == "HEAD":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_HEAD(self):
        self._route(with_body=False)

    def do_GET(self):
        self._route(with_body=True)


@pytest.fixture
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.requests = []
    httpd.payload = PAYLOAD
    httpd.ignore_range = False
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def opener():
    # Bypass any proxy configured in the environment so traffic stays local.
    return checker.build_opener(urllib.request.ProxyHandler({}))


def _url(server, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _pins(server, path: str, *, size: int = len(PAYLOAD), sha256: str = PAYLOAD_SHA256):
    return checker.WheelPins(url=_url(server, path), sha256=sha256, size=size)


# --- constant parsing -----------------------------------------------------


def test_real_runtime_bundle_constants_parse():
    pins = checker.load_pins()
    assert pins.url.startswith("https://github.com/Project-N-E-K-O/N.E.K.O/releases/download/")
    assert pins.url.endswith(".whl")
    assert len(pins.sha256) == 64
    assert pins.size > 0


def test_parse_follows_name_concatenation(tmp_path):
    bundle = tmp_path / "runtime_bundle.py"
    bundle.write_text(
        'import os\n'
        'WHEEL_NAME = "pkg-1.0-py3-none-any.whl"\n'
        'WHEEL_URL = ("https://example.invalid/releases/download/"\n'
        '             "tag/" + WHEEL_NAME)\n'
        f'WHEEL_SHA256 = "{"a" * 64}"\n'
        'WHEEL_BYTES: int = 1024 * 4\n'
        'OTHER = os.getcwd()\n',
        encoding="utf-8",
    )
    pins = checker.load_pins(bundle)
    assert pins == checker.WheelPins(
        url="https://example.invalid/releases/download/tag/pkg-1.0-py3-none-any.whl",
        sha256="a" * 64,
        size=4096,
    )


@pytest.mark.parametrize(
    "source",
    [
        'WHEEL_URL = "https://x.invalid/a.whl"\nWHEEL_SHA256 = "' + "a" * 64 + '"\n',  # no WHEEL_BYTES
        'WHEEL_URL = "http://x.invalid/a.whl"\nWHEEL_SHA256 = "' + "a" * 64 + '"\nWHEEL_BYTES = 1\n',
        'WHEEL_URL = "https://x.invalid/a.whl"\nWHEEL_SHA256 = "abc"\nWHEEL_BYTES = 1\n',
        'WHEEL_URL = make_url()\nWHEEL_SHA256 = "' + "a" * 64 + '"\nWHEEL_BYTES = 1\n',
    ],
    ids=["missing-bytes", "not-https", "bad-sha", "unsupported-expr"],
)
def test_parse_rejects_bad_constants(tmp_path, source):
    bundle = tmp_path / "runtime_bundle.py"
    bundle.write_text(source, encoding="utf-8")
    with pytest.raises(checker.CheckError):
        checker.load_pins(bundle)


# --- head mode ------------------------------------------------------------


def test_head_2xx_passes(server, opener):
    assert checker.check_head(_pins(server, "/wheel"), opener=opener).startswith("HEAD 200")
    assert [(method, path) for method, path, _ in server.requests] == [("HEAD", "/wheel")]


def test_head_404_fails(server, opener):
    with pytest.raises(checker.CheckError, match="HTTP 404"):
        checker.check_head(_pins(server, "/missing"), opener=opener)
    # A 404 is final: no ranged GET fallback.
    assert [method for method, _, _ in server.requests] == ["HEAD"]


def test_head_follows_redirect_and_keeps_head(server, opener):
    checker.check_head(_pins(server, "/redirect"), opener=opener)
    assert [(method, path) for method, path, _ in server.requests] == [("HEAD", "/redirect"), ("HEAD", "/wheel")]


def test_head_403_falls_back_to_one_byte_ranged_get(server, opener):
    result = checker.check_head(_pins(server, "/redirect-to-head403"), opener=opener)
    assert result.startswith("ranged GET 206")
    calls = [(method, path, headers.get("Range")) for method, path, headers in server.requests]
    assert calls == [
        ("HEAD", "/redirect-to-head403", None),
        ("HEAD", "/head403", None),
        ("GET", "/redirect-to-head403", "bytes=0-0"),
        ("GET", "/head403", "bytes=0-0"),  # Range survives the redirect
    ]


def test_ranged_get_ignored_range_still_passes_without_reading_body(server, opener):
    server.ignore_range = True
    assert checker.check_head(_pins(server, "/head403"), opener=opener).startswith("ranged GET 200")


def test_ranged_get_failure_is_reported(server, opener):
    with pytest.raises(checker.CheckError, match="ranged GET failed: HTTP 404"):
        checker.check_head(_pins(server, "/head403-get404"), opener=opener)
    assert [method for method, _, _ in server.requests] == ["HEAD", "GET"]


@pytest.mark.parametrize("path", ["/wheel", "/redirect-to-head403"], ids=["head-length", "content-range"])
def test_head_mode_rejects_reported_size_mismatch(server, opener, path):
    with pytest.raises(checker.CheckError, match="size mismatch"):
        checker.check_head(_pins(server, path, size=len(PAYLOAD) + 1), opener=opener)


def test_requests_are_anonymous(server, opener):
    checker.check_head(_pins(server, "/redirect-to-head403"), opener=opener)
    checker.check_full(_pins(server, "/redirect"), opener=opener)
    for _, _, headers in server.requests:
        assert "Authorization" not in headers
        assert headers.get("User-Agent") == checker.USER_AGENT


# --- full mode ------------------------------------------------------------


def test_full_passes_on_matching_size_and_hash(server, opener):
    result = checker.check_full(_pins(server, "/redirect"), opener=opener)
    assert f"downloaded {len(PAYLOAD)} bytes" in result


def test_full_404_fails(server, opener):
    with pytest.raises(checker.CheckError, match="HTTP 404"):
        checker.check_full(_pins(server, "/missing"), opener=opener)


@pytest.mark.parametrize("delta", [1, -1], ids=["server-shorter", "server-longer"])
def test_full_size_mismatch_fails(server, opener, delta):
    with pytest.raises(checker.CheckError, match="size mismatch"):
        checker.check_full(_pins(server, "/wheel", size=len(PAYLOAD) + delta), opener=opener)


def test_full_hash_mismatch_fails(server, opener):
    with pytest.raises(checker.CheckError, match="SHA256 mismatch"):
        checker.check_full(_pins(server, "/wheel", sha256="0" * 64), opener=opener)


# --- CLI ------------------------------------------------------------------


def _write_bundle(tmp_path, url: str, sha256: str = PAYLOAD_SHA256, size: int = len(PAYLOAD)) -> Path:
    bundle = tmp_path / "runtime_bundle.py"
    bundle.write_text(
        f'WHEEL_URL = "{url}"\nWHEEL_SHA256 = "{sha256}"\nWHEEL_BYTES = {size}\n', encoding="utf-8"
    )
    return bundle


@pytest.mark.parametrize("mode", ["--head", "--full"])
@pytest.mark.parametrize(("target", "code", "marker"), [("/redirect", 0, "OK:"), ("/missing", 1, "FAIL:")])
def test_cli_exit_codes(server, opener, tmp_path, capsys, monkeypatch, mode, target, code, marker):
    # load_pins only accepts https URLs, so parse a real bundle file and then
    # point the parsed pins at the local server.
    bundle = _write_bundle(tmp_path, "https://placeholder.invalid/wheel")
    original = checker.load_pins
    monkeypatch.setattr(
        checker, "load_pins",
        lambda path: checker.WheelPins(url=_url(server, target), sha256=original(path).sha256,
                                       size=original(path).size),
    )
    assert checker.main([mode, "--bundle", str(bundle)], opener=opener) == code
    assert marker in capsys.readouterr().out


def test_cli_unparsable_bundle_fails(tmp_path, capsys):
    bundle = tmp_path / "runtime_bundle.py"
    bundle.write_text("WHEEL_URL = (\n", encoding="utf-8")
    assert checker.main(["--head", "--bundle", str(bundle)]) == 1
    assert "FAIL: cannot parse" in capsys.readouterr().out
