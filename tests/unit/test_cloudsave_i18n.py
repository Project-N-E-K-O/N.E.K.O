import json
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCALE_DIR = PROJECT_ROOT / "static" / "locales"
CLOUDSAVE_JS = PROJECT_ROOT / "static" / "js" / "cloudsave_manager.js"
CLOUDSAVE_CSS = PROJECT_ROOT / "static" / "css" / "cloudsave_manager.css"
CLOUDSAVE_TEMPLATE = PROJECT_ROOT / "templates" / "cloudsave_manager.html"
CHARA_TEMPLATE = PROJECT_ROOT / "templates" / "chara_manager.html"
CHARA_MANAGER_JS = PROJECT_ROOT / "static" / "js" / "chara_manager.js"
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
    pattern = re.compile(r"(cloudsave\.[A-Za-z0-9_.]+|character\.openCloudsaveManager)")
    for path in (CLOUDSAVE_JS, CLOUDSAVE_TEMPLATE, CHARA_TEMPLATE):
        keys.update(pattern.findall(path.read_text(encoding="utf-8")))
    return keys


@pytest.mark.unit
def test_cloudsave_templates_use_i18n_keys():
    cloudsave_template = CLOUDSAVE_TEMPLATE.read_text(encoding="utf-8")
    assert 'data-i18n="cloudsave.pageTitle"' in cloudsave_template
    assert 'data-i18n="cloudsave.headerTitle"' in cloudsave_template
    assert 'data-i18n="cloudsave.subtitle"' in cloudsave_template
    assert 'data-i18n="cloudsave.refresh"' in cloudsave_template
    assert 'data-i18n="cloudsave.backToCharacterManager"' in cloudsave_template
    assert 'data-i18n="cloudsave.loadingSummary"' in cloudsave_template
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

    assert "formatWorkshopStatus(item, 'local')" in script
    assert "formatWorkshopStatus(item, 'cloud')" in script
    assert "formatWorkshopStatus(item, 'local_origin')" in script
    assert "formatWorkshopStatus(item, 'cloud_origin')" in script
    assert "summarizeAssetSource(item.local_asset_source)" in script
    assert "summarizeAssetSource(item.cloud_asset_source)" in script
    assert "steamWorkshopWithId" not in script
    assert "workshopStatusWithTitle" not in script


@pytest.mark.unit
def test_cloudsave_manager_separates_local_and_cloud_meta_sections():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "function buildMetaSection(sectionClassName, titleText, entries)" in script
    assert "cloudsave.meta.groupLocal" in script
    assert "cloudsave.meta.groupCloud" in script
    assert "cloudsave-meta-sections" in script


@pytest.mark.unit
def test_cloudsave_manager_supports_collapsible_item_details_by_default():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")
    stylesheet = CLOUDSAVE_CSS.read_text(encoding="utf-8")

    assert "expandedCharacterNames: new Set()" in script
    assert "function isCharacterExpanded(characterName)" in script
    assert "function setCharacterExpanded(characterName, expanded)" in script
    assert "function updateExpandButtonState(button, expanded)" in script
    assert "cloudsave.action.expandDetails" in script
    assert "cloudsave.action.collapseDetails" in script
    assert "details.hidden = !shouldBeOpen;" in script
    assert "setCharacterExpanded(item.character_name, nextExpanded);" in script
    assert ".cloudsave-item-main" in stylesheet
    assert ".cloudsave-item-expand" in stylesheet
    assert ".cloudsave-item-details" in stylesheet


@pytest.mark.unit
def test_cloudsave_manager_confirm_hints_cover_workshop_origin_paths():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "hasWorkshopOriginOverride(item, 'local')" in script
    assert "hasWorkshopOriginOverride(item, 'cloud')" in script
    assert "item.local_origin_workshop_status || ''" in script
    assert "item.cloud_origin_workshop_status || ''" in script
    assert "cloudsave.hint.uploadOriginResubscribe" in script
    assert "cloudsave.hint.uploadOriginUnavailable" in script
    assert "cloudsave.hint.uploadOriginUnconfirmed" in script
    assert "cloudsave.hint.downloadOriginResubscribe" in script
    assert "cloudsave.hint.downloadOriginUnavailable" in script
    assert "cloudsave.hint.downloadOriginUnconfirmed" in script


@pytest.mark.unit
def test_cloudsave_manager_does_not_render_origin_badges():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "cloudsave.badge.localOriginWorkshop" not in script
    assert "cloudsave.badge.cloudOriginWorkshop" not in script


@pytest.mark.unit
def test_cloudsave_manager_only_shows_modified_model_guidance_for_workshop_origin_overrides():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "function shouldShowLocalManualSourceGuidance(item)" in script
    assert "function shouldShowCloudManualSourceGuidance(item)" in script
    assert "function shouldShowLocalModifiedWorkshopModelGuidance(item)" in script
    assert "&& !hasWorkshopOriginOverride(item, 'local');" in script
    assert "&& !hasWorkshopOriginOverride(item, 'cloud');" in script
    assert "&& hasWorkshopOriginOverride(item, 'local');" in script


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
def test_cloudsave_manager_renders_my_characters_first_and_sorts_by_local_update_time():
    script = CLOUDSAVE_JS.read_text(encoding="utf-8")

    assert "function getItemLocalUpdatedAtSortValue(item)" in script
    assert "function getLocallyUpdatedItems(items)" in script
    assert "const leftTime = getItemLocalUpdatedAtSortValue(left);" in script
    assert "const rightTime = getItemLocalUpdatedAtSortValue(right);" in script
    assert "return rightTime - leftTime;" in script
    assert "items: getLocallyUpdatedItems(otherItems)," in script
    assert "items: getOrderedItems(workshopItems)," in script
    assert script.index("kind: 'other'") < script.index("kind: 'workshop'")


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
def test_cloudsave_popup_url_carries_current_ui_language():
    script = CHARA_MANAGER_JS.read_text(encoding="utf-8")

    assert "function getCurrentUiLanguage()" in script
    assert "query.set('ui_lang', currentUiLanguage);" in script


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
