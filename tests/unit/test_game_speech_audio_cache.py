from __future__ import annotations

import pytest

from main_logic.core.game_speech_audio_cache import (
    GameSpeechAudioCache,
    GameSpeechCaptureOwner,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _capture(
    cache: GameSpeechAudioCache,
    owner: object,
    speech_id: str,
    cache_key: str,
    *chunks: bytes,
    signature: str = "voice-a",
) -> bool:
    assert cache.begin_capture(owner, speech_id, cache_key, signature)
    for chunk in chunks:
        assert cache.append_capture(owner, speech_id, chunk)
    return cache.complete_capture(owner, speech_id, signature)


@pytest.mark.unit
def test_completed_audio_is_immutable_and_lru_bounded() -> None:
    cache = GameSpeechAudioCache(max_entries=2, max_total_bytes=12, max_entry_bytes=8)
    owner = GameSpeechCaptureOwner()

    assert _capture(cache, owner, "s1", "one", b"a")
    assert _capture(cache, owner, "s2", "two", b"bb")
    assert cache.get("one") == (b"a",)
    assert _capture(cache, owner, "s3", "three", b"ccc")

    assert cache.get("two") is None
    assert cache.get("one") == (b"a",)
    assert cache.get("three") == (b"ccc",)
    assert cache.stats() == {
        "entries": 2,
        "entry_bytes": 4,
        "captures": 0,
        "capture_bytes": 0,
    }


@pytest.mark.unit
def test_completed_audio_expires_and_total_bytes_evict_oldest() -> None:
    clock = _Clock()
    cache = GameSpeechAudioCache(
        max_entries=4,
        max_total_bytes=5,
        max_entry_bytes=5,
        entry_ttl_seconds=10,
        clock=clock,
    )
    owner = GameSpeechCaptureOwner()

    assert _capture(cache, owner, "s1", "one", b"aaa")
    assert _capture(cache, owner, "s2", "two", b"bbb")
    assert cache.get("one") is None
    assert cache.get("two") == (b"bbb",)

    clock.now += 10
    assert cache.get("two") is None
    assert cache.stats()["entry_bytes"] == 0


@pytest.mark.unit
def test_capture_limits_signature_guard_and_release_paths() -> None:
    clock = _Clock()
    cache = GameSpeechAudioCache(
        max_entry_bytes=4,
        max_captures=2,
        max_capture_total_bytes=5,
        capture_ttl_seconds=10,
        clock=clock,
    )
    owner_a = GameSpeechCaptureOwner()
    owner_b = GameSpeechCaptureOwner()
    owner_c = GameSpeechCaptureOwner()

    assert cache.begin_capture(owner_a, "a", "key-a", "voice-a")
    assert cache.append_capture(owner_a, "a", b"aaa")
    assert cache.begin_capture(owner_b, "b", "key-b", "voice-b")
    assert not cache.begin_capture(owner_c, "c", "key-c", "voice-c")
    assert not cache.append_capture(owner_b, "b", b"bbb")
    assert cache.stats()["captures"] == 1
    assert not cache.complete_capture(owner_a, "a", "changed-voice")
    assert cache.get("key-a") is None

    assert cache.begin_capture(owner_a, "oversized", "large", "voice-a")
    assert not cache.append_capture(owner_a, "oversized", b"12345")
    assert cache.stats()["captures"] == 0

    assert cache.begin_capture(owner_a, "stale", "stale", "voice-a")
    clock.now += 10
    assert cache.stats()["captures"] == 0

    assert cache.begin_capture(owner_a, "release-a", "release-a", "voice-a")
    assert cache.begin_capture(owner_b, "release-b", "release-b", "voice-b")
    assert cache.discard_owner(owner_a) == 1
    assert cache.stats()["captures"] == 1
    cache.clear()
    assert cache.stats() == {
        "entries": 0,
        "entry_bytes": 0,
        "captures": 0,
        "capture_bytes": 0,
    }


@pytest.mark.unit
def test_unscoped_audio_is_discarded_when_capture_identity_is_ambiguous() -> None:
    cache = GameSpeechAudioCache(max_captures=3)
    owner = GameSpeechCaptureOwner()

    assert cache.begin_capture(owner, "one", "key-one", "voice")
    assert cache.begin_capture(owner, "two", "key-two", "voice")
    assert not cache.append_unscoped_capture(owner, "two", b"ambiguous")
    assert cache.stats()["captures"] == 0

    assert cache.begin_capture(owner, "only", "key-only", "voice")
    assert cache.append_unscoped_capture(owner, "legacy-worker-id", b"safe")
    assert cache.complete_capture(owner, "only", "voice")
    assert cache.get("key-only") == (b"safe",)


def test_owner_identity_is_never_reused_across_owner_lifetimes():
    """A dead owner's identity must never be handed to a new owner.

    Keying captures on ``id(owner)`` looks fine until CPython recycles the
    address of a freed owner -- the isolated preload batches are exactly the
    short-lived, same-sized allocations that get recycled -- at which point a
    fresh owner inherits the previous one's still-live captures and its audio
    is appended to someone else's utterance.
    """
    import gc

    cache = GameSpeechAudioCache()
    tokens = []
    for _ in range(50):
        owner = GameSpeechCaptureOwner()
        tokens.append(cache._owner_token(owner))
        del owner
        gc.collect()

    assert len(set(tokens)) == len(tokens), (
        "owner identity was recycled across owner lifetimes"
    )


def test_owner_token_is_stable_under_concurrent_first_touch():
    """Two threads first touching one owner must agree on its token.

    ``_owner_token`` is a check-then-act pair over the weak map. Without the
    lock, both threads miss the lookup and mint separate tokens, so captures for
    one ``(owner, speech_id)`` split across two identities and neither
    ``complete_capture`` nor ``discard_owner`` can find the other's rows.

    The barrier makes that window deterministic rather than relying on timing:
    it is released only once a thread is past the lookup, so an unlocked
    implementation is guaranteed to interleave, and a locked one is guaranteed
    to serialize (the second thread blocks before it ever reaches the barrier).
    """
    import threading

    cache = GameSpeechAudioCache()
    owner = GameSpeechCaptureOwner()
    real_map = cache._owner_tokens
    entered = threading.Event()
    proceed = threading.Event()

    class SlowLookupMap:
        def get(self, key, default=None):
            value = real_map.get(key, default)
            if value is None:
                entered.set()
                # Bounded: a correct (locked) implementation never has a second
                # thread waiting here, so this must not hang the suite.
                proceed.wait(timeout=1.0)
            return value

        def __setitem__(self, key, value):
            real_map[key] = value

    cache._owner_tokens = SlowLookupMap()
    tokens: list[int] = []
    lock = threading.Lock()

    def take():
        token = cache._owner_token(owner)
        with lock:
            tokens.append(token)

    first = threading.Thread(target=take)
    first.start()
    assert entered.wait(timeout=2.0), "the first thread never reached the lookup"
    second = threading.Thread(target=take)
    second.start()
    proceed.set()
    first.join(timeout=5.0)
    second.join(timeout=5.0)

    assert len(tokens) == 2
    assert tokens[0] == tokens[1], (
        "concurrent first touch minted two identities for one owner"
    )
