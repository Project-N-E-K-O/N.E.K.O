"""Spine ZIP import, stable package identity, and local-library behaviour."""
from __future__ import annotations
import io
import json
import re
import zipfile
from PIL import Image, ImageDraw
from playwright.sync_api import Page, expect
from tests.emote_fixtures import spine_zip

LIBRARY_KEY = "neko-emote-maker-template-library"


def _open(page, server):
    page.goto(server + "/emote_maker", wait_until="networkidle")
    page.wait_for_function("typeof window.NekoEmoteSpineRenderer?.create === 'function'")


def _import(page, name="character.zip", data=None):
    with page.expect_response(lambda response: "/api/emote-maker/import" in response.url and response.request.method == "POST"):
        page.set_input_files("#template-archive-file", {"name": name, "mimeType": "application/zip", "buffer": data or spine_zip()})
    expect(page.locator("body")).to_have_class(re.compile("editor-has-template"), timeout=30000)
    expect(page.locator("#template-runtime-badge")).to_be_hidden()


def _entries(page):
    return page.evaluate("(JSON.parse(localStorage.getItem('neko-emote-maker-template-library')) || {}).entries || []")


def test_imports_plain_spine_and_lists_unprefixed_animations(page: Page, running_server: str):
    _open(page, running_server)
    _import(page)
    expect(page.locator("#animation-list .animation-card")).to_have_count(2)
    expect(page.locator("#animation-list")).to_contain_text("idle0")
    expect(page.locator("#export-png-btn")).to_be_enabled()
    assert _entries(page)[0]["key"].startswith("spine-")
    assert page.locator("input[webkitdirectory]").count() == 0


def test_same_resource_zip_renamed_does_not_duplicate_library_entry(page: Page, running_server: str):
    _open(page, running_server)
    _import(page, "first.zip")
    identity = _entries(page)[0]["key"]
    _import(page, "renamed.zip")
    page.wait_for_function("() => (JSON.parse(localStorage.getItem('neko-emote-maker-template-library')) || {}).entries?.[0]?.displayName === 'renamed'")
    assert len(_entries(page)) == 1
    assert _entries(page)[0]["key"] == identity


def test_builtin_template_opens_without_upload_and_keeps_wardrobe_selection(page: Page, running_server: str, builtin_emote_template_dir):
    page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
        const renderer = window.NekoEmoteSpineRenderer.SpineRenderer;
        const create = renderer.create;
        renderer.create = async function (...args) {
            window.wardrobeTestRenderer = await create.apply(this, args);
            return window.wardrobeTestRenderer;
        };
    })""")
    _open(page, running_server)
    uploads = []
    page.on("request", lambda request: uploads.append(request.url) if request.method == "POST"
            and "/api/emote-maker/" in request.url else None)
    card = page.locator('[data-template-built-in="true"]')
    expect(card.locator(".template-lib-remove")).to_have_count(0)
    expect(card.locator(".template-lib-open")).to_have_attribute("title", "YUI 0.5")
    card.locator(".template-lib-open").click()
    expect(page.locator("[data-template-attachment-slot]")).to_have_count(18, timeout=15000)
    expect(page.locator("#animation-list .animation-card")).to_have_count(11)
    select = page.locator('[data-template-attachment-slot="faka1"]')
    search = page.locator("#template-attachment-search")
    visible_parts = page.locator("#template-attachment-controls .template-compat-row:visible")
    part_label = page.locator('label:has([data-template-attachment-slot="faka1"]) > span').inner_text()
    search.fill(part_label)
    expect(visible_parts.locator('[data-template-attachment-slot="faka1"]')).to_have_count(1)
    search.fill("tui1_luolita")
    expect(visible_parts.locator('[data-template-attachment-slot="tui1"]')).to_have_count(1)
    search.fill(" FAKA1 ")
    expect(visible_parts).to_have_count(1)
    select.select_option("hide", force=True)
    expect(search).to_have_value(" FAKA1 ")
    expect(visible_parts).to_have_count(1)
    search.fill("no-matching-wardrobe-part")
    expect(visible_parts).to_have_count(0)
    expect(page.locator("#template-attachment-empty")).to_be_visible()
    search.fill("")
    expect(visible_parts).to_have_count(18)
    expect(page.locator("#template-attachment-empty")).to_be_hidden()
    for slot in ("faka", "fashi_1"):
        control = page.locator(f'[data-template-attachment-slot="{slot}"]')
        page.wait_for_function("slot => Boolean(window.wardrobeTestRenderer.skeleton.findSlot(slot).appliedPose.attachment)", arg=slot)
        control.select_option("hide", force=True)
        page.wait_for_function("slot => window.wardrobeTestRenderer.skeleton.findSlot(slot).appliedPose.attachment === null", arg=slot)
        control.select_option("show", force=True)
        page.wait_for_function("slot => Boolean(window.wardrobeTestRenderer.skeleton.findSlot(slot).appliedPose.attachment)", arg=slot)
        control.select_option("hide", force=True)
    page.locator("#inspector-tab-colors").click()
    menu = page.locator(".part-color-menu")
    part_options = page.locator(".part-color-options:popover-open")
    color_trigger = page.locator('#template-slot-controls button[data-color-field="tintColor"]')
    color_picker = page.locator(".part-color-picker:popover-open:not(.changed-part-color-editor)")
    changed = page.locator("#changed-part-colors")
    changed_hair = changed.locator('[data-changed-color-part="liuhai_1"]')
    changed_top = changed.locator('[data-changed-color-part="shangbansheng"]')
    expect(changed).to_be_hidden()

    def choose_part(name):
        menu.click()
        part_options.locator(f'[data-color-part="{name}"]').click()
        color_trigger.click()
        return color_picker.locator(".part-color-hex")

    menu.click()
    expect(part_options.locator('[data-color-part="faka"]')).to_have_count(0)
    expect(part_options.locator('[data-color-part="fashi_1"]')).to_have_count(0)
    part_options.locator('[data-color-part="liuhai_1"]').hover()
    page.wait_for_function("() => window.wardrobeTestRenderer.options.highlightedPart() === 'liuhai_1'")
    assert page.evaluate("""() => {
        const renderer = window.wardrobeTestRenderer;
        renderer.render({ highlight: false });
        const before = renderer.captureFrame({ render: false }).data;
        renderer.render();
        const after = renderer.captureFrame({ render: false }).data;
        return before.some((value, index) => Math.abs(value - after[index]) > 10);
    }""")
    part_options.locator('[data-color-part="liuhai_1"]').click()
    color_trigger.click()
    hair_tint = color_picker.locator(".part-color-hex")
    hair_tint.fill("#6095de")
    hair_tint.press("Enter")
    expect(changed_hair.locator("button.part-color-swatch")).to_have_attribute("title", re.compile("#6095DE$"))
    top_tint = choose_part("shangbansheng")
    top_tint.fill("#712ACE")
    expect(changed_top).to_have_count(0)
    color_trigger.click()
    color_trigger.click()
    expect(top_tint).to_have_value("#FFFFFF")
    top_tint.fill("#b92e94")
    top_tint.press("Enter")
    expect(changed_top.locator("button.part-color-swatch")).to_have_attribute("title", re.compile("#B92E94$"))
    colors = page.evaluate("""() => {
        const renderer = window.wardrobeTestRenderer;
        const { data } = renderer.captureFrame();
        const matchingColors = new Set();
        for (let i = 0; i < data.length; i += 4) {
            if (data[i + 3] === 255 && Math.abs(data[i] - 185) <= 2
                && Math.abs(data[i + 1] - 46) <= 2 && Math.abs(data[i + 2] - 148) <= 2) matchingColors.add('base');
            if (data[i + 3] === 255 && data[i] >= 120 && data[i] <= 175 && data[i + 1] >= 25
                && Math.abs(data[i + 1] / data[i] - 46 / 185) < .01
                && Math.abs(data[i + 2] / data[i] - 148 / 185) < .01) matchingColors.add('shadow');
        }
        return [...matchingColors];
    }""")
    assert set(colors) == {"base", "shadow"}
    color_trigger.click()
    changed_top.hover()
    highlighted_part = "window.wardrobeTestRenderer.options.highlightedPart()"
    page.wait_for_function(f"() => {highlighted_part} === 'shangbansheng'")
    page.wait_for_function(f"() => {highlighted_part} === null")
    page.wait_for_function(f"() => {highlighted_part} === 'shangbansheng'")
    page.locator("#changed-part-colors-title").hover()
    assert page.evaluate(highlighted_part) is None
    page.reload(wait_until="networkidle")
    card.locator(".template-lib-open").click()
    expect(select).to_have_value("hide", timeout=15000)
    for slot in ("faka", "fashi_1"):
        expect(page.locator(f'[data-template-attachment-slot="{slot}"]')).to_have_value("hide")
    page.locator("#inspector-tab-colors").click()
    hair_tint = choose_part("liuhai_1")
    expect(hair_tint).to_have_value("#6095DE")
    color_picker.get_by_role("button", name="#B92E94").click()
    expect(hair_tint).to_have_value("#B92E94")
    expect(changed_top).to_be_visible()
    top_swatch = changed_top.locator('button[data-color-field="tintColor"]')
    floating_picker = page.locator('.changed-part-color-editor[data-color-field="tintColor"]:popover-open')
    floating_hex = floating_picker.locator(".part-color-hex")
    row_height = changed_top.bounding_box()["height"]
    top_swatch.click()
    expect(top_swatch).to_have_attribute("aria-expanded", "true")
    expect(floating_hex).to_have_value("#B92E94")
    expect(floating_hex).to_be_focused()
    expect(floating_picker.locator("summary")).to_have_count(0)
    assert changed_top.bounding_box()["height"] == row_height
    assert floating_picker.bounding_box()["x"] < top_swatch.bounding_box()["x"]
    floating_hex.fill("#445566")
    floating_hex.press("Enter")
    expect(top_swatch).to_have_attribute("title", re.compile("#445566$"))
    expect(floating_hex).to_have_value("#445566")
    expect(color_trigger.locator("code")).to_have_text("#B92E94")
    top_swatch.press("Enter")
    expect(top_swatch).to_have_attribute("aria-expanded", "false")
    expect(floating_picker).to_have_count(0)
    page.locator("#inspector-tab-parts").click()
    page.locator('[data-template-attachment-slot="luolita"]').select_option("show", force=True)
    page.locator("#inspector-tab-colors").click()
    menu.click()
    expect(part_options.locator('[data-color-part="shangbansheng"]')).to_have_count(0)
    menu.press("Escape")
    top_swatch.click()
    expect(floating_hex).to_have_value("#445566")
    floating_hex.fill("#113355")
    floating_hex.press("Escape")
    expect(floating_picker).to_have_count(0)
    expect(top_swatch).to_be_focused()
    top_swatch.click()
    expect(floating_hex).to_have_value("#445566")
    page.locator("#inspector-tab-colors").click()
    expect(floating_picker).to_have_count(0)
    changed_top.locator(".text-button").click()
    expect(changed_top).to_have_count(0)
    expect(changed_hair).to_be_visible()
    page.locator("#undo-btn").click()
    expect(changed_top).to_be_visible()
    page.locator("#redo-btn").click()
    expect(changed_top).to_have_count(0)
    changed_hair.locator(".text-button").click()
    expect(changed).to_be_hidden()
    assert uploads == []
    assert _entries(page)[0]["key"] == "builtin:yui-0.2"
    _import(page)
    expect(page.locator("[data-template-attachment-slot]")).to_have_count(0)
    page.reload(wait_until="networkidle")
    card.locator(".template-lib-open").click()
    expect(select).to_have_value("hide", timeout=15000)


def test_builtin_yui_upgrade_restores_old_actions_and_customizations(page: Page, running_server: str, builtin_emote_template_dir):
    page.add_init_script("""(() => {
        const libraryKey = 'neko-emote-maker-template-library';
        if (localStorage.getItem(libraryKey)) return;
        const key = 'builtin:yui-0.2';
        const stand = 'animation:d_stand';
        const angry = 'animation:d_angry';
        const surprise = 'animation:d_surprise';
        const graph = {
            schemaVersion: 1,
            nodes: [
                {id: 'source-angry', type: 'source.scene', params: {sceneId: angry}},
                {id: 'source-surprise', type: 'source.scene', params: {sceneId: surprise}},
                {id: 'out-angry', type: 'output.state', params: {stateKey: 'angry_image', startAt: .2, seconds: .5}},
                {id: 'out-surprise', type: 'output.state', params: {stateKey: 'surprised_image'}},
            ],
            links: [
                {from: 'source-angry', fromSocket: 'look', to: 'out-angry', toSocket: 'look'},
                {from: 'source-surprise', fromSocket: 'look', to: 'out-surprise', toSocket: 'look'},
            ],
        };
        const session = {
            fingerprint: key, schemaVersion: 1, sceneId: angry,
            packSceneIds: {idle_image: stand, angry_image: angry, surprised_image: surprise},
            character: {slots: [{name: 'faka1', attachmentMode: 'hide'}]},
        };
        localStorage.setItem(libraryKey, JSON.stringify({entries: [{
            key, sourceName: 'YUI 0.2', displayName: 'YUI 0.2', packageName: 'yui-0.2.zip',
            session: {...session, nodeGraph: graph, packFormat: 'layered'},
        }], currentKey: key}));
        localStorage.setItem('neko-emote-maker-last-template', JSON.stringify({
            ...session, groupId: 'group:yui',
            groups: [{id: 'group:yui', name: 'YUI', items: [
                {kind: 'scene', ref: angry}, {kind: 'scene', ref: surprise}, {kind: 'shot', ref: 'shot:yui'},
            ]}],
            shots: [{id: 'shot:yui', name: 'YUI shot', sourceSceneId: surprise, progress: 20}],
        }));
    })()""")
    _open(page, running_server)
    card = page.locator('[data-template-built-in="true"] .template-lib-open')
    expect(card).to_have_attribute("title", "YUI 0.5")
    card.click()
    expect(page.locator('[data-animation-id="d_angry2"]')).to_have_class(re.compile("is-selected"))
    expect(page.locator('[data-template-attachment-slot="faka1"]')).to_have_value("hide")
    expect(page.locator("#pack-format")).to_have_value("layered")

    (entry,) = _entries(page)
    assert entry["sourceName"] == entry["displayName"] == "YUI 0.5"
    session = entry["session"]
    assert session["sceneId"] == "animation:d_angry2"
    assert session["packSceneIds"] == {
        "idle_image": "animation:d_stand", "angry_image": "animation:d_angry2",
        "surprised_image": "animation:d_surprise1",
    }
    assert [item["ref"] for item in session["groups"][0]["items"]] == [
        "animation:d_angry2", "animation:d_surprise1", "shot:yui",
    ]
    assert session["shots"][0]["sourceSceneId"] == "animation:d_surprise1"
    nodes = {node["id"]: node["params"] for node in session["nodeGraph"]["nodes"]}
    assert nodes["source-angry"]["sceneId"] == "animation:d_angry2"
    assert nodes["source-surprise"]["sceneId"] == "animation:d_surprise1"
    assert nodes["out-angry"]["startAt"] == 0.2 and nodes["out-angry"]["seconds"] == 0.5
    assert {slot["name"] for slot in session["character"]["slots"]} >= {
        "yanjing_1", "yanjing_1_B", "yanjing_1_C",
    }


def test_bad_zip_and_bad_texture_preserve_the_current_template(page: Page, running_server: str):
    _open(page, running_server)
    _import(page)
    key = _entries(page)[0]["key"]
    for data in (b"not a zip", spine_zip(model="invalid", invalid_image=True)):
        page.set_input_files("#template-archive-file", {"name": "bad.zip", "mimeType": "application/zip", "buffer": data})
        expect(page.locator("#template-import-status")).to_have_attribute("data-tone", "error", timeout=30000)
        assert _entries(page)[0]["key"] == key
        expect(page.locator("#template-runtime-badge")).to_be_hidden()


def test_archive_storage_uses_our_identity_and_filename(page: Page, running_server: str):
    _open(page, running_server)
    _import(page, "external-original-name.zip")
    key = _entries(page)[0]["key"]
    page.wait_for_function("""async key => {
        const response = await fetch('/api/emote-maker/library');
        const body = await response.json();
        return body.entries.some(item => item.key === key);
    }""", arg=key)
    saved = page.request.get(running_server + "/api/emote-maker/library/archive", params={"key": key})
    assert saved.status == 200
    assert saved.headers["x-archive-filename"] == key + ".zip"


def test_delete_waits_for_pending_archive_save_and_releases_copy(page: Page, running_server: str):
    _open(page, running_server)
    pending = []

    def pause_save(route):
        if route.request.method == "POST":
            pending.append(route)
        else:
            route.continue_()

    page.route("**/api/emote-maker/library/archive?*", pause_save)
    with page.expect_request(lambda request: "/library/archive?" in request.url and request.method == "POST"):
        _import(page)
    key = _entries(page)[0]["key"]
    remove = page.locator(".template-lib-remove")
    remove.click(force=True)
    page.locator("#emote-confirm-ok").click()
    expect(remove).to_be_disabled()
    with page.expect_response(lambda response: response.request.method == "DELETE" and "/library?" in response.url) as deleted:
        pending[0].continue_()
    assert deleted.value.json()["freedBytes"] > 0
    expect(page.locator(".template-lib-card:not([data-template-built-in])")).to_have_count(0)
    assert page.request.get(running_server + "/api/emote-maker/library/archive", params={"key": key}).status == 404
    page.reload(wait_until="networkidle")
    expect(page.locator(".template-lib-card:not([data-template-built-in])")).to_have_count(0)
    assert page.evaluate("localStorage.getItem('neko-emote-maker-last-template')") is None


def test_delete_failure_leaves_template_available_for_retry(page: Page, running_server: str):
    _open(page, running_server)
    _import(page)
    key = _entries(page)[0]["key"]
    page.wait_for_function("""async key => (await (await fetch('/api/emote-maker/library')).json()).entries.some(item => item.key === key)""", arg=key)

    def fail_delete(route):
        if route.request.method == "DELETE":
            route.fulfill(status=500, json={"success": False, "error": "cannot delete"})
        else:
            route.continue_()

    page.route("**/api/emote-maker/library?*", fail_delete)
    remove = page.locator(".template-lib-remove")
    remove.click(force=True)
    page.locator("#emote-confirm-ok").click()
    expect(page.locator("#emote-toast")).to_have_class(re.compile("is-visible"))
    expect(remove).to_be_enabled()
    expect(page.locator("body")).to_have_class(re.compile("editor-has-template"))
    assert _entries(page)[0]["key"] == key
    assert page.request.get(running_server + "/api/emote-maker/library/archive", params={"key": key}).ok
    page.unroute("**/api/emote-maker/library?*", fail_delete)
    remove.click(force=True)
    page.locator("#emote-confirm-ok").click()
    expect(page.locator(".template-lib-card:not([data-template-built-in])")).to_have_count(0)


def test_static_spine_has_png_without_fake_animation(page: Page, running_server: str):
    _open(page, running_server)
    _import(page, data=spine_zip(animations=0))
    expect(page.locator("#animation-list .animation-card")).to_have_count(0)
    expect(page.locator("#export-png-btn")).to_be_enabled()
    expect(page.locator("#play-animation-btn")).to_be_disabled()


def test_reopens_owned_archive_after_reload_without_original_filename(page: Page, running_server: str):
    _open(page, running_server)
    _import(page, "user-given-name.zip")
    identity = _entries(page)[0]["key"]
    page.wait_for_function("""async key => (await (await fetch('/api/emote-maker/library')).json()).entries.some(item => item.key === key)""", arg=identity)
    with zipfile.ZipFile(io.BytesIO(spine_zip())) as archive:
        image = archive.read("assets/page.png")
    saved = page.request.post(running_server + "/api/emote-maker/library/textures", headers={'x-neko-emote-library': '1'}, params={"key": identity}, multipart={
        "texture_paths": "StageImages/page.png",
        "textures": {"name": "page.png", "mimeType": "image/png", "buffer": image},
    })
    assert saved.status == 200
    texture_reads = []
    page.on("request", lambda request: texture_reads.append(request.url) if "path=StageImages%2Fpage.png" in request.url else None)
    page.reload(wait_until="networkidle")
    imported = page.locator(f'[data-template-key="{identity}"].template-lib-card')
    imported.locator(".template-lib-open").click()
    expect(page.locator("body")).to_have_class(re.compile("editor-has-template"), timeout=15000)
    expect(page.locator("#template-runtime-badge")).to_be_hidden()
    assert len(_entries(page)) == 1 and _entries(page)[0]["key"] == identity
    expect(imported.locator(".asset-card-label")).to_have_text("user-given-name")
    assert not texture_reads

    page.locator("#inspector-tab-export").click()
    page.locator("#pack-mode-graph").click()
    for _ in range(2):
        page.locator("#open-state-stage-btn").click()
        page.locator(".st-rail").click()
        page.locator(".st-drawer-head .list-segment-btn").last.click()
        expect(page.locator(".st-userimg", has_text="page").locator("img")).to_have_attribute("src", re.compile(r"^data:image/png;"))
        page.locator(".st-close").click()
    assert len(texture_reads) == 1


def test_multiple_models_need_explicit_selection(page: Page, running_server: str):
    _open(page, running_server)
    source = spine_zip()
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as old, zipfile.ZipFile(stream, "w") as new:
        for path in old.namelist():
            new.writestr(path, old.read(path))
        new.writestr("assets/second.json", old.read("assets/character.json"))
    page.set_input_files("#template-archive-file", {"name": "multiple.zip", "mimeType": "application/zip", "buffer": stream.getvalue()})
    expect(page.get_by_role("dialog")).to_be_visible()
    select = page.locator(".emote-confirm select")
    select.select_option("assets/second.json")
    page.get_by_role("dialog").locator(".emote-confirm-ok").click()
    expect(page.locator("body")).to_have_class(re.compile("editor-has-template"), timeout=30000)


def test_png_export_contains_a_real_rendered_spine_model(page: Page, running_server: str):
    _open(page, running_server)
    _import(page)
    with page.expect_download() as download:
        page.locator('#export-png-btn').click()
    with Image.open(download.value.path()) as image:
        assert image.format == 'PNG'
        assert image.convert('RGBA').getchannel('A').getextrema()[1] > 0


def test_gif_export_samples_a_warmed_physics_loop_with_full_duration(page: Page, running_server: str, builtin_emote_template_dir):
    page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
        const renderer = window.NekoEmoteSpineRenderer.SpineRenderer;
        const create = renderer.create;
        renderer.create = async function (...args) {
            window.gifTestRenderer = await create.apply(this, args);
            return window.gifTestRenderer;
        };
    })""")
    _open(page, running_server)
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator('[data-animation-id="d_angry2"]').click()
    page.evaluate("""() => {
        for (const [selector, value] of [['#export-format', 'gif'], ['#export-fps', '30'], ['#export-resolution', '128']]) {
            const select = document.querySelector(selector);
            select.value = value;
            select.dispatchEvent(new Event('change', {bubbles: true}));
        }
        const renderer = window.gifTestRenderer;
        const previous = renderer.setTime;
        window.exportSampleTimes = [];
        renderer.setTime = function (time, options) {
            window.exportSampleTimes.push(time);
            return previous.call(this, time, options);
        };
    }""")
    with page.expect_download() as download:
        page.locator("#export-png-btn").click()
    sample_times = page.evaluate("window.exportSampleTimes")
    assert sample_times[0] >= 1
    assert 2.9 < max(sample_times) < 3
    from PIL import Image, ImageSequence

    with Image.open(download.value.path()) as exported:
        assert exported.n_frames == 30
        assert exported.info["loop"] == 0
        delays = [frame.info["duration"] for frame in ImageSequence.Iterator(exported)]
    assert set(delays) == {30, 40}
    assert sum(delays) == 1000


def test_advanced_pack_exports_a_four_second_idle_at_thirty_fps(page: Page, running_server: str, builtin_emote_template_dir):
    _open(page, running_server)
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator('[data-animation-id="d_stand"]').wait_for()
    page.locator("#inspector-tab-export").click()
    page.locator("#pack-scene-idle").select_option("animation:d_stand", force=True)
    page.locator("#pack-mode-graph").click()
    page.evaluate("""() => {
        for (const [selector, value] of [['#pack-format', 'layered'], ['#export-fps', '30'], ['#export-resolution', '512']]) {
            const select = document.querySelector(selector);
            select.value = value;
            select.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }""")
    page.locator("#open-state-stage-btn").click()
    expect(page.locator(".st-span")).to_have_text("0.00s – 4.00s", timeout=15000)
    page.locator(".st-close").click()
    with page.expect_download(timeout=45000) as download:
        page.locator("#pack-export-btn").click()
    with zipfile.ZipFile(download.value.path()) as archive:
        metadata_path = next(path for path in archive.namelist() if path.endswith("/metadata.json"))
        root = metadata_path.rsplit("/", 1)[0]
        layer = json.loads(archive.read(metadata_path))["layers"][0]
        sheet = archive.read(f"{root}/{layer['image']}")
        fallback = archive.read(f"{root}/idle.png")
    with Image.open(io.BytesIO(fallback)) as idle_png, Image.open(io.BytesIO(sheet)) as atlas:
        assert idle_png.format == "PNG"
        assert idle_png.size == (512, 512)
        assert idle_png.convert("RGBA").tobytes() == atlas.crop((0, 0, 512, 512)).convert("RGBA").tobytes()
    idle = layer["states"][0]
    assert idle["frames"] == 120
    assert idle["frame_width"] == idle["frame_height"] == 512
    assert abs(idle["frames"] / (idle["animation_speed"] * 4) - 4) < 1e-6
    page.route("**/test-idle-sheet.png", lambda route: route.fulfill(content_type="image/png", body=sheet))
    result = page.evaluate("""async (idle) => {
        const image = new Image();
        image.src = '/test-idle-sheet.png';
        await image.decode();
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 32;
        const ctx = canvas.getContext('2d');
        const lastFrame = idle.frames - 1;
        const col = lastFrame % idle.hframes;
        const row = Math.floor(lastFrame / idle.hframes);
        ctx.drawImage(image, col * 512, row * 512, 512, 512, 0, 0, 32, 32);
        return {
            width: image.naturalWidth, height: image.naturalHeight,
            lastFrameVisible: ctx.getImageData(0, 0, 32, 32).data.some((value, index) => index % 4 === 3 && value > 0),
        };
    }""", idle)
    assert result == {"width": 4096, "height": 7680, "lastFrameVisible": True}


def test_flat_pack_exports_click_as_a_one_shot_and_idle_as_a_loop(page: Page, running_server: str, builtin_emote_template_dir):
    from PIL import Image

    _open(page, running_server)
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator('[data-animation-id="d_tap1"]').wait_for()
    page.locator("#inspector-tab-export").click()
    page.locator("#pack-scene-idle").select_option("animation:d_stand", force=True)
    page.locator("#pack-scene-click").select_option("animation:d_tap1", force=True)
    page.locator("#pack-format").select_option("flat", force=True)
    page.locator("#export-resolution").select_option("128", force=True)
    with page.expect_download(timeout=45000) as download:
        page.locator("#pack-export-btn").click()
    with zipfile.ZipFile(download.value.path()) as archive:
        for filename, expected in (("idle.gif", 0), ("click.gif", None)):
            path = next(path for path in archive.namelist() if path.endswith("/" + filename))
            with Image.open(io.BytesIO(archive.read(path))) as exported:
                assert exported.n_frames > 1
                assert exported.info.get("loop") == expected


def test_wardrobe_single_crop_sends_optional_prompt(page: Page, running_server: str, builtin_emote_template_dir):
    api = "/api/emote-maker/builtin/wardrobe"
    page.route("**" + api, lambda route: route.fulfill(json={"spritesheetConfigurationCode": ""}))
    page.route("**" + api + "/generate-spritesheet",
               lambda route: route.fulfill(status=400, json={"code": "provider_request_rejected"}))
    _open(page, running_server)
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator("#wardrobe-open").click()
    source = io.BytesIO()
    Image.new("RGB", (256, 200), "blue").save(source, "PNG")
    page.locator("#wardrobe-reference").set_input_files({
        "name": "reference.png", "mimeType": "image/png", "buffer": source.getvalue(),
    })
    layer = page.locator("#wardrobe-annotation-crops")
    expect(layer).to_be_visible()
    bounds = layer.bounding_box()
    x, y, w, h = (bounds[key] for key in ("x", "y", "width", "height"))
    page.mouse.move(x + w * .2, y + h * .2)
    page.mouse.down()
    page.mouse.move(x + w * .8, y + h * .8, steps=4)
    page.mouse.up()
    page.locator("#wardrobe-prompt").fill("  改成深蓝色  ")
    with page.expect_request("**" + api + "/generate-spritesheet") as request:
        page.locator("#wardrobe-submit").click()
    payload = request.value.post_data_buffer
    assert b'name="supplemental_prompt"\r\n\r\n' + "改成深蓝色".encode() + b"\r\n" in payload


def test_wardrobe_spritesheet_selects_multiple_crops_and_places_sprites(
    page: Page, running_server: str, builtin_emote_template_dir, monkeypatch, tmp_path,
):
    from main_routers import emote_template_router

    library = tmp_path / "sheets"
    monkeypatch.setattr(emote_template_router, "_wardrobe_sheet_directory", lambda: library)
    api = "/api/emote-maker/builtin/wardrobe"
    generated = Image.new("RGBA", (200, 100))
    draw = ImageDraw.Draw(generated)
    draw.rectangle((20, 30, 62, 74), fill="#fe00ee")
    draw.rectangle((135, 25, 170, 67), fill="#00cc22")
    sheet = io.BytesIO()
    generated.save(sheet, "PNG")
    reference = io.BytesIO()
    Image.new("RGB", (200, 100), "#3366aa").save(reference, "PNG")
    submitted = []

    def status(route):
        response = route.fetch()
        payload = response.json()
        payload.update(spritesheetConfigurationCode="", spritesheetCropAspectLimit=10)
        route.fulfill(response=response, json=payload)

    def generate(route):
        data = route.request.post_data_buffer
        assert re.findall(rb'Content-Disposition: form-data; name="([^"]+)"', data) == [
            b"reference", b"annotation",
        ]
        submitted.append(json.loads(re.search(rb'name="annotation"\r\n\r\n([^\r]+)', data).group(1)))
        route.fulfill(status=200, content_type="image/png", body=sheet.getvalue(),
                      headers={"X-Wardrobe-Spritesheet-Layout": "check", "X-Wardrobe-Spritesheet-Padded": "1"})

    def box_select(selector, left, top, right, bottom):
        surface = page.locator(selector)
        expect(surface).to_have_attribute("aria-disabled", "false")
        bounds = surface.bounding_box()
        page.mouse.move(bounds["x"] + left * bounds["width"], bounds["y"] + top * bounds["height"])
        page.mouse.down()
        page.mouse.move(bounds["x"] + right * bounds["width"], bounds["y"] + bottom * bounds["height"], steps=6)
        page.mouse.up()

    def resize_box(selector, edge, dx, dy):
        handle = page.locator(f'{selector} .is-selected [data-edge="{edge}"]')
        expect(handle).to_be_visible()
        bounds = handle.bounding_box()
        surface = page.locator(selector).bounding_box()
        x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + dx * surface["width"], y + dy * surface["height"], steps=6)
        page.mouse.up()

    page.route("**" + api, status)
    page.route("**" + api + "/generate-spritesheet", generate)
    _open(page, running_server)
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator("#wardrobe-open").click()
    submit = page.locator("#wardrobe-submit")
    expect(page.locator(".wardrobe-view-tab")).to_have_count(2)
    expect(page.locator("#wardrobe-source-view")).to_have_text("挂件生成")
    expect(page.locator("#wardrobe-dialog input[type=radio]")).to_have_count(0)
    expect(page.locator("#wardrobe-spritesheet-open")).to_be_visible()
    expect(submit).to_be_disabled()
    expect(page.locator("#wardrobe-reference")).to_be_hidden()
    with page.expect_file_chooser() as chooser:
        page.locator("#wardrobe-reference-open").click()
    chooser.value.set_files({
        "name": "reference.png", "mimeType": "image/png", "buffer": reference.getvalue(),
    })
    source = "#wardrobe-annotation-crops"
    expect(page.locator(source)).to_be_visible()
    expect(page.locator("#wardrobe-annotation-count")).to_have_text("0/3")
    expect(page.locator("#wardrobe-annotation-hint")).to_be_hidden()
    expect(page.locator("#wardrobe-dialog")).not_to_contain_text("reference.png")
    expect(page.locator("#wardrobe-reference-open")).to_be_enabled()
    expect(page.locator("#wardrobe-minimap")).to_be_visible()
    minimap = page.locator("#wardrobe-minimap-image")
    expect(minimap.locator("img")).to_have_js_property("naturalWidth", 200)
    view_box = page.locator("#wardrobe-minimap-window")
    expect(view_box).to_have_css("width", f'{minimap.bounding_box()["width"]:.0f}px')
    viewport = page.locator("#wardrobe-annotation-viewport")
    initial_viewport = viewport.bounding_box()
    toolbar = page.locator(".wardrobe-annotation-tools")
    assert toolbar.bounding_box()["y"] > initial_viewport["y"] + initial_viewport["height"] / 2
    counter = page.locator("#wardrobe-annotation-count").bounding_box()
    assert counter["x"] > initial_viewport["x"] + initial_viewport["width"] / 2
    assert counter["y"] < initial_viewport["y"] + 48
    for _ in range(4):
        page.locator("#wardrobe-annotation-zoom-in").click()
    expect(page.locator("#wardrobe-annotation-scale")).to_have_text("244%")
    assert view_box.bounding_box()["width"] < minimap.bounding_box()["width"]
    assert viewport.bounding_box() == initial_viewport
    nav = minimap.bounding_box()
    minimap.click(position={"x": nav["width"] * .15, "y": nav["height"] / 2})
    left = viewport.evaluate("el => el.scrollLeft")
    minimap.click(position={"x": nav["width"] * .85, "y": nav["height"] / 2})
    assert viewport.evaluate("el => el.scrollLeft") > left
    right = viewport.evaluate("el => el.scrollLeft")
    page.locator("#wardrobe-annotation-pan").click()
    page.mouse.move(initial_viewport["x"] + initial_viewport["width"] * .5,
                    initial_viewport["y"] + initial_viewport["height"] * .5)
    page.mouse.down()
    page.mouse.move(initial_viewport["x"] + initial_viewport["width"] * .6,
                    initial_viewport["y"] + initial_viewport["height"] * .5, steps=5)
    page.mouse.up()
    assert viewport.evaluate("el => el.scrollLeft") < right
    page.locator("#wardrobe-annotation-pan").click()
    page.locator("#wardrobe-annotation-fit").click()
    expect(page.locator("#wardrobe-annotation-scale")).to_have_text("100%")
    expect(submit).to_be_disabled()
    prompt_field = page.locator("#wardrobe-prompt")
    expect(prompt_field).to_be_hidden()
    box_select(source, .1, .3, .22, .5)
    notice = page.locator("#wardrobe-padding-notice")
    expect(notice.locator('[data-i18n="emoteMaker.wardrobeCropLowResolution"]')).to_be_visible()
    expect(submit).to_be_enabled()
    expect(prompt_field).to_be_visible()
    prompt_field.fill("颜色改成深蓝色")
    page.locator("#wardrobe-padding-close").click()
    expect(notice).to_be_hidden()
    page.locator("#wardrobe-annotation-clear").click()
    expect(page.locator("#wardrobe-annotation-count")).to_have_text("0/3")
    box_select(source, .05, .1, .35, .8)
    expect(prompt_field).to_have_value("")
    prompt_field.fill("保留金属质感")
    box_select(source, .44, .12, .54, .82)
    expect(prompt_field).to_be_hidden()
    box_select(source, .6, .1, .95, .8)
    expect(page.locator("#wardrobe-annotation-count")).to_have_text("3/3")
    # Move the first box at the limit, then resize it and delete the middle box.
    box_select(source, .2, .45, .24, .5)
    resize_box(source, "se", .04, .05)
    page.locator(f"{source} .wardrobe-crop-box").nth(1).locator(".wardrobe-crop-remove").click()
    expect(page.locator("#wardrobe-annotation-count")).to_have_text("2/3")
    bounds = page.locator(source).bounding_box()
    page.mouse.move(bounds["x"] + bounds["width"] * .46, bounds["y"] + bounds["height"] * .1)
    page.mouse.down()
    page.mouse.move(bounds["x"] + bounds["width"] * .48, bounds["y"] + bounds["height"] * .9, steps=6)
    expect(notice).to_be_hidden()
    page.mouse.up()
    expect(notice).to_be_visible()
    expect(notice.locator('[data-i18n="emoteMaker.wardrobeCropPadded"]')).to_be_visible()
    expect(notice.locator(".emote-timed-tip-progress")).to_have_css("background-color", "rgb(168, 217, 239)")
    assert notice.evaluate("el => el.getAnimations()[0].effect.getTiming().duration") == 6000
    dismiss = page.locator("#wardrobe-padding-close")
    click_x = notice.evaluate("""el => {
        const notice = el.getBoundingClientRect();
        const button = el.querySelector('button').getBoundingClientRect();
        if (Math.abs(notice.height - button.height) > 2 || Math.abs(notice.right - button.right) > 2) return null;
        return button.width - 4;
    }""")
    assert click_x is not None
    dismiss.click(position={"x": click_x, "y": 4})
    expect(dismiss).to_be_disabled()
    exit_motion = notice.evaluate("""el => {
        const animation = el.getAnimations()[0];
        const frames = animation.effect.getKeyframes();
        const lifted = el.matches(':popover-open');
        animation.pause();
        animation.currentTime = animation.effect.getTiming().duration - 1;
        const pastBottom = el.getBoundingClientRect().top >= window.innerHeight;
        const opacity = getComputedStyle(el).opacity;
        animation.finish();
        return { lifted, hop: frames[1].transform, pastBottom, opacity };
    }""")
    assert exit_motion["lifted"] and "-15px" in exit_motion["hop"]
    assert exit_motion["pastBottom"] and exit_motion["opacity"] == "1"
    expect(notice).to_be_hidden()
    # Dismissing the notice leaves the cooldown in effect.
    page.locator(f"{source} .wardrobe-crop-box").last.locator(".wardrobe-crop-remove").click()
    box_select(source, .46, .1, .48, .9)
    expect(notice).to_be_hidden()
    expect(submit).to_be_enabled()
    submit.click()
    picker = "#wardrobe-spritesheet-picker"
    expect(page.locator(picker)).to_be_visible()
    expect(page.locator("#wardrobe-minimap")).to_be_hidden()
    expect(page.locator("#wardrobe-spritesheet-layout-warning")).to_be_visible()
    assert len(submitted) == 1 and len(submitted[0]) == 3
    for actual, expected in zip(submitted[0], ((.09, .15, .43, .9), (.6, .1, .95, .8), (.46, .1, .48, .9)), strict=True):
        assert all(abs(a - b) < .015 for a, b in zip(actual, expected, strict=True)), submitted
    expect(notice).to_be_hidden()
    save = page.locator("#wardrobe-spritesheet-save")
    card = page.locator(".wardrobe-library-card")
    expect(page.locator("#wardrobe-library-view")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#wardrobe-spritesheet-picker")).to_be_visible()
    expect(card.locator("[data-sheet-open]")).to_have_attribute("aria-pressed", "true")
    expect(save).to_be_hidden()
    stored = list(library.glob("*.png"))
    assert len(stored) == 1 and stored[0].read_bytes() == sheet.getvalue()

    page.reload(wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator("#wardrobe-library-open").click()
    expect(card.locator("img")).to_have_js_property("naturalWidth", 200)
    expect(page.locator("#wardrobe-form")).to_be_hidden()
    card.locator("[data-sheet-open]").click()
    expect(page.locator(picker)).to_be_visible()
    expect(page.locator("#wardrobe-spritesheet-layout-warning")).to_be_visible()
    expect(save).to_be_hidden()
    assert len(submitted) == 1

    add = page.locator("#wardrobe-sprite-add")
    expect(add).to_be_disabled()
    box_select(picker, .05, .15, .25, .9)
    resize_box(picker, "e", .15, 0)
    add.click()
    expect(page.locator(f"{picker} .wardrobe-crop-box")).to_have_count(0)
    expect(add).to_be_disabled()
    page.locator("#wardrobe-close").click()
    placement_tip = page.locator("#emote-page-tips .emote-timed-tip").filter(has_text="可右键挂件设置跟随骨骼以及调整图层")
    expect(placement_tip).to_be_visible()
    placement_tip.locator(".emote-timed-tip-close").click()
    expect(placement_tip).to_have_count(0)
    page.locator("#wardrobe-library-open").click()
    box_select(picker, .5, .15, .82, .9)
    box_select(picker, .66, .5, .76, .5)
    add.click()
    expect(page.locator(f"{picker} .wardrobe-crop-box")).to_have_count(0)
    expect(add).to_be_disabled()
    page.locator("#wardrobe-close").click()
    expect(placement_tip).to_have_count(0)
    sprites = page.locator(".wardrobe-placement")
    expect(sprites).to_have_count(2)
    expect(sprites.first.locator("img")).to_have_js_property("naturalWidth", 43)
    second = sprites.last
    expect(second.locator("img")).to_have_js_property("naturalWidth", 36)
    page.keyboard.press("Control+z")
    expect(sprites).to_have_count(1)
    page.keyboard.press("Control+Shift+z")
    expect(sprites).to_have_count(2)
    expect(second.locator("img")).to_have_js_property("naturalWidth", 36)

    page.locator("#wardrobe-library-open").click()
    card.locator("[data-sheet-remove]").click()
    confirm = page.locator("#wardrobe-library-confirm")
    confirm.locator('button[value="cancel"]').click()
    expect(card).to_have_count(1)
    card.locator("[data-sheet-remove]").click()
    confirm.locator('button[value="delete"]').click()
    expect(page.locator("#wardrobe-library-empty")).to_be_visible()
    assert not stored[0].exists()
    page.locator("#wardrobe-close").click()
    expect(sprites).to_have_count(2)

    artwork = page.locator("#wardrobe-artwork")

    def center_color():
        return artwork.evaluate("canvas => Array.from(canvas.getContext('2d').getImageData("
                                "canvas.width / 2, canvas.height / 2, 1, 1).data)")

    up = page.locator("#wardrobe-layer-up")
    down = page.locator("#wardrobe-layer-down")
    second.click(button="right")
    expect(up).to_be_disabled()
    assert center_color() == [0, 204, 34, 255]
    down.click()
    expect(down).to_be_enabled()
    expect(sprites.last.locator("img")).to_have_js_property("naturalWidth", 43)
    assert center_color() == [254, 0, 238, 255]
    page.keyboard.press("Control+z")
    expect(sprites.last.locator("img")).to_have_js_property("naturalWidth", 36)
    page.keyboard.press("Control+Shift+z")
    expect(sprites.last.locator("img")).to_have_js_property("naturalWidth", 43)

    page.reload(wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    expect(sprites.last.locator("img")).to_have_js_property("naturalWidth", 43)
    expect(sprites.first.locator("img")).to_have_js_property("naturalWidth", 36)
    assert center_color() == [254, 0, 238, 255]
    with page.expect_download() as download:
        page.locator("#export-png-btn").click()
    with Image.open(download.value.path()) as exported:
        assert exported.convert("RGBA").getpixel((exported.width // 2, exported.height // 2)) == (254, 0, 238, 255)
    sprites.first.press("Shift+F10")
    up.click()
    expect(up).to_be_disabled()
    expect(sprites.last.locator("img")).to_have_js_property("naturalWidth", 36)
    assert center_color() == [0, 204, 34, 255]
    down.press("Escape")

    start = second.bounding_box()
    x, y = start["x"] + start["width"] / 2, start["y"] + start["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 65, y - 50, steps=8)
    page.mouse.up()
    assert float(second.evaluate("el => parseFloat(el.style.left)")) > 55
    expect(second).to_have_attribute("aria-pressed", "true")
    frame = page.locator("#wardrobe-transform")
    expect(frame).to_be_visible()
    corner = frame.locator('.wardrobe-transform-scale[data-x="1"][data-y="1"]').bounding_box()
    x, y = corner["x"] + corner["width"] / 2, corner["y"] + corner["height"] / 2
    width_before = float(second.evaluate("el => parseFloat(el.style.width)"))
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 20, y + 22, steps=6)
    page.mouse.up()
    new_width = float(second.evaluate("el => parseFloat(el.style.width)"))
    assert new_width > width_before
    rotation = frame.locator(".wardrobe-transform-rotate").bounding_box()
    x, y = rotation["x"] + rotation["width"] / 2, rotation["y"] + rotation["height"] / 2
    transform_before = second.evaluate("el => el.style.transform")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 30, y + 12, steps=6)
    page.mouse.up()
    assert second.evaluate("el => el.style.transform") != transform_before
    saved_transform = page.evaluate("""() => {
        const items = JSON.parse(localStorage.getItem('neko.emote.wardrobeSprites.builtin:yui-0.2'));
        return items[1];
    }""")
    assert abs(saved_transform["basis"][1]) > .05

    page.keyboard.press("Control+z")
    assert second.evaluate("el => el.style.transform") == transform_before
    page.keyboard.press("Control+z")
    assert float(second.evaluate("el => parseFloat(el.style.width)")) == width_before
    page.keyboard.press("Control+z")
    assert abs(float(second.evaluate("el => parseFloat(el.style.left)")) - 50) < .01
    for _ in range(3):
        page.keyboard.press("Control+Shift+z")
    assert page.evaluate("""() => JSON.parse(
        localStorage.getItem('neko.emote.wardrobeSprites.builtin:yui-0.2'))[1]""") == saved_transform
    second.press("Shift+F10")
    page.locator("#wardrobe-sprite-bone").select_option("free", force=True)
    read_placement = """() => JSON.parse(localStorage.getItem('neko.emote.wardrobeSprites.builtin:yui-0.2'))[1]"""
    assert page.evaluate(read_placement)["bindingMode"] == "free"
    page.keyboard.press("Control+z")
    assert page.evaluate(read_placement) == saved_transform
    page.keyboard.press("Control+Shift+z")
    assert page.evaluate(read_placement)["bindingMode"] == "free"
    page.keyboard.press("Control+z")

    page.reload(wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    expect(sprites).to_have_count(2)
    expect(second.locator("img")).to_have_js_property("naturalWidth", 36)
    assert float(second.evaluate("el => parseFloat(el.style.left)")) > 55
    second.focus()
    expect(frame).to_be_visible()
    assert abs(float(second.evaluate("el => parseFloat(el.style.width)")) - new_width) < .001
    assert page.evaluate("""() => JSON.parse(
        localStorage.getItem('neko.emote.wardrobeSprites.builtin:yui-0.2'))[1]""") == saved_transform
    expect(page.locator("#export-format")).to_have_value("png")
    with page.expect_download() as download:
        page.locator("#export-png-btn").click()
    with Image.open(download.value.path()) as exported:
        assert (0, 204, 34, 255) in set(exported.convert("RGBA").getdata())
    page.locator("#wardrobe-sprite-remove").click()
    expect(sprites).to_have_count(1)
    page.keyboard.press("Control+z")
    expect(sprites).to_have_count(2)
    expect(second.locator("img")).to_have_js_property("naturalWidth", 36)
    assert page.evaluate("""() => JSON.parse(
        localStorage.getItem('neko.emote.wardrobeSprites.builtin:yui-0.2'))[1]""") == saved_transform
    texture_url = f"{running_server}/api/emote-maker/library/texture"
    params = {"key": "builtin:yui-0.2", "path": f"WardrobeSprites/{saved_transform['id']}.png"}
    assert page.request.get(texture_url, params=params).ok

    failed_remove = "**/library/textures/remove?*"
    page.route(failed_remove, lambda route: route.fulfill(status=500))
    with page.expect_response(lambda response: "/textures/remove?" in response.url and response.status == 500):
        page.keyboard.press("Control+Shift+z")
    expect(page.locator("#redo-btn")).to_be_enabled()
    expect(sprites).to_have_count(2)
    page.unroute(failed_remove)
    page.keyboard.press("Control+Shift+z")
    expect(sprites).to_have_count(1)
    assert page.request.get(texture_url, params=params).status == 404

    expect(placement_tip).to_have_count(0)
    sprites.first.press("Delete")
    expect(sprites).to_have_count(0)
    page.locator("#wardrobe-open").click()
    expect(page.locator("#wardrobe-spritesheet-open")).to_be_enabled()
    page.locator("#wardrobe-spritesheet-file").set_input_files({
        "name": "preview.png", "mimeType": "image/png", "buffer": sheet.getvalue(),
    })
    expect(page.locator(picker)).to_be_visible()
    box_select(picker, .05, .15, .34, .9)
    add.click()
    expect(add).to_be_disabled()
    page.locator("#wardrobe-close").click()
    expect(sprites).to_have_count(1)
    expect(placement_tip).to_be_visible()


def test_yui_accessories_follow_the_visible_arm_when_talking(
    page: Page, running_server: str, builtin_emote_template_dir,
):
    key = "builtin:yui-0.2"
    storage_key = f"neko.emote.wardrobeSprites.{key}"
    anchors = ["bone5", "bone6", "bone110", "bone9"]
    sprites = [{
        "id": f"sprite-{index + 0xA100:016x}", "x": .5, "y": .5, "size": .12,
        "anchor": {"name": name, "matrix": [900, -90, 90, 900, 100, 8]},
        "aboveSlot": slot, "bindingMode": "manual",
    } for index, (name, slot) in enumerate(zip(anchors, ("shou1", "shou1", "shou1_shuohua", "shou2")))]
    png = io.BytesIO()
    Image.new("RGBA", (20, 20), "cyan").save(png, "PNG")
    _open(page, running_server)
    for sprite in sprites:
        filename = f"{sprite['id']}.png"
        response = page.request.post(running_server + "/api/emote-maker/library/textures", headers={'x-neko-emote-library': '1'}, params={"key": key}, multipart={
            "textures": {"name": filename, "mimeType": "image/png", "buffer": png.getvalue()},
            "texture_paths": f"WardrobeSprites/{filename}",
        })
        assert response.ok, response.text()
    page.evaluate("""([key, sprites]) => {
        localStorage.setItem(key, JSON.stringify(sprites));
        const renderer = window.NekoEmoteSpineRenderer.SpineRenderer;
        const create = renderer.create;
        renderer.create = async function (...args) {
            window.wardrobeArmRenderer = await create.apply(this, args);
            return window.wardrobeArmRenderer;
        };
    }""", [storage_key, sprites])
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.wait_for_function("() => window.wardrobeArmRenderer?.options.imageLayers().length === 4")

    def select_action(name):
        page.locator(f'[data-animation-id="{name}"]').click()
        page.locator("#play-animation-btn").evaluate("button => { if (window.wardrobeArmRenderer.playing) button.click(); }")
        page.locator("#animation-progress").fill("35")

    def check_pose(bones, slots):
        actual = page.evaluate("""([key, bones]) => {
            const renderer = window.wardrobeArmRenderer;
            const saved = JSON.parse(localStorage.getItem(key));
            return renderer.options.imageLayers().map(({corners, aboveSlot}, index) => {
                const sprite = saved[index];
                const bone = bones[index];
                const [a, b, c, d, x, y] = renderer.boneCanvasTransform(bone);
                const scale = renderer.skeleton.findBone(bone).data.length
                    / renderer.skeleton.findBone(sprite.anchor.name).data.length;
                const [e, f, , , u, v] = sprite.anchor.matrix;
                const center = [(corners[0][0] + corners[2][0]) / 2, (corners[0][1] + corners[2][1]) / 2];
                const axis = [(corners[3][0] - corners[0][0]) / sprite.size, (corners[3][1] - corners[0][1]) / sprite.size];
                const expected = [a * scale * u + c * v + x, b * scale * u + d * v + y,
                    a * scale * e + c * f, b * scale * e + d * f];
                const error = Math.max(...[...center, ...axis].map((value, i) => Math.abs(value - expected[i])));
                return {center, axis, aboveSlot, error};
            });
        }""", [storage_key, bones])
        assert [layer["aboveSlot"] for layer in actual] == slots, actual
        assert all(layer["error"] < 1e-6 for layer in actual), actual
        return actual

    normal_bones = ["bone5", "bone6", "bone7", "bone9"]
    normal_slots = ["shou1", "shou1", "shou1", "shou2"]
    select_action("d_stand")
    before = check_pose(normal_bones, normal_slots)
    select_action("d_talk")
    talking = check_pose(["bone108", "bone109", "bone110", "bone9"],
                         ["shou1_shuohua2", "shou1_shuohua", "shou1_shuohua", "shou2"])
    assert all(talking[index]["center"] != before[index]["center"]
               and talking[index]["axis"] != before[index]["axis"] for index in range(3))
    forearm = page.locator(".wardrobe-placement").nth(1)
    before_nudge = forearm.evaluate("button => parseFloat(button.style.left)")
    forearm.press("ArrowRight")
    assert abs(forearm.evaluate("button => parseFloat(button.style.left)") - before_nudge - 1) < .001
    assert page.evaluate("key => JSON.parse(localStorage.getItem(key)).map(sprite => sprite.anchor.name)", storage_key) == anchors
    select_action("d_stand")
    check_pose(normal_bones, normal_slots)


def test_restored_accessory_waits_for_the_full_png_before_caching_texture(
    page: Page, running_server: str, builtin_emote_template_dir, monkeypatch,
):
    import asyncio
    from threading import Event

    from starlette.responses import StreamingResponse

    from main_routers import emote_template_router

    key = "builtin:yui-0.2"
    sprite = {"id": "sprite-00000000000000a1", "x": .12, "y": .12, "size": .16, "aboveSlot": "yanjing_5"}
    filename = f"{sprite['id']}.png"
    source = Image.new("RGBA", (96, 78))
    ImageDraw.Draw(source).rectangle((20, 20, 75, 57), fill=(24, 170, 238, 255))
    encoded = io.BytesIO()
    source.save(encoded, "PNG", compress_level=0)
    content = encoded.getvalue()
    release = Event()
    response_class = emote_template_router.Response

    def delay_accessory(data=b"", *args, **kwargs):
        if data != content or kwargs.get('media_type') != 'image/png':
            return response_class(data, *args, **kwargs)

        async def body():
            yield content[:8192]
            await asyncio.to_thread(release.wait, 20)
            yield content[8192:]

        return StreamingResponse(body(), media_type="image/png", headers={
            **kwargs.get('headers', {}), "Content-Length": str(len(content)),
        })

    monkeypatch.setattr(emote_template_router, "Response", delay_accessory)
    _open(page, running_server)
    response = page.request.post(running_server + "/api/emote-maker/library/textures", headers={'x-neko-emote-library': '1'}, params={"key": key}, multipart={
        "textures": {"name": filename, "mimeType": "image/png", "buffer": content},
        "texture_paths": f"WardrobeSprites/{filename}",
    })
    assert response.ok, response.text()
    page.evaluate("""([key, sprite]) => {
        localStorage.setItem(`neko.emote.wardrobeSprites.${key}`, JSON.stringify([sprite]));
        const renderer = window.NekoEmoteSpineRenderer.SpineRenderer;
        const create = renderer.create;
        renderer.create = async function (...args) {
            window.restoredAccessoryRenderer = await create.apply(this, args);
            return window.restoredAccessoryRenderer;
        };
    }""", [key, sprite])
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    selector = ".wardrobe-placement img"
    try:
        page.wait_for_function("""selector => {
            const image = document.querySelector(selector);
            return image?.naturalWidth === 96 && !image.complete;
        }""", arg=selector)
        assert page.locator(selector).evaluate("""image => {
            const renderer = window.restoredAccessoryRenderer;
            renderer.render();
            return !renderer._layerTextures.has(image);
        }""")
    finally:
        release.set()
    page.wait_for_function("""selector => {
        const image = document.querySelector(selector);
        return image?.complete && window.restoredAccessoryRenderer._layerTextures.has(image);
    }""", arg=selector)
    assert page.evaluate("""([x, y]) => {
        const {data, width, height} = window.restoredAccessoryRenderer.captureFrame();
        const center = (Math.round(y * height) * width + Math.round(x * width)) * 4;
        return Array.from(data.slice(center, center + 4));
    }""", [sprite["x"], sprite["y"]]) == [24, 170, 238, 255]


def test_wardrobe_library_imports_static_images(
    page: Page, running_server: str, builtin_emote_template_dir, monkeypatch, tmp_path,
):
    from main_routers import emote_template_router

    library = tmp_path / "sheets"
    monkeypatch.setattr(emote_template_router, "_wardrobe_sheet_directory", lambda: library)
    generation_requests = []
    page.on("request", lambda request: generation_requests.append(request.url)
            if "/generate-spritesheet" in request.url else None)
    _open(page, running_server)
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator("#wardrobe-library-open").click()

    upload = page.locator("#wardrobe-library-image")
    import_button = page.locator("#wardrobe-library-import")
    cards = page.locator(".wardrobe-library-card")
    preview = page.locator("#wardrobe-spritesheet-surface img")
    gif = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(gif, "GIF")
    upload.set_input_files({"name": "unsupported.gif", "mimeType": "image/gif", "buffer": gif.getvalue()})
    expect(page.locator("#wardrobe-library-status")).to_contain_text("静态 PNG、JPG 或 WebP")
    expect(cards).to_have_count(0)
    assert not list(library.glob("*.png"))

    jpeg = io.BytesIO()
    orientation = Image.Exif()
    orientation[274] = 6
    Image.new("RGB", (100, 60), "red").save(jpeg, "JPEG", exif=orientation)
    with page.expect_file_chooser() as chooser:
        import_button.click()
    chooser.value.set_files({"name": "accessory.jpg", "mimeType": "image/jpeg", "buffer": jpeg.getvalue()})
    expect(preview).to_have_js_property("naturalWidth", 60)
    expect(preview).to_have_js_property("naturalHeight", 100)
    expect(cards).to_have_count(1)
    expect(import_button).to_be_enabled()

    webp = io.BytesIO()
    accessory = Image.new("RGBA", (128, 128))
    ImageDraw.Draw(accessory).rectangle((32, 32, 127, 127), fill="cyan")
    accessory.save(webp, "WEBP", lossless=True)
    upload.set_input_files({"name": "accessory.webp", "mimeType": "image/webp", "buffer": webp.getvalue()})
    expect(preview).to_have_js_property("naturalWidth", 128)
    expect(cards).to_have_count(2)
    sheet_id = cards.locator('[aria-pressed="true"]').get_attribute("data-sheet-open")
    with Image.open(library / f"{sheet_id}.png") as stored:
        assert stored.format == "PNG"
        assert stored.getpixel((0, 0))[3] == 0
        assert stored.getpixel((32, 32)) == (0, 255, 255, 255)

    page.reload(wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator("#wardrobe-library-open").click()
    cards.locator(f'[data-sheet-open="{sheet_id}"]').click()
    expect(preview).to_have_js_property("naturalWidth", 128)
    picker = page.locator("#wardrobe-spritesheet-picker")
    bounds = picker.bounding_box()
    x, y, w, h = (bounds[key] for key in ("x", "y", "width", "height"))
    picker.evaluate("""el => el.addEventListener('gotpointercapture', event => {
        el.dataset.capturedPointer = event.pointerId;
    }, { once: true })""")
    page.mouse.move(x + w * .15, y + h * .15)
    page.mouse.down()
    page.mouse.move(x + w * .85, y + h * .60, steps=5)
    # A host can drop pointer capture during the gesture. Crossing the image
    # edge afterwards should keep the draft alive up to the eventual release.
    picker.evaluate("el => el.releasePointerCapture(Number(el.dataset.capturedPointer))")
    page.mouse.move(x + w + 12, y + h * .80, steps=5)
    expect(picker.locator(".is-drawing")).to_be_visible()
    add = page.locator("#wardrobe-sprite-add")
    expect(add).to_be_disabled()
    page.mouse.move(x + w + 12, y + h + 12, steps=5)
    page.mouse.up()

    selected = picker.locator(".wardrobe-crop-box.is-selected")
    corner = selected.locator('[data-edge="nw"]').bounding_box()
    page.mouse.move(corner["x"] + 2, corner["y"] + 2)
    page.mouse.down()
    page.mouse.move(x - 12, y - 12, steps=5)
    page.mouse.up()
    assert selected.evaluate("el => [el.style.left, el.style.top, el.style.width, el.style.height]") == [
        "0%", "0%", "100%", "100%",
    ]
    add.click()
    page.locator("#wardrobe-close").click()
    placed_image = page.locator(".wardrobe-placement img")
    expect(placed_image).to_have_js_property("naturalWidth", 96)
    expect(placed_image).to_have_js_property("naturalHeight", 96)
    assert not generation_requests


def test_wardrobe_opens_local_spritesheet_without_generation(
    page: Page, running_server: str, builtin_emote_template_dir,
):
    api = "/api/emote-maker/builtin/wardrobe"
    requests = []

    def status(route):
        response = route.fetch()
        payload = response.json()
        payload.update(spritesheetConfigurationCode="missing_api_key")
        route.fulfill(response=response, json=payload)

    page.route("**" + api, status)
    page.on("request", lambda request: requests.append(request.url) if "/generate-spritesheet" in request.url else None)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.add_init_script("""(() => {
        document.addEventListener('DOMContentLoaded', () => {
            const renderer = window.NekoEmoteSpineRenderer.SpineRenderer;
            const create = renderer.create;
            renderer.create = async function (...args) {
                window.wardrobeTestRenderer = await create.apply(this, args);
                return window.wardrobeTestRenderer;
            };
        });
    })()""")
    _open(page, running_server)
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator("#wardrobe-open").click()
    expect(page.locator("#wardrobe-submit")).to_be_disabled()

    source = io.BytesIO()
    local = Image.new("RGBA", (128, 128))
    draw = ImageDraw.Draw(local)
    draw.rectangle((32, 32, 95, 95), fill="cyan")
    draw.rectangle((46, 46, 49, 49), fill="red")
    local.save(source, "PNG")
    with page.expect_file_chooser() as chooser:
        page.locator("#wardrobe-spritesheet-open").click()
    chooser.value.set_files({"name": "backup.png", "mimeType": "image/png", "buffer": source.getvalue()})

    picker = page.locator("#wardrobe-spritesheet-picker")
    expect(picker).to_be_visible()
    expect(page.locator("#wardrobe-library-view")).to_have_attribute("aria-pressed", "true")
    save = page.locator("#wardrobe-spritesheet-save")
    expect(save).to_be_enabled()

    def reject_library_save(route):
        if route.request.method == "POST":
            route.fulfill(status=500, json={"code": "wardrobe_library_failed"})
        else:
            route.continue_()

    page.route("**" + api + "/spritesheets", reject_library_save)
    save.click()
    expect(page.locator("#wardrobe-sheet-status")).to_have_text("无法存入素材库。可在当前预览中重试。")
    expect(save).to_be_enabled()
    expect(picker).to_be_visible()
    page.unroute("**" + api + "/spritesheets", reject_library_save)
    expect(page.locator("#wardrobe-submit")).to_be_disabled()
    box = picker.bounding_box()
    page.mouse.move(box["x"] + box["width"] * .15, box["y"] + box["height"] * .15)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * .85, box["y"] + box["height"] * .85, steps=5)
    page.mouse.up()
    page.locator("#wardrobe-sprite-add").click()
    expect(picker.locator(".wardrobe-crop-box")).to_have_count(0)
    expect(page.locator("#wardrobe-sprite-add")).to_be_disabled()
    expect(page.locator("#wardrobe-padding-notice")).to_be_hidden()
    page.locator("#wardrobe-close").click()
    placement = page.locator(".wardrobe-placement")
    expect(placement.locator("img")).to_have_js_property("naturalWidth", 64)
    assert not requests

    # A single accessory can cross the character layer, even with the preview zoomed in.
    for _ in range(8):
        page.locator("#zoom-in-btn").click()
    viewport = page.locator(".canvas-stage").bounding_box()
    accessory = placement.bounding_box()
    page.mouse.click(min(accessory["x"] + accessory["width"] - 30, viewport["x"] + viewport["width"] - 12),
                     min(accessory["y"] + accessory["height"] - 60, viewport["y"] + viewport["height"] - 52),
                     button="right")
    menu = page.locator("#wardrobe-sprite-menu")
    expect(menu).to_be_visible()
    bounds = menu.bounding_box()
    assert bounds["x"] + bounds["width"] <= viewport["x"] + viewport["width"]
    assert bounds["y"] + bounds["height"] <= viewport["y"] + viewport["height"]
    up = page.locator("#wardrobe-layer-up")
    down = page.locator("#wardrobe-layer-down")
    layer = page.locator("#wardrobe-layer-slot")
    expect(up).to_be_disabled()
    down.click()
    expect(layer).to_have_value(re.compile(r"^slot:"))
    expect(down).to_be_enabled()
    expect(page.locator("#wardrobe-artwork")).to_be_hidden()
    layer.select_option("slot:tou")
    expect(layer).to_have_value("slot:tou")
    assert page.evaluate("""() => JSON.parse(localStorage.getItem(
        'neko.emote.wardrobeSprites.builtin:yui-0.2'))[0].aboveSlot""") == "tou"
    def accessory_pixels():
        with page.expect_download() as download:
            page.locator("#export-png-btn").click()
        with Image.open(download.value.path()) as exported:
            return sum(pixel == (0, 255, 255, 255) for pixel in exported.convert("RGBA").get_flattened_data())

    # Move the tile over the face and bangs so the intervening layer is visible in export.
    page.locator("#fit-btn").click()
    placement.focus()
    for _ in range(15):
        placement.press("ArrowUp")
    front_position = page.evaluate("""() => window.wardrobeTestRenderer.drawOrderNames().at(-1)""")
    placement.press("Shift+F10")
    layer.select_option(f"slot:{front_position}")
    magnified_sampling = page.evaluate("""() => {
        const renderer = window.wardrobeTestRenderer;
        renderer.render();
        const texture = [...renderer._layerTextures.values()][0].texture;
        const { data } = renderer.captureFrame({ render: false });
        const shader = renderer.renderer.batcherShader.getFragmentSource();
        let redPixels = 0;
        for (let i = 0; i < data.length; i += 4) {
            if (data[i] === 255 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 255) redPixels++;
        }
        return { cubic: shader.includes('sampleAccessory'), redPixels, mipmaps: texture.useMipMaps };
    }""")
    assert magnified_sampling["cubic"] and magnified_sampling["mipmaps"] and magnified_sampling["redPixels"] > 100
    front_pixels = accessory_pixels()
    placement.press("Shift+F10")
    layer.select_option("slot:tou")
    inside_pixels = accessory_pixels()
    page.reload(wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    expect(placement.locator("img")).to_have_js_property("naturalWidth", 64)
    assert accessory_pixels() == inside_pixels
    sampling = page.evaluate("""() => {
        const renderer = window.wardrobeTestRenderer;
        renderer.render();
        const layer = [...renderer._layerTextures.values()][0];
        const gl = renderer.gl;
        layer.texture.bind();
        return {
            mipmaps: gl.getTexParameter(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER) === gl.LINEAR_MIPMAP_LINEAR,
            imageSize: [layer.texture.getImage().naturalWidth, layer.texture.getImage().naturalHeight],
            magnifiedSize: [layer.slot.appliedPose.darkColor.r, layer.slot.appliedPose.darkColor.g],
            error: gl.getError(),
        };
    }""")
    assert sampling == {"mipmaps": True, "imageSize": [64, 64], "magnifiedSize": [64, 64], "error": 0}
    placement.press("Shift+F10")
    layer.select_option("back")
    back_pixels = accessory_pixels()
    assert back_pixels < inside_pixels < front_pixels, (back_pixels, inside_pixels, front_pixels, page_errors)
    placement.click(button="right")
    layer.select_option("back")
    expect(down).to_be_disabled()
    expect(page.locator("#wardrobe-artwork-behind")).to_be_visible()
    expect(page.locator("#wardrobe-artwork")).to_be_hidden()
    assert page.evaluate("""() => JSON.parse(localStorage.getItem(
        'neko.emote.wardrobeSprites.builtin:yui-0.2'))[0].behind""")
    page.locator("#inspector-tab-export").click()
    page.locator("#transparent-background").uncheck()
    with page.expect_download() as download:
        page.locator("#export-png-btn").click()
    with Image.open(download.value.path()) as exported:
        pixels = exported.convert("RGBA")
        assert pixels.getpixel((exported.width // 2, exported.height // 2)) != (0, 255, 255, 255)
        assert pixels.getpixel((0, 0))[3] == 255
    page.locator("#transparent-background").check()

    page.reload(wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    expect(placement.locator("img")).to_have_js_property("naturalWidth", 64)
    expect(page.locator("#wardrobe-artwork-behind")).to_be_visible()
    placement.click(button="right")
    layer.select_option("front")
    expect(page.locator("#wardrobe-artwork")).to_be_visible()
    expect(page.locator("#wardrobe-artwork-behind")).to_be_hidden()
    down.press("Escape")
    placement.focus()
    for _ in range(15):
        placement.press("ArrowDown")

    page.locator("#play-animation-btn").evaluate("""button => {
        if (window.wardrobeTestRenderer.playing) button.click();
    }""")
    progress = page.locator("#animation-progress")
    progress.fill("10")
    head = page.evaluate("""() => {
        const renderer = window.wardrobeTestRenderer;
        for (let y = 0.15; y < 0.7; y += 0.01) {
            for (let x = 0.35; x < 0.65; x += 0.01) {
                if (renderer.attachmentsAtCanvasPoint(x, y)[0]?.slot === 'tou') return [x, y];
            }
        }
        return null;
    }""")
    assert head
    canvas = page.locator("#wardrobe-placements").bounding_box()
    page.mouse.move(canvas["x"] + canvas["width"] / 2, canvas["y"] + canvas["height"] / 2)
    page.mouse.down()
    page.mouse.move(canvas["x"] + canvas["width"] * head[0], canvas["y"] + canvas["height"] * head[1], steps=8)
    page.mouse.up()
    binding = page.locator("#wardrobe-sprite-bone")
    placement.click(button="right")
    expect(binding).to_be_visible()
    expect(binding).to_have_value("auto")
    expect(binding.locator('option[value="auto"]')).to_contain_text("bone4")
    expect(binding.locator('optgroup').first.locator('option[value="bone:bone4"]')).to_have_count(1)
    recorded = page.evaluate("""() => JSON.parse(
        localStorage.getItem('neko.emote.wardrobeSprites.builtin:yui-0.2'))[0].anchor""")
    assert recorded["name"] == "bone4"
    artwork = page.locator("#wardrobe-artwork")
    expect(artwork).to_be_visible()
    expect(placement.locator("img")).to_have_css("visibility", "hidden")
    page.locator('[data-animation-id="d_tap1"]').click()
    playback = artwork.evaluate("""async (canvas, anchor) => {
        const renderer = window.wardrobeTestRenderer;
        const play = document.querySelector('#play-animation-btn');
        if (!renderer.playing) play.click();
        const context = canvas.getContext('2d');
        const centers = new Set();
        let matched = true;
        for (let frame = 0; frame < 16; frame++) {
            await new Promise(requestAnimationFrame);
            const [a, b, c, d, x, y] = renderer.boneCanvasTransform(anchor.name);
            const [e, f, g, h, offsetX, offsetY] = anchor.matrix;
            // Original red marker is centered 16 px up and left of the 64 px crop center.
            const marker = -.3 * 16 / 64;
            const u = offsetX + (e + g) * marker;
            const v = offsetY + (f + h) * marker;
            const px = Math.floor((a * u + c * v + x) * canvas.width);
            const py = Math.floor((b * u + d * v + y) * canvas.height);
            const [red, green, blue, alpha] = context.getImageData(px, py, 1, 1).data;
            matched &&= red === 255 && green === 0 && blue === 0 && alpha === 255;
            centers.add(`${px},${py}`);
        }
        if (renderer.playing) play.click();
        return { matched, distinctPositions: centers.size };
    }""", recorded)
    assert playback["matched"] and playback["distinctPositions"] > 1
    before = placement.evaluate("el => [el.style.left, el.style.top, el.style.transform]")
    progress.fill("64")
    assert placement.evaluate("el => [el.style.left, el.style.top, el.style.transform]") != before
    bound_position = placement.evaluate("el => [parseFloat(el.style.left) / 100, parseFloat(el.style.top) / 100]")
    with page.expect_download() as download:
        page.locator("#export-png-btn").click()
    with Image.open(download.value.path()) as exported:
        px, py = (int(fraction * size) for fraction, size in zip(bound_position, exported.size))
        assert exported.convert("RGBA").getpixel((px, py)) == (0, 255, 255, 255)

    def pose():
        return placement.evaluate("""el => {
            const m = new DOMMatrix(getComputedStyle(el).transform);
            return [parseFloat(el.style.left), parseFloat(el.style.top), m.a, m.b, m.c, m.d];
        }""")

    bound = pose()
    placement.click(button="right")
    binding.select_option("bone:bone4")
    assert all(abs(a - b) < .001 for a, b in zip(bound, pose()))
    page.reload(wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    expect(placement.locator("img")).to_have_js_property("naturalWidth", 64)
    placement.click(button="right")
    expect(binding).to_have_value("bone:bone4")
    page.locator("#play-animation-btn").evaluate("""button => {
        if (window.wardrobeTestRenderer.playing) button.click();
    }""")
    bound = pose()
    binding.select_option("free")
    free = pose()
    assert all(abs(a - b) < .001 for a, b in zip(bound, free))
    progress.fill("30")
    assert pose() == free
