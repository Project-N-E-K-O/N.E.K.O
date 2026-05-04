import json
import os
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = REPO_ROOT / "static" / "locales"
REQUIRED_KEYS = (
    "autostartPrompt.title",
    "autostartPrompt.message",
    "autostartPrompt.note",
    "autostartPrompt.startNow",
    "autostartPrompt.later",
    "autostartPrompt.never",
    "autostartPrompt.requiresApproval",
    "tutorialPrompt.title",
    "tutorialPrompt.message",
    "tutorialPrompt.note",
    "tutorialPrompt.startNow",
    "tutorialPrompt.later",
    "tutorialPrompt.never",
    "tutorialPrompt.startFailed",
)


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: locale coverage checks are file-only."""
    yield




def _flatten_leaf_strings(payload, prefix=""):
    if isinstance(payload, dict):
        for key, value in payload.items():
            dotted = f"{prefix}.{key}" if prefix else key
            yield from _flatten_leaf_strings(value, dotted)
    elif isinstance(payload, str):
        yield prefix, payload


def _iter_tracked_source_files():
    ignored_dirs = {"node_modules", "dist", "build"}
    source_roots = (REPO_ROOT / "static" / "js", REPO_ROOT / "templates")
    source_suffixes = {".html", ".js", ".jsx", ".py", ".ts", ".tsx"}
    for source_root in source_roots:
        if not source_root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(source_root):
            dirnames[:] = [dirname for dirname in dirnames if dirname not in ignored_dirs]
            root = Path(dirpath)
            if root == LOCALES_DIR or LOCALES_DIR in root.parents:
                continue
            for filename in filenames:
                path = root / filename
                if path.suffix in source_suffixes:
                    yield path


def _extract_call(text: str, start: int) -> str | None:
    paren = text.find("(", start)
    if paren < 0:
        return None

    depth = 0
    quote = None
    escaped = False
    for index in range(paren, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None

def _has_nested_key(data: dict, dotted_key: str) -> bool:
    current = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


@pytest.mark.unit
def test_tutorial_prompt_locale_keys_exist_in_all_locales():
    missing_by_locale: dict[str, list[str]] = {}

    for locale_path in sorted(LOCALES_DIR.glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = [key for key in REQUIRED_KEYS if not _has_nested_key(data, key)]
        if missing:
            missing_by_locale[locale_path.name] = missing

    assert missing_by_locale == {}


@pytest.mark.unit
def test_error_placeholder_i18n_calls_pass_error_params():
    locale_error_keys: dict[str, set[str]] = {}
    for locale_path in sorted(LOCALES_DIR.glob("*.json")):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        locale_error_keys[locale_path.name] = {
            key for key, value in _flatten_leaf_strings(payload) if "{{error}}" in value
        }

    baseline = locale_error_keys["en.json"]
    assert all(keys == baseline for keys in locale_error_keys.values())

    source_texts = [
        (path, path.read_text(encoding="utf-8", errors="ignore"))
        for path in _iter_tracked_source_files()
    ]
    missing_error_params: list[str] = []

    for key in sorted(baseline):
        pattern = re.compile(r"(?:window\.)?t\s*\(\s*['\"]" + re.escape(key) + r"['\"]")
        for path, source in source_texts:
            for match in pattern.finditer(source):
                call = _extract_call(source, match.start())
                line = source.count("\n", 0, match.start()) + 1
                if call is None or not re.search(r"\berror\s*:", call):
                    missing_error_params.append(f"{path.relative_to(REPO_ROOT)}:{line}: {key}")

    assert missing_error_params == []
