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


def compact_music_history(text):
    from utils.tokenize import truncate_to_tokens

    return "[网易云查询摘要，详细曲目需按页重新读取]\n" + truncate_to_tokens(
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
    if lang in {"zh-TW", "zh_Hant", "zh-Hant"}:
        return (
            "\n[音樂能力] 背景可處理網易雲帳號、雲端音樂庫和播放請求；具體能力按需呼叫。"
            "先簡短回應，等真實結果再回答或宣稱成功。收藏預設存網易雲：收藏這首即點紅心，指定歌單則加入；只有明確說本地或本機才存N.E.K.O。\n"
        )
    if lang == "zh":
        return (
            "\n[音乐能力] 后台可处理网易云账号、云端音乐库和播放请求；具体能力按需调用。"
            "先简短回应，等真实结果再回答或宣称成功。收藏默认存网易云：收藏这首即点红心，指定歌单则加入该云端歌单；只有明确说本地才存N.E.K.O。\n"
        )
    return (
        "\n[Music capability] Background tasks can handle NetEase accounts, cloud libraries and playback on demand. "
        "Wait for results before claiming success. Saves default to NetEase: like the song, or add it to the named cloud playlist. Save in N.E.K.O only when local storage is explicitly requested.\n"
    )


def requests_local_music(text):
    import re

    return bool(
        re.search(r"本地|本機|\blocal\b|ローカル|로컬|локаль", str(text or ""), re.I)
    )
