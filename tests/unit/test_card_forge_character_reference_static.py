from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_CHAT_AVATAR_PATH = PROJECT_ROOT / "static" / "app-chat-avatar.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_card_forge_character_reference_retries_independently_of_avatar_cache():
    source = _read(APP_CHAT_AVATAR_PATH)

    assert "const CHARACTER_REFERENCE_RETRY_LIMIT = 30;" in source
    assert "function scheduleCharacterReferenceSync(reason)" in source
    assert "function syncCharacterReferenceToCardForge(reason)" in source
    assert "function queueCharacterReferenceRetry(reason)" in source
    assert "characterReferenceRetryAttempts >= CHARACTER_REFERENCE_RETRY_LIMIT" in source
    assert "postCharacterReferenceToCardForge(characterReferenceDataUrl)" in source
    assert "scheduleCharacterReferenceSync('avatar-sync');" in source
    assert (
        "if (hasUsableCachedPreview()) {\n"
        "            scheduleCharacterReferenceSync(reason || 'cached-preview');"
    ) in source
    assert (
        "if (cachedPreview && cachedPreview.dataUrl && cachedPreview.cacheKey === newCacheKey) {\n"
        "            // 不同猫娘可能复用同一模型/cache key；即使头像无需重抓，也要把当前名称\n"
        "            // 和缓存预览重新 POST 给 Card Forge。该函数内部也会安排参考图同步。\n"
        "            syncAvatarToCardForge(cachedPreview.dataUrl);"
    ) in source
    assert "scheduleCharacterReferenceSync(reason || 'cached-avatar-model-loaded');" not in source
    assert "captureCharacterReferenceDataUrl().then(function (characterReferenceDataUrl)" not in source
