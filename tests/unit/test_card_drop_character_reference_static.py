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
    assert "postCharacterReferenceToCardDrop(characterReferenceDataUrl, captureRevision)" in source
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
def test_card_drop_snapshot_is_bound_to_current_model_identity():
    source = _read(APP_CHAT_AVATAR_PATH)

    assert "if (modelType === 'pngtuber') return 'pngtuber';" in source
    assert "config.layered_metadata" in source
    assert "config.idle_image" in source
    assert "config.talking_image" in source
    assert "function normalizeModelIdentityPart(value)" in source
    assert "Object.keys(nestedValue).sort()" in source
    assert "return 'pngtuber:' + JSON.stringify(identity);" in source
    assert "window.vrmManager?.currentModel?.url" in source
    assert "window.vrmModel" in source
    assert "function appendCardDropModelIdentity(body, options = {})" in source
    assert "Object.prototype.hasOwnProperty.call(options, 'modelKey')" in source
    assert "if (modelType)" in source
    assert "body.modelType = modelType;" in source
    assert "body.modelKey = modelKey && !modelKey.endsWith(':') ? modelKey : '';" in source
    assert "let cardDropModelRevision = Date.now();" in source
    assert "body.modelRevision = cardDropModelRevision;" in source
    assert "function advanceCardDropModelRevision()" in source
    assert "cardDropModelRevision = Math.max(cardDropModelRevision + 1, Date.now());" in source
    assert "cacheKey !== getCurrentModelCacheKey()" in source
    assert "captureRevision !== cardDropModelRevision" in source
    assert "const captureRevision = cardDropModelRevision;" in source
    assert "applyPreviewResult(result, cacheKey, captureRevision);" in source
    assert "pendingAutoCapture = true;" in source
    assert "window.addEventListener('pngtuber-model-loaded'" in source


@pytest.mark.unit
def test_chat_follower_does_not_mutate_card_drop_snapshot():
    source = _read(APP_CHAT_AVATAR_PATH)
    sync_block = source.split("function syncAvatarToCardDrop(dataUrl, options = {})", 1)[1].split(
        "function applyPreviewResult",
        1,
    )[0]

    assert "function isCardDropIdentityFollowerWindow()" in source
    assert "/^\\/chat(?:_full)?(?:\\/|$)/.test(pathname)" in source
    assert "if (isCardDropIdentityFollowerWindow()) return;" in sync_block
    assert sync_block.index("if (isCardDropIdentityFollowerWindow()) return;") < sync_block.index(
        "fetch('/api/card-drop/active-character'"
    )


@pytest.mark.unit
def test_pngtuber_loading_invalidates_snapshot_without_early_reference_capture():
    source = _read(APP_CHAT_AVATAR_PATH)
    loading_block = source.split("function handleModelLoading()", 1)[1].split(
        "function bindModelLoadListeners()",
        1,
    )[0]

    assert "window.addEventListener('pngtuber-model-loading'" in source
    assert "let pngtuberModelLoading = false;" in source
    assert "pngtuberModelLoading = true;" in loading_block
    assert "clearTimeout(autoCaptureTimer);" in loading_block
    assert "autoCaptureTimer = null;" in loading_block
    assert "advanceCardDropModelRevision();" in loading_block
    assert "invalidateCachedPreview();" in loading_block
    assert "setPreviewImage('');" in loading_block
    assert "modelType: 'pngtuber'" in loading_block
    assert "modelKey: ''" in loading_block
    assert "syncAvatarToCardDrop('', {" in loading_block
    assert "pngtuberModelLoading = false;" in source
    assert "pngtuberModelLoading && getCurrentModelType() === 'pngtuber'" in source
    render_block = source.split("async function renderAvatarPreview(options = {})", 1)[1].split(
        "function scheduleAutoCapture",
        1,
    )[0]
    assert "if (pngtuberModelLoading && getCurrentModelType() === 'pngtuber')" in render_block
    assert "pendingAutoCapture = true;" in render_block
    assert "window.addEventListener('pngtuber-model-load-finished'" in source


@pytest.mark.unit
def test_pngtuber_loading_state_ignores_stale_lifecycle_events():
    source = _read(APP_CHAT_AVATAR_PATH)
    listeners = source.split("function bindModelLoadListeners()", 1)[1].split(
        "function handleOutsidePointer",
        1,
    )[0]

    assert "let pngtuberModelLoadToken = 0;" in source
    assert "pngtuber-model-loading', function (event)" in listeners
    assert "if (loadToken < pngtuberModelLoadToken) return;" in listeners
    assert "if (loadToken === pngtuberModelLoadToken && pngtuberModelLoading) return;" in listeners
    assert "pngtuberModelLoadToken = loadToken;" in listeners
    assert listeners.count("if (loadToken !== pngtuberModelLoadToken) return;") == 2


@pytest.mark.unit
def test_same_cache_key_reload_invalidates_inflight_capture_revision():
    source = _read(APP_CHAT_AVATAR_PATH)
    model_loaded_block = source.split("function handleModelLoaded(reason)", 1)[1].split(
        "function bindModelLoadListeners()",
        1,
    )[0]
    avatar_capture_block = source.split("async function renderAvatarPreview", 1)[1].split(
        "function scheduleAutoCapture",
        1,
    )[0]
    reference_capture_block = source.split(
        "function captureCharacterReferenceDataUrl(captureRevision)", 1
    )[1].split("/**\n     * 把当前头像", 1)[0]

    assert model_loaded_block.index("advanceCardDropModelRevision();") < model_loaded_block.index(
        "var newCacheKey = getCurrentModelCacheKey();"
    )
    assert "const captureRevision = cardDropModelRevision;" in avatar_capture_block
    assert "if (captureRevision !== cardDropModelRevision)" in avatar_capture_block
    assert "pendingCharacterReferenceRevision === captureRevision" in reference_capture_block
    assert "if (cardDropModelRevision !== captureRevision) return '';" in reference_capture_block
    assert "(cacheKey || '') + ':' + cardDropModelRevision" in source


@pytest.mark.unit
def test_manual_crop_cancel_does_not_restore_preview_from_old_model_revision():
    source = _read(APP_CHAT_AVATAR_PATH)
    capture_block = source.split("async function renderAvatarPreview(options = {})", 1)[1].split(
        "function scheduleAutoCapture",
        1,
    )[0]
    cancel_block = capture_block.split("                } else {", 1)[1].split(
        "                    setPreviewNote(",
        1,
    )[0]

    assert "if (captureRevision !== cardDropModelRevision)" in cancel_block
    assert "cachedPreview = null;" in cancel_block
    assert "pendingAutoCapture = true;" in cancel_block
    assert "} else if (prevCachedPreview) {" in cancel_block
    assert cancel_block.index("if (captureRevision !== cardDropModelRevision)") < cancel_block.index(
        "cachedPreview = prevCachedPreview;"
    )


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
    assert "response.json()" in post_block
    assert "payload.ok === false || payload.stale === true" in post_block
    assert "return false;" in post_block


@pytest.mark.unit
def test_character_reference_pending_capture_is_bound_to_model_revision():
    source = _read(APP_CHAT_AVATAR_PATH)
    capture_block = source.split(
        "function captureCharacterReferenceDataUrl(captureRevision)", 1
    )[1].split(
        "/**\n     * 把当前头像",
        1,
    )[0]
    matching_pending_guard = (
        "if (\n"
        "            pendingCharacterReference &&\n"
        "            pendingCharacterReferenceCacheKey === cacheKey &&\n"
        "            pendingCharacterReferenceRevision === captureRevision\n"
        "        ) {\n"
        "            return pendingCharacterReference;\n"
        "        }"
    )
    stale_result_guard = (
        ".then(function (result) {\n"
        "                if (getCharacterReferenceCacheKey() !== cacheKey) return '';\n"
        "                if (cardDropModelRevision !== captureRevision) return '';\n"
        "                return rememberCharacterReferenceResult(result, cacheKey, captureRevision);\n"
        "            })"
    )
    pending_capture_binding = (
        ".finally(function () {\n"
        "                if (pendingCharacterReference === capturePromise) {\n"
        "                    pendingCharacterReference = null;\n"
        "                    pendingCharacterReferenceCacheKey = '';\n"
        "                    pendingCharacterReferenceRevision = 0;\n"
        "                }\n"
        "            });\n"
        "        pendingCharacterReference = capturePromise;\n"
        "        pendingCharacterReferenceCacheKey = cacheKey;\n"
        "        pendingCharacterReferenceRevision = captureRevision;\n"
        "        return capturePromise;"
    )

    assert "let pendingCharacterReferenceCacheKey = '';" in source
    assert "let pendingCharacterReferenceRevision = 0;" in source
    assert "cachedCharacterReference.modelRevision === captureRevision" in capture_block
    assert "referenceBody.modelRevision = captureRevision;" in source
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
