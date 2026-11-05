'''Remote (GitHub) reads behind the read-only publish status.'''

from __future__ import annotations

import asyncio

import httpx
import pytest

from plugin.server.application import plugin_publish_status as publish_status

BASE = '/repos/owner/repo'
VERIFY = BASE + '/actions/workflows/verify.yml/runs'
RELEASE = BASE + '/releases/tags/v1.0.0'
HEAD = 'a' * 40
ASSETS = ('demo.neko-plugin', 'demo.market-release-check.txt', 'market-evidence.json')
PUBLIC = (200, {'private': False}, None)
LIMITED = (403, {}, {'x-ratelimit-remaining': '0'})


class Github:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        entry = self.routes.get(request.url.path, (404, {}, None))
        if isinstance(entry, Exception):
            raise entry
        status, payload, headers = entry
        return httpx.Response(status, json=payload, headers=headers)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    publish_status._cache.clear()
    monkeypatch.delenv('GH_TOKEN', raising=False)
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    yield
    publish_status._cache.clear()


def _serve(monkeypatch, routes):
    github = Github(routes)
    transport = httpx.MockTransport(github.handler)

    class Shim:
        Timeout = httpx.Timeout
        HTTPError = httpx.HTTPError

        @staticmethod
        def AsyncClient(**kwargs):
            return httpx.AsyncClient(transport=transport, **kwargs)

    async def fake_httpx():
        return Shim

    monkeypatch.setattr(publish_status, 'ensure_httpx', fake_httpx)
    return github


def _read(head=HEAD):
    return asyncio.run(publish_status._read_remote('owner/repo', head, 'v1.0.0', 'demo'))


def _runs(status, conclusion=None):
    return (200, {'workflow_runs': [{'status': status, 'conclusion': conclusion}]}, None)


def _asset(name, *, state='uploaded', size=10):
    return {'name': name, 'state': state, 'size': size}


def _release(*assets):
    return (200, {'assets': list(assets)}, None)


def test_public_repository_with_passing_verify_and_complete_release(monkeypatch):
    github = _serve(
        monkeypatch,
        {
            BASE: PUBLIC,
            VERIFY: _runs('completed', 'success'),
            RELEASE: _release(*[_asset(name) for name in ASSETS]),
        },
    )
    assert _read() == {
        'rate_limited': False,
        'visibility': 'public',
        'verify': 'success',
        'release': {'state': 'published', 'missing_assets': []},
    }
    # Only the public GitHub API is ever contacted.
    assert {request.url.host for request in github.requests} == {'api.github.com'}


def test_missing_repository_workflow_and_release_are_reported_as_such(monkeypatch):
    _serve(monkeypatch, {})
    result = _read()
    assert result['visibility'] == 'not_found'
    assert result['verify'] == 'missing'
    assert result['release'] == {'state': 'missing', 'missing_assets': []}
    assert result['rate_limited'] is False


def test_private_repository_is_reported_as_private(monkeypatch):
    _serve(monkeypatch, {BASE: (200, {'private': True}, None)})
    assert _read()['visibility'] == 'private'


@pytest.mark.parametrize(
    ('entry', 'expected'),
    [
        (_runs('in_progress'), 'running'),
        (_runs('completed', 'failure'), 'failure'),
        (_runs('completed', 'cancelled'), 'failure'),
        ((200, {'workflow_runs': []}, None), 'not_run'),
    ],
)
def test_verify_state_follows_the_latest_run_of_this_commit(monkeypatch, entry, expected):
    _serve(monkeypatch, {BASE: PUBLIC, VERIFY: entry})
    assert _read()['verify'] == expected


def test_release_assets_that_are_not_uploaded_or_empty_count_as_missing(monkeypatch):
    _serve(
        monkeypatch,
        {
            RELEASE: _release(
                _asset(ASSETS[0]),
                _asset(ASSETS[1], state='starter'),
                _asset(ASSETS[2], size=0),
            )
        },
    )
    assert _read()['release'] == {
        'state': 'published',
        'missing_assets': [ASSETS[1], ASSETS[2]],
    }


@pytest.mark.parametrize('entry', [LIMITED, (429, {}, None)])
def test_rate_limit_leaves_remote_steps_unknown_and_is_not_cached(monkeypatch, entry):
    github = _serve(monkeypatch, {BASE: entry, VERIFY: entry, RELEASE: entry})
    result = _read()
    assert result['rate_limited'] is True
    assert result['visibility'] == 'unknown'
    assert result['verify'] == 'unknown'
    assert result['release'] == {'state': 'unknown', 'missing_assets': []}
    first = len(github.requests)
    _read()
    assert len(github.requests) > first


def test_forbidden_without_an_exhausted_quota_is_not_a_rate_limit(monkeypatch):
    forbidden = (403, {}, {'x-ratelimit-remaining': '12'})
    _serve(monkeypatch, {BASE: forbidden})
    result = _read()
    assert result['rate_limited'] is False
    assert result['visibility'] == 'unknown'


def test_answers_are_cached_per_repository_commit_and_tag(monkeypatch):
    github = _serve(
        monkeypatch,
        {BASE: PUBLIC, VERIFY: _runs('completed', 'success'), RELEASE: _release()},
    )
    first = _read()
    assert len(github.requests) == 3
    assert _read() == first
    assert len(github.requests) == 3
    # Another commit is a different question.
    _read(head='b' * 40)
    assert len(github.requests) == 6


def test_network_failures_leave_everything_unknown_without_raising(monkeypatch):
    error = httpx.ConnectError('boom')
    _serve(monkeypatch, {BASE: error, VERIFY: error, RELEASE: error})
    result = _read()
    assert result['rate_limited'] is False
    assert result['visibility'] == 'unknown'
    assert result['verify'] == 'unknown'
    assert result['release']['state'] == 'unknown'


def test_verify_is_not_read_without_a_head_commit(monkeypatch):
    github = _serve(monkeypatch, {BASE: PUBLIC})
    result = _read(head=None)
    assert result['verify'] == 'unknown'
    assert all(not request.url.path.endswith('/runs') for request in github.requests)


def test_token_is_sent_only_when_configured(monkeypatch):
    github = _serve(monkeypatch, {BASE: PUBLIC})
    _read()
    assert all('authorization' not in request.headers for request in github.requests)
    publish_status._cache.clear()
    monkeypatch.setenv('GH_TOKEN', 'secret')
    github.requests.clear()
    _read()
    assert github.requests
    assert all(request.headers['authorization'] == 'Bearer secret' for request in github.requests)
