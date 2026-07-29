from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_CHAT_AVATAR_PATH = PROJECT_ROOT / "static" / "app" / "app-chat-avatar.js"
GUIDE_MESSAGE_RELAY_PATH = (
    PROJECT_ROOT / "static" / "app" / "app-interpage" / "guide-message-relay.js"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_card_drop_character_reference_retries_independently_of_avatar_cache():
    source = _read(APP_CHAT_AVATAR_PATH)

    assert "fetch('/api/card-drop/active-character'" in source
    assert "/card-forge/active-character" not in source
    assert "const CHARACTER_REFERENCE_RETRY_LIMIT = 30;" in source
    assert "function scheduleCharacterReferenceSync(reason)" in source
    assert "function syncCharacterReferenceToCardDrop(reason)" in source
    assert "function queueCharacterReferenceRetry(reason)" in source
    assert "characterReferenceRetryAttempts >= CHARACTER_REFERENCE_RETRY_LIMIT" in source
    assert "postCharacterReferenceToCardDrop(characterReferenceDataUrl)" in source
    assert "scheduleCharacterReferenceSync('avatar-sync');" in source
    assert (
        "if (hasUsableCachedPreview()) {\n"
        "            scheduleCharacterReferenceSync(reason || 'cached-preview');"
    ) in source
    assert (
        "if (cachedPreview && cachedPreview.dataUrl && cachedPreview.cacheKey === newCacheKey) {\n"
        "            // 不同猫娘可能复用同一模型/cache key；即使头像无需重抓，也要把当前名称\n"
        "            // 和缓存预览重新同步到 card-drop 角色快照。该函数内部也会安排参考图同步。\n"
        "            syncAvatarToCardDrop(cachedPreview.dataUrl);"
    ) in source
    assert "scheduleCharacterReferenceSync(reason || 'cached-avatar-model-loaded');" not in source
    assert "captureCharacterReferenceDataUrl().then(function (characterReferenceDataUrl)" not in source


@pytest.mark.unit
def test_card_drop_name_sync_does_not_wait_for_avatar_capture():
    source = _read(APP_CHAT_AVATAR_PATH)
    model_loaded_block = source.split("function handleModelLoaded(reason)", 1)[1].split(
        "function bindModelLoadListeners()",
        1,
    )[0]
    empty_init_block = source.split("} else {\n            cachedPreview = null;", 1)[1].split(
        "bindModelLoadListeners();",
        1,
    )[0]

    assert "syncAvatarToCardDrop('');" in model_loaded_block
    assert model_loaded_block.index("syncAvatarToCardDrop('');") < model_loaded_block.index(
        "scheduleAutoCapture(reason);"
    )
    assert "syncAvatarToCardDrop('');" in empty_init_block


@pytest.mark.unit
def test_card_drop_character_reference_http_failures_remain_retryable():
    source = _read(APP_CHAT_AVATAR_PATH)
    post_block = source.split("function postCharacterReferenceToCardDrop", 1)[1].split(
        "function queueCharacterReferenceRetry",
        1,
    )[0]

    assert ".then(function (response)" in post_block
    assert "if (!response.ok)" in post_block
    assert "response.status" in post_block
    assert "return false;" in post_block


@pytest.mark.unit
def test_character_reference_pending_capture_is_bound_to_its_cache_key():
    source = _read(APP_CHAT_AVATAR_PATH)
    capture_block = source.split("function captureCharacterReferenceDataUrl()", 1)[1].split(
        "/**\n     * 把当前头像",
        1,
    )[0]
    matching_pending_guard = (
        "if (\n"
        "            pendingCharacterReference &&\n"
        "            pendingCharacterReferenceCacheKey === cacheKey\n"
        "        ) {\n"
        "            return pendingCharacterReference;\n"
        "        }"
    )
    stale_result_guard = (
        ".then(function (result) {\n"
        "                if (getCharacterReferenceCacheKey() !== cacheKey) return '';\n"
        "                return rememberCharacterReferenceResult(result, cacheKey);\n"
        "            })"
    )
    pending_capture_binding = (
        ".finally(function () {\n"
        "                if (pendingCharacterReference === capturePromise) {\n"
        "                    pendingCharacterReference = null;\n"
        "                    pendingCharacterReferenceCacheKey = '';\n"
        "                }\n"
        "            });\n"
        "        pendingCharacterReference = capturePromise;\n"
        "        pendingCharacterReferenceCacheKey = cacheKey;\n"
        "        return capturePromise;"
    )

    assert "let pendingCharacterReferenceCacheKey = '';" in source
    assert matching_pending_guard in capture_block
    assert stale_result_guard in capture_block
    assert pending_capture_binding in capture_block
    assert capture_block.index(matching_pending_guard) < capture_block.index(
        "var capturePromise = Promise.resolve()"
    )
    assert capture_block.index(stale_result_guard) < capture_block.index(
        pending_capture_binding
    )


@pytest.mark.unit
def test_card_drop_character_reference_keeps_full_body_capture_contract():
    source = _read(GUIDE_MESSAGE_RELAY_PATH)
    character_reference_block = source.split(
        "var captureOptions = captureMode === 'character_reference'",
        1,
    )[1].split(
        ": {",
        1,
    )[0]

    assert "width: 768, height: 1024, padding: 0.08" in character_reference_block
    assert "cropMode: 'portrait'" in character_reference_block
    assert "includeDataUrl: true" in character_reference_block
    assert "includeSourceDataUrl: false" in character_reference_block
