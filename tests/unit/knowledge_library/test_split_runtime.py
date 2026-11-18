"""Opt-in native computation over TCP, including an owning-process restart."""

import asyncio
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from main_routers import knowledge_library_router as routes
from services.knowledge_library.host import LibraryHost
from tests.process_isolation import child_process_env
from tests.unit.knowledge_library.test_routes import HEADERS, import_text, wait_job


pytestmark = [pytest.mark.integration_serial, pytest.mark.skipif(
    not os.environ.get("NEKO_REAL_EMBEDDING_ROOT"),
    reason="requires an explicit read-only native model root",
)]


_MEMORY_CHILD = r'''
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys

from tests.process_isolation import isolate_test_process
isolate_test_process()
from fastapi import FastAPI
import uvicorn
from app.memory_server import library_embeddings as compute
from memory.embeddings import EmbeddingService

root, model_root, dimension = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
owner = EmbeddingService(model_dir=model_root, embedding_dim_setting=dimension)
compute._encoder = lambda: owner

@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await owner.close()

app = FastAPI(lifespan=lifespan)
app.include_router(compute.router)

@app.middleware("http")
async def interruption_checkpoint(request, call_next):
    # Pause only this harness's next batch before native work. Killing the
    # process here tests real transport loss, not native-thread cancellation.
    if request.url.path.endswith("/batch") and (root / "hold-batch").exists():
        (root / "batch-entered").write_text("ready", encoding="utf-8")
        while (root / "hold-batch").exists():
            await asyncio.sleep(0.02)
    return await call_next(request)

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen(128)
(root / "endpoint.part").write_text(json.dumps({"port": sock.getsockname()[1]}), encoding="utf-8")
os.replace(root / "endpoint.part", root / "endpoint.json")
server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
server.run(sockets=[sock])
'''


def _stop_memory(process):
    """Reap only the test-owned child; never leave a native owner running."""
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


async def _start_memory(root, dimension, monkeypatch, processes):
    """Publish an OS-assigned loopback endpoint after the actual spec responds."""
    root.mkdir()
    # File-backed child output avoids a full pipe blocking a long-running
    # server and keeps diagnostic logs inside the disposable test directory.
    with (root / "server.log").open("wb") as output:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-u", "-c", _MEMORY_CHILD, str(root),
             os.environ["NEKO_REAL_EMBEDDING_ROOT"], str(dimension)],
            env={**child_process_env(root / "runtime"), "PYTHONIOENCODING": "utf-8"},
            stdout=output, stderr=subprocess.STDOUT,
        )
    processes.append(process)
    deadline = time.monotonic() + 30
    async with httpx.AsyncClient(trust_env=False) as http:
        while time.monotonic() < deadline and process.poll() is None:
            endpoint = root / "endpoint.json"
            if endpoint.is_file():
                port = json.loads(endpoint.read_text(encoding="utf-8"))["port"]
                try:
                    response = await http.get(
                        f"http://127.0.0.1:{port}/internal/knowledge-library/embeddings/spec", timeout=0.5,
                    )
                    if response.status_code == 200:
                        monkeypatch.setattr("config.MEMORY_SERVER_PORT", port)
                        return response.json()["specification"]
                except httpx.HTTPError:
                    pass  # The bound socket may precede ASGI startup by a moment.
            await asyncio.sleep(0.02)
    raise AssertionError("isolated native computation server did not become ready")


async def test_native_tcp_loss_frozen_retry_and_lower_dimension_restart(tmp_path, monkeypatch):
    """Keep committed data across a real Memory process loss and explicit retry."""
    processes = []
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes, "get_config_manager", lambda: SimpleNamespace(app_docs_dir=tmp_path / "main"))
    # Storage-maintenance admission has its own API coverage. This harness
    # isolates that host dependency while retaining the real jobs and files.
    monkeypatch.setattr(LibraryHost, "write_guard", lambda self: nullcontext())
    report = {}
    try:
        specification = await _start_memory(tmp_path / "memory-256", 256, monkeypatch, processes)
        assert specification["dimension"] == 256
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            prefix = "/api/knowledge-library"
            library = (await http.post(prefix + "/libraries", headers=HEADERS,
                                       json={"name": "Split runtime references"})).json()["library"]
            identifier = library["library_id"]
            await import_text(http, identifier, "lighthouse.txt", "The violet lighthouse stands on a rocky coast.")
            documents = (await http.get(f"{prefix}/libraries/{identifier}")).json()["documents"]
            assert all(document["semantic_state"] == "ready" for document in documents)
            selection = {"document_ids": [documents[0]["document_id"]], "version": "native-tcp-1",
                         "source": {"author": "Synthetic fixture", "license": "CC0-1.0", "url": ""}}
            first = await http.post(f"{prefix}/libraries/{identifier}/packages", headers=HEADERS, json=selection)
            artifact = await wait_job(http, first.json()["job"])
            original = await http.get(prefix + "/packages/" + artifact["artifact_id"] + "/download")
            assert original.status_code == 200
            original_hash = hashlib.sha256(original.content).hexdigest()
            host = app.state.knowledge_library_host
            service = await host.service()

            # Stop the actual encoder process with a build request in flight.
            # No half-built artifact may be registered or replace the old one.
            gate = tmp_path / "memory-256/hold-batch"
            gate.write_text("hold", encoding="utf-8")
            second = await http.post(f"{prefix}/libraries/{identifier}/packages", headers=HEADERS,
                                     json={**selection, "version": "native-tcp-2"})
            assert second.status_code == 200
            job_id = second.json()["job"]["job_id"]
            frozen = deepcopy(service.jobs.records[job_id]["frozen_input"])
            deadline = time.monotonic() + 15
            while not (gate.parent / "batch-entered").exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert (gate.parent / "batch-entered").exists(), "build never reached real TCP checkpoint"
            await asyncio.to_thread(_stop_memory, processes[-1])
            await asyncio.wait_for(service.jobs.queue.join(), 10)
            failed = (await http.get(prefix + "/jobs/" + job_id)).json()["job"]
            assert failed["state"] == "failed" and failed["reason"] == "embedding_unavailable", failed
            assert len((await http.get(prefix + "/packages")).json()["packages"]) == 1
            assert hashlib.sha256((await http.get(prefix + "/packages/" + artifact["artifact_id"] + "/download")).content).hexdigest() == original_hash
            fallback = await http.post(f"{prefix}/libraries/{identifier}/search", headers=HEADERS,
                                       json={"query": "lighthouse"})
            assert fallback.status_code == 200 and fallback.json()["semantic_state"] == "unavailable"
            assert fallback.json()["hits"][0]["match_type"] == "keyword"

            # A new process with the same real resources can retry precisely
            # the admitted snapshot; the interrupted attempt is not rewritten.
            restarted = await _start_memory(tmp_path / "memory-restarted", 256, monkeypatch, processes)
            assert restarted == specification
            retry = await http.post(prefix + "/jobs/" + job_id + "/retry", headers=HEADERS)
            assert retry.status_code == 200, retry.text
            rebuilt = await wait_job(http, retry.json()["job"])
            assert service.jobs.records[retry.json()["job"]["job_id"]]["frozen_input"] == frozen
            assert service.jobs.records[job_id]["state"] == "failed"
            assert rebuilt["artifact_id"] != artifact["artifact_id"]
            await asyncio.to_thread(_stop_memory, processes[-1])

            # A lower-dimensional native owner is a fresh process, not a
            # mutation of the author's encoder instance or stored vectors.
            lower = await _start_memory(tmp_path / "memory-128", 128, monkeypatch, processes)
            assert lower["dimension"] == 128
            install_gate = tmp_path / "memory-128/hold-batch"
            install_gate.write_text("hold", encoding="utf-8")
            installed_response = await http.post(prefix + "/packages/install", headers=HEADERS,
                files={"file": ("native.neko-knowledge-pack.zip", original.content, "application/zip")})
            assert installed_response.status_code == 200, installed_response.text
            installed = await wait_job(http, installed_response.json()["job"])
            assert not (install_gate.parent / "batch-entered").exists(), "installation requested native vectors"
            install_gate.unlink()

            # Recreate Main's domain host from its saved files. The Memory
            # process remains independent; no in-memory job/library map is reused.
            await host.close()
            del app.state.knowledge_library_host
            restored = (await http.get(f"{prefix}/libraries/{installed['library_id']}")).json()
            assert restored["library"]["library_id"] == installed["library_id"]
            assert restored["documents"][0]["document_id"] == documents[0]["document_id"]
            found = await http.post(f"{prefix}/libraries/{installed['library_id']}/search", headers=HEADERS,
                                    json={"query": "Where is the navigation beacon located?"})
            assert found.status_code == 200 and found.json()["semantic_state"] == "ready", found.text
            assert found.json()["hits"][0]["filename"] == "lighthouse.txt"
            assert found.json()["hits"][0]["match_type"] in {"hybrid", "semantic"}
            assert hashlib.sha256((await http.get(prefix + "/packages/" + artifact["artifact_id"] + "/download")).content).hexdigest() == original_hash
            report.update(author_dimension=256, consumer_dimension=128, native_processes=len(processes),
                          interrupted_state=failed["state"], retry_state="completed", keyword_fallback=True,
                          artifact_unchanged=True, frozen_input_unchanged=True, install_batches=0,
                          main_host_recreated=True)
    finally:
        # Release a pending transport even when an assertion fails at the
        # checkpoint; closing Main first could wait for the held HTTP request.
        for process in processes:
            await asyncio.to_thread(_stop_memory, process)
        host = getattr(app.state, "knowledge_library_host", None)
        if host is not None:
            await host.close()
    output = Path(os.environ.get("NEKO_SPLIT_KB_REPORT", str(tmp_path / "split-runtime.json")))
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
