"""A capability hint for chat, never a catalog of music tools or schemas."""

MUSIC_RESULT_TOKENS = 2500
MUSIC_HISTORY_TOKENS = 300


def is_music_evidence(callback):
    if not isinstance(callback, dict):
        return False
    metadata = callback.get("metadata")
    return (
        callback.get("context_source") == "music_library.result"
        or isinstance(metadata, dict)
        and metadata.get("music_library_evidence") is True
    )


def music_result_token_limit(plugin_id, entry_id):
    return (
        MUSIC_RESULT_TOKENS
        if (plugin_id, entry_id) == ("netease_music", "cloud_music")
        else None
    )


def compact_music_history(text, lang="zh"):
    from utils.tokenize import truncate_to_tokens

    from config.prompts.prompts_music_capability import music_history_prefix

    return music_history_prefix(lang) + truncate_to_tokens(
        text, MUSIC_HISTORY_TOKENS
    )


def music_capability_prompt(plugin, lang="zh"):
    if isinstance(plugin, dict) and "plugin" in plugin:
        plugin = plugin["plugin"]
    if (
        not isinstance(plugin, dict)
        or plugin.get("status") != "running"
        or plugin.get("runtime_enabled") is False
    ):
        return ""
    if not any(
        e.get("id") == "cloud_music"
        for e in plugin.get("entries", [])
        if isinstance(e, dict)
    ):
        return ""
    from config.prompts.prompts_music_capability import music_capability_text

    return music_capability_text(lang)
