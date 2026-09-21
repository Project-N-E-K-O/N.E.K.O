import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = REPO_ROOT / "static" / "locales"
STORAGE_LOCATION_JS = REPO_ROOT / "static" / "app" / "app-storage-location.js"
MEMORY_BROWSER_JS = REPO_ROOT / "static" / "js" / "memory_browser.js"
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
        ("en.json", "Clean up migration backup", "Not now"),
        ("es.json", "Limpiar copia de migración", "Ahora no"),
        ("ja.json", "移行バックアップを削除", "今はしない"),
        ("ko.json", "마이그레이션 백업 정리", "나중에"),
        ("pt.json", "Limpar backup de migração", "Agora não"),
        ("ru.json", "Удалить резерв миграции", "Не сейчас"),
        ("zh-CN.json", "清理迁移备份", "暂时不处理"),
        ("zh-TW.json", "清理遷移備份", "暫時不處理"),
    ),
)
def test_storage_location_completion_actions_match_locale(locale_name, expected_cleanup, expected_defer):
    payload = json.loads((LOCALES_DIR / locale_name).read_text(encoding="utf-8"))
    storage = payload.get("storage", {})

    assert storage.get("cleanupRetainedRoot") == expected_cleanup
    assert storage.get("deferRetainedRootCleanup") == expected_defer


@pytest.mark.unit
def test_backend_directory_picker_uses_its_own_interactive_timeout_budget():
    shared_source = STORAGE_LOCATION_JS.read_text(encoding="utf-8")
    memory_source = MEMORY_BROWSER_JS.read_text(encoding="utf-8")

    for source in (shared_source, memory_source):
        picker_timeout = re.search(
            r"STORAGE_DIRECTORY_PICKER_TIMEOUT_MS\s*=\s*(\d+)",
            source,
        )
        assert picker_timeout is not None
        assert int(picker_timeout.group(1)) > 120_000

    shared_picker_block = shared_source.split(
        "async function pickDirectoryWithBackend",
        1,
    )[1].split("function getHostBridge", 1)[0]
    memory_picker_block = memory_source.split(
        "async function pickStorageTargetDirectory",
        1,
    )[1].split("function formatPreflightResult", 1)[0]
    for picker_block in (shared_picker_block, memory_picker_block):
        assert "STORAGE_DIRECTORY_PICKER_TIMEOUT_MS" in picker_block
        assert "STORAGE_MUTATION_REQUEST_TIMEOUT_MS" not in picker_block


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
