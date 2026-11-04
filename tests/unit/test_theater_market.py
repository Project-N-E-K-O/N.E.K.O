"""Market imports install packages without starting a performance."""

import asyncio
import hashlib
import json
import time
from copy import deepcopy

import httpx
import pytest

from main_routers import theater_market_router as market
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_router import _client
from utils.theater_activity import clear_all_theater_activity


@pytest.fixture
def setup(tmp_path, monkeypatch):
    market._launches.clear()
    clear_all_theater_activity()
    client = _client(tmp_path, monkeypatch)
    client.base_url = "http://127.0.0.1:48911"
    client.app.include_router(market.router)
    binding = market.theater._current_catgirl_binding(market.theater.get_config_manager())
    value = {"request_id": "test_request", "character_id": binding["character_id"],
             "phase": "browsing",
             "selection": None, "lock": asyncio.Lock(), "expires": time.monotonic() + 60}
    market._launches[value["request_id"]] = value
    story = numeric_v2_story()
    story["meta"]["story_id"] = "market_new_story"
    raw = json.dumps(story, ensure_ascii=False).encode()
    descriptor = {"id": 9, "revision": 2, "file_sha256": hashlib.sha256(raw).hexdigest(),
                  "title": "A script", "byte_size": len(raw)}
    async def selected(*args):
        return deepcopy(descriptor)
    monkeypatch.setattr(market, "market_story", selected)
    yield client, tmp_path, value, raw, descriptor
    market._launches.clear()
    clear_all_theater_activity()


def import_package(client, raw, descriptor):
    return client.post("/api/theater-market/launch/test_request/import", content=raw,
        params={"share_id": descriptor["id"], "revision": descriptor["revision"],
                "file_sha256": descriptor["file_sha256"]})


def test_import_is_idempotent_and_never_starts_a_session(setup):
    client, root, value, raw, descriptor = setup
    with client:
        imported = import_package(client, raw, descriptor)
        assert imported.status_code == 200, imported.text
        assert imported.json()['phase'] == 'imported'
        assert imported.json()['story_id'] == 'market_new_story'
        path = root / 'theater/numeric_v2/packages/market_new_story.json'
        original = path.read_bytes()
        assert import_package(client, raw, descriptor).json()['phase'] == 'imported'
        assert path.read_bytes() == original
        assert not list((root / 'theater/numeric_v2/sessions').glob('*.json'))
        # A stale Market client cannot bypass the local selector and auto-start.
        for action in ('claim', 'complete', 'failed', 'retry'):
            assert client.post('/api/theater-market/launch/test_request/' + action, json={}).status_code == 404


def test_import_rejects_hash_revision_and_identity_changes_before_writing(setup, monkeypatch):
    client, root, value, raw, descriptor = setup
    target = root / "theater/numeric_v2/packages/market_new_story.json"
    assert import_package(client, raw + b" ", descriptor).json()["reason"] == "theater_market_hash_mismatch"
    bad = dict(descriptor, revision=1)
    assert import_package(client, raw, bad).json()["reason"] == "theater_market_file_changed"
    assert not target.exists()
    monkeypatch.setattr(market.theater, "_current_catgirl_binding", lambda *_: {"character_id": "other-cat"})
    assert import_package(client, raw, descriptor).json()["reason"] == "catgirl_changed_requires_new_session"
    assert not target.exists()


@pytest.mark.parametrize("raw", [b"{", b"\xff", b"[" * 1100 + b"]" * 1100], ids=["syntax", "encoding", "nesting"])
def test_invalid_market_json_returns_stable_error_without_writing(setup, raw):
    client, root, value, _, descriptor = setup
    descriptor.update(file_sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))
    response = import_package(client, raw, descriptor)
    assert response.status_code == 422
    assert response.json()["reason"] == "numeric_story_package_invalid_json"
    assert value["phase"] == "browsing"
    assert not (root / 'theater/numeric_v2/packages/market_new_story.json').exists()


def test_local_new_start_replaces_skipped_ended_run_but_preserves_active_run(setup):
    client, _, value, raw, descriptor = setup
    scope = {'story_id': 'market_new_story', 'character_id': value['character_id']}
    with client:
        assert import_package(client, raw, descriptor).status_code == 200
        first = client.post('/api/theater-numeric/session/start', json={
            **scope, 'session_id': 'previous-run', 'replace_existing': True,
        }).json()
        assert first['session']['session_id'] == 'previous-run'
        ended = client.post('/api/theater-numeric/session/end', json={
            **scope, 'session_id': 'previous-run', 'base_revision': first['session']['revision'],
            'base_lifecycle_revision': first['session']['lifecycle_revision'],
        }).json()
        skipped = client.post('/api/theater-numeric/session/archive/skip', json={
            **scope, 'session_id': 'previous-run', 'revision': ended['session']['revision'],
            'end_receipt_id': ended['end_receipt_id'],
        })
        assert skipped.status_code == 200, skipped.text
        assert skipped.json()['status'] == 'skipped'
        fresh = client.post('/api/theater-numeric/session/start', json={
            **scope, 'session_id': 'local-new-session', 'replace_existing': True,
        }).json()
        assert fresh.get('resumed') is not True
        assert fresh['session']['session_id'] == 'local-new-session'
        assert fresh['session']['status'] == 'active'
        assert fresh['session']['revision'] == 0
        refused = client.post('/api/theater-numeric/session/start', json={
            **scope, 'session_id': 'cannot-overwrite-active', 'replace_existing': True,
        })
        assert refused.json()['reason'] == 'numeric_active_session_cannot_restart'


def test_same_id_different_package_never_overwrites(setup):
    client, root, value, raw, descriptor = setup
    assert import_package(client, raw, descriptor).status_code == 200
    path = root / "theater/numeric_v2/packages/market_new_story.json"
    original = path.read_bytes()
    story = json.loads(raw)
    story["meta"]["revision"] = "different"
    raw = json.dumps(story).encode()
    descriptor.update(file_sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))
    value.update(phase="browsing", selection=None)
    assert import_package(client, raw, descriptor).json()["reason"] == "numeric_story_exists"
    assert path.read_bytes() == original


def test_expired_request_and_local_mutation_checks(setup, monkeypatch):
    client, _, value, raw, descriptor = setup
    monkeypatch.setattr(market.theater, "_validate_local_mutation_request",
                        lambda *a, **kw: market.theater._error("csrf_validation_failed", 403))
    assert import_package(client, raw, descriptor).status_code == 403
    assert client.post("/api/theater-market/launch", json={}).status_code == 403
    value["expires"] = time.monotonic() - 1
    assert client.get("/api/theater-market/launch/test_request").status_code == 409


def test_market_bootstrap_uses_real_pair_server_and_hash_router(setup, monkeypatch):
    client, _, _, _, descriptor = setup
    from config import network
    from utils import market_config
    monkeypatch.setattr(network, "resolve_user_plugin_base", lambda: "http://127.0.0.1:19011")
    monkeypatch.setattr(market_config, "get_market_web_url", lambda: "http://127.0.0.1:15173")
    class PairClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            assert url == "http://127.0.0.1:19011/market/pair-code"
            assert kwargs["headers"]["Origin"] == "http://127.0.0.1:19011"
            return httpx.Response(200, json={"one_time_code": "single-use-code"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "AsyncClient", PairClient)
    result = client.post("/api/theater-market/launch", json={"random": True})
    assert result.status_code == 200, result.text
    from urllib.parse import urlsplit, parse_qs
    url = urlsplit(result.json()["url"])
    assert url.path == "/" and not url.query
    route, query = url.fragment.split("?", 1)
    assert route == "/shares/9"
    assert parse_qs(query)["neko_code"] == ["single-use-code"]
    assert market._launches[result.json()["request_id"]]["selection"] == descriptor


def test_lan_market_entry_is_rejected_before_pairing(setup, monkeypatch):
    client, _, _, _, _ = setup
    async def forbidden(*args, **kwargs):
        raise AssertionError("LAN requests must not issue a pairing code")
    monkeypatch.setattr(market, "market_story", forbidden)
    client.base_url = "http://192.168.1.100:48911"
    response = client.post("/api/theater-market/launch", json={"random": True})
    assert response.status_code == 400
    assert response.json()["reason"] == "theater_market_local_required"


@pytest.mark.asyncio
async def test_random_market_selection_includes_only_public_opening_for_synopsis(monkeypatch):
    from services.theater.market_selection import market_story
    seen = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url):
            seen.append(url)
            return httpx.Response(200, request=httpx.Request('GET', url), json={
                'id': 7, 'revision': 4, 'file_sha256': 'a' * 64, 'title': '猫灯', 'byte_size': 100,
                'kind': 'theater', 'status': 'published', 'theater_compatible': True,
                'description': 'Marketing text must not replace the public premise',
                'preview': {'intro': {'background': '你在末班列车的月台遇见守灯人。'},
                            'nodes': [{'ending': 'hidden ending'}]}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **_: Client())
    selected = await market_story()
    assert seen[0].endswith('/api/v1/shares/theater/random')
    assert selected['background'] == '你在末班列车的月台遇见守灯人。'
    assert set(selected) == {'id', 'revision', 'file_sha256', 'title', 'byte_size', 'background'}
