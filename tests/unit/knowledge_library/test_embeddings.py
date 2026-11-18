"""Exercise the real computation API with an isolated, deterministic encoder."""

import hashlib
import asyncio
import threading
import os

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import library_embeddings as compute

pytestmark = pytest.mark.unit


class Encoder:
    """Record lifecycle and non-sticky inference without loading native models."""

    _profile_id = "test-profile"

    def __init__(self, root):
        self.root = root
        self.loads = 0
        self.calls = []
        self.priorities = []
        for name in ("model.onnx", "model.onnx_data", "tokenizer.json"):
            (root / name).write_bytes(name.encode())
        # A real deterministic tokenizer makes the API test actual token
        # accounting, including post-processor tokens, rather than character counts.
        tokenizers = pytest.importorskip("tokenizers")
        tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel({"[UNK]": 0, "x": 1}, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Split("", behavior="isolated")
        tokenizer.post_processor = tokenizers.processors.TemplateProcessing(
            single="[BOS] $A [EOS]", special_tokens=[("[BOS]", 2), ("[EOS]", 3)],
        )
        tokenizer.save(str(root / "tokenizer.json"))

    def model_id(self):
        return "test-profile:2:fp32:1024"

    def is_disabled(self):
        return False

    def dim(self):
        return 2

    def quantization(self):
        return "fp32"

    def _model_file_path(self):
        return self.root / "model.onnx"

    def _tokenizer_file_path(self):
        return self.root / "tokenizer.json"

    async def request_load(self):
        self.loads += 1
        return True

    async def embed_batch(self, texts, *, sticky_failure, priority="foreground"):
        self.calls.append((texts, sticky_failure))
        self.priorities.append(priority)
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """Use a bare app while preserving the production router's dependencies."""
    encoder = Encoder(tmp_path)
    monkeypatch.setattr(compute, "_encoder", lambda: encoder)
    compute._fingerprints.clear()
    app = FastAPI()
    app.include_router(compute.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http, encoder


async def test_spec_binds_actual_files_without_loading_model(client):
    http, encoder = client
    result = (await http.get("/internal/knowledge-library/embeddings/spec")).json()
    spec = result["specification"]
    assert encoder.loads == 0
    assert spec["weights_sha256"]["model"] == hashlib.sha256(b"model.onnx").hexdigest()
    assert spec["processing_version"] == "neko-onnx-last-token-truncate-l2-v1"
    assert spec["dimension"] == 2


async def test_browser_requests_are_refused_before_computation(client):
    http, encoder = client
    for header in ("Origin", "Referer"):
        result = await http.post("/internal/knowledge-library/embeddings/batch", headers={header: "http://localhost"})
        assert result.status_code == 403
    assert not encoder.calls


async def test_explicit_batch_loads_once_and_uses_non_sticky_inference(client):
    http, encoder = client
    spec = (await http.get("/internal/knowledge-library/embeddings/spec")).json()["specification"]
    response = await http.post("/internal/knowledge-library/embeddings/batch", json={"texts": ["private source"], "specification": spec})
    assert response.json()["vectors"] == [[1.0, 0.0]]
    assert encoder.loads == 1
    assert encoder.calls == [(["private source"], False)]


async def test_identity_drift_and_oversized_input_do_not_run_model(client):
    http, encoder = client
    for texts, spec, status in [(["x"], {}, 409), (["x" * 2001], {}, 400), (["x"] * 17, {}, 400)]:
        response = await http.post("/internal/knowledge-library/embeddings/batch", json={"texts": texts, "specification": spec})
        assert response.status_code == status
    assert encoder.loads == 0


async def test_missing_files_report_unavailable_without_paths(client):
    http, encoder = client
    (encoder.root / "model.onnx_data").unlink()
    response = await http.get("/internal/knowledge-library/embeddings/spec")
    assert response.status_code == 503
    assert response.json() == {"ok": False, "reason": "embedding_unavailable"}


async def test_body_and_content_type_are_bounded(client):
    http, encoder = client
    endpoint = "/internal/knowledge-library/embeddings/batch"
    assert (await http.post(endpoint, content="texts=x")).status_code == 415
    response = await http.post(endpoint, content=b" " * (compute.MAX_BODY_BYTES + 1), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert not encoder.calls


async def test_main_adapter_uses_same_bounded_api_without_browser_credentials(client, monkeypatch):
    """Both process layouts use the real JSON contract, with 16-row RPC limits."""
    from services.knowledge_library.host import HttpEmbeddingAdapter

    http, encoder = client
    monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: http)
    adapter = HttpEmbeddingAdapter()
    spec = await adapter.specification()
    rows = await adapter.embed_batch(["reference"] * 17)
    assert spec["weights_sha256"]["tokenizer"] == hashlib.sha256((encoder.root / "tokenizer.json").read_bytes()).hexdigest()
    assert len(rows) == 17
    assert [len(call[0]) for call in encoder.calls] == [16, 1]
    assert all(call[1] is False for call in encoder.calls)


async def test_plan_covers_unicode_source_without_loading_or_truncating(client):
    """Every original character survives and every actual model input fits."""
    http, encoder = client
    spec = (await http.get("/internal/knowledge-library/embeddings/spec")).json()["specification"]
    body = "龘🧑🏽‍💻" * 240
    prefix = "private-source.txt\n"
    response = await http.post("/internal/knowledge-library/embeddings/plan", json={
        "inputs": [{"prefix": prefix, "body": body}], "specification": spec,
    })
    assert response.status_code == 200, response.text
    parts = response.json()["parts"][0]
    assert len(parts) > 1
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(encoder.root / "tokenizer.json"))
    position, rebuilt = 0, ""
    for part in parts:
        assert part["start"] == position < part["end"] <= len(body)
        piece = body[part["start"]:part["end"]]
        assert len(tokenizer.encode(prefix + piece).ids) <= spec["max_length"]
        rebuilt += piece
        position = part["end"]
    assert rebuilt == body
    assert encoder.loads == 0
    assert encoder.calls == []


async def test_batch_rejects_token_overflow_before_model_load(client):
    http, encoder = client
    spec = (await http.get("/internal/knowledge-library/embeddings/spec")).json()["specification"]
    response = await http.post("/internal/knowledge-library/embeddings/batch", json={
        "texts": ["x" * 1200], "specification": spec,
    })
    assert response.status_code == 422
    assert response.json() == {"ok": False, "reason": "input_token_limit"}
    assert not encoder.calls
    assert encoder.loads == 0


async def test_plan_checks_spec_and_bounded_input(client):
    http, _encoder = client
    endpoint = "/internal/knowledge-library/embeddings/plan"
    spec = (await http.get("/internal/knowledge-library/embeddings/spec")).json()["specification"]
    for inputs in ([], [{"prefix": "x" * 201, "body": "x"}], [{"prefix": "", "body": "x" * 1201}]):
        assert (await http.post(endpoint, json={"inputs": inputs, "specification": spec})).status_code == 400
    assert (await http.post(endpoint, json={"inputs": [{"prefix": "", "body": "x"}], "specification": {}})).status_code == 409


async def test_planning_ignores_saved_truncation_without_mutating_its_resource(client):
    from tokenizers import Tokenizer

    http, encoder = client
    path = encoder.root / "tokenizer.json"
    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.enable_truncation(max_length=16)
    tokenizer.save(str(path))
    original = path.read_bytes()
    spec = (await http.get("/internal/knowledge-library/embeddings/spec")).json()["specification"]
    response = await http.post("/internal/knowledge-library/embeddings/plan", json={
        "specification": spec, "inputs": [{"prefix": "name\n", "body": "x" * 1200}],
    })
    assert response.status_code == 200
    assert len(response.json()["parts"][0]) == 2
    assert path.read_bytes() == original
    assert Tokenizer.from_file(str(path)).truncation["max_length"] == 16
    assert encoder.loads == 0


async def test_adapter_uses_explicit_task_identity_after_shared_cache_refresh(client, monkeypatch):
    from services.knowledge_library.host import HttpEmbeddingAdapter

    http, encoder = client
    monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: http)
    adapter = HttpEmbeddingAdapter()
    spec = await adapter.specification()
    adapter._spec = {**spec, "dimension": 999}
    parts = await adapter.plan_inputs([{"prefix": "title\n", "body": "reference"}], expected_spec=spec)
    assert parts == [[{"start": 0, "end": 9}]]
    assert await adapter.embed_background(["reference"], expected_spec=spec) == [[1.0, 0.0]]
    assert encoder.priorities == ["background"]
    assert adapter._spec["dimension"] == 999


async def test_batch_discards_results_when_resources_change_during_inference(client, monkeypatch):
    http, encoder = client
    original = encoder.embed_batch

    async def change_resources(texts, **kwargs):
        rows = await original(texts, **kwargs)
        (encoder.root / "model.onnx").write_bytes(b"replacement model identity")
        return rows

    monkeypatch.setattr(encoder, "embed_batch", change_resources)
    spec = (await http.get("/internal/knowledge-library/embeddings/spec")).json()["specification"]
    response = await http.post("/internal/knowledge-library/embeddings/batch", json={
        "texts": ["reference"], "specification": spec,
    })
    assert response.status_code == 409
    assert response.json() == {"ok": False, "reason": "embedding_incompatible"}


async def test_loaded_session_is_not_relabelled_with_replaced_disk_weights(client):
    from memory._embeddings.resources import resource_file_identity

    http, encoder = client
    paths = [encoder.root / name for name in ("model.onnx", "model.onnx_data", "tokenizer.json")]
    encoder._loaded_file_identity = resource_file_identity(paths)
    (encoder.root / "model.onnx_data").write_bytes(b"different external weights after model load")
    response = await http.get("/internal/knowledge-library/embeddings/spec")
    assert response.status_code == 503
    assert response.json() == {"ok": False, "reason": "embedding_unavailable"}


@pytest.mark.parametrize("loaded", [False, True])
async def test_same_size_and_preserved_mtime_replacement_invalidates_resource_identity(client, loaded):
    """A copy-style replacement must invalidate hashes and already loaded sessions."""
    from memory._embeddings.resources import resource_file_identity

    http, encoder = client
    endpoint = "/internal/knowledge-library/embeddings/spec"
    before_spec = (await http.get(endpoint)).json()["specification"]
    paths = [encoder.root / name for name in ("model.onnx", "model.onnx_data", "tokenizer.json")]
    if loaded:
        encoder._loaded_file_identity = resource_file_identity(paths)
    path = paths[0]
    old_stat = path.stat()
    replacement = path.with_suffix(".replacement")
    raw = b"Z" * old_stat.st_size
    replacement.write_bytes(raw)
    os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    os.replace(replacement, path)
    current_stat = path.stat()
    assert (current_stat.st_size, current_stat.st_mtime_ns) == (old_stat.st_size, old_stat.st_mtime_ns)
    response = await http.get(endpoint)
    if loaded:
        assert response.status_code == 503
        assert response.json() == {"ok": False, "reason": "embedding_unavailable"}
    else:
        assert response.status_code == 200
        actual = response.json()["specification"]["weights_sha256"]["model"]
        assert actual == hashlib.sha256(raw).hexdigest()
        assert actual != before_spec["weights_sha256"]["model"]


@pytest.mark.parametrize("cancel_first", [False, True])
@pytest.mark.parametrize("operation", ["batch", "plan"])
async def test_preflight_admission_is_bounded_and_drains_cancelled_workers(client, monkeypatch, cancel_first, operation):
    """Admission covers tokenizer/hash/load work before the native inference queue."""
    http, _encoder = client
    entered, release = threading.Event(), threading.Event()
    active = 0
    callback = "_check_token_lengths" if operation == "batch" else "_plan_parts"
    original = getattr(compute, callback)

    def preflight(*_args):
        nonlocal active
        active += 1
        entered.set()
        try:
            assert release.wait(3)
            return original(*_args)
        finally:
            active -= 1

    monkeypatch.setattr(compute, callback, preflight)
    spec = (await http.get("/internal/knowledge-library/embeddings/spec")).json()["specification"]
    payload = ({"texts": ["private"], "priority": "background", "specification": spec} if operation == "batch"
               else {"inputs": [{"prefix": "name\n", "body": "private"}], "specification": spec})
    endpoint = "/internal/knowledge-library/embeddings/" + operation
    first = asyncio.create_task(http.post(endpoint, json=payload))
    tasks = [first]
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if cancel_first:
            first.cancel()
            await asyncio.sleep(0)
        other = asyncio.create_task(http.post(endpoint, json=payload))
        tasks.append(other)
        done, _ = await asyncio.wait({other}, timeout=0.3)
        assert done, "Excess work must be rejected before starting another preprocessing thread"
        assert other.result().status_code == 429
        assert other.result().json() == {"ok": False, "reason": "embedding_busy"}
        assert active == 1
        # GET/spec retains separate foreground capacity even during a build.
        assert (await http.get("/internal/knowledge-library/embeddings/spec")).status_code == 200
        if cancel_first:
            assert not first.done()
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert active == 0
