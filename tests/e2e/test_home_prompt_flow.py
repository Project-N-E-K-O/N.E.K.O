import json
import shutil
import time
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, expect

from utils.autostart_prompt_state import (
    AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
    save_autostart_prompt_state,
)
from utils.config_manager import get_config_manager
from utils.tutorial_prompt_state import save_tutorial_prompt_state


TUTORIAL_PROMPT_TITLE = "要不要先看一下新手引导？"
AUTOSTART_PROMPT_TITLE = "要不要让 N.E.K.O 开机自动启动？"
LATER_BUTTON_TEXT = "稍后再说"


def _load_json_response(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def _wait_for_prompt_status(base_url: str, path: str, expected_status: str, timeout_s: float = 10.0) -> dict:
    deadline = time.time() + timeout_s
    last_state = None

    while time.time() < deadline:
        body = _load_json_response(base_url + path)
        last_state = body.get("state") or {}
        if last_state.get("status") == expected_status:
            return last_state
        time.sleep(0.1)

    raise AssertionError(
        f"Timed out waiting for {path} to reach status={expected_status!r}, "
        f"last_state={last_state!r}"
    )


def _prepare_new_user_prompt_state(*, autostart_foreground_ms: int = 0) -> None:
    config = get_config_manager("N.E.K.O")

    token_usage_path = config.config_dir / "token_usage.json"
    if token_usage_path.exists():
        token_usage_path.unlink()

    shutil.rmtree(config.memory_dir, ignore_errors=True)
    config.memory_dir.mkdir(parents=True, exist_ok=True)

    save_tutorial_prompt_state(
        {"prompt_kind": "tutorial_prompt"},
        config_manager=config,
    )

    autostart_state = {"prompt_kind": "autostart_prompt"}
    if autostart_foreground_ms > 0:
        autostart_state["foreground_ms"] = autostart_foreground_ms
    save_autostart_prompt_state(autostart_state, config_manager=config)


def _install_prompt_test_hooks(page: Page) -> None:
    page.add_init_script(
        """
        (() => {
            localStorage.clear();

            const stats = {
                maxOverlayCount: 0,
                titleHistory: [],
            };
            window.__nekoPromptE2E = stats;

            const sample = () => {
                const overlays = document.querySelectorAll('.modal-overlay');
                stats.maxOverlayCount = Math.max(stats.maxOverlayCount, overlays.length);

                const titleNode = document.querySelector('.modal-overlay .modal-title');
                const titleText = titleNode ? titleNode.textContent.trim() : '';
                if (titleText && stats.titleHistory[stats.titleHistory.length - 1] !== titleText) {
                    stats.titleHistory.push(titleText);
                }
            };

            const startObserver = () => {
                sample();
                if (!document.documentElement) {
                    return;
                }

                const observer = new MutationObserver(sample);
                observer.observe(document.documentElement, {
                    childList: true,
                    subtree: true,
                    characterData: true,
                });
                window.__nekoPromptE2EObserver = observer;
            };

            if (document.documentElement) {
                startObserver();
            } else {
                document.addEventListener('DOMContentLoaded', startObserver, { once: true });
            }

            window.nekoAutostartProvider = {
                getStatus: async () => ({
                    ok: true,
                    supported: true,
                    enabled: false,
                    authoritative: true,
                    provider: 'e2e-test',
                    mechanism: 'browser-e2e',
                    platform: 'macos',
                }),
                enable: async () => ({
                    ok: true,
                    supported: true,
                    enabled: true,
                    authoritative: true,
                    provider: 'e2e-test',
                    mechanism: 'browser-e2e',
                    platform: 'macos',
                }),
                disable: async () => ({
                    ok: true,
                    supported: true,
                    enabled: false,
                    authoritative: true,
                    provider: 'e2e-test',
                    mechanism: 'browser-e2e',
                    platform: 'macos',
                }),
            };
        })();
        """
    )


def _expect_prompt_title(page: Page, title: str) -> None:
    expect(page.locator(".modal-title")).to_have_text(title, timeout=15_000)
    expect(page.locator(".modal-overlay")).to_have_count(1)


def _dismiss_prompt_with_later(page: Page) -> None:
    page.locator(".modal-overlay").get_by_role("button", name=LATER_BUTTON_TEXT).click()


@pytest.mark.e2e
def test_home_serializes_tutorial_and_autostart_prompts(mock_page: Page, running_server: str):
    _prepare_new_user_prompt_state(
        autostart_foreground_ms=AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
    )

    page = mock_page
    _install_prompt_test_hooks(page)
    page.goto(f"{running_server}/")

    expect(page.locator("#chatContainer")).to_be_attached(timeout=15_000)

    _expect_prompt_title(page, TUTORIAL_PROMPT_TITLE)
    _dismiss_prompt_with_later(page)

    _expect_prompt_title(page, AUTOSTART_PROMPT_TITLE)
    _dismiss_prompt_with_later(page)

    expect(page.locator(".modal-overlay")).to_have_count(0, timeout=10_000)

    tutorial_state = _wait_for_prompt_status(
        running_server,
        "/api/tutorial-prompt/state",
        "deferred",
    )
    autostart_state = _wait_for_prompt_status(
        running_server,
        "/api/autostart-prompt/state",
        "deferred",
    )

    assert tutorial_state["shown_count"] == 1
    assert tutorial_state["deferred_until"] > 0
    assert autostart_state["shown_count"] == 1
    assert autostart_state["deferred_until"] > 0

    prompt_stats = page.evaluate("window.__nekoPromptE2E")
    assert prompt_stats["titleHistory"][:2] == [
        TUTORIAL_PROMPT_TITLE,
        AUTOSTART_PROMPT_TITLE,
    ]
    assert prompt_stats["maxOverlayCount"] == 1
