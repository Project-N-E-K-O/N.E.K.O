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
        ("en.json", "Use dedicated home", "Other home"),
        ("es.json", "Usar refugio dedicado", "Otro refugio"),
        ("ja.json", "専用のおうちを使う", "別のおうち"),
        ("ko.json", "전용 보금자리 쓰기", "다른 보금자리"),
        ("pt.json", "Usar cantinho dedicado", "Outro cantinho"),
        ("ru.json", "Использовать свой домик", "Другой домик"),
        ("zh-CN.json", "使用专属小窝", "其他小窝"),
        ("zh-TW.json", "使用專屬小窩", "其他小窩"),
    ),
)
def test_storage_location_intro_button_copy_matches_locale(locale_name, expected_pick_new, expected_use_current):
    payload = json.loads((LOCALES_DIR / locale_name).read_text(encoding="utf-8"))
    storage = payload.get("storage", {})

    assert storage.get("selectionIntroPickNew") == expected_pick_new
    assert storage.get("selectionIntroUseCurrent") == expected_use_current
