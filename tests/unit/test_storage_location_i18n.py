import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = REPO_ROOT / "static" / "locales"
STORAGE_LOCATION_JS = REPO_ROOT / "static" / "app" / "app-storage-location.js"
STORAGE_KEY_RE = re.compile(r"""['"]storage\.([A-Za-z0-9_.-]+)['"]""")


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: locale coverage checks are file-only."""
    yield


def _storage_location_keys() -> set[str]:
    text = STORAGE_LOCATION_JS.read_text(encoding="utf-8")
    return {match.group(1) for match in STORAGE_KEY_RE.finditer(text)}


@pytest.mark.unit
def test_storage_location_locale_namespace_matches_used_keys():
    used_keys = _storage_location_keys()
    assert used_keys

    issues: dict[str, dict[str, list[str]]] = {}
    for locale_path in sorted(LOCALES_DIR.glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        storage = data.get("storage")
        if not isinstance(storage, dict):
            issues[locale_path.name] = {"missing_namespace": ["storage"]}
            continue

        locale_keys = set(storage)
        missing = sorted(used_keys - locale_keys)
        extra = sorted(locale_keys - used_keys)
        empty = sorted(key for key in used_keys & locale_keys if not str(storage.get(key) or "").strip())
        if missing or extra or empty:
            issues[locale_path.name] = {
                "missing": missing,
                "extra": extra,
                "empty": empty,
            }

    assert issues == {}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("locale_name", "expected_cleanup", "expected_defer"),
    (
        ("en.json", "Clean up old data", "Not now"),
        ("es.json", "Limpiar datos antiguos", "Ahora no"),
        ("ja.json", "古いデータを削除", "今はしない"),
        ("ko.json", "이전 데이터 정리", "나중에"),
        ("pt.json", "Limpar dados antigos", "Agora não"),
        ("ru.json", "Очистить старые данные", "Не сейчас"),
        ("zh-CN.json", "清理旧数据", "暂时不处理"),
        ("zh-TW.json", "清理舊資料", "暫時不處理"),
    ),
)
def test_storage_location_completion_actions_match_locale(locale_name, expected_cleanup, expected_defer):
    payload = json.loads((LOCALES_DIR / locale_name).read_text(encoding="utf-8"))
    storage = payload.get("storage", {})

    assert storage.get("cleanupRetainedRoot") == expected_cleanup
    assert storage.get("deferRetainedRootCleanup") == expected_defer


@pytest.mark.unit
def test_storage_location_cloudsave_local_state_error_code_is_translated():
    source = STORAGE_LOCATION_JS.read_text(encoding="utf-8")

    branch_pattern = re.compile(
        r"case\s+['\"]cloudsave_local_state_unavailable['\"]\s*:"
        r"(?:(?!\n\s*(?:case\s+['\"]|default\s*:)).)*"
        r"storage\.cloudsaveLocalStateUnavailable",
        re.DOTALL | re.MULTILINE,
    )
    assert branch_pattern.search(source)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("locale_name", "expected_pick_new", "expected_use_current"),
    (
        ("en.json", "Recommended Storage Location", "Other Location"),
        ("es.json", "Ubicación de almacenamiento recomendada", "Otra ubicación"),
        ("ja.json", "おすすめの保存先", "その他の場所"),
        ("ko.json", "추천 저장 위치", "다른 위치"),
        ("pt.json", "Local de armazenamento recomendado", "Outro local"),
        ("ru.json", "Рекомендуемое место хранения", "Другое место"),
        ("zh-CN.json", "推荐存储位置", "其他位置"),
        ("zh-TW.json", "推薦儲存位置", "其他位置"),
    ),
)
def test_storage_location_intro_button_copy_matches_locale(locale_name, expected_pick_new, expected_use_current):
    payload = json.loads((LOCALES_DIR / locale_name).read_text(encoding="utf-8"))
    storage = payload.get("storage", {})

    assert storage.get("selectionIntroPickNew") == expected_pick_new
    assert storage.get("selectionIntroUseCurrent") == expected_use_current
