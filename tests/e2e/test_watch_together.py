"""Exercise the real module graph behind the watch-together page controls."""
import mimetypes
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.e2e
def test_watch_together_discovery_buttons_with_windows_module_association(
    page: Page, running_server: str, monkeypatch
):
    # Windows may associate .mjs with a text editor. Static responses must still
    # let Chromium execute the host and its relative imports.
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, ".mjs", "text/plain")
    script_errors = []
    page.on("pageerror", lambda error: script_errors.append(str(error)))

    def api(route):
        path = urlparse(route.request.url).path
        data = {}
        if path == "/api/watch-together/history":
            data = {"analyses": [], "watches": [], "next_offset": None}
        elif path == "/api/watch-together/watches":
            data = {"watches": [], "next_offset": None}
        elif path == "/api/game/watch-together/character":
            data = {"lanlan_name": "Test Neko", "model_type": "unavailable"}
        elif path == "/api/watch-together/discover":
            data = {"video": None}
        route.fulfill(json=data)

    # All API work is local test data; scripts and styles use the real server.
    page.route("**/api/**", api)
    page.goto(
        f"{running_server}/watch_together?lanlan_name=Test%20Neko&ui_lang=en",
        wait_until="domcontentloaded",
    )
    expect(page.locator("#status")).to_have_text(
        "Pick one from the library or paste a Bilibili link."
    )
    page.locator("#topic").fill("cat videos")
    for selector in ("#empty-discover", "#discover-button"):
        with page.expect_request("**/api/watch-together/discover") as request:
            page.locator(selector).click()
        assert request.value.post_data_json == {"topic": "cat videos", "exclude": []}
        expect(page.locator("#status")).to_have_text(
            "No qualifying video found. Try another topic; the limits remain unchanged."
        )
        expect(page.locator("#discover-button")).to_be_enabled()
    page.locator("#empty-paste").click()
    expect(page.locator("#url")).to_be_focused()
    assert not script_errors
