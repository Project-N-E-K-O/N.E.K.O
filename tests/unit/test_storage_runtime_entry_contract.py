"""Every top-level directory the code keeps under ``app_docs_dir`` must be
either migrated or known to be regenerable; anything else would be left
behind in the old root by a storage-location migration (see #3336)."""

import re
from pathlib import Path

import pytest

from utils.storage_migration import (
    MIGRATED_RUNTIME_ENTRY_NAMES,
    REGENERABLE_RUNTIME_ENTRY_NAMES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_QUOTES = "[" + '"' + chr(39) + "]"
# Matches ``app_docs_dir / "name"`` and ``Path(...app_docs_dir) / "name"``.
_TOP_LEVEL_DIR = re.compile(
    "app_docs_dir[)]?[ ]*/[ ]*" + _QUOTES + "([A-Za-z0-9_.-]+)" + _QUOTES
)
_SKIPPED_PARTS = {"tests", ".venv", "node_modules", ".git", ".claude", "dist", "build"}


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: this check reads source only."""
    yield


def _top_level_dirs_in_source() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in REPO_ROOT.rglob("*.py"):
        relative = path.relative_to(REPO_ROOT)
        if _SKIPPED_PARTS.intersection(relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _TOP_LEVEL_DIR.finditer(text):
            found.setdefault(match.group(1), []).append(relative.as_posix())
    return found


@pytest.mark.unit
def test_every_runtime_top_level_dir_is_migrated_or_regenerable():
    found = _top_level_dirs_in_source()
    # The scan must actually see the known directories, or it proves nothing.
    assert {"config", "memory", "pngtuber", "watch_together", "logs"} <= set(found)

    known = set(MIGRATED_RUNTIME_ENTRY_NAMES) | set(REGENERABLE_RUNTIME_ENTRY_NAMES)
    unlisted = {name: sorted(set(files)) for name, files in found.items() if name not in known}

    assert unlisted == {}, (
        "These directories live under app_docs_dir but are neither migrated nor "
        "regenerable. Add each to MIGRATED_RUNTIME_ENTRY_NAMES (user data) or "
        "REGENERABLE_RUNTIME_ENTRY_NAMES (recreated by the app) in "
        "utils/storage/migration.py."
    )


@pytest.mark.unit
def test_migrated_and_regenerable_lists_do_not_overlap():
    assert not set(MIGRATED_RUNTIME_ENTRY_NAMES) & set(REGENERABLE_RUNTIME_ENTRY_NAMES)
