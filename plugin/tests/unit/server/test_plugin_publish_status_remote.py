'''Remote (GitHub) reads behind the read-only publish status.'''

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from plugin.server.application import plugin_publish_status as publish_status
from tests.fake_clock import patch_module_clock

BASE = '/repos/owner/repo'
VERIFY = BASE + '/actions/workflows/verify.yml/runs'
RELEASE_WORKFLOW = BASE + '/actions/workflows/release.yml/runs'
RELEASE = BASE + '/releases/tags/v1.0.0'
TAG_REF = BASE + '/git/ref/tags/v1.0.0'
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


def _reset():
    publish_status._cache.clear()
    publish_status._inflight.clear()
    publish_status._client = None
    publish_status._rate_limited_until = 0.0


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    _reset()
    monkeypatch.delenv('GH_TOKEN', raising=False)
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    yield
    _reset()


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


def _read(head=HEAD, *, fresh=False):
    return asyncio.run(
        publish_status.read_remote_github('owner/repo', head, 'v1.0.0', 'demo', fresh=fresh)
    )


def _runs(status, conclusion=None):
    return (200, {'workflow_runs': [{'status': status, 'conclusion': conclusion, 'head_branch': 'v1.0.0', 'event': 'push'}]}, None)


def _asset(name, *, state='uploaded', size=10):
    return {'name': name, 'state': state, 'size': size, 'browser_download_url': f'https://github.com/owner/repo/{name}'}


def _release(*assets):
    return (200, {'assets': list(assets)}, None)


def _tag_ref(sha, kind='commit'):
    return (200, {'object': {'sha': sha, 'type': kind}}, None)


FINAL = {
    BASE: PUBLIC,
    VERIFY: _runs('completed', 'success'),
    RELEASE_WORKFLOW: _runs('completed', 'success'),
    RELEASE: _release(*[_asset(name) for name in ASSETS]),
    TAG_REF: _tag_ref(HEAD),
}


def test_public_repository_with_passing_verify_and_complete_release(monkeypatch):
    github = _serve(monkeypatch, FINAL)
    assert _read() == {
        'rate_limited': False,
        'reachable': True,
        'visibility': 'public',
        'verify': 'success',
        'release': {'state': 'published', 'missing_assets': [], 'workflow': 'success', 'ready': True},
        'tag': 'at_head',
    }
    # Only the public GitHub API is ever contacted.
    assert {request.url.host for request in github.requests} == {'api.github.com'}


@pytest.mark.parametrize(('status', 'conclusion', 'expected'), [
    ('in_progress', None, 'running'),
    ('completed', 'failure', 'failure'),
    ('completed', 'success', 'success'),
])
def test_release_readiness_requires_its_tag_workflow(monkeypatch, status, conclusion, expected):
    _serve(monkeypatch, {**FINAL, RELEASE_WORKFLOW: _runs(status, conclusion)})
    release = _read()['release']
    assert release['workflow'] == expected
    assert release['ready'] is (expected == 'success')


@pytest.mark.parametrize('local_head', [None, 'b' * 40])
def test_release_workflow_uses_the_remote_tag_commit(monkeypatch, local_head):
    github = _serve(monkeypatch, FINAL)
    assert _read(head=local_head)['release']['ready'] is True
    request = next(r for r in github.requests if r.url.path == RELEASE_WORKFLOW)
    assert request.url.params['head_sha'] == HEAD


def test_release_requires_downloadable_assets_and_a_matching_tag_run(monkeypatch):
    asset = {**_asset(ASSETS[0]), 'browser_download_url': ''}
    _serve(monkeypatch, {**FINAL, RELEASE: _release(asset, *[_asset(n) for n in ASSETS[1:]])})
    assert _read()['release']['missing_assets'] == [ASSETS[0]]
    _reset()
    _serve(monkeypatch, {**FINAL, RELEASE_WORKFLOW: (200, {'workflow_runs': []}, None)})
    assert _read()['release']['ready'] is False


def test_missing_repository_workflow_release_and_tag_are_reported_as_such(monkeypatch):
    _serve(monkeypatch, {})
    result = _read()
    assert result['visibility'] == 'not_found'
    assert result['verify'] == 'missing'
    assert result['release'] == {'state': 'missing', 'missing_assets': [], 'workflow': 'unknown', 'ready': False}
    assert result['tag'] == 'missing'
    assert result['rate_limited'] is False
    assert result['reachable'] is True


def test_private_repository_is_reported_as_private(monkeypatch):
    _serve(monkeypatch, {BASE: (200, {'private': True}, None)})
    assert _read()['visibility'] == 'private'


def test_remote_tag_on_another_commit_is_elsewhere(monkeypatch):
    _serve(monkeypatch, {TAG_REF: _tag_ref('b' * 40)})
    assert _read()['tag'] == 'elsewhere'


def test_annotated_remote_tag_is_followed_to_its_commit(monkeypatch):
    _serve(
        monkeypatch,
        {
            TAG_REF: _tag_ref('c' * 40, kind='tag'),
            BASE + '/git/tags/' + 'c' * 40: (200, {'object': {'sha': HEAD, 'type': 'commit'}}, None),
        },
    )
    assert _read()['tag'] == 'at_head'


def test_remote_tag_is_unknown_without_a_local_head(monkeypatch):
    _serve(monkeypatch, {TAG_REF: _tag_ref(HEAD)})
    assert _read(head=None)['tag'] == 'unknown'


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
            **FINAL,
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
        'workflow': 'success', 'ready': False}


@pytest.mark.parametrize('entry', [LIMITED, (429, {}, None)])
def test_rate_limit_leaves_remote_steps_unknown_and_is_not_cached(monkeypatch, entry):
    github = _serve(monkeypatch, {BASE: entry, VERIFY: entry, RELEASE: entry, TAG_REF: entry})
    result = _read()
    assert result['rate_limited'] is True
    assert result['visibility'] == 'unknown'
    assert result['verify'] == 'unknown'
    assert result['release'] == {'state': 'unknown', 'missing_assets': [], 'workflow': 'unknown', 'ready': False}
    assert result['tag'] == 'unknown'
    first = len(github.requests)
    _read()
    assert len(github.requests) > first


def test_a_known_rate_limit_reset_is_waited_out_even_when_fresh(monkeypatch):
    reset = str(int(time.time()) + 600)
    limited = (403, {}, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': reset})
    github = _serve(monkeypatch, {BASE: limited})
    assert _read()['rate_limited'] is True
    first = len(github.requests)
    assert _read(fresh=True)['rate_limited'] is True
    assert len(github.requests) == first


def test_forbidden_without_an_exhausted_quota_is_not_a_rate_limit(monkeypatch):
    forbidden = (403, {}, {'x-ratelimit-remaining': '12'})
    _serve(monkeypatch, {BASE: forbidden})
    result = _read()
    assert result['rate_limited'] is False
    assert result['reachable'] is True
    assert result['visibility'] == 'unknown'


def test_final_answers_are_cached_per_repository_commit_and_tag(monkeypatch):
    github = _serve(monkeypatch, FINAL)
    first = _read()
    assert len(github.requests) == 5
    assert _read() == first
    assert len(github.requests) == 5
    # Another commit is a different question.
    _read(head='b' * 40)
    assert len(github.requests) == 10


def test_fresh_bypasses_the_cache(monkeypatch):
    github = _serve(monkeypatch, FINAL)
    _read()
    _read(fresh=True)
    assert len(github.requests) == 10


@pytest.mark.parametrize(
    'routes',
    [
        # Verify still running.
        {**FINAL, VERIFY: _runs('in_progress')},
        # Verify run not created yet right after a push.
        {**FINAL, VERIFY: (200, {'workflow_runs': []}, None)},
        # Uploaded assets do not make a running or failed release workflow final.
        {**FINAL, RELEASE_WORKFLOW: _runs('in_progress')},
        {**FINAL, RELEASE_WORKFLOW: _runs('completed', 'failure')},
        # Release not created yet, or still uploading.
        {**FINAL, RELEASE: (404, {}, None)},
        {**FINAL, RELEASE: _release(_asset(ASSETS[0]))},
    ],
)
def test_answers_that_can_still_change_are_kept_only_briefly(monkeypatch, routes):
    github = _serve(monkeypatch, routes)
    clock = [1000.0]
    patch_module_clock(monkeypatch, publish_status, monotonic=lambda: clock[0])
    _read()
    first = len(github.requests)
    _read()
    assert len(github.requests) == first
    clock[0] += publish_status._CHANGING_CACHE_SECONDS + 1
    _read()
    assert len(github.requests) == 2 * first


def test_network_failures_are_unreachable_and_not_cached(monkeypatch):
    error = httpx.ConnectError('boom')
    github = _serve(monkeypatch, {BASE: error, VERIFY: error, RELEASE: error, TAG_REF: error})
    result = _read()
    assert result['rate_limited'] is False
    assert result['reachable'] is False
    assert result['visibility'] == 'unknown'
    assert result['verify'] == 'unknown'
    assert result['release']['state'] == 'unknown'
    assert result['tag'] == 'unknown'
    first = len(github.requests)
    _read()
    assert len(github.requests) == 2 * first


def test_a_server_error_is_unreachable_and_not_cached(monkeypatch):
    github = _serve(monkeypatch, {**FINAL, RELEASE: (502, {}, None)})
    result = _read()
    assert result['reachable'] is False
    assert result['release']['state'] == 'unknown'
    _read()
    assert len(github.requests) == 8


def test_concurrent_reads_of_one_release_share_one_set_of_requests(monkeypatch):
    github = _serve(monkeypatch, {**FINAL, VERIFY: _runs('in_progress')})

    async def both():
        return await asyncio.gather(
            publish_status.read_remote_github('owner/repo', HEAD, 'v1.0.0', 'demo'),
            publish_status.read_remote_github('owner/repo', HEAD, 'v1.0.0', 'demo', fresh=True),
        )

    first, second = asyncio.run(both())
    assert first == second
    assert len(github.requests) == 5


def test_verify_is_not_read_without_a_head_commit(monkeypatch):
    github = _serve(monkeypatch, {BASE: PUBLIC})
    result = _read(head=None)
    assert result['verify'] == 'unknown'
    assert all(not request.url.path.endswith('/runs') for request in github.requests)


def test_token_is_sent_only_when_configured(monkeypatch):
    github = _serve(monkeypatch, {BASE: PUBLIC})
    _read()
    assert all('authorization' not in request.headers for request in github.requests)
    monkeypatch.setenv('GH_TOKEN', 'secret')
    github.requests.clear()
    _read(fresh=True)
    assert github.requests
    assert all(request.headers['authorization'] == 'Bearer secret' for request in github.requests)
