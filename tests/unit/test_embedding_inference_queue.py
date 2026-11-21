"""Exercise shared inference admission without native models or real user data."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit_fast


def test_cold_singleton_construction_is_single_flight(monkeypatch):
    """Park construction so a second thread observes the cold-entry window."""
    from memory import embeddings

    entered, release, duplicate, second_started = (threading.Event() for _ in range(4))
    instances = []

    def factory():
        instance = object()
        instances.append(instance)
        (entered if len(instances) == 1 else duplicate).set()
        assert release.wait(3)
        return instance

    def second():
        second_started.set()
        return embeddings.get_embedding_service()

    monkeypatch.setattr(embeddings, "_SERVICE", None)
    monkeypatch.setattr(embeddings, "_build_default_service", factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(embeddings.get_embedding_service)
        try:
            assert entered.wait(2)
            other = executor.submit(second)
            assert second_started.wait(2)
            duplicate.wait(0.1)  # The old unlocked entry deterministically reaches the factory.
        finally:
            release.set()
        assert first.result(3) is other.result(3)
    assert len(instances) == 1


async def test_foreground_overtakes_waiting_background_and_cancel_releases_admission():
    from memory._embeddings.scheduling import InferenceQueue

    queue = InferenceQueue()
    order = []
    entered = asyncio.Event()

    async def run(name, priority):
        entered.set()
        async with queue.slot(priority):
            order.append(name)

    async with queue.slot("background"):
        background = asyncio.create_task(run("background", "background"))
        await entered.wait()
        entered.clear()
        foreground = asyncio.create_task(run("foreground", "foreground"))
        await entered.wait()
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(background, foreground), 3)
    assert order == ["foreground", "background"]


async def test_background_queue_is_bounded_without_disabling_foreground():
    from memory._embeddings.scheduling import InferenceBusy, InferenceQueue

    queue = InferenceQueue(max_background=1)
    entered = asyncio.Event()

    async def wait():
        entered.set()
        async with queue.slot("background"):
            raise AssertionError("A cancelled waiter must not run")

    async with queue.slot("foreground"):
        waiting = asyncio.create_task(wait())
        await entered.wait()
        await asyncio.sleep(0)
        with pytest.raises(InferenceBusy):
            async with queue.slot("background"):
                pass
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    async with queue.slot("foreground"):
        pass
    async with queue.slot("background"):
        pass


async def test_foreground_admission_accepts_more_than_the_old_32_waiters():
    """Accept every interactive request and release cancelled waiters cleanly."""
    from memory._embeddings.scheduling import InferenceQueue

    queue = InferenceQueue()
    completed = []

    async def request(index):
        async with queue.slot("foreground"):
            completed.append(index)

    tasks = []
    try:
        async with queue.slot("background"):
            tasks = [asyncio.create_task(request(index)) for index in range(64)]
            # Every waiter reaches its first suspension before this next turn.
            await asyncio.sleep(0)
            assert queue._waiting["foreground"] == 64
            assert not any(task.done() for task in tasks)
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            assert queue._waiting["foreground"] == 63
        await asyncio.wait_for(asyncio.gather(*tasks[1:]), 3)
        assert sorted(completed) == list(range(1, 64))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_background_gets_a_turn_after_bounded_foreground_burst():
    from memory._embeddings.scheduling import InferenceQueue

    queue = InferenceQueue(foreground_burst=2)
    order = []
    background_turns = []

    async def run(name, priority):
        async with queue.slot(priority) as minimum_batch:
            order.append(name)
            if priority == "background":
                background_turns.append(minimum_batch)

    async with queue.slot("background"):
        background = asyncio.create_task(run("background", "background"))
        front = [asyncio.create_task(run(str(i), "foreground")) for i in range(4)]
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(background, *front), 3)
    assert order.index("background") == 2
    assert background_turns == [True]


async def test_fresh_background_does_not_inherit_an_uncontended_foreground_burst():
    """Only interactive work that overtook a real background waiter counts."""
    from memory._embeddings.scheduling import InferenceQueue

    queue = InferenceQueue(foreground_burst=2)
    order = []
    turns = []
    for _index in range(4):
        async with queue.slot("foreground"):
            pass

    async def run(priority):
        async with queue.slot(priority) as minimum_batch:
            order.append(priority)
            if priority == "background":
                turns.append(minimum_batch)

    async with queue.slot("foreground"):
        background = asyncio.create_task(run("background"))
        foreground = asyncio.create_task(run("foreground"))
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(background, foreground), 3)
    assert order == ["foreground", "background"]
    assert turns == [False]


def _ready_service(monkeypatch):
    """Inject supported hardware without loading a native session."""
    from memory import embeddings

    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: False)
    service = embeddings.EmbeddingService(model_dir="unused", ram_gb=4, has_vnni=True, has_avx2=True)
    service._state = embeddings.EmbeddingState.READY
    # Full-length rows on the lowest enabled tier occupy one token microbatch,
    # so these lifecycle tests still exercise a yield after each native call.
    service._tokenizer = SimpleNamespace(
        encode_batch=lambda texts: [SimpleNamespace(ids=range(1024)) for _text in texts],
    )
    return service


async def test_service_keeps_native_slot_until_cancelled_thread_really_exits(monkeypatch):
    """Cancellation cannot admit a second native call while the first still runs."""
    service = _ready_service(monkeypatch)
    started, release = threading.Event(), threading.Event()
    order = []

    def infer(texts):
        name = texts[0]
        order.append(name)
        if name == "first":
            started.set()
            assert release.wait(3)
        return [[1.0] for _ in texts]

    service._infer_blocking = infer
    first = asyncio.create_task(service.embed_batch(["first"], priority="background"))
    tasks = [first]
    try:
        assert await asyncio.to_thread(started.wait, 2)
        background = asyncio.create_task(service.embed_batch(["background"], priority="background"))
        foreground = asyncio.create_task(service.embed("foreground"))
        tasks.extend([background, foreground])
        async def wait_for_both_priorities():
            # Background token planning runs off-loop before native admission.
            while service._inference_queue._waiting != {"foreground": 1, "background": 1}:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_both_priorities(), 2)
        assert service._inference_queue._waiting == {"foreground": 1, "background": 1}
        first.cancel()
        await asyncio.sleep(0)
        assert order == ["first"]
        assert not first.done()
        assert service._active_operations == 3
    finally:
        release.set()
        done, pending = await asyncio.wait(tasks, timeout=3)
        assert not pending
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1:] == [[[1.0]], [1.0]]
    assert order == ["first", "foreground", "background"]


async def test_queue_pressure_is_not_a_sticky_model_failure(monkeypatch):
    from memory._embeddings.scheduling import InferenceBusy, InferenceQueue

    service = _ready_service(monkeypatch)
    service._inference_queue = InferenceQueue(max_background=0)
    service._infer_blocking = lambda texts: [[1.0] for _ in texts]
    try:
        with pytest.raises(InferenceBusy):
            await service.embed_batch(["background"], priority="background")
        assert service.is_available()
        assert await service.embed("foreground") == [1.0]
    finally:
        await service.close()


@pytest.mark.parametrize("sticky", [False, True])
async def test_inference_errors_do_not_log_private_exception_text(monkeypatch, sticky):
    from memory import embeddings

    service = _ready_service(monkeypatch)
    messages = []
    canary = "PRIVATE_QUERY_AND_SOURCE_CANARY"

    def failed(_texts):
        raise ValueError(canary)

    monkeypatch.setattr(embeddings.logger, "warning", lambda message, *args, **kwargs: messages.append(message % args))
    service._infer_blocking = failed
    try:
        assert await service.embed(canary, sticky_failure=sticky) is None
        assert messages
        assert any("ValueError" in message for message in messages)
        assert all(canary not in message for message in messages)
        assert service.is_disabled() is sticky
    finally:
        await service.close()


@pytest.mark.parametrize("replace_during_load", [False, True])
async def test_loaded_resource_identity_is_captured_and_checked(tmp_path, monkeypatch, replace_during_load):
    """A fake native loader exposes file replacement without importing ONNX."""
    from memory import embeddings

    service = _ready_service(monkeypatch)
    service._state = embeddings.EmbeddingState.INIT
    service._model_dir = str(tmp_path)
    paths = [Path(service._model_file_path()), Path(service._model_file_path() + "_data"),
             Path(service._tokenizer_file_path())]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixed resource")

    def load(*args, **kwargs):
        if replace_during_load:
            paths[1].write_bytes(b"replaced external weights during native construction")
        return object()

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=lambda: SimpleNamespace(add_session_config_entry=lambda *args: None),
        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL=1), InferenceSession=load,
    ))
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(
        Tokenizer=SimpleNamespace(from_file=lambda _path: SimpleNamespace(enable_truncation=lambda **kwargs: None)),
    ))
    try:
        assert await service.request_load() is not replace_during_load
        if replace_during_load:
            assert service.disable_reason() == embeddings._DisableReason.LOAD_ERROR.value
            assert service._loaded_file_identity is None
        else:
            from memory._embeddings.resources import resource_file_identity
            assert service._loaded_file_identity == resource_file_identity(paths)
    finally:
        await service.close()


async def test_async_cold_getter_keeps_the_event_loop_responsive(monkeypatch):
    """A contended singleton lock is awaited in a worker, never on the loop."""
    from memory import embeddings

    entered, release = threading.Event(), threading.Event()
    marker = object()

    def factory():
        entered.set()
        assert release.wait(3)
        return marker

    monkeypatch.setattr(embeddings, "_SERVICE", None)
    monkeypatch.setattr(embeddings, "_build_default_service", factory)
    with ThreadPoolExecutor(max_workers=1) as executor:
        cold = executor.submit(embeddings.get_embedding_service)
        assert await asyncio.to_thread(entered.wait, 2)
        getter = asyncio.create_task(embeddings.aget_embedding_service())
        heartbeat = asyncio.create_task(asyncio.sleep(0))
        try:
            done, _ = await asyncio.wait({heartbeat}, timeout=0.2)
            assert done
            assert not getter.done()
        finally:
            release.set()
            assert await getter is marker
            assert cold.result(3) is marker


@pytest.mark.parametrize("consumer", ["cosine", "turn_context"])
async def test_foreground_memory_consumers_do_not_wait_for_cold_lock_on_loop(monkeypatch, consumer):
    """Run real recall entry points while a worker owns cold construction."""
    from memory import embeddings, hybrid_recall, turn_context

    entered, release = threading.Event(), threading.Event()
    marker = SimpleNamespace(is_available=lambda: False)

    def factory():
        entered.set()
        assert release.wait(3)
        return marker

    async def empty_pools(*args):
        return [], []

    async def run():
        if consumer == "cosine":
            return await hybrid_recall._cosine_rank("query", [{}])
        return await turn_context._arecall(
            lanlan_name="test", query="query", fact_store=None, reflection_engine=None,
            persona_manager=None, config_manager=None, deadline=0, language="en",
            session_started_at=None, superseded=lambda: False,
            finish=lambda state: {"state": state}, in_worker=None, track=lambda task: None,
        )

    monkeypatch.setattr(embeddings, "_SERVICE", None)
    monkeypatch.setattr(embeddings, "_build_default_service", factory)
    monkeypatch.setattr(turn_context, "_aload_pools", empty_pools)
    with ThreadPoolExecutor(max_workers=1) as executor:
        cold = executor.submit(embeddings.get_embedding_service)
        assert await asyncio.to_thread(entered.wait, 2)
        # An external release bounds a broken synchronous consumer too: the
        # event loop must remain free before that release, not merely after it.
        timer = threading.Timer(0.5, release.set)
        timer.start()
        task = asyncio.create_task(run())
        heartbeat = asyncio.create_task(asyncio.sleep(0.01))
        try:
            done, _ = await asyncio.wait({task, heartbeat}, timeout=0.2, return_when=asyncio.FIRST_COMPLETED)
            assert heartbeat in done
            assert not task.done()
        finally:
            release.set()
            timer.cancel()
            timer.join(1)
            results = await asyncio.gather(task, heartbeat)
            assert cold.result(3) is marker
        assert results[0] == ([] if consumer == "cosine" else {"state": "miss"})


@pytest.mark.parametrize("lock_stage", ["snapshot", "clear"])
async def test_release_singleton_lock_waits_do_not_block_the_event_loop(monkeypatch, lock_stage):
    """Exercise both release lock sections against a controlled worker owner."""
    from memory import embeddings

    entered, release = threading.Event(), threading.Event()
    ready = asyncio.Event()
    heartbeat = None
    closed = []
    timer = threading.Timer(0.5, release.set)
    executor = ThreadPoolExecutor(max_workers=1)

    def hold_lock():
        with embeddings._SERVICE_LOCK:
            entered.set()
            assert release.wait(3)

    async def close():
        nonlocal heartbeat
        closed.append(True)
        if lock_stage == "clear":
            executor.submit(hold_lock)
            assert await asyncio.to_thread(entered.wait, 2)
            # Start the heartbeat immediately before release reaches its second
            # lock; an old synchronous clear must not get credit for earlier awaits.
            heartbeat = asyncio.create_task(asyncio.sleep(0.01))
            timer.start()
            ready.set()

    marker = SimpleNamespace(close=close)

    def factory():
        entered.set()
        assert release.wait(3)
        return marker

    monkeypatch.setattr(embeddings, "_SERVICE", None if lock_stage == "snapshot" else marker)
    monkeypatch.setattr(embeddings, "_build_default_service", factory)
    if lock_stage == "snapshot":
        executor.submit(embeddings.get_embedding_service)
        assert await asyncio.to_thread(entered.wait, 2)
        heartbeat = asyncio.create_task(asyncio.sleep(0.01))
        timer.start()
    task = asyncio.create_task(embeddings.release_embedding_service())
    try:
        if lock_stage == "clear":
            await asyncio.wait_for(ready.wait(), 2)
        done, _ = await asyncio.wait({task, heartbeat}, timeout=0.2, return_when=asyncio.FIRST_COMPLETED)
        assert heartbeat in done
        assert not task.done()
    finally:
        release.set()
        timer.cancel()
        timer.join(1)
        await asyncio.gather(task, heartbeat)
        executor.shutdown(wait=True)
    assert closed == [True]
    assert embeddings._SERVICE is None


async def test_release_empty_singleton_never_constructs_a_service(monkeypatch):
    from memory import embeddings

    calls = []
    monkeypatch.setattr(embeddings, "_SERVICE", None)
    monkeypatch.setattr(embeddings, "_build_default_service", lambda: calls.append("unexpected construction"))
    await embeddings.release_embedding_service()
    assert not calls
    assert embeddings._SERVICE is None


@pytest.mark.parametrize("cancel_background", [False, True])
async def test_background_batch_yields_between_texts_and_keeps_output_order(monkeypatch, cancel_background):
    """A foreground arrival overtakes the remainder of a four-text build batch."""
    service = _ready_service(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []
    values = {"one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "foreground": 99.0}

    def infer(texts):
        calls.extend(texts)
        if texts[0] == "one":
            entered.set()
            assert release.wait(3)
        return [[values[text]] for text in texts]

    service._infer_blocking = infer
    background = asyncio.create_task(service.embed_batch(["one", "two", "three", "four"],
                                                        sticky_failure=False, priority="background"))
    tasks = [background]
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        foreground = asyncio.create_task(service.embed("foreground"))
        tasks.append(foreground)
        await asyncio.sleep(0)
        assert service._inference_queue._waiting["foreground"] == 1
        if cancel_background:
            background.cancel()
            await asyncio.sleep(0)
        assert calls == ["one"]
        assert not foreground.done()
        assert not background.done()
    finally:
        release.set()
        done, pending = await asyncio.wait(tasks, timeout=3)
        assert not pending
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()
    assert results[1] == [99.0]
    if cancel_background:
        assert isinstance(results[0], asyncio.CancelledError)
        assert calls == ["one", "foreground"]
    else:
        assert results[0] == [[1.0], [2.0], [3.0], [4.0]]
        assert calls == ["one", "foreground", "two", "three", "four"]


async def test_background_microbatch_failure_discards_partial_results(monkeypatch):
    """The public batch contract must never mislabel early rows as full success."""
    service = _ready_service(monkeypatch)
    calls = []

    def infer(texts):
        calls.append(list(texts))
        if "two" in texts:
            raise ValueError("controlled inference failure")
        return [[1.0] for _ in texts]

    service._infer_blocking = infer
    try:
        rows = await service.embed_batch(["one", "two", "three", "four"],
                                         sticky_failure=False, priority="background")
        assert rows == [None, None, None, None]
        assert service.is_available()
        assert calls == [["one"], ["two"]]
        calls.clear()
        assert await service.embed_batch(["one", "three"]) == [[1.0], [1.0]]
        assert calls == [["one", "three"]]  # Existing memory batching is unchanged.
    finally:
        await service.close()


async def test_cancelling_a_cooling_background_waiter_beside_a_notify_is_not_swallowed():
    """A wake-up landing in the same loop turn as cancellation must not win the slot."""
    from memory._embeddings.scheduling import InferenceQueue

    queue = InferenceQueue()
    loop = asyncio.get_running_loop()
    queue._background_ready_at = loop.time() + 60
    acquired = []

    async def background():
        async with queue.slot("background"):
            acquired.append(True)

    waiter = asyncio.create_task(background())
    while queue._waiting["background"] == 0:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    async with queue._condition:
        queue._background_ready_at = loop.time()
        queue._condition.notify_all()
        waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert acquired == []
    assert queue._active is False
    assert queue._waiting["background"] == 0
