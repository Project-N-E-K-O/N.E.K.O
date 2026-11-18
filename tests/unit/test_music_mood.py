import asyncio
import pytest
from utils.music_mood import MoodTrack, MusicMoodClassifier, VERSION

TRACK = MoodTrack(track_id="netease:123", netease_id="123", name="arbitrary song")
RESULT = dict(category="sad", valence=-0.7, arousal=0.2, confidence=0.95)


@pytest.mark.asyncio
async def test_disabled_classifier_does_not_fetch_lyrics_or_call_a_model(monkeypatch):
    monkeypatch.setenv("NEKO_MUSIC_MOOD_ENABLED", "0")

    async def forbidden(*args):
        pytest.fail("disabled classification must not request evidence or inference")

    result = await MusicMoodClassifier(forbidden, forbidden).classify(TRACK)
    assert result["category"] == "unknown" and result["reason"] == "disabled"


async def lyrics(track):
    return "Evidence text about loss and remembrance. " * 8


async def infer(track, text):
    return RESULT


@pytest.mark.asyncio
async def test_cache_identity_evidence_and_expiry():
    now, calls = [100], []

    async def counted(track, text):
        calls.append(track.track_id)
        return RESULT

    c = MusicMoodClassifier(lyrics, counted, clock=lambda: now[0])
    first = await c.classify(TRACK)
    assert first["classifier_version"] == VERSION
    assert first["source"] == "lyrics_text_model" and first["confidence"] == 0.78
    assert (await c.classify(TRACK))["cache_hit"]
    changed = TRACK.model_copy(update={"name": "different version"})
    assert not (await c.classify(changed))["cache_hit"]
    now[0] += 86401
    assert not (await c.classify(TRACK))["cache_hit"]
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_title_only_never_calls_model():
    async def none(track):
        return ""

    async def fail(*args):
        pytest.fail("no inference without evidence")

    result = await MusicMoodClassifier(none, fail).classify(TRACK)
    assert result["category"] == "unknown" and result["confidence"] == 0
    assert result["reason"] == "insufficient_evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["failure", "timeout", "invalid", "nan", "low"])
async def test_failure_and_low_confidence(mode):
    async def model(*args):
        if mode == "failure":
            raise RuntimeError("provider failure")
        if mode == "timeout":
            await asyncio.sleep(10)
        return {
            **RESULT,
            **(
                {"confidence": float("nan")}
                if mode == "nan"
                else {"category": "invalid"}
                if mode == "invalid"
                else {"confidence": 0.3}
            ),
        }

    result = await MusicMoodClassifier(lyrics, model, timeout=0.02).classify(TRACK)
    assert result["confidence"] == (0.3 if mode == "low" else 0)
    if mode != "low":
        assert result["category"] == "unknown"


@pytest.mark.asyncio
async def test_coalescing_cancellation_and_admission():
    gate = asyncio.Event()
    calls = []

    async def model(track, text):
        calls.append(track.track_id)
        await gate.wait()
        return RESULT

    c = MusicMoodClassifier(lyrics, model)
    a = asyncio.create_task(c.classify(TRACK))
    duplicate = asyncio.create_task(c.classify(TRACK))
    b = asyncio.create_task(c.classify(TRACK.model_copy(update={"track_id": "b"})))
    await asyncio.sleep(0.01)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    result = await c.classify(TRACK.model_copy(update={"track_id": "c"}))
    assert result["reason"] == "rate_limited"
    gate.set()
    assert (await duplicate)["category"] == "sad"
    await b
    assert calls == [TRACK.track_id, "b"]
    assert not c.pending
