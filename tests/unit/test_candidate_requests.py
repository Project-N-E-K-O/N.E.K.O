"""Behavioral coverage for shared candidate racing and resource cleanup."""

import asyncio

import pytest

from main_routers.config_router.candidate_requests import race_candidate_requests


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize('prefer_configured_order', [False, True])
async def test_failure_policy_preserves_completion_or_configuration_order(prefer_configured_order):
    async def request(url):
        if url == 'preferred':
            await asyncio.sleep(0.02)
        return {'success': False, 'error_code': url}

    result = await race_candidate_requests(
        ['preferred', 'fallback'], request, prefer_configured_order=prefer_configured_order,
    )
    assert result['error_code'] == ('preferred' if prefer_configured_order else 'fallback')


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_cancels_and_drains_loser():
    closed = []

    async def request(url):
        if url == 'slow':
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(url)
        return {'success': True}

    result = await race_candidate_requests(['slow', 'healthy'], request)
    assert result == {'success': True, 'resolved_url': 'healthy'}
    assert closed == ['slow']


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_cancels_all_candidates():
    closed = []

    async def request(url):
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(url)

    result = await race_candidate_requests(['a', 'b'], request, timeout=0.01)
    assert result['error_code'] == 'timeout'
    assert sorted(closed) == ['a', 'b']


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pending_preferred_timeout_is_not_masked_by_fallback_404():
    async def request(url):
        if url == 'preferred':
            await asyncio.Event().wait()
        return {'success': False, 'error_code': 'unsupported'}

    result = await race_candidate_requests(
        ['preferred', 'fallback'], request, timeout=0.01, prefer_configured_order=True,
    )
    assert result['error_code'] == 'timeout'


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize('fallback_result', ['success', 'auth_failed', 'stalls'])
async def test_regional_auth_failure_waits_for_remaining_candidates(fallback_result):
    preferred_finished = asyncio.Event()
    fallback_closed = asyncio.Event()

    async def request(url):
        if url == 'preferred':
            preferred_finished.set()
            return {'success': False, 'error_code': 'auth_failed'}
        await preferred_finished.wait()
        try:
            if fallback_result == 'stalls':
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            return {'success': fallback_result == 'success', 'error_code': 'auth_failed'}
        finally:
            fallback_closed.set()

    race = asyncio.create_task(race_candidate_requests(
        ['preferred', 'fallback'], request,
        timeout=0.05 if fallback_result == 'stalls' else 10,
        prefer_configured_order=True,
    ))
    if fallback_result == 'stalls':
        await preferred_finished.wait()
        await asyncio.sleep(0.01)
        assert not race.done()
    result = await asyncio.wait_for(race, timeout=0.5)
    if fallback_result == 'success':
        assert result['success'] is True
        assert result['resolved_url'] == 'fallback'
    else:
        assert result['error_code'] == 'auth_failed'
    assert fallback_closed.is_set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_caller_cancellation_drains_candidates():
    started = asyncio.Event()
    closed = []

    async def request(url):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(url)

    task = asyncio.create_task(race_candidate_requests(['a', 'b'], request))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(closed) == ['a', 'b']


@pytest.mark.unit
@pytest.mark.asyncio
async def test_candidate_exception_does_not_prevent_fallback_success():
    async def request(url):
        if url == 'broken':
            raise RuntimeError('probe failed')
        return {'success': True}

    assert (await race_candidate_requests(['broken', 'healthy'], request))['resolved_url'] == 'healthy'
