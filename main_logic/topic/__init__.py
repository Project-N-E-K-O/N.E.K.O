"""Background topic-hook support for proactive chat."""

from main_logic.topic.delivery import (
    build_topic_hook_callback,
    clear_topic_session_manager_getter,
    register_topic_session_manager_getter,
    trigger_topic_hook_once,
)
from main_logic.topic.hooks import build_topic_hook_prompt
from main_logic.topic.materials import enrich_topic_materials_online
from main_logic.topic.pipeline import TopicHookPool, get_topic_hook_pool
from main_logic.topic.signals import TopicSignalStore, TopicTurnSignal

__all__ = [
    "TopicHookPool",
    "TopicSignalStore",
    "TopicTurnSignal",
    "build_topic_hook_callback",
    "build_topic_hook_prompt",
    "clear_topic_session_manager_getter",
    "enrich_topic_materials_online",
    "get_topic_hook_pool",
    "register_topic_session_manager_getter",
    "trigger_topic_hook_once",
]
