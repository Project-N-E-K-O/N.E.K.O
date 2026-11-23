"""The license gates built-in activation, without restricting local imports."""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from main_logic.emote_template_adapter import inspect_spine_zip
from tests.emote_fixtures import spine_zip

pytestmark = [pytest.mark.template_license, pytest.mark.usefixtures("emote_template_library_dir")]
KEY = "builtin:license-test"


def _builtin(page, *, hooks=False):
    archive = spine_zip()
    package = inspect_spine_zip(archive, filename="license-test.zip")
    package["id"] = KEY
    package["source"].update(packageId=KEY, builtIn=True)
    downloads = []
    page.route("**/api/emote-maker/builtins", lambda route: route.fulfill(
        json={"success": True, "packages": [package]}))

    def download(route):
        if parse_qs(urlsplit(route.request.url).query).get("key") != [KEY]:
            route.continue_()
            return
        downloads.append(route.request.url)
        route.fulfill(body=archive, content_type="application/zip")

    page.route("**/api/emote-maker/library/archive?*", download)
    if hooks:
        source = (Path(__file__).resolve().parents[2] / "static/js/emote_maker.js").read_text(encoding="utf-8")
        marker = "    if (document.readyState === 'loading') {"
        assert source.count(marker) == 1
        injected = """    window.licenseTest = {
            tryRestoreTemplateFromArchive, applyTemplateFromRegistryNow, importTemplateFileNow,
            cache: () => loadedTemplates.set('builtin:license-test', {
                pkg: builtinTemplates.get('builtin:license-test'), store: {},
            }),
            pkg: () => builtinTemplates.get('builtin:license-test'),
        };
"""
        page.route("**/static/js/emote_maker.js*", lambda route: route.fulfill(
            body=source.replace(marker, injected + marker), content_type="application/javascript"))
    return downloads


def test_builtin_activation_requires_the_acknowledgement_button(page, running_server, tmp_path):
    downloads = _builtin(page)
    page.goto(running_server + "/emote_maker", wait_until="networkidle")
    dialog = page.locator("#builtin-template-license")
    card = page.locator('[data-template-built-in="true"] .template-lib-open')
    expect(dialog).to_be_visible()
    expect(page.locator("body")).not_to_have_class(re.compile(r"\beditor-has-template\b"))
    expect(dialog.locator(".builtin-license-full")).not_to_have_attribute("open", "")
    dialog.locator("summary").click()
    expect(dialog.locator("article[lang='zh-CN']")).to_contain_text("个人自用制作及非商业无偿赠送实体周边")
    expect(dialog.locator("article[lang='en']")).to_contain_text("Sales require permission")
    dialog.locator("summary").click()
    page.screenshot(path=str(tmp_path / "template-license-light.png"), animations="disabled")
    page.set_viewport_size({"width": 560, "height": 420})
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    for button in dialog.locator(".builtin-license-actions button").all():
        rect = button.bounding_box()
        assert rect and 0 <= rect["y"] < rect["y"] + rect["height"] <= 420
    assert dialog.evaluate("element => element.scrollWidth <= element.clientWidth")
    page.screenshot(path=str(tmp_path / "template-license-dark-small.png"), animations="disabled")
    page.set_viewport_size({"width": 1280, "height": 720})
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    card.click()
    expect(dialog).to_be_visible()
    # Closing a native dialog with an arbitrary return value is not acceptance.
    page.evaluate("document.querySelector('#builtin-template-license').close('acknowledged')")
    expect(dialog).to_be_hidden()
    assert downloads == []
    card.click()
    expect(dialog).to_be_visible()
    page.locator("#builtin-license-acknowledge").click()
    expect(dialog).to_be_hidden()
    expect(page.locator("body")).to_have_class(re.compile(r"\beditor-has-template\b"), timeout=30000)
    assert len(downloads) == 1
    # The selected built-in must still offer a review button after acceptance.
    expect(card).to_have_attribute("aria-disabled", "true")
    info = page.locator('[data-template-built-in="true"] .template-lib-license')
    info.click()
    expect(dialog).to_be_visible()
    expect(dialog.locator("form button")).to_have_text("关闭")
    dialog.locator("form button").click()
    expect(dialog).to_be_hidden()
    expect(page.locator("body")).to_have_class(re.compile(r"\beditor-has-template\b"))
    expect(page.locator("#animation-list .animation-card")).to_have_count(2)
    assert len(downloads) == 1
    page.screenshot(path=str(tmp_path / "template-license-icon.png"), animations="disabled")


def test_declining_allows_importing_a_user_template(page, running_server):
    downloads = _builtin(page)
    page.goto(running_server + "/emote_maker", wait_until="networkidle")
    dialog = page.locator("#builtin-template-license")
    expect(dialog).to_be_visible()
    dialog.locator("form button").click()
    page.set_input_files("#template-archive-file", {
        "name": "own-template.zip", "mimeType": "application/zip", "buffer": spine_zip(),
    })
    expect(page.locator("body")).to_have_class(re.compile(r"\beditor-has-template\b"), timeout=30000)
    expect(page.locator("#animation-list .animation-card")).to_have_count(2)
    expect(page.locator('.template-lib-card:not([data-template-built-in]) .template-lib-license')).to_have_count(0)
    page.locator('[data-template-built-in="true"] .template-lib-license').click()
    expect(dialog).to_be_visible()
    dialog.locator("form button").click()
    expect(page.locator('.template-lib-card.is-selected')).not_to_have_attribute('data-template-built-in', 'true')
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    expect(dialog).to_be_visible()
    dialog.locator("form button").click()
    expect(page.locator("#animation-list .animation-card")).to_have_count(2)
    assert downloads == []


def test_reopening_and_cached_restore_paths_require_acknowledgement(page, running_server):
    downloads = _builtin(page, hooks=True)
    page.goto(running_server + "/emote_maker", wait_until="networkidle")
    dialog = page.locator("#builtin-template-license")
    expect(dialog).to_be_visible()
    assert page.evaluate("""async () => {
        const gate = window.licenseTest;
        gate.cache();
        return await gate.tryRestoreTemplateFromArchive('builtin:license-test') === false
            && await gate.applyTemplateFromRegistryNow('builtin:license-test') === false
            && await gate.importTemplateFileNow(new File([''], 'test.zip'), {
                builtinPackage: gate.pkg(), restoreKey: 'builtin:license-test',
            }) === false;
    }""")
    assert downloads == []
    page.locator("#builtin-license-acknowledge").click()
    expect(dialog).to_be_hidden()
    page.reload(wait_until="networkidle")
    expect(dialog).to_be_visible()
    expect(page.locator("body")).not_to_have_class(re.compile(r"\beditor-has-template\b"))
    assert downloads == []
