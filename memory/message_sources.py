"""Source-specific memory message identities, separate from provider protocols.

These predicates inspect persisted metadata only. They do not initialize memory
stores or import the theater runtime.
"""

from typing import Any

from utils.llm_client.messages import message_metadata


THEATER_MEMORY_SOURCE = "theater_numeric_v2"


def is_theater_memory_message(message: Any) -> bool:
    return message_metadata(message).get("source") == THEATER_MEMORY_SOURCE


def theater_memory_episode_key(message: Any) -> tuple[str, str]:
    """Incremental archives of one story session belong to the same episode."""
    metadata = message_metadata(message)
    return (
        str(metadata.get("story_id") or ""),
        str(metadata.get("session_id") or ""),
    )


def is_theater_episode_summary(message: Any) -> bool:
    """Identify the episode capsule a theater archive writes to ordinary memory."""
    metadata = message_metadata(message)
    return (
        metadata.get("source") == THEATER_MEMORY_SOURCE
        and metadata.get("memory_tier") == "episode_summary"
    )
