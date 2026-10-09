"""Production PC ownership assembly with isolated IO/model dependencies."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.main_server import topic_recommendation_runtime as assembly
from main_logic.topic.recommendation import registry
from tests.unit.test_topic_recommendation_runtime import CAT, OTHER, setup, turn, state_path


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


@pytest.fixture
async def owned(tmp_path):
    service, sink, analyzer, root = await setup(tmp_path)
    registry.configure_recommendation_service(service)
    try:
        yield service, sink, analyzer, root
    finally:
        registry.configure_recommendation_service(None)
        await service.close()


class Dispatcher:
    def __init__(self):
        self.sinks = []
    def add_sink(self, sink):
        self.sinks.append(sink)
    def remove_sink(self, sink):
        self.sinks.remove(sink)


def manager(name="Yui"):
    return SimpleNamespace(lanlan_name=name, _turn_dispatcher=Dispatcher())


def character(identifier=CAT):
    return {"_reserved": {"character_id": identifier}}


@pytest.mark.asyncio
async def test_actual_pc_bind_authorizes_legacy_private_memory_and_replaces_rename_sink(owned):
    service, _, analyzer, _ = owned
    instance = manager()
    assembly.bind_topic_recommendation_manager(instance, character())
    original = instance._recommendation_turn_sink
    assert service._characters[CAT].private_memory_allowed
    assert service._characters[CAT].subjects_scope is None
    assembly.bind_topic_recommendation_manager(instance, character())
    assert instance._turn_dispatcher.sinks == [original]
    config = SimpleNamespace(load_characters=Mock(return_value={"猫娘": {"Renamed": character()}}))
    await assembly.synchronize_topic_recommendation_characters(config)
    config.load_characters.assert_called_once_with(require_authoritative=True)
    instance.lanlan_name = "Renamed"
    assembly.bind_topic_recommendation_manager(instance, character())
    new = instance._recommendation_turn_sink
    assert instance._turn_dispatcher.sinks == [new] and new is not original
    original.note_turn(turn(session_id=original.session_id))
    assert not service._characters[CAT].events
    new.note_turn(turn(session_id=new.session_id))
    await service.process_pending(CAT)
    assert analyzer.calls
    assembly.unbind_topic_recommendation_manager(instance)
    assert not instance._turn_dispatcher.sinks and not service._characters[CAT].session_id


@pytest.mark.asyncio
async def test_authoritative_identity_read_failure_pauses_without_deleting_profile(owned):
    service, sink, _, root = owned
    sink.note_turn(turn())
    await service.process_pending(CAT)
    config = SimpleNamespace(load_characters=Mock(side_effect=OSError("read failure")))
    await assembly.synchronize_topic_recommendation_characters(config)
    assert state_path(root).exists() and service._characters[CAT].state["subjects"]
    assert not service._controls_valid and not service._characters[CAT].deleted


@pytest.mark.asyncio
async def test_strict_delete_removes_only_absent_identity(owned):
    service, sink, _, root = owned
    sink.note_turn(turn())
    await service.process_pending(CAT)
    config = SimpleNamespace(load_characters=Mock(return_value={"猫娘": {"Other": character(OTHER)}}))
    await assembly.synchronize_topic_recommendation_characters(config)
    assert not state_path(root).exists() and service._characters[CAT].deleted
    assert service._characters[OTHER].name == "Other"


@pytest.mark.asyncio
async def test_partial_initialization_failure_closes_owner_and_clears_registry(tmp_path, monkeypatch):
    from main_routers import recommendation_controls
    service, _, _, _ = await setup(tmp_path)
    registry.configure_recommendation_service(None)
    monkeypatch.setattr(assembly, "TopicRecommendationService", lambda *args, **kwargs: service)
    monkeypatch.setattr(assembly.RecommendationStore, "for_config_manager", lambda *_: service.store)
    refresh = AsyncMock(side_effect=RuntimeError("strict settings unavailable"))
    monkeypatch.setattr(recommendation_controls, "refresh_recommendation_controls", refresh)
    config = SimpleNamespace(load_characters=Mock(return_value={"猫娘": {"Yui": character()}}))
    instance = manager()
    with pytest.raises(RuntimeError, match="strict settings unavailable"):
        await assembly.initialize_topic_recommendation_runtime(config, [("Yui", instance)], {"Yui": character()})
    assert registry.get_recommendation_service() is None and service._closing
    assert not instance._turn_dispatcher.sinks


@pytest.mark.asyncio
async def test_assembly_shutdown_flushes_actual_publication_before_releasing_owner(owned):
    import json
    service, sink, analyzer, root = owned
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "shutdown-receipt", "How is your painting?")
    calls = len(analyzer.calls)
    await assembly.close_topic_recommendation_runtime()
    assert registry.get_recommendation_service() is None
    assert json.loads(state_path(root).read_text(encoding="utf-8"))["deliveries"][0]["delivery_id"] == "shutdown-receipt"
    assert len(analyzer.calls) == calls


@pytest.mark.asyncio
async def test_storage_recovery_loads_unavailable_new_identity_before_resuming(owned, monkeypatch):
    from main_logic.topic.recommendation.maintenance import recommendation_maintenance
    from main_logic.topic.recommendation.contracts import RecommendationError
    service, _, _, _ = owned
    actual_load = service.store.load
    unavailable = True
    async def load(identifier):
        if unavailable and identifier == OTHER:
            raise RecommendationError("maintenance")
        return await actual_load(identifier)
    monkeypatch.setattr(service.store, "load", load)
    async with recommendation_maintenance():
        await service.sync_characters({CAT: "Yui", OTHER: "Other"})
        assert service._characters[OTHER].state is None
        unavailable = False
    assert not service._maintenance and service._characters[OTHER].state is not None
    assert service._characters[OTHER].last_error is None
