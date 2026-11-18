"""Verify bounded background batches without loading a model or user data."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from memory import embeddings
from memory._embeddings import hardware
from memory._embeddings.scheduling import InferenceBusy, InferenceQueue


pytestmark = pytest.mark.unit_fast


def _service(monkeypatch, ram_gb, lengths):
    """Keep actual service lifecycle/queue behavior with deterministic token IDs."""
    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: False)
    service = embeddings.EmbeddingService(model_dir="unused", ram_gb=ram_gb,
                                          has_vnni=True, has_avx2=True)
    service._state = embeddings.EmbeddingState.READY
    service._tokenizer = SimpleNamespace(
        encode_batch=lambda texts: [SimpleNamespace(ids=[0] * lengths[text]) for text in texts],
    )
    return service


@pytest.mark.parametrize("ram_gb,expected", [
    (None, 1024), (0, 1024), (4, 1024), (7.99, 1024),
    (8, 2048), (15.99, 2048), (16, 4096), (64, 4096),
])
def test_background_token_budget_tracks_existing_memory_tiers(ram_gb, expected):
    """Unknown memory remains conservative without changing model enablement."""
    assert hardware.resolve_background_token_budget(ram_gb) == expected


@pytest.mark.parametrize("ram_gb,counts", [(4, [2, 2, 1]), (8, [4, 1]), (16, [5])])
async def test_background_batches_use_padded_token_cost_and_preserve_order(monkeypatch, ram_gb, counts):
    """Identical text counts may yield different microbatches on each memory tier."""
    lengths = {str(index): 500 for index in range(5)}
    service = _service(monkeypatch, ram_gb, lengths)
    calls = []

    def infer(texts):
        calls.append(list(texts))
        return [[float(text)] for text in texts]

    async def unexpected_load():
        raise AssertionError("Background inference must not request model loading")

    service._infer_blocking = infer
    service.request_load = unexpected_load
    try:
        assert await service.embed_batch(list(lengths), priority="background") == [[float(index)] for index in range(5)]
        assert [len(call) for call in calls] == counts
        budget = hardware.resolve_background_token_budget(ram_gb)
        assert all(len(call) * max(lengths[text] for text in call) <= budget for call in calls)
    finally:
        await service.close()


async def test_background_budget_uses_tokenizer_lengths_instead_of_characters(monkeypatch):
    """A short token-heavy source cannot share a batch based on character length."""
    lengths = {"a": 800, "b": 800, "long" * 300: 20, "other" * 300: 20}
    service = _service(monkeypatch, 4, lengths)
    calls = []

    def infer(texts):
        calls.append(list(texts))
        return [[1.0] for _text in texts]

    service._infer_blocking = infer
    try:
        assert await service.embed_batch(list(lengths), priority="background") == [[1.0]] * 4
        assert calls == [["a"], ["b"], ["long" * 300, "other" * 300]]
    finally:
        await service.close()


@pytest.mark.parametrize("cancel_background", [False, True])
async def test_background_yields_after_token_batch_and_drains_cancelled_worker(monkeypatch, cancel_background):
    """New foreground work passes the next batch only after native ownership ends."""
    lengths = {"one": 500, "two": 500, "three": 500, "foreground": 10}
    service = _service(monkeypatch, 4, lengths)
    started, release = threading.Event(), threading.Event()
    calls = []

    def infer(texts):
        calls.append(list(texts))
        if texts[0] == "one":
            started.set()
            assert release.wait(3)
        return [[float(list(lengths).index(text))] for text in texts]

    service._infer_blocking = infer
    background = asyncio.create_task(service.embed_batch(["one", "two", "three"], priority="background"))
    tasks = [background]
    try:
        assert await asyncio.to_thread(started.wait, 2)
        foreground = asyncio.create_task(service.embed("foreground"))
        tasks.append(foreground)
        await asyncio.sleep(0)
        if cancel_background:
            background.cancel()
            await asyncio.sleep(0)
        assert calls == [["one", "two"]]
        assert not foreground.done() and not background.done()
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
        await service.close()
    assert results[1] == [3.0]
    assert calls[:2] == [["one", "two"], ["foreground"]]
    if cancel_background:
        assert isinstance(results[0], asyncio.CancelledError)
        assert len(calls) == 2
    else:
        assert results[0] == [[0.0], [1.0], [2.0]]
        assert calls[2] == ["three"]


@pytest.mark.parametrize("batch", [False, True])
async def test_background_busy_propagates_without_disabling_model(monkeypatch, batch):
    """Callers must distinguish pressure from missing vectors or inference failure."""
    service = _service(monkeypatch, 16, {"source": 4})
    service._inference_queue = InferenceQueue(max_background=0)
    service._infer_blocking = lambda texts: [[1.0] for _text in texts]
    try:
        with pytest.raises(InferenceBusy):
            if batch:
                await service.embed_batch(["source"], priority="background")
            else:
                await service.embed("source", priority="background")
        assert service.is_available()
        assert await service.embed("source") == [1.0]
    finally:
        await service.close()


async def test_background_fairness_turn_runs_one_row_then_resumes_idle_token_batch(monkeypatch):
    """A starvation-prevention turn cannot run an entire high-memory batch."""
    lengths = {"one": 500, "two": 500, "three": 500,
               "front-a": 10, "front-b": 10, "front-c": 10}
    service = _service(monkeypatch, 16, lengths)
    queue = InferenceQueue(foreground_burst=2)
    service._inference_queue = queue
    calls, tasks = [], []

    def infer(texts):
        calls.append(list(texts))
        return [[float(list(lengths).index(text))] for text in texts]

    async def wait_for_waiters(priority, count):
        # Wait for actual admission rather than relying on native thread timing.
        while queue._waiting[priority] != count:
            await asyncio.sleep(0)

    service._infer_blocking = infer
    try:
        async with queue.slot("foreground"):
            background = asyncio.create_task(service.embed_batch(["one", "two", "three"], priority="background"))
            tasks.append(background)
            await asyncio.wait_for(wait_for_waiters("background", 1), 2)
            tasks.extend(asyncio.create_task(service.embed(text)) for text in ("front-a", "front-b", "front-c"))
            await asyncio.wait_for(wait_for_waiters("foreground", 3), 2)
        results = await asyncio.wait_for(asyncio.gather(*tasks), 3)
        assert results[0] == [[0.0], [1.0], [2.0]]
        assert calls[2] == ["one"]
        assert calls[-1] == ["two", "three"]
        assert sorted(text for call in calls for text in call) == sorted(lengths)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()
