"""Contract tests for README plugin list and privacy/offline guides (`R1111-P3-02`, `R1111-P3-03`)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
README_FILES = (
    REPO_ROOT / "README.MD",
    REPO_ROOT / "docs/README_zh-CN.md",
    REPO_ROOT / "docs/README_ja.md",
    REPO_ROOT / "docs/README_ru.md",
)
PRIVACY_GUIDES = (
    REPO_ROOT / "docs/guide/data-and-privacy.md",
    REPO_ROOT / "docs/zh-CN/guide/data-and-privacy.md",
    REPO_ROOT / "docs/ja/guide/data-and-privacy.md",
)
OFFLINE_GUIDES = (
    REPO_ROOT / "docs/guide/local-and-offline.md",
    REPO_ROOT / "docs/zh-CN/guide/local-and-offline.md",
    REPO_ROOT / "docs/ja/guide/local-and-offline.md",
)


def _extract_chat_sync_constants() -> dict[str, str]:
    runtime_path = REPO_ROOT / "services/chat_sync/runtime.py"
    tree = ast.parse(runtime_path.read_text(encoding="utf-8"), filename=str(runtime_path))
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"_COMMUNITY_ORIGIN", "_BUILTIN_SERVICE_URL"} and isinstance(node.value, ast.Constant):
                constants[name] = str(node.value.value)
    return constants


@pytest.mark.parametrize("readme_path", README_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_readme_mirrors_do_not_claim_wechat_builtin_plugin(readme_path: Path):
    """Verify bundled WeChat plugin directory is absent and no README mirror lists WeChat."""
    assert not (REPO_ROOT / "plugin/plugins/wechat_integration").exists()

    text = readme_path.read_text(encoding="utf-8")
    plugin_lines = [
        line
        for line in text.splitlines()
        if "MCP" in line and any(word in line.lower() for word in ("reminder", "提醒", "リマインダー", "напоминани"))
    ]
    assert len(plugin_lines) == 1, f"Expected built-in plugins bullet in {readme_path}"
    bullet = plugin_lines[0]
    assert "wechat" not in bullet.lower()
    assert "微信" not in bullet


@pytest.mark.parametrize("guide_path", PRIVACY_GUIDES + OFFLINE_GUIDES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_privacy_and_offline_guides_document_opt_in_chat_sync(guide_path: Path):
    """Assert all 6 privacy/offline guide mirrors disclose combined card-save sync consent and REST default."""
    constants = _extract_chat_sync_constants()
    sync_host = constants["_BUILTIN_SERVICE_URL"].removeprefix("https://")
    community_host = constants["_COMMUNITY_ORIGIN"].removeprefix("https://")

    text = guide_path.read_text(encoding="utf-8")
    assert sync_host in text, f"Missing sync service host {sync_host} in {guide_path}"
    assert community_host in text, f"Missing community OAuth host {community_host} in {guide_path}"
    assert "includeHistory" in text, f"Missing includeHistory card-save vs REST-default detail in {guide_path}"
    assert "includeHistory: true" in text, f"Missing UI includeHistory: true disclosure in {guide_path}"
    for stale_claim in (
        "requires explicit choice",
        "separate explicit",
        "需单独选择",
        "需要单独显式确认",
        "個別選択が必要",
        "明示的な選択が必要",
    ):
        assert stale_claim not in text, f"Stale separate-history-consent claim {stale_claim!r} in {guide_path}"
    assert "Steam Cloud" in text, f"Missing Steam Cloud separation context in {guide_path}"
    for artifact in ("facts", "persona", "reflections"):
        assert artifact in text, f"Missing synced artifact `{artifact}` in {guide_path}"
