"""Exercise unified knowledge embedding outcomes without real user data."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge.chunking import derive_chunks
from knowledge.models import KnowledgeEntry
from memory import embeddings
from memory._embeddings.scheduling import InferenceQueue
from memory._embeddings.hardware import resolve_background_duty
from utils.embedding_contract import EmbedDeferred

pytestmark = pytest.mark.unit_fast


def _service(monkeypatch, *, ready=True, ram=8):
    """Keep the actual lifecycle and scheduler while replacing native inference."""
    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: False)
    service = embeddings.EmbeddingService(model_dir="unused", ram_gb=ram,
                                          has_vnni=True, has_avx2=True)
    if ready:
        service._state = embeddings.EmbeddingState.READY
    service._tokenizer = SimpleNamespace(
        encode_batch=lambda texts: [SimpleNamespace(ids=[1] * len(text)) for text in texts],
    )
    service._infer_blocking = lambda texts: [[1.0] for _text in texts]
    return service


async def test_structured_outcomes_keep_busy_out_of_legacy_none_and_attempts(monkeypatch):
    service = _service(monkeypatch)
    service._inference_queue = InferenceQueue(max_background=0)
    try:
        result = await service.embed_ex(["document"], lane="background")
        assert result.status == "busy" and result.retry_after_ms > 0
        assert service.status_counts()["busy"] == 1
        with pytest.raises(EmbedDeferred):
            await service.embed_documents(["document"])
        assert service.is_available()
        assert await service.embed("query") == [1.0]
    finally:
        await service.close()


@pytest.mark.parametrize("state,status", [("INIT", "unavailable"), ("LOADING", "loading"), ("DISABLED", "disabled")])
async def test_structured_unready_states_never_load_model(monkeypatch, state, status):
    service = _service(monkeypatch, ready=False)
    service._state = getattr(embeddings.EmbeddingState, state)
    service.request_load = lambda: pytest.fail("embedding must not load a model")
    try:
        assert (await service.embed_ex(["document"])).status == status
        assert await service.embed_batch(["document"]) == [None]
    finally:
        await service.close()


async def test_background_cooldown_does_not_reserve_foreground_slot():
    queue = InferenceQueue(background_duty=0.15, foreground_burst=1)
    queue._background_ready_at = asyncio.get_running_loop().time() + 60
    order = []

    async def background():
        async with queue.slot("background"):
            order.append("background")

    waiter = asyncio.create_task(background())
    try:
        await asyncio.sleep(0)
        # A fairness threshold cannot block foreground for an ineligible build.
        queue._foreground_streak = 8
        async with asyncio.timeout(0.5):
            async with queue.slot("foreground"):
                order.append("foreground")
        assert order == ["foreground"]
        async with queue._condition:
            queue._background_ready_at = 0
            queue._condition.notify_all()
        await asyncio.wait_for(waiter, 1)
        assert order == ["foreground", "background"]
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


def test_token_fitted_chunks_cover_the_tail_without_slicing_final_inputs():
    content = "".join(chr(0x4E00 + index) for index in range(2500))
    entry = KnowledgeEntry(title="A long title", summary="summary", content=content)
    chunks = derive_chunks(entry, fit=lambda value: len(value) <= 200)
    assert len(chunks) > 1
    assert all(len(chunk.embed_text) <= 200 for chunk in chunks)
    assert content[-1] in chunks[-1].embed_text
    # Every source character survives, including subdivisions near a header.
    assert set(content) <= set("".join(chunk.embed_text for chunk in chunks))


def test_spec_and_full_token_count_do_not_load_or_mutate_native_tokenizer(tmp_path, monkeypatch):
    tokenizers = pytest.importorskip("tokenizers")
    service = _service(monkeypatch, ready=False)
    service._model_dir = str(tmp_path)
    model = Path(service._model_file_path())
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"synthetic model")
    Path(str(model) + "_data").write_bytes(b"synthetic weights")
    tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel({"x": 1, "[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(max_length=4)
    path = Path(service._tokenizer_file_path())
    tokenizer.save(str(path))
    original = path.read_bytes()
    spec = service.spec()
    assert spec["dimension"] == service.dim()
    assert service.count_tokens(["x " * 20]) == [20]
    assert not service.is_available()
    assert path.read_bytes() == original


async def test_memory_backfill_uses_background_lane_and_defers_without_writes(monkeypatch):
    from memory.embedding_worker import EmbeddingWarmupWorker

    worker = object.__new__(EmbeddingWarmupWorker)
    calls = []

    async def background(texts, *, sticky_failure):
        calls.append((texts, sticky_failure))
        raise EmbedDeferred(1)

    worker._service = SimpleNamespace(embed_documents=background)
    with pytest.raises(EmbedDeferred):
        await worker._embed_texts(["private synthetic document"])
    assert calls == [(["private synthetic document"], True)]


@pytest.mark.parametrize("ram,duty", [(None, 0.15), (4, 0.15), (7.99, 0.15), (8, 0.25), (64, 0.25)])
def test_background_duty_uses_low_memory_and_normal_profiles(ram, duty):
    assert resolve_background_duty(ram) == duty


async def test_timeout_is_observable_without_disabling_the_model(monkeypatch):
    service = _service(monkeypatch)

    async def slow(*_args):
        await asyncio.sleep(60)

    service._run_inference = slow
    try:
        result = await service.embed_ex(["query"], deadline=asyncio.get_running_loop().time() + 0.01)
        assert result.status == "timeout"
        assert service.status_counts() == {"timeout": 1}
        assert service.is_available()
    finally:
        await service.close()


async def test_background_cooldown_wakes_without_any_foreground_completion():
    """A lone queued build progresses when its own cooling deadline expires."""
    queue = InferenceQueue(background_duty=0.25)
    queue._background_ready_at = asyncio.get_running_loop().time() + 60
    entered = asyncio.Event()

    async def wait_for_cooldown():
        async with queue.slot("background"):
            assert asyncio.get_running_loop().time() >= queue._background_ready_at
            entered.set()

    waiter = asyncio.create_task(wait_for_cooldown())
    try:
        async with asyncio.timeout(1):
            while not queue._waiting["background"]:
                await asyncio.sleep(0)
            assert not entered.is_set()
            async with queue._condition:
                queue._background_ready_at = asyncio.get_running_loop().time() + 0.01
                queue._condition.notify_all()
            await waiter  # No foreground completion or further notifications.
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def test_backfill_busy_is_delayed_without_failure_logging(monkeypatch):
    from memory import embedding_worker

    worker = object.__new__(embedding_worker.EmbeddingWarmupWorker)
    worker._stop_event = asyncio.Event()
    calls = []

    async def ready():
        return True

    async def sweep():
        calls.append(True)
        if len(calls) == 1:
            raise EmbedDeferred(1)
        worker._stop_event.set()
        return 0

    worker._service = SimpleNamespace(is_disabled=lambda: False, request_load=ready)
    worker._wait_for_warmup_trigger = ready
    worker._sweep_once = sweep
    monkeypatch.setattr(embedding_worker.logger, "warning", lambda *_args, **_kwargs: pytest.fail("busy was logged as failure"))
    await asyncio.wait_for(worker._run(), 1)
    assert len(calls) == 2


async def test_shared_knowledge_documents_use_background_and_keep_deferral(monkeypatch):
    from app.memory_server.knowledge_routes import _SharedEmbedder

    seen = []

    async def documents(texts, *, sticky_failure):
        seen.append((texts, sticky_failure))
        raise EmbedDeferred(20)

    shared = _SharedEmbedder(SimpleNamespace(embed_documents=documents))
    with pytest.raises(EmbedDeferred):
        await shared.embed_documents(["source"])
    assert seen == [(["source"], False)]


def test_resource_spec_ignores_permission_metadata(tmp_path, monkeypatch):
    service = _service(monkeypatch, ready=False)
    service._model_dir = str(tmp_path)
    paths = [Path(service._model_file_path()), Path(service._model_file_path() + "_data"), Path(service._tokenizer_file_path())]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixed trusted model resource")
    before = service.spec()
    service._loaded_weights_sha256 = before["weights_sha256"]
    path = paths[0]
    original_mode = path.stat().st_mode
    try:
        path.chmod(0o600)
        assert service.spec() == before
    finally:
        path.chmod(original_mode)


async def test_shutdown_wakes_background_waiting_through_a_long_cooldown(monkeypatch):
    """Stopping must not wait for a future background eligibility deadline."""
    service = _service(monkeypatch)
    service._inference_queue._background_ready_at = asyncio.get_running_loop().time() + 60
    waiting = asyncio.create_task(service.embed_ex(["source"], lane="background"))
    try:
        async with asyncio.timeout(1):
            while not service._inference_queue._waiting["background"]:
                await asyncio.sleep(0)
            await service.close()
            result = await waiting
        assert result.status == "unavailable"
        assert service._active_operations == 0
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


async def test_shared_deferred_signal_is_counted_as_busy_without_sticky_failure(monkeypatch):
    service = _service(monkeypatch)

    async def deferred(*_args):
        raise EmbedDeferred(35)

    service._run_inference = deferred
    try:
        result = await service.embed_ex(["source"], lane="background")
        assert result.status == "busy" and result.retry_after_ms == 35
        assert service.is_available()
    finally:
        await service.close()
