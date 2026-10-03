"""Pages that mount the React chat bundle must provide the mutation token helper.

HtmlCardBlock posts plugin card actions to the main server, which requires
``X-CSRF-Token``. The bundle reads it from ``window.nekoLocalMutationSecurity``,
created by ``static/app/app-prompt-shared.js``. A page that mounts cards
without loading that helper sends the action without a token and gets 403
(the standalone Agent HUD regressed this way in PR #3271).
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
CHAT_BUNDLE = "/static/react/neko-chat/neko-chat-window.iife.js"
SECURITY_HELPER = "/static/app/app-prompt-shared.js"


def _pages_with_chat_bundle() -> list[Path]:
    return sorted(
        path for path in TEMPLATES_DIR.glob("*.html")
        if CHAT_BUNDLE in path.read_text(encoding="utf-8")
    )


@pytest.mark.unit
def test_chat_bundle_pages_are_discovered():
    names = {path.name for path in _pages_with_chat_bundle()}
    assert {"index.html", "chat.html", "agenthud.html"} <= names


@pytest.mark.unit
@pytest.mark.parametrize("page", _pages_with_chat_bundle(), ids=lambda path: path.name)
def test_chat_bundle_page_loads_mutation_security_helper_first(page: Path):
    source = page.read_text(encoding="utf-8")
    assert SECURITY_HELPER in source, f"{page.name} mounts chat cards without {SECURITY_HELPER}"
    assert source.index(SECURITY_HELPER) < source.index(CHAT_BUNDLE), (
        f"{page.name} must load {SECURITY_HELPER} before the chat bundle"
    )
