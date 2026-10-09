"""Main-server assembly owns the sole recommendation service and writer."""
from __future__ import annotations

import asyncio
from uuid import uuid4

from utils.logger_config import get_module_logger

from main_logic.topic.recommendation.adapters import ReadOnlyMemoryAdapter
from main_logic.topic.recommendation.registry import (
    configure_recommendation_service, get_recommendation_service,
)
from main_logic.topic.recommendation.service import TopicRecommendationService
from main_logic.topic.recommendation.store import RecommendationStore
from utils.config_manager.reserved_schema import get_reserved, normalize_character_id

logger = get_module_logger(__name__, 'Main')


async def synchronize_topic_recommendation_characters(config_manager) -> None:
    service = get_recommendation_service()
    if service is None:
        return
    try:
        characters = await asyncio.to_thread(config_manager.load_characters, require_authoritative=True)
        mapping = {}
        for name, data in characters.get('猫娘', {}).items():
            identifier = normalize_character_id(get_reserved(data, 'character_id', default=''))
            if identifier:
                mapping[identifier] = name
        if service is get_recommendation_service():
            await service.sync_characters(mapping)
    except Exception as exc:
        service.pause_controls()
        logger.warning('Topic recommendation character sync unavailable (%s)', type(exc).__name__)


def bind_topic_recommendation_manager(manager, character_data) -> None:
    service = get_recommendation_service()
    if service is None:
        return
    identifier = normalize_character_id(get_reserved(character_data, 'character_id', default=''))
    if not identifier:
        return
    existing = getattr(manager, '_recommendation_turn_sink', None)
    if (existing is not None and existing.service is service and existing.character_id == identifier
            and service.binding_is_current(identifier, existing.session_id, existing.binding_generation, manager.lanlan_name)):
        return
    if existing is not None:
        existing.service.unbind(existing.character_id, existing.session_id)
        manager._turn_dispatcher.remove_sink(existing)
    observer_id = uuid4().hex
    manager._conversation_observer_id = observer_id
    manager._recommendation_character_id = identifier
    try:
        # This assembly is the existing per-character PC private-chat owner;
        # preserve its recall_memory scope without inventing a user identity.
        sink = service.bind(identifier, observer_id, manager.lanlan_name, allow_private_memory=True)
    except Exception as exc:
        manager._conversation_observer_id = None
        manager._recommendation_character_id = None
        service.pause_controls()
        logger.warning('Topic recommendation binding unavailable (%s)', type(exc).__name__)
        return
    manager._recommendation_turn_sink = sink
    manager._turn_dispatcher.add_sink(sink)


def unbind_topic_recommendation_manager(manager) -> None:
    sink = getattr(manager, '_recommendation_turn_sink', None)
    if sink is not None:
        sink.service.unbind(sink.character_id, sink.session_id)
        manager._turn_dispatcher.remove_sink(sink)
        manager._recommendation_turn_sink = None
        manager._conversation_observer_id = None


async def initialize_topic_recommendation_runtime(config_manager, managers, character_data) -> None:
    if get_recommendation_service() is not None:
        return
    from main_routers.recommendation_controls import refresh_recommendation_controls
    service = TopicRecommendationService(
        RecommendationStore.for_config_manager(config_manager), memory_reader=ReadOnlyMemoryAdapter(),
        controls_refresher=refresh_recommendation_controls,
    )
    configure_recommendation_service(service)
    try:
        await synchronize_topic_recommendation_characters(config_manager)
        await refresh_recommendation_controls()
        for name, manager in managers:
            if name in character_data:
                bind_topic_recommendation_manager(manager, character_data[name])
    except BaseException:
        await service.close()
        if service is get_recommendation_service():
            configure_recommendation_service(None)
        raise


async def close_topic_recommendation_runtime(*, deadline: float | None = None) -> None:
    service = get_recommendation_service()
    if service is None:
        return
    service.pause_controls()
    await service.close(deadline=deadline)
    if service is get_recommendation_service():
        configure_recommendation_service(None)
