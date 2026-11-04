"""Reuse proactive candidates, renewing short-lived audio URLs when needed."""

import asyncio
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import random
import time

from main_logic.music_requests import mark_music_request_query, was_music_request_recent
from utils import music_crawlers
from utils.logger_config import get_module_logger
from utils.music_audio import _playback_failed_recently, filter_playable_candidates

from .decisions import _should_skip_source
from .state import _source_hash

POOL_SIZE = 20
POOL_TTL_SECONDS = 20 * 60
FAILURE_TTL_SECONDS = 60
MAX_POOLS = 32
_pools = OrderedDict()
_pool_lock = asyncio.Lock()
logger = get_module_logger(__name__, 'Main')


async def _owner():
    def snapshot():
        crawler = music_crawlers.get_music_crawlers()['netease']
        crawler._check_cookie_freshness()
        cookies = hashlib.sha256(json.dumps(crawler._cookies, sort_keys=True).encode()).hexdigest()
        # VIP is lazily discovered during a refill; it is not an account switch.
        # Actual playback authorization still gates every returned candidate.
        return (id(crawler), crawler._get_cookie_file_mtime(), cookies)
    return await asyncio.to_thread(snapshot)


def _available(result):
    available = []
    for track in result.get('data', []):
        title = f"{track.get('name', '')} - {track.get('artist', '')}".strip(' -')
        key = _source_hash('', title)
        legacy = _source_hash(track.get('url', ''), title)
        if _should_skip_source(key) or (legacy != key and _should_skip_source(legacy)):
            continue
        available.append(track)
    random.shuffle(available)
    return {**deepcopy(result), 'data': deepcopy(available[:5])} if available else None


async def _verified(result, payload, owner, *, renew=False):
    # Try at most two small batches, so a broken leading source can yield to
    # another cached candidate without starting a fresh provider search.
    deferred = set()
    for _ in range(2):
        if not result:
            return None
        tracks = result['data']
        valid = await filter_playable_candidates(tracks, deferred_urls=deferred)
        if await _owner() != owner:
            return None
        expired = [track for track in tracks if track not in valid and track.get('_audio_url')
                   and str(track.get('url', '')).startswith('/api/music/play/netease/')
                   and not _playback_failed_recently(track['url'])
                   and track.get('url') not in deferred] if renew else []
        if expired:
            if payload.get('_authorization_retry_after', 0) > time.monotonic():
                deferred.update(track['url'] for track in expired)
            else:
                crawler = await asyncio.to_thread(lambda: music_crawlers.get_music_crawlers()['netease'])
                if await _owner() != owner:
                    return None
                try:
                    refreshed = await crawler.filter_playable_tracks(expired, raise_on_unavailable=True)
                except music_crawlers.MusicAuthorizationUnavailable:
                    refreshed = None
                if await _owner() != owner:
                    return None
                if refreshed is None:
                    # A renewal outage must not delete reusable song metadata or
                    # hit authorization again every proactive round.
                    payload['_authorization_retry_after'] = time.monotonic() + FAILURE_TTL_SECONDS
                    deferred.update(track['url'] for track in expired)
                else:
                    renewed = {track['url']: track for track in refreshed}
                    payload['data'] = [renewed.get(track.get('url'), track) for track in payload['data']]
                    valid.extend(await filter_playable_candidates(refreshed, deferred_urls=deferred))
                    if await _owner() != owner:
                        return None
        valid_urls = {track.get('url') for track in valid}
        failed = {track.get('url') for track in tracks if track.get('url') not in valid_urls and track.get('url') not in deferred}
        payload['data'] = [track for track in payload.get('data', []) if track.get('url') not in failed]
        result['data'] = valid
        if valid:
            return result
        result = _available({**payload, 'data': [track for track in payload['data'] if track.get('url') not in deferred]})
    return None


async def fetch_candidates(scope, request, fetch):
    """Share one bounded refill, then serve unused songs before search cooldowns."""
    # A single lock prevents simultaneous empty-pool requests duplicating provider calls.
    # Both lock acquisition and provider work have a total deadline; cancellation propagates.
    async with asyncio.timeout(45):
        async with _pool_lock:
            owner = await _owner()
            key = (scope, request, owner)
            now = time.monotonic()
            for stale in [k for k, (expiry, _) in _pools.items() if expiry <= now or (k[0] == scope and k[2] != owner)]:
                _pools.pop(stale, None)
            cached = _pools.get(key)
            if cached:
                _pools.move_to_end(key)
                available = _available(cached[1])
                result = await _verified(available, cached[1], owner, renew=True)
                if await _owner() != owner:
                    return None
                if result or not cached[1].get('success') or (available and cached[1].get('data')):
                    if result:
                        logger.info('[%s] 命中音乐候选缓存，本轮不重新搜索音乐源', scope)
                    return result
            if was_music_request_recent(scope, request):
                return None
            fetched = await fetch(POOL_SIZE)
            # A login/root change during the refill must not publish the prior owner's pool.
            if await _owner() != owner:
                return None
            successful = bool(fetched and fetched.get('success') and fetched.get('data'))
            payload = deepcopy(fetched) if successful else {'success': False, 'data': []}
            payload['data'] = payload.get('data', [])[:POOL_SIZE]
            result = await _verified(_available(payload), payload, owner)
            if await _owner() != owner:
                return None
            if result:
                mark_music_request_query(scope, request)
            elif not payload['data']:
                payload['success'] = False
            ttl = POOL_TTL_SECONDS if result else FAILURE_TTL_SECONDS
            _pools[key] = (time.monotonic() + ttl, payload)
            _pools.move_to_end(key)
            while len(_pools) > MAX_POOLS:
                _pools.popitem(last=False)
            return result
