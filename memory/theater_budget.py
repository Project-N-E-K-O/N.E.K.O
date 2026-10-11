"""Independent, bounded hot-memory allowance for fictional performances."""

import json

from memory.message_sources import is_theater_memory_message
from utils.llm_client import messages_to_dict
from utils.tokenize import count_tokens


THEATER_MEMORY_BUDGET_TOKENS = 6000


def theater_capsule_cost(message) -> int:
    """Charge each summary once; recovery backups never enter the prompt."""
    payload = messages_to_dict([message])[0]
    data = payload.get("data", payload)
    metadata = dict(data.get("metadata") or {})
    metadata.pop("_theater_previous_episode", None)
    summary = metadata.get("episode_summary") or metadata.get("ending_summary")
    if summary:
        metadata["episode_summary"] = summary
        if metadata.get("ending_summary") == summary:
            metadata.pop("ending_summary", None)
        # Episode rendering uses metadata; content is a compatibility copy.
        data["content"] = []
    data["metadata"] = metadata
    return count_tokens(json.dumps([payload], ensure_ascii=False, sort_keys=True))


def bound_theater_history(history: list) -> list:
    """Keep newest theater capsules within their own budget; preserve ordinary rows.

    Count metadata as well as content, since titles and ending lists also reach
    the prompt. Oversized capsules remain available in the cold archive.
    """
    selected = set()
    used = 0
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if not is_theater_memory_message(message):
            selected.add(index)
            continue
        cost = theater_capsule_cost(message)
        if used + cost <= THEATER_MEMORY_BUDGET_TOKENS:
            selected.add(index)
            used += cost
    return [message for index, message in enumerate(history) if index in selected]
