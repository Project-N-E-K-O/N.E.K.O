from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


LOADER = Path(__file__).resolve().parents[2] / "static/theme-studio/loader/neko-theme-studio.js"
ORIGIN = "http://theme-studio.test"
PALETTE = {"schema": 1, "name": "Saved", "updated_at": "1", "enabled": True,
           "vars": {"--nts-accent": "#224466"}, "dark_vars": {}, "poll_revision": "one"}


def open_theme_page(page, palette, path="/chat"):
    requests = []
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{ORIGIN}/**", lambda route: route.fulfill(status=404))
    page.route(f"{ORIGIN}{path}", lambda route: route.fulfill(content_type="text/html", body="""
        <html data-nts-l-old="1"><head><style id="nts-boot">:root{--nts-accent:#000}</style></head>
        <body><div class="chat-window"><span class="message-bubble-user">Hi</span></div></body></html>"""))

    def serve_palette(route):
        requests.append(route.request.url)
        if route.request.url.endswith("?rev=" + palette["poll_revision"]):
            route.fulfill(status=304)
        else:
            route.fulfill(json=palette)

    page.route(f"{ORIGIN}/api/theme_studio/theme.json*", serve_palette)
    page.goto(f"{ORIGIN}{path}")
    page.add_script_tag(path=str(LOADER))
    return requests, errors


@pytest.mark.frontend
def test_loader_hands_server_gates_to_runtime_and_repairs_removed_styles(page: Page):
    palette = dict(PALETTE)
    requests, errors = open_theme_page(page, palette)
    root = page.locator("html")
    expect(root).to_have_attribute("data-nts-l-accent", "1")
    expect(page.locator("#nts-boot")).to_have_count(0)
    assert root.get_attribute("data-nts-l-old") is None
    with page.expect_response(lambda r: r.status == 304):
        page.evaluate("window.dispatchEvent(new Event('focus'))")
    assert requests[-1].endswith("?rev=one")
    page.evaluate("document.querySelector('#nts-dynamic').remove()")
    with page.expect_response(lambda r: r.url.endswith("theme.json")):
        page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.locator("#nts-dynamic")).to_have_count(1)
    palette.update(enabled=False, poll_revision="two")
    page.evaluate("window.NekoThemeStudio.refresh()")
    expect(page.locator("#nts-dynamic")).to_have_count(0)
    assert root.get_attribute("data-nts-l-accent") is None
    assert not errors


@pytest.mark.frontend
def test_loader_reverts_expired_preview_and_keeps_saved_cache(page: Page):
    palette = dict(PALETTE)
    _, errors = open_theme_page(page, palette)
    expect(page.locator("#nts-dynamic")).to_have_count(1)
    expires_at = page.evaluate("Date.now() / 1000 + 1.5")
    palette.update(name="Temporary", poll_revision="preview",
                   vars={"--nts-accent": "#774411"}, preview={"active": True, "expires_at": expires_at})
    page.evaluate("window.NekoThemeStudio.refresh()")
    expect(page.locator("#nts-preview-badge")).to_be_visible()
    assert page.evaluate("JSON.parse(localStorage.getItem('neko.themeStudio.palette')).name") == "Saved"
    expect(page.locator("#nts-preview-badge")).to_have_count(0)
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--nts-accent').trim()") == "#224466"
    assert not errors


@pytest.mark.frontend
@pytest.mark.parametrize("enabled,variables", [(False, PALETTE["vars"]), (True, {})])
def test_loader_clears_first_paint_for_native_appearance(page: Page, enabled, variables):
    _, errors = open_theme_page(page, dict(PALETTE, enabled=enabled, vars=variables))
    expect(page.locator("#nts-boot")).to_have_count(0)
    assert page.locator("html").get_attribute("data-nts-l-old") is None
    expect(page.locator("#nts-dynamic")).to_have_count(0)
    assert not errors



@pytest.mark.frontend
def test_loader_material_preferences_and_long_chat_degradation(page: Page):
    palette = dict(PALETTE, material_vars={"light": {"--nts-mt-blur": "8px"}},
                   material_attrs={"data-nts-mt": "1", "data-nts-mt-quality": "high"})
    _, errors = open_theme_page(page, palette)
    root = page.locator("html")
    expect(root).to_have_attribute("data-nts-mt", "1")
    page.evaluate("""() => {
        NekoThemeStudio.setPlate(2);
        NekoThemeStudio.setRim(0);
        NekoThemeStudio.setChroma(2);
        NekoThemeStudio.setHeavy('on');
    }""")
    expect(root).to_have_attribute("data-nts-plate", "2")
    expect(root).to_have_attribute("data-nts-rim", "0")
    expect(root).to_have_attribute("data-nts-chroma", "2")
    expect(root).to_have_attribute("data-nts-mt-heavy", "1")
    bubble = page.locator(".message-bubble-user")
    assert bubble.is_visible()
    assert bubble.evaluate("el => getComputedStyle(el, '::after').display") == "none"
    palette.update(enabled=False, poll_revision="off")
    page.evaluate("window.NekoThemeStudio.refresh()")
    expect(page.locator("#nts-dynamic")).to_have_count(0)
    for name in ("mt", "plate", "rim", "chroma", "mt-heavy"):
        assert root.get_attribute("data-nts-" + name) is None
    assert not errors


@pytest.mark.frontend
def test_late_poll_cannot_overwrite_cross_window_storage_update(page: Page):
    _, errors = open_theme_page(page, dict(PALETTE))
    expect(page.locator("#nts-dynamic")).to_have_count(1)
    page.evaluate("""() => {
        const originalFetch = window.fetch;
        window.fetch = (url, opts) => {
            if (!String(url).includes('/theme.json')) return originalFetch(url, opts);
            return new Promise(resolve => { window.releaseLate = value => resolve(Response.json(value)); });
        };
        NekoThemeStudio.refresh();
    }""")
    page.wait_for_function("typeof window.releaseLate === 'function'")
    replacement = dict(PALETTE, name="Replacement", vars={"--nts-accent": "#123456"})
    page.evaluate("""async ([replacement, stale]) => {
        const key = 'neko.themeStudio.palette';
        localStorage.setItem(key, JSON.stringify(replacement));
        dispatchEvent(new StorageEvent('storage', { key, newValue: JSON.stringify(replacement) }));
        releaseLate(stale);
        await new Promise(resolve => setTimeout(resolve, 20));
    }""", [replacement, PALETTE])
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--nts-accent').trim()") == "#123456"
    assert page.evaluate("JSON.parse(localStorage.getItem('neko.themeStudio.palette')).name") == "Replacement"
    assert not errors



@pytest.mark.frontend
def test_server_native_appearance_wins_over_previous_cached_theme(page: Page):
    page.route(f"{ORIGIN}/", lambda route: route.fulfill(content_type="text/html", body="""
        <html data-nts-boot-ready><body></body></html>"""))
    page.goto(f"{ORIGIN}/")
    page.evaluate("""palette => {
        localStorage.setItem('neko.themeStudio.palette', JSON.stringify(palette));
        // Keep the authoritative request pending to inspect the actual handover frame.
        window.fetch = () => new Promise(() => {});
    }""", PALETTE)
    page.add_script_tag(path=str(LOADER))
    assert page.locator("#nts-dynamic").count() == 0
    assert page.locator("html").get_attribute("data-nts-l-accent") is None
