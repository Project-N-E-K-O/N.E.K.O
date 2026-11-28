"""Synthetic packs, embedders and explicit controls for isolated knowledge tests.

Import fixtures explicitly in each test module so their scope stays local.
Factories return fresh data; callers own service teardown and temporary roots.
This module never imports a collected test module or changes runtime defaults.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

import services.knowledge.service as service_module
from services.knowledge import indexing as index_module


def _pack(pack_id: str = "demo-memes", *, material_type: str = "knowledge", entries=None) -> dict:
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "material_type": material_type,
        "source": {"name": "Demo", "homepage": "https://example.invalid", "license": "CC0-1.0"},
        "entries": entries
        or [
            {
                "title": "绝绝子",
                "terms": {"alias": ["jjz"], "recognition": ["绝绝子是什么意思"]},
                "tags": ["domain:meme"],
                "summary": "表示极好的网络用语。",
                "content": "绝绝子是网络流行语，表示非常好，常用来夸赞。",
            },
            {
                "title": "Rubber duck debugging",
                "terms": {"alias": ["rubber ducking"]},
                "tags": ["domain:programming"],
                "summary": "Explaining code aloud to find bugs.",
                "content": "Rubber duck debugging means explaining your code line by line to a duck.",
            },
        ],
    }


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class FakeEmbedder:
    def __init__(self, *, model_id: str = "fake-16", state: str = "ready", delay: float = 0.0):
        self._model_id = model_id
        self._state = state
        self.delay = delay
        self.batches: list[int] = []

    def state(self) -> str:
        return self._state

    def model_id(self) -> str | None:
        return self._model_id if self._state == "ready" else None

    @staticmethod
    def vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()[:16]
        return (np.frombuffer(digest, dtype=np.uint8).astype(float) - 127.5).tolist()

    async def embed(self, text: str):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.vector(text)

    async def embed_batch(self, texts):
        self.batches.append(len(texts))
        return [self.vector(text) for text in texts]


@pytest.fixture
def fast_indexer(monkeypatch):
    monkeypatch.setattr(index_module, "INDEX_IDLE_SECONDS", 0.05)
    monkeypatch.setattr(index_module, "INDEX_BATCH_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(index_module, "INDEX_ROUND_PAUSE_SECONDS", 0.01)


async def _started(root: Path, embedder=None) -> service_module.KnowledgeService:
    service = service_module.KnowledgeService(root, embedder=embedder)
    await service.start()
    return service


async def _import(service: service_module.KnowledgeService, payload: dict) -> dict:
    result = await service.import_pack(_raw(payload))
    assert result["ok"] is True, result
    if result.get("unchanged"):
        return result
    for _ in range(200):
        job = next(job for job in service.list_jobs() if job["job_id"] == result["job_id"])
        if job["state"] not in ("queued", "building"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("import job did not finish")


def _entries(prefix: str, count: int, word: str = "kotatsu") -> list[dict]:
    return [
        {"title": f"{prefix} {i}", "summary": word, "content": f"{word} {word} note {i}"}
        for i in range(count)
    ]


async def _finished(service, result):
    """Wait for the existing import runner's observable terminal state."""
    for _ in range(300):
        job = next(item for item in service.list_jobs() if item["job_id"] == result["job_id"])
        if job["state"] not in {"queued", "building"}:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("import did not finish")


async def _manual_index(service):
    """Leave the real import runner alive while controlling index rounds."""
    service._tasks[1].cancel()
    await asyncio.gather(service._tasks[1], return_exceptions=True)


def _updated_pack() -> dict:
    payload = _pack()
    payload["entries"][0]["summary"] = "NEWER"
    return payload


async def _wait_for_last_job(service) -> list[str]:
    for _ in range(200):
        states = [job["state"] for job in service.list_jobs()]
        if states and states[0] not in ("queued", "building"):
            break
        await asyncio.sleep(0.01)
    return states
