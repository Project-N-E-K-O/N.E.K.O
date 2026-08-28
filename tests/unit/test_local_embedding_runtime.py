from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_local_embedding_provider():
    from utils import local_embedding_runtime

    local_embedding_runtime.reset_local_embedding_provider_for_tests()
    yield
    local_embedding_runtime.reset_local_embedding_provider_for_tests()


class _Service:
    def __init__(self, state: str):
        self.state = state

    def is_available(self):
        return self.state == "ready"

    def is_disabled(self):
        return self.state == "disabled"

    def disable_reason(self):
        return "fixture_disabled"

    def model_id(self):
        return "fixture-3d-fp32"

    def dim(self):
        return 3


def test_unbound_local_embedding_provider_fails_closed():
    from utils import local_embedding_runtime

    status = local_embedding_runtime.get_local_embedding_status()

    assert status.state == "disabled"
    assert status.disable_reason == "provider_unconfigured"


@pytest.mark.asyncio
async def test_bound_local_embedding_provider_is_released():
    from utils import local_embedding_runtime

    service = _Service("ready")
    released: list[bool] = []

    async def release() -> None:
        released.append(True)

    local_embedding_runtime.bind_local_embedding_provider(
        lambda: service,
        release,
    )

    assert local_embedding_runtime.get_local_embedding_service() is service
    await local_embedding_runtime.release_local_embedding_service()
    assert released == [True]


@pytest.mark.asyncio
async def test_process_provider_binds_legacy_owner(monkeypatch):
    from memory import local_embedding_provider
    from utils import local_embedding_runtime

    service = _Service("ready")
    released: list[bool] = []

    async def release() -> None:
        released.append(True)

    monkeypatch.setattr(
        local_embedding_provider,
        "_get_embedding_service",
        lambda: service,
    )
    monkeypatch.setattr(
        local_embedding_provider,
        "_release_embedding_service",
        release,
    )

    local_embedding_provider.bind_process_local_embedding_provider()

    assert local_embedding_runtime.get_local_embedding_service() is service
    await local_embedding_runtime.release_local_embedding_service()
    assert released == [True]


def test_shared_local_embedding_status_is_domain_neutral(monkeypatch):
    from utils import local_embedding_runtime

    monkeypatch.setattr(
        local_embedding_runtime,
        "get_local_embedding_service",
        lambda: _Service("ready"),
    )
    status = local_embedding_runtime.get_local_embedding_status()
    assert status.ready
    assert status.model_id == "fixture-3d-fp32"
    assert status.dimensions == 3


def test_shared_local_embedding_status_reports_disabled_reason(monkeypatch):
    from utils import local_embedding_runtime

    monkeypatch.setattr(
        local_embedding_runtime,
        "get_local_embedding_service",
        lambda: _Service("disabled"),
    )
    status = local_embedding_runtime.get_local_embedding_status()
    assert status.state == "disabled"
    assert status.disable_reason == "fixture_disabled"


def test_knowledge_runtime_imports_no_memory_server_business_module():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for relative_path in (
        "knowledge/vector_index.py",
        "knowledge/indexer.py",
        "knowledge/service.py",
    ):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "app.memory_server" not in source
        assert "/internal/embeddings" not in source

    shared_runtime = (root / "utils/local_embedding_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "from memory" not in shared_runtime
