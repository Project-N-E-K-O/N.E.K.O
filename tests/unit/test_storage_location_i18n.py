import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = REPO_ROOT / "static" / "locales"
STORAGE_LOCATION_JS = REPO_ROOT / "static" / "app-storage-location.js"
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
    ("locale_name", "expected_pick_new", "expected_use_current"),
    (
        ("en.json", "Pick a new home", "Keep current home"),
        ("es.json", "Elegir un nuevo hogar", "Mantener el actual"),
        ("ja.json", "新しいおうちを選ぶ", "今のおうちを使う"),
        ("ko.json", "새 보금자리 선택하기", "지금 보금자리 쓰기"),
        ("pt.json", "Escolher um novo cantinho", "Manter o cantinho atual"),
        ("ru.json", "Выбрать новый домик", "Оставить текущий домик"),
        ("zh-TW.json", "選個新的小窩", "保持不動"),
    ),
)
def test_storage_location_intro_button_copy_matches_locale(locale_name, expected_pick_new, expected_use_current):
    payload = json.loads((LOCALES_DIR / locale_name).read_text(encoding="utf-8"))
    storage = payload.get("storage", {})

    assert storage.get("selectionIntroPickNew") == expected_pick_new
    assert storage.get("selectionIntroUseCurrent") == expected_use_current
