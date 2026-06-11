import json
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCALE_DIR = PROJECT_ROOT / "static" / "locales"
CLOUDSAVE_JS = PROJECT_ROOT / "static" / "js" / "cloudsave_manager.js"
CLOUDSAVE_CSS = PROJECT_ROOT / "static" / "css" / "cloudsave_manager.css"
CLOUDSAVE_TEMPLATE = PROJECT_ROOT / "templates" / "cloudsave_manager.html"
CHARA_TEMPLATE = PROJECT_ROOT / "templates" / "character_card_manager.html"
CHARA_MANAGER_JS = PROJECT_ROOT / "static" / "js" / "character_card_manager.js"
I18N_JS = PROJECT_ROOT / "static" / "i18n-i18next.js"


def _get_nested_value(payload: dict, dotted_key: str):
    value = payload
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(dotted_key)
        value = value[part]
    return value


def _iter_leaf_strings(payload):
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _iter_leaf_strings(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _iter_leaf_strings(value)
    elif isinstance(payload, str):
        yield payload


def _extract_i18n_keys() -> set[str]:
    keys: set[str] = set()
    pattern = re.compile(r"(cloudsave\.[A-Za-z0-9_.]+|character\.[A-Za-z0-9_.]+)")
    for path in (CLOUDSAVE_JS, CLOUDSAVE_TEMPLATE, CHARA_TEMPLATE, CHARA_MANAGER_JS):
        keys.update(pattern.findall(path.read_text(encoding="utf-8")))
    # 过滤掉以点结尾的动态键前缀（如 'character.field.' + key）
    keys = {k for k in keys if not k.endswith('.')}
    return keys


@pytest.mark.unit
def test_cloudsave_templates_use_i18n_keys():
    cloudsave_template = CLOUDSAVE_TEMPLATE.read_text(encoding="utf-8")
    assert 'data-i18n="cloudsave.pageTitle"' in cloudsave_template
    assert 'data-i18n="cloudsave.headerTitle"' in cloudsave_template
    assert 'data-i18n="cloudsave.subtitle"' in cloudsave_template
    assert 'data-i18n="cloudsave.refresh"' in cloudsave_template
    assert 'data-i18n="cloudsave.backToCharacterManager"' in cloudsave_template
    assert 'id="cloudsave-provider-status"' in cloudsave_template
    assert 'data-i18n="cloudsave.loadingSummary"' in cloudsave_template
    assert 'id="cloudsave-current-character"' in cloudsave_template
    assert 'data-i18n="cloudsave.nameConflictNotice.title"' in cloudsave_template
    assert 'data-i18n="cloudsave.nameConflictNotice.bodyStorage"' in cloudsave_template
    assert 'data-i18n="cloudsave.nameConflictNotice.bodyImpact"' in cloudsave_template
    assert 'data-i18n="cloudsave.nameConflictNotice.bodyAction"' in cloudsave_template
    assert 'data-i18n="cloudsave.emptyState"' in cloudsave_template

    chara_template = CHARA_TEMPLATE.read_text(encoding="utf-8")
    assert 'data-i18n="character.openCloudsaveManager"' in chara_template


@pytest.mark.unit
def test_cloudsave_page_i18n_keys_exist_in_all_locales():
    keys = sorted(_extract_i18n_keys())
    locale_files = sorted(LOCALE_DIR.glob("*.json"))
    assert locale_files, "expected locale files to exist"

    for locale_path in locale_files:
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = []
        for key in keys:
            try:
                _get_nested_value(payload, key)
            except KeyError:
                missing.append(key)
        assert not missing, f"{locale_path.name} is missing cloudsave i18n keys: {missing}"


@pytest.mark.unit
def test_cloudsave_manager_js_is_ascii_only():
    assert CLOUDSAVE_JS.read_text(encoding="utf-8").isascii()


@pytest.mark.unit
def test_cloudsave_manager_compacts_workshop_status_display_for_all_paths():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "cloudsave.meta.localWorkshopStatus" in script
    assert "cloudsave.meta.cloudWorkshopStatus" in script
    assert "cloudsave.meta.localOriginWorkshopStatus" in script
    assert "cloudsave.meta.cloudOriginWorkshopStatus" in script
    assert "local_origin_workshop_status" in script
    assert "cloud_origin_workshop_status" in script
    assert "local_asset_source" in script
    assert "cloud_asset_source" in script
    assert "steamWorkshopWithId" not in script
    assert "workshopStatusWithTitle" not in script


@pytest.mark.unit
def test_cloudsave_manager_separates_local_and_cloud_meta_sections():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")
    stylesheet = CLOUDSAVE_CSS.read_text(encoding="utf-8")

    assert "cloudsave.meta.groupLocal" in script
    assert "cloudsave.meta.groupCloud" in script
    assert "cloudsave-meta-sections" in script
    assert ".cloudsave-meta-sections" in stylesheet


@pytest.mark.unit
def test_cloudsave_manager_supports_collapsible_item_details_by_default():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")
    stylesheet = CLOUDSAVE_CSS.read_text(encoding="utf-8")

    assert "cloudsave.action.expandDetails" in script
    assert "cloudsave.action.collapseDetails" in script
    assert "aria-controls" in script
    assert "details.hidden = !shouldBeOpen;" in script
    assert "details.hidden = !nextExpanded;" in script
    assert ".cloudsave-item-main" in stylesheet
    assert ".cloudsave-item-expand" in stylesheet
    assert ".cloudsave-item-details" in stylesheet


@pytest.mark.unit
def test_cloudsave_manager_confirm_hints_cover_workshop_origin_paths():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")
    en_payload = json.loads((LOCALE_DIR / "en.json").read_text(encoding="utf-8"))

    assert "item.local_origin_workshop_status || ''" in script
    assert "item.cloud_origin_workshop_status || ''" in script
    hint_keys = [
        "cloudsave.hint.uploadOriginResubscribe",
        "cloudsave.hint.uploadOriginUnavailable",
        "cloudsave.hint.uploadOriginUnconfirmed",
        "cloudsave.hint.downloadOriginResubscribe",
        "cloudsave.hint.downloadOriginUnavailable",
        "cloudsave.hint.downloadOriginUnconfirmed",
    ]
    for key in hint_keys:
        assert key in script
        assert _get_nested_value(en_payload, key)


@pytest.mark.unit
def test_cloudsave_manager_does_not_render_origin_badges():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "cloudsave.badge.localOriginWorkshop" not in script
    assert "cloudsave.badge.cloudOriginWorkshop" not in script


@pytest.mark.unit
def test_cloudsave_manager_only_shows_modified_model_guidance_for_workshop_origin_overrides():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")
    en_payload = json.loads((LOCALE_DIR / "en.json").read_text(encoding="utf-8"))

    guidance_keys = [
        "cloudsave.guidance.localManualSource.title",
        "cloudsave.guidance.localManualSource.body",
        "cloudsave.guidance.cloudManualSource.title",
        "cloudsave.guidance.cloudManualSource.body",
        "cloudsave.guidance.localModifiedModel.title",
        "cloudsave.guidance.localModifiedModel.body",
    ]
    for key in guidance_keys:
        assert key in script
        assert _get_nested_value(en_payload, key)
    assert re.search(r"!hasWorkshopOriginOverride\(item,\s*'local'\)", script)
    assert re.search(r"!hasWorkshopOriginOverride\(item,\s*'cloud'\)", script)
    assert re.search(r"hasWorkshopOriginOverride\(item,\s*'local'\)", script)


@pytest.mark.unit
def test_cloudsave_workshop_meta_labels_use_compact_copy_in_all_supported_locales():
    expected = {
        "en.json": {
            "cloudsave.meta.localWorkshopStatus": "Local current status",
            "cloudsave.meta.cloudWorkshopStatus": "Cloud current status",
        },
        "zh-CN.json": {
            "cloudsave.meta.localWorkshopStatus": "本地当前状态",
            "cloudsave.meta.cloudWorkshopStatus": "云端当前状态",
        },
        "zh-TW.json": {
            "cloudsave.meta.localWorkshopStatus": "本地目前狀態",
            "cloudsave.meta.cloudWorkshopStatus": "雲端目前狀態",
        },
        "ja.json": {
            "cloudsave.meta.localWorkshopStatus": "ローカル現在の状態",
            "cloudsave.meta.cloudWorkshopStatus": "クラウド現在の状態",
        },
        "ko.json": {
            "cloudsave.meta.localWorkshopStatus": "로컬 현재 상태",
            "cloudsave.meta.cloudWorkshopStatus": "클라우드 현재 상태",
        },
        "ru.json": {
            "cloudsave.meta.localWorkshopStatus": "Текущее локальное состояние",
            "cloudsave.meta.cloudWorkshopStatus": "Текущее облачное состояние",
        },
    }

    for locale_name, assertions in expected.items():
        payload = json.loads((LOCALE_DIR / locale_name).read_text(encoding="utf-8"))
        for key, value in assertions.items():
            assert _get_nested_value(payload, key) == value


@pytest.mark.unit
def test_cloudsave_manager_waits_for_i18n_and_rebinds_dynamic_labels():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "function waitForI18nReady(timeoutMs = 2500)" in script
    assert "await waitForI18nReady();" in script
    assert "function setTranslatedText(element, key, fallback, params = {})" in script
    assert "setTranslatedText(" in script
    assert "window.setTimeout(() => {" in script
    assert "renderSummary(state.summary);" in script


@pytest.mark.unit
def test_cloudsave_manager_translate_short_circuits_for_empty_i18n_key():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "function translate(key, fallback, params = {}) {" in script
    assert "if (!key) {" in script
    assert "return interpolateText(fallback, params);" in script


@pytest.mark.unit
def test_cloudsave_manager_prefers_backend_error_i18n_keys():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "payload.message_key" in script
    assert "payload.message_params" in script
    assert "cloudsave.error.invalidJsonBody" in script
    assert "cloudsave.error.invalidBooleanParameter" in script


@pytest.mark.unit
def test_cloudsave_manager_renders_provider_status_card_messages():
    cloudsave_template = CLOUDSAVE_TEMPLATE.read_text(encoding="utf-8")
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert 'id="cloudsave-provider-status"' in cloudsave_template
    assert 'id="cloudsave-provider-scope"' in cloudsave_template
    provider_keys = [
        "cloudsave.providerSteamAutoCloudSourceLaunch",
        "cloudsave.providerSteamAutoCloudReady",
        "cloudsave.providerSteamAutoCloudOffline",
        "cloudsave.providerSnapshotScope",
        "cloudsave.providerAvailable",
        "cloudsave.providerUnavailable",
    ]
    for key in provider_keys:
        assert key in script
        for locale_path in sorted(LOCALE_DIR.glob("*.json")):
            payload = json.loads(locale_path.read_text(encoding="utf-8"))
            assert _get_nested_value(payload, key)


@pytest.mark.unit
def test_cloudsave_manager_renders_my_characters_first_and_sorts_by_local_update_time():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "cloudsave.group.otherTitle" in script
    assert "cloudsave.group.workshopTitle" in script
    assert "local_updated_at_utc" in script
    assert "state.preferredCharacterName" in script
    assert "localeCompare" in script
    assert script.index("kind: 'other'") < script.index("kind: 'workshop'")


@pytest.mark.unit
def test_cloudsave_manager_groups_workshop_section_by_character_origin_only():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")
    match = re.search(
        r"function isWorkshopCharacterItem\(item\) \{(?P<body>.*?)\n    \}",
        script,
        re.S,
    )
    assert match, "expected isWorkshopCharacterItem function to exist"
    body = match.group("body")

    assert "item.local_origin_source" in body
    assert "item.cloud_origin_source" in body
    assert "item.asset_source" not in body
    assert "item.local_asset_source" not in body
    assert "item.cloud_asset_source" not in body


@pytest.mark.unit
def test_cloudsave_group_titles_use_my_characters_copy_in_all_supported_locales():
    expected = {
        "en.json": "My characters",
        "zh-CN.json": "我的角色",
        "zh-TW.json": "我的角色",
        "ja.json": "マイキャラクター",
        "ko.json": "내 캐릭터",
        "ru.json": "Мои персонажи",
    }

    for locale_name, expected_value in expected.items():
        payload = json.loads((LOCALE_DIR / locale_name).read_text(encoding="utf-8"))
        assert _get_nested_value(payload, "cloudsave.group.otherTitle") == expected_value


@pytest.mark.unit
def test_chara_manager_cloudsave_window_handle_is_cached_after_open():
    script = CHARA_MANAGER_JS.read_text(encoding="utf-8")

    assert "window._openedWindows[windowName] = openedWindow;" in script
    assert "if (!window._openedWindows || typeof window._openedWindows !== 'object') {" in script


@pytest.mark.unit
def test_chara_manager_unsaved_draft_branch_does_not_commit_cloudsave_sync_timestamp():
    script = CHARA_MANAGER_JS.read_text(encoding="utf-8")
    match = re.search(
        r"if \(hasUnsavedNewCatgirlDraft\(\)\) \{(?P<body>.*?)\n\s*\}",
        script,
        re.S,
    )
    assert match, "expected hasUnsavedNewCatgirlDraft guard to exist"
    assert "shouldCommitTimestamp = true;" not in match.group("body")


@pytest.mark.unit
def test_partial_save_voice_failed_key_is_not_duplicated_in_en_ko_and_zh_tw_locales():
    en_raw = (LOCALE_DIR / "en.json").read_text(encoding="utf-8")
    ko_raw = (LOCALE_DIR / "ko.json").read_text(encoding="utf-8")
    zh_tw_raw = (LOCALE_DIR / "zh-TW.json").read_text(encoding="utf-8")

    assert en_raw.count('"partialSaveVoiceFailed"') == 1
    assert ko_raw.count('"partialSaveVoiceFailed"') == 1
    assert zh_tw_raw.count('"partialSaveVoiceFailed"') == 1
    assert "음성 업데이트에 실패했습니다" in ko_raw
    assert "角色已保存，但音色更新失敗" in zh_tw_raw


@pytest.mark.unit
def test_cloudsave_action_labels_use_snapshot_copy_in_all_supported_locales():
    expected = {
        "en.json": {
            "cloudsave.action.uploadDisabledByProvider": "Prepare snapshot unavailable",
            "cloudsave.action.uploadOverwrite": "Prepare snapshot",
            "cloudsave.action.uploadNew": "Prepare snapshot",
            "cloudsave.action.uploadUnavailable": "Prepare snapshot unavailable",
            "cloudsave.action.downloadDisabledByProvider": "Apply snapshot unavailable",
            "cloudsave.action.downloadOverwrite": "Apply snapshot",
            "cloudsave.action.downloadNew": "Apply snapshot",
            "cloudsave.action.downloadUnavailable": "Apply snapshot unavailable",
        },
        "zh-CN.json": {
            "cloudsave.action.uploadDisabledByProvider": "暂不可生成快照",
            "cloudsave.action.uploadOverwrite": "生成快照",
            "cloudsave.action.uploadNew": "生成快照",
            "cloudsave.action.uploadUnavailable": "暂不可生成快照",
            "cloudsave.action.downloadDisabledByProvider": "暂不可应用快照",
            "cloudsave.action.downloadOverwrite": "应用快照",
            "cloudsave.action.downloadNew": "应用快照",
            "cloudsave.action.downloadUnavailable": "暂不可应用快照",
        },
        "zh-TW.json": {
            "cloudsave.action.uploadDisabledByProvider": "暫時無法產生快照",
            "cloudsave.action.uploadOverwrite": "產生快照",
            "cloudsave.action.uploadNew": "產生快照",
            "cloudsave.action.uploadUnavailable": "暫時無法產生快照",
            "cloudsave.action.downloadDisabledByProvider": "暫時無法套用快照",
            "cloudsave.action.downloadOverwrite": "套用快照",
            "cloudsave.action.downloadNew": "套用快照",
            "cloudsave.action.downloadUnavailable": "暫時無法套用快照",
        },
        "ja.json": {
            "cloudsave.action.uploadDisabledByProvider": "スナップショットを準備できません",
            "cloudsave.action.uploadOverwrite": "スナップショットを準備",
            "cloudsave.action.uploadNew": "スナップショットを準備",
            "cloudsave.action.uploadUnavailable": "スナップショットを準備できません",
            "cloudsave.action.downloadDisabledByProvider": "スナップショットを適用できません",
            "cloudsave.action.downloadOverwrite": "スナップショットを適用",
            "cloudsave.action.downloadNew": "スナップショットを適用",
            "cloudsave.action.downloadUnavailable": "スナップショットを適用できません",
        },
        "ko.json": {
            "cloudsave.action.uploadDisabledByProvider": "스냅샷 준비 불가",
            "cloudsave.action.uploadOverwrite": "스냅샷 준비",
            "cloudsave.action.uploadNew": "스냅샷 준비",
            "cloudsave.action.uploadUnavailable": "스냅샷 준비 불가",
            "cloudsave.action.downloadDisabledByProvider": "스냅샷 적용 불가",
            "cloudsave.action.downloadOverwrite": "스냅샷 적용",
            "cloudsave.action.downloadNew": "스냅샷 적용",
            "cloudsave.action.downloadUnavailable": "스냅샷 적용 불가",
        },
        "ru.json": {
            "cloudsave.action.uploadDisabledByProvider": "Подготовка снимка недоступна",
            "cloudsave.action.uploadOverwrite": "Подготовить снимок",
            "cloudsave.action.uploadNew": "Подготовить снимок",
            "cloudsave.action.uploadUnavailable": "Подготовка снимка недоступна",
            "cloudsave.action.downloadDisabledByProvider": "Применение снимка недоступно",
            "cloudsave.action.downloadOverwrite": "Применить снимок",
            "cloudsave.action.downloadNew": "Применить снимок",
            "cloudsave.action.downloadUnavailable": "Применение снимка недоступно",
        },
    }

    for locale_name, assertions in expected.items():
        payload = json.loads((LOCALE_DIR / locale_name).read_text(encoding="utf-8"))
        for key, value in assertions.items():
            assert _get_nested_value(payload, key) == value


@pytest.mark.unit
def test_cloudsave_popup_url_carries_current_ui_language():
    script = CHARA_MANAGER_JS.read_text(encoding="utf-8")

    assert "function getCurrentUiLanguage()" in script
    assert "query.set('ui_lang', currentUiLanguage);" in script


@pytest.mark.unit
def test_cloudsave_back_to_character_manager_replaces_history_entry():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "window.location.replace('/character_card_manager');" in script
    assert "window.location.href = '/character_card_manager';" not in script


@pytest.mark.unit
def test_cloudsave_download_notifies_open_character_manager_to_refresh():
    cloudsave_script = CLOUDSAVE_JS.read_text(encoding="utf-8")
    chara_script = CHARA_MANAGER_JS.read_text(encoding="utf-8")

    assert "const CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY = 'neko_cloudsave_character_sync';" in cloudsave_script
    assert "const CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE = 'cloudsave_character_changed';" in cloudsave_script
    assert "const CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME = 'neko_cloudsave_character_sync';" in cloudsave_script
    assert "localStorage.setItem(CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY, JSON.stringify(payload));" in cloudsave_script
    assert "window.opener.postMessage(payload, window.location.origin);" in cloudsave_script
    assert "new BroadcastChannel(CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME);" in cloudsave_script
    assert "channel.postMessage(payload);" in cloudsave_script
    assert "if (action === 'download') {" in cloudsave_script
    assert "notifyCharacterManagerSync({" in cloudsave_script
    assert "const CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY = 'neko_cloudsave_character_sync';" in chara_script
    assert "const CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE = 'cloudsave_character_changed';" in chara_script
    assert "const CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME = 'neko_cloudsave_character_sync';" in chara_script
    assert "handleCloudsaveCharacterSync(event.data);" in chara_script
    assert "event.key !== CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY" in chara_script
    assert "fetch('/api/characters', { cache: 'no-store' });" in chara_script
    assert "cache: 'no-store'" in chara_script
    assert "new BroadcastChannel(CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME);" in chara_script


@pytest.mark.unit
def test_cloudsave_manager_surfaces_rollback_failure_details():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "cloudsave.dialog.rollbackFailed" in script
    assert "cloudsave.dialog.operationInProgress" in script
    assert "payloadError.rollback_error" in script
    assert "Rollback also failed: {{message}}" in script


@pytest.mark.unit
def test_cloudsave_manager_fallback_copy_uses_snapshot_terms_for_actions():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "Upload to cloud" not in script
    assert "Download to local" not in script
    assert "continue with the upload" not in script
    assert "continue with the download" not in script
    assert "Prepare snapshot" in script
    assert "Apply snapshot" in script
    assert "continue preparing the Steam Cloud snapshot" in script
    assert "continue restoring from the Steam Cloud snapshot" in script


@pytest.mark.unit
def test_cloudsave_manager_formats_timestamps_with_locale_aware_intl_formatter():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "function getPreferredLocale()" in script
    assert "new Intl.DateTimeFormat(getPreferredLocale()" in script
    assert "return normalizedValue;" in script


@pytest.mark.unit
def test_cloudsave_manager_supports_live3d_model_type_alias_in_all_locales():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")
    assert "cloudsave.modelType.live3d" in script

    for locale_name in sorted(path.name for path in LOCALE_DIR.glob("*.json")):
        payload = json.loads((LOCALE_DIR / locale_name).read_text(encoding="utf-8"))
        assert _get_nested_value(payload, "cloudsave.modelType.live3d") == "VRM"


@pytest.mark.unit
def test_i18n_script_supports_explicit_popup_language_query():
    script = I18N_JS.read_text(encoding="utf-8")

    assert "function getLanguageFromQuery()" in script
    assert "params.get('ui_lang')" in script


@pytest.mark.unit
def test_cloudsave_chinese_copy_does_not_leave_bare_workshop_in_user_facing_values():
    for locale_name in ("zh-CN.json", "zh-TW.json"):
        payload = json.loads((LOCALE_DIR / locale_name).read_text(encoding="utf-8"))
        cloudsave_payload = payload.get("cloudsave", {})
        leaf_strings = list(_iter_leaf_strings(cloudsave_payload))
        assert all("Workshop" not in value for value in leaf_strings), locale_name


@pytest.mark.unit
@pytest.mark.parametrize(
    ("locale_name", "forbidden_pattern"),
    (
        ("en.json", r"(?<!Steam )Workshop"),
        ("ja.json", r"(?<!Steam )Workshop"),
        ("ko.json", r"Workshop"),
        ("ru.json", r"Workshop"),
    ),
)
def test_cloudsave_other_locales_use_clear_workshop_wording(locale_name, forbidden_pattern):
    payload = json.loads((LOCALE_DIR / locale_name).read_text(encoding="utf-8"))
    cloudsave_payload = payload.get("cloudsave", {})
    leaf_strings = list(_iter_leaf_strings(cloudsave_payload))
    offenders = [value for value in leaf_strings if re.search(forbidden_pattern, value)]
    assert not offenders, f"{locale_name} has ambiguous Workshop wording: {offenders[:5]}"
