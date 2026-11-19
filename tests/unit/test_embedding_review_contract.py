"""Review regressions for immutable encoder identity and incremental backfill."""

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory import embeddings
from memory._embeddings import spec
from memory._embeddings.resources import _change_stamp
from memory.embedding_worker import EmbeddingWarmupWorker, BATCH_SIZE

pytestmark = pytest.mark.unit_fast


def _service(monkeypatch, tmp_path):
    monkeypatch.setattr(embeddings, '_cpu_is_blocklisted', lambda:False)
    service = embeddings.EmbeddingService(model_dir=str(tmp_path), ram_gb=8, has_vnni=True, has_avx2=True)
    for path in (service._model_file_path(),service._model_file_path()+'_data',service._tokenizer_file_path()):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b'first synthetic resource')
    return service


def test_resource_cache_rejects_equal_length_overwrite_with_restored_mtime(tmp_path, monkeypatch):
    service = _service(monkeypatch,tmp_path)
    first = service.spec()
    path = Path(service._model_file_path())
    stamp = path.stat()
    # The change stamp comes from a coarse kernel clock; back-to-back writes
    # can share one tick, so retry until it observably moves.
    before = _change_stamp(path, stamp)
    deadline = time.monotonic() + 2
    while True:
        path.write_bytes(b'other synthetic resource')
        os.utime(path, ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        if _change_stamp(path, path.stat()) != before or time.monotonic() > deadline:
            break
        time.sleep(0.01)
    assert service.spec()['weights_sha256']['model'] != first['weights_sha256']['model']


def test_equivalent_dotted_resource_paths_work_after_native_load(tmp_path,monkeypatch):
    service = _service(monkeypatch,tmp_path)
    service._model_dir = str(tmp_path) + '/./'
    from memory._embeddings.resources import resource_file_identity
    service._loaded_file_identity = resource_file_identity([service._model_file_path(),service._model_file_path()+'_data',service._tokenizer_file_path()])
    assert service.spec() is not None


def test_counting_tokenizer_is_not_cached_if_resource_changes_during_load(tmp_path,monkeypatch):
    tokenizers = pytest.importorskip('tokenizers')
    service = _service(monkeypatch,tmp_path)
    path = Path(service._tokenizer_file_path())
    tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel({'[UNK]':0},unk_token='[UNK]'))
    tokenizer.save(str(path))
    specification = spec.specification

    def replace_after_fingerprint(*args,**kwargs):
        expected = specification(*args,**kwargs)
        path.write_text(path.read_text()+' ')
        return expected

    monkeypatch.setattr(spec,'specification', replace_after_fingerprint)
    spec._tokenizers.clear()
    assert service.count_tokens(['private text']) is None
    assert not spec._tokenizers


async def test_expired_query_never_submits_native_work(tmp_path,monkeypatch):
    service = _service(monkeypatch,tmp_path)
    service._state = embeddings.EmbeddingState.READY
    calls = []

    async def infer(*_):
        calls.append(True)
        return [[1.0]]

    service._run_inference = infer
    try:
        result = await service.embed_ex(['query'],deadline=asyncio.get_running_loop().time()-1)
        assert result.status == 'timeout' and not calls
    finally:
        await service.close()


async def test_completed_backfill_batches_are_applied_before_deferral():
    from utils.embedding_contract import EmbedDeferred
    worker = object.__new__(EmbeddingWarmupWorker)
    texts = [str(i) for i in range(BATCH_SIZE+1)]
    applied = []
    calls = []

    async def embed(chunk, **_):
        calls.append(chunk)
        if len(calls)>1:
            raise EmbedDeferred(10)
        return [[1.0] for _ in chunk]

    async def barrier(step):
        return await step()

    async def plan():
        return [('synthetic','fact',texts)]

    async def apply(plan,vectors,model):
        applied.append((vectors,model))
        return len(vectors)

    worker._service = SimpleNamespace(model_id=lambda:'trusted',embed_documents=embed)
    worker._under_sweep_barrier = barrier
    worker._collect_sweep_plan = plan
    worker._apply_sweep_plan = apply
    with pytest.raises(EmbedDeferred):
        await worker._sweep_once()
    assert applied == [({text:[1.0] for text in texts[:BATCH_SIZE]},'trusted')]
