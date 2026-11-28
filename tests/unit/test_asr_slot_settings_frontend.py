"""Run the ASR slot settings harness, and keep its URL check in step with the server's."""

import json
import os
import shutil
from pathlib import Path

import pytest

from main_logic.asr_client.slot import normalize_custom_asr_url
from tests.node_harness import run_node_stdin

ROOT = Path(__file__).resolve().parents[2]

# The page refuses to save what the server would refuse, so both must agree on
# which URLs are usable. URL() lower-cases the host and drops a default port,
# so exact output is compared only where the input is already in that form.
URLS = [
    "http://lan-box:8081", "http://lan-box:8081/", "http://lan-box:8081/v1", "http://lan-box:8081/v1/",
    "https://asr.example/api/v2", " https://asr.example/v1/audio/transcriptions ",
    "http://localhost:8081", "http://[::1]:8081", "https://asr.example/v1?tenant=a",
    "http://192.168.1.5:8081/v1#frag", "http://user:pw@lan-box:8081/v1",
    "", "lan-box:8081", "ftp://lan-box/v1", "ws://lan-box:8081/v1", "http://:8081", "http://user@:8081",
    "http:///v1", "http://lan-box:0", "http://lan-box:99999", "http://lan-box:abc", "http://[::1",
    "http://lan box:8081", "http://lan-box:8081/v 1",
]
SPELLED_DIFFERENTLY = [
    "HTTP://LAN-BOX:8081/v1", "https://asr.example:443/v1", "http://lan-box:/v1",
]

_NORMALIZE_SCRIPT = """
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({ URL, window: {}, document: {} });
vm.runInContext(fs.readFileSync('static/js/asr_slot_settings.js', 'utf8'), context);
const urls = __URLS__;
process.stdout.write(JSON.stringify(urls.map(url => context.normalizeCustomAsrUrl(url))));
"""


def _node() -> str:
    node = shutil.which("node")
    if not node and not os.environ.get("CI"):
        pytest.skip("Node.js is not installed locally")
    assert node, "Node.js is required for the frontend behavior tests"
    return node


def test_asr_slot_settings_frontend():
    result = run_node_stdin(
        _node(), "require('./tests/frontend/asr_slot_settings.test.cjs');",
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_page_and_server_agree_on_custom_asr_urls():
    urls = URLS + SPELLED_DIFFERENTLY
    script = _NORMALIZE_SCRIPT.replace("__URLS__", json.dumps(urls))
    result = run_node_stdin(_node(), script, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    page = dict(zip(urls, json.loads(result.stdout)))
    for url in urls:
        server = normalize_custom_asr_url(url)
        assert bool(page[url]) == bool(server), (url, page[url], server)
        if url in URLS:
            assert page[url] == server, url
