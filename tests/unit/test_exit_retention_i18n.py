import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = REPO_ROOT / "static" / "locales"
REQUIRED_KEYS = (
    "exitRetention.goodbyeBubble",
    "exitRetention.returnBubble",
    "exitRetention.stayButton",
)


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: locale coverage checks are file-only."""
    yield


def _has_nested_key(data: dict, dotted_key: str) -> bool:
    current = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return isinstance(current, str) and current.strip() != ""


@pytest.mark.unit
def test_exit_retention_locale_keys_exist_in_all_locales():
    missing_by_locale: dict[str, list[str]] = {}

    for locale_path in sorted(LOCALES_DIR.glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = [key for key in REQUIRED_KEYS if not _has_nested_key(data, key)]
        if missing:
            missing_by_locale[locale_path.name] = missing

    assert missing_by_locale == {}
