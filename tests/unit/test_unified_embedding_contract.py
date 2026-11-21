"""Knowledge borrows memory's encoder only through its own adapter.

Memory's paths (``embed`` / ``embed_batch``, the warmup worker, model loading)
stay as on main; scheduling and the model fingerprint live in the knowledge
adapter and run on demand. Synthetic resources only, no real model.
"""

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.memory_server.knowledge_routes import _SharedEmbedder
from knowledge.chunking import derive_chunks
from knowledge.models import KnowledgeEntry
from memory import embeddings
from memory._embeddings import spec as spec_module
from memory._embeddings.resources import _change_stamp

pytestmark = pytest.mark.unit_fast


def _service(monkeypatch, tmp_path, *, ready=False):
    """A real service over synthetic resource files; nothing loads ONNX."""
    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: False)
    service = embeddings.EmbeddingService(model_dir=str(tmp_path), ram_gb=8,
                                          has_vnni=True, has_avx2=True)
    if ready:
        service._state = embeddings.EmbeddingState.READY
    for path in (service._model_file_path(), service._model_file_path() + "_data",
                 service._tokenizer_file_path()):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"first synthetic resource")
    return service


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
    service = _service(monkeypatch, tmp_path)
    tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel({"x": 1, "[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(max_length=4)
    path = Path(service._tokenizer_file_path())
    tokenizer.save(str(path))
    original = path.read_bytes()
    shared = _SharedEmbedder(service)
    spec = shared.spec()
    assert spec["dimension"] == service.dim()
    assert spec["max_length"] == embeddings.DEFAULT_VECTORS_MAX_LENGTH
    assert shared.count_tokens(["x " * 20]) == [20]
    assert not service.is_available() and service._session is None
    assert path.read_bytes() == original


def test_model_loading_never_computes_the_fingerprint(tmp_path, monkeypatch):
    """The share-only fingerprint stays off memory's loading path."""
    service = _service(monkeypatch, tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("fingerprint computed while loading")

    monkeypatch.setattr(spec_module, "specification", forbidden)
    # Stop right after the file checks: the import is where ONNX loading starts.
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", None)
    with pytest.raises(embeddings._DisabledError) as raised:
        service._load_session_blocking()
    assert raised.value.reason is embeddings._DisableReason.NO_ONNXRUNTIME


def test_unreadable_fingerprint_affects_knowledge_only(tmp_path, monkeypatch):
    service = _service(monkeypatch, tmp_path, ready=True)
    Path(service._model_file_path() + "_data").unlink()
    shared = _SharedEmbedder(service)
    assert shared.spec() is None
    assert shared.count_tokens(["text"]) is None
    assert service.is_available() and not service.is_disabled()


def test_adapter_without_a_service_or_with_the_fallback_has_no_spec():
    assert _SharedEmbedder(None).spec() is None
    assert _SharedEmbedder(None).count_tokens(["text"]) is None
    from memory.embeddings_fallback import get_embedding_service
    fallback = _SharedEmbedder(get_embedding_service())
    assert fallback.spec() is None
    assert fallback.count_tokens(["text"]) is None


def test_resource_spec_ignores_permission_metadata(tmp_path, monkeypatch):
    service = _service(monkeypatch, tmp_path)
    shared = _SharedEmbedder(service)
    before = shared.spec()
    path = Path(service._model_file_path())
    original_mode = path.stat().st_mode
    try:
        path.chmod(0o600)
        assert shared.spec() == before
    finally:
        path.chmod(original_mode)


def test_resource_cache_rejects_equal_length_overwrite_with_restored_mtime(tmp_path, monkeypatch):
    service = _service(monkeypatch, tmp_path)
    shared = _SharedEmbedder(service)
    first = shared.spec()
    path = Path(service._model_file_path())
    stamp = path.stat()
    # The change stamp comes from a coarse kernel clock; back-to-back writes
    # can share one tick, so retry until it observably moves.
    before = _change_stamp(path, stamp)
    deadline = time.monotonic() + 2
    while True:
        path.write_bytes(b"other synthetic resource")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        if _change_stamp(path, path.stat()) != before or time.monotonic() > deadline:
            break
        time.sleep(0.01)
    assert shared.spec()["weights_sha256"]["model"] != first["weights_sha256"]["model"]


def test_equivalent_dotted_resource_paths_are_fingerprinted(tmp_path, monkeypatch):
    service = _service(monkeypatch, tmp_path)
    service._model_dir = str(tmp_path) + "/./"
    assert _SharedEmbedder(service).spec() is not None


def test_counting_tokenizer_is_not_cached_if_resource_changes_during_load(tmp_path, monkeypatch):
    tokenizers = pytest.importorskip("tokenizers")
    service = _service(monkeypatch, tmp_path)
    path = Path(service._tokenizer_file_path())
    tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.save(str(path))
    specification = spec_module.specification

    def replace_after_fingerprint(*args, **kwargs):
        expected = specification(*args, **kwargs)
        path.write_text(path.read_text() + " ")
        return expected

    monkeypatch.setattr(spec_module, "specification", replace_after_fingerprint)
    spec_module._tokenizers.clear()
    assert _SharedEmbedder(service).count_tokens(["private text"]) is None
    assert not spec_module._tokenizers


class _FakeService:
    """Records memory's public calls; one index batch can be held open."""

    def __init__(self):
        self.calls = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.running = 0
        self.peak = 0

    def is_available(self):
        return True

    async def embed(self, text, *, sticky_failure=True):
        self.calls.append(("embed", text, sticky_failure))
        return [1.0]

    async def embed_batch(self, texts, *, sticky_failure=True):
        self.calls.append(("embed_batch", list(texts), sticky_failure))
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await self.gate.wait()
            return [[1.0] for _ in texts]
        finally:
            self.running -= 1


async def test_knowledge_index_batches_run_one_at_a_time_without_queueing_queries():
    service = _FakeService()
    service.gate.clear()
    shared = _SharedEmbedder(service)
    first = asyncio.create_task(shared.embed_documents(["a", "b"]))
    second = asyncio.create_task(shared.embed_documents(["c"]))
    async with asyncio.timeout(1):
        while service.running == 0:
            await asyncio.sleep(0)
        # A knowledge query is not queued behind the running index batch.
        assert await shared.embed_query("question") == [1.0]
        service.gate.set()
        assert await first == [[1.0], [1.0]]
        assert await second == [[1.0]]
    assert service.peak == 1
    assert ("embed", "question", False) in service.calls
    # Knowledge never asks memory for sticky failures.
    assert all(call[2] is False for call in service.calls)


async def test_knowledge_documents_isolate_a_bad_row_without_disabling_memory():
    service = _FakeService()

    async def all_missing(texts, *, sticky_failure=True):
        service.calls.append(("embed_batch", list(texts), sticky_failure))
        return [None] * len(texts)

    service.embed_batch = all_missing
    vectors = await _SharedEmbedder(service).embed_documents(["good", "bad"])
    assert vectors == [[1.0], [1.0]]
    assert [call[0] for call in service.calls] == ["embed_batch", "embed", "embed"]
