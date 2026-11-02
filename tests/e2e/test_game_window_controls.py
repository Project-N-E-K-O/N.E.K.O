"""Game windows must remain closable when their module bootstrap fails."""
import pytest
from playwright.sync_api import Page, expect


@pytest.mark.e2e
@pytest.mark.parametrize("viewport", [(1280, 800), (390, 844)])
@pytest.mark.parametrize("route, module, button", [
    ("air_basketball", "air_basketball/game.js", "#close-game"),
    ("watch_together", "watch-together/host.mjs", "#exit"),
])
def test_game_host_close_without_module_bootstrap(
    page: Page, running_server: str, viewport, route, module, button
):
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.route(f"**/static/game/games/{module}*", lambda request: request.abort())
    page.route("**/api/**", lambda request: request.fulfill(json={}))
    page.add_init_script("""
        window.closeRequests = 0;
        window.nekoHost = {closeWindow:async () => {
            window.closeRequests++;
            return {ok:true};
        }};
    """)
    page.goto(f"{running_server}/{route}?ui_lang=en", wait_until="domcontentloaded")
    close = page.locator(button)
    expect(close).to_be_visible()
    expect(close).to_have_attribute("data-neko-window-control-bound", "1")
    close.click()
    page.wait_for_function("window.closeRequests === 1")


@pytest.mark.e2e
@pytest.mark.parametrize("route, module, button", [
    ("air_basketball", "air_basketball/game.js", "#close-game"),
    ("watch_together", "watch-together/host.mjs", "#exit"),
])
def test_game_browser_popup_closes_without_host_bridge(
    page: Page, running_server: str, route, module, button
):
    page.context.route(f"**/static/game/games/{module}*", lambda request: request.abort())
    page.context.route("**/api/**", lambda request: request.fulfill(json={}))
    page.goto(running_server, wait_until="domcontentloaded")
    with page.expect_popup() as popup:
        page.evaluate("url => window.open(url)", f"/{route}?ui_lang=en")
    child = popup.value
    expect(child.locator(button)).to_have_attribute("data-neko-window-control-bound", "1")
    with child.expect_event("close"):
        child.locator(button).click()
