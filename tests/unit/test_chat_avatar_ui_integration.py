"""Run browser-module ownership regressions in the regular unit-test gate."""
import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

ROOT = Path(__file__).resolve().parents[2]


def test_chat_avatar_browser_module_integration():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the avatar integration harness")
    suite = ROOT / "tests/frontend/chat_avatar_integration.test.cjs"
    result = run_node_script(
        node, "require(" + json.dumps(suite.as_posix()) + ");",
        cwd=str(ROOT),
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("template", ["index.html", "chat.html"])
def test_avatar_editor_is_available_in_both_chat_hosts(template):
    source = (ROOT / "templates" / template).read_text(encoding="utf-8")
    for control in ["upload", "file-input", "save", "cancel-edit", "restore", "custom-status"]:
        assert source.count(f'id="chat-avatar-{control}"') == 1
    scripts = [
        "app-chat-avatar-image.js", "app-chat-avatar-state.js",
        "app-chat-avatar-editor.js", "app-chat-avatar.js",
    ]
    assert [source.index(script) for script in scripts] == sorted(source.index(script) for script in scripts)
    assert 'accept="image/png,image/jpeg,image/webp"' in source
    assert 'id="chat-avatar-custom-status"' in source
    assert 'aria-live="polite"' in source


def test_custom_avatar_locale_keys_match_across_all_languages():
    expected = {
        "upload", "save", "cancel", "restore", "custom", "ready", "saving",
        "unavailable", "invalidImage", "tooLarge", "tooManyPixels", "saveFailed",
        "conflict", "readFailed", "unknownOutcome", "roleMissing", "loading",
        "chooseHint", "help", "maintenance", "storageChanged",
    }
    for language in ["en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"]:
        payload = json.loads((ROOT / "static/locales" / f"{language}.json").read_text(encoding="utf-8"))
        translations = payload["chatAvatar"]["custom"]
        assert set(translations) == expected
        assert all(isinstance(value, str) and value.strip() for value in translations.values())
