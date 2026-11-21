"""Test the user-image tab switch, import flow and state assignments."""
from __future__ import annotations

import io
import json
import re
import zipfile

from PIL import Image, ImageDraw
from playwright.sync_api import Page, expect

import tests.frontend.test_emote_state_stage as harness


def _template() -> bytes:
    from tests.emote_fixtures import spine_zip
    return spine_zip()


def _open_stage(page: Page, running_server: str, images=None) -> None:
    """Import a template, inject its image list and record texture-upload requests."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.set_input_files("#template-archive-file", {
        "name": "first.zip", "mimeType": "application/zip", "buffer": _template(),
    })
    page.wait_for_function("() => document.body.classList.contains('editor-has-template')",
                           timeout=25000)
    images = images if images is not None else []
    page.evaluate(harness.REVEAL)
    page.evaluate(harness.LOCALE, "/static/locales/zh-CN.json")
    page.evaluate(
        """(images) => {
            window.__userImages = images;   // 活引用：测试从浏览器侧往这里追加
            const posts = [];
            window.__texturePosts = posts;
            const original = window.fetch;
            window.fetch = function (input, init) {
                try {
                    const url = typeof input === 'string' ? input : input.url;
                    if ((init && init.method === 'POST') && url.includes('/library/textures')) {
                        const paths = init && init.body && init.body.get
                            ? init.body.getAll('texture_paths') : [];
                        posts.push({ url, paths });
                    }
                } catch (_) { /* 记录失败不影响主流程 */ }
                return original.apply(this, arguments);
            };
            window.NekoEmoteStateStage.setUserImages(window.__userImages);
        }""", images)
    page.evaluate(harness.BOOT, 1.0)
    page.wait_for_timeout(400)
    page.evaluate("() => document.querySelector('.st-rail').click()")
    page.wait_for_function(
        "() => document.querySelector('.st-drawer').classList.contains('is-open')",
        timeout=10000)


def _switch_to_images(page: Page) -> None:
    page.evaluate("""() => {
        const btns = [...document.querySelectorAll('.st-drawer-head .list-segment-btn')];
        btns[1].click();
    }""")


def _switch_to_library(page: Page) -> None:
    page.evaluate("""() => {
        const btns = [...document.querySelectorAll('.st-drawer-head .list-segment-btn')];
        btns.find((b) => b.textContent.includes('动作库')).click();
    }""")


def test_capsule_switches_between_library_and_images(page: Page, running_server: str) -> None:
    _open_stage(page, running_server)
    initial = page.evaluate("""() => ({
        sceneActive: [...document.querySelectorAll('.st-drawer-head .list-segment-btn')]
            .find((b) => b.textContent.includes('动作库')).className.includes('is-active'),
        searchShown: !document.querySelector('.st-library-search').hidden,
        libraryShown: getComputedStyle(document.querySelector('.st-library')).display !== 'none',
        panelHidden: document.querySelector('.st-userimg-panel').hidden,
    })""")
    assert initial["sceneActive"] and initial["libraryShown"] and initial["panelHidden"], initial

    _switch_to_images(page)
    switched = page.evaluate("""() => ({
        sceneShown: getComputedStyle(document.querySelector('.st-library')).display !== 'none',
        searchHidden: document.querySelector('.st-library-search').hidden,
        panelShown: getComputedStyle(document.querySelector('.st-userimg-panel')).display !== 'none',
        importShown: getComputedStyle(document.querySelector('.st-userimg-import')).display !== 'none',
    })""")
    assert not switched["sceneShown"], switched
    assert switched["searchHidden"] and switched["panelShown"] and switched["importShown"], switched
    _switch_to_library(page)


def test_import_persists_into_the_template_copy(page: Page, running_server: str) -> None:
    """Import a PNG through the file/change entry point and persist it under StageImages/."""
    images = []
    _open_stage(page, running_server, images=images)
    _switch_to_images(page)
    with page.expect_response(lambda response: '/library/textures?' in response.url
                              and response.request.method == 'POST') as uploaded:
        page.evaluate("""() => {
            const bytes = Uint8Array.from(atob(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg=='),
                (c) => c.charCodeAt(0));
            const file = new File([bytes], '我的图片.png', { type: 'image/png' });
            const transfer = new DataTransfer();
            transfer.items.add(file);
            const input = document.querySelector('input[type=file][accept*="image/gif"]');
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
    assert uploaded.value.ok
    paths = page.evaluate("window.__texturePosts.flatMap(post=>post.paths).filter(path=>path.startsWith('StageImages/'))")
    assert paths
    page.wait_for_function("""async paths => {
        const key = JSON.parse(localStorage.getItem('neko-emote-maker-template-library')).currentKey;
        const response = await fetch('/api/emote-maker/library/textures?key=' + encodeURIComponent(key));
        const body = await response.json();
        return paths.every(path=>body.paths?.includes(path));
    }""", arg=paths)
    page.reload(wait_until='networkidle')
    page.locator('.template-lib-card:not([data-template-built-in]) .template-lib-open').first.click()
    expect(page.locator('#export-png-btn')).to_be_enabled()
    page.locator('#inspector-tab-export').click()
    page.locator('#pack-mode-graph').click()
    page.locator('#open-state-stage-btn').click()
    page.locator('.st-rail').click()
    page.locator('.st-drawer-head .list-segment-btn').last.click()
    expect(page.locator('.st-userimg').filter(has_text='我的图片.png')).to_be_visible()


def test_gif_stage_image_survives_reload_and_exports_timed_frames(
    page: Page, running_server: str, builtin_emote_template_dir,
) -> None:
    source = io.BytesIO()
    first = Image.new("RGBA", (32, 32))
    second = first.copy()
    ImageDraw.Draw(first).rectangle((4, 4, 23, 23), fill="red")
    ImageDraw.Draw(second).rectangle((10, 10, 27, 27), fill="blue")
    first.save(source, "GIF", save_all=True, append_images=[second], duration=[120, 280], loop=0, disposal=2)

    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator('[data-animation-id="d_stand"]').wait_for()
    page.locator("#inspector-tab-export").click()
    page.locator("#pack-format").select_option("flat", force=True)
    page.locator("#export-resolution").select_option("128", force=True)
    page.locator("#pack-mode-graph").click()
    page.locator("#open-state-stage-btn").click()
    page.locator(".st-rail").click()
    idle = page.locator('.st-tile[data-state="idle_image"] .st-tile-sub')
    page.locator('.st-pick[data-scene="animation:d_stand"]').click()
    expect(idle).to_have_text("d_stand")
    page.keyboard.press("Control+z")
    expect(idle).not_to_have_text("d_stand")
    page.keyboard.press("Control+Shift+z")
    expect(idle).to_have_text("d_stand")
    page.locator(".st-drawer-head .list-segment-btn").last.click()
    with page.expect_file_chooser() as chooser:
        page.locator(".st-userimg-import").click()
    chooser.value.set_files({"name": "state.GIF", "mimeType": "image/gif", "buffer": source.getvalue()})
    gif = page.locator(".st-userimg").filter(has_text="state.GIF")
    expect(gif).to_be_visible()
    expect(gif.locator("img")).to_have_attribute("src", re.compile(r"^data:image/gif;"))

    for state in ("idle_image", "click_image", "talking_image"):
        page.locator(f'.st-tile[data-state="{state}"]').click()
        gif.click()
        expect(page.locator(f'.st-tile[data-state="{state}"] .st-tile-sub')).to_have_text("state.GIF")
    talking = page.locator('.st-tile[data-state="talking_image"] .st-tile-sub')
    page.keyboard.press("Control+z")
    expect(talking).not_to_have_text("state.GIF")
    page.keyboard.press("Control+Shift+z")
    expect(talking).to_have_text("state.GIF")
    page.locator(".st-rail").click()
    page.locator(".st-bound-chip-x").click()
    expect(talking).not_to_have_text("state.GIF")
    page.keyboard.press("Control+z")
    expect(talking).to_have_text("state.GIF")
    page.locator('.st-tile[data-state="idle_image"]').click()
    expect(page.locator(".st-span")).to_have_text("0.00s – 0.40s")
    page.wait_for_function("""() => {
        const canvas = document.querySelector('.st-stage');
        const [r,,b] = canvas.getContext('2d').getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data;
        return r < 20 && b > 200;
    }""")
    page.locator(".st-close").click()
    page.reload(wait_until="networkidle")
    page.locator('[data-template-built-in="true"] .template-lib-open').click()
    page.locator('[data-animation-id="d_stand"]').wait_for()
    page.locator("#inspector-tab-export").click()
    with page.expect_download(timeout=45000) as download:
        page.locator("#pack-export-btn").click()
    with zipfile.ZipFile(download.value.path()) as archive:
        for filename, durations, loop in (("idle.gif", [120, 280], 0), ("click.gif", [120, 280], None),
                                          ("talking.gif", [120], 0)):
            path = next(path for path in archive.namelist() if path.endswith("/" + filename))
            with Image.open(io.BytesIO(archive.read(path))) as exported:
                assert exported.n_frames == len(durations)
                assert exported.info.get("loop") == loop
                for index, duration in enumerate(durations):
                    exported.seek(index)
                    assert exported.info["duration"] == duration
                    assert exported.convert("RGBA").getpixel((0, 0))[3] == 0

    page.locator("#pack-format").select_option("layered", force=True)
    with page.expect_download(timeout=45000) as download:
        page.locator("#pack-export-btn").click()
    with zipfile.ZipFile(download.value.path()) as archive:
        path = next(path for path in archive.namelist() if path.endswith("/metadata.json"))
        metadata = json.loads(archive.read(path))
    states = [state for layer in metadata["layers"] for state in layer["states"] if state.get("visible")]
    assert len(states) == 3
    for state in states:
        assert state["frames"] > 1
        assert abs(state["frames"] / (state["animation_speed"] * 4) - .4) < 1e-6

    page.locator("#open-state-stage-btn").click()
    page.locator(".st-rail").click()
    page.locator(".st-drawer-head .list-segment-btn").last.click()
    failed_remove = "**/library/textures/remove?*"
    page.route(failed_remove, lambda route: route.fulfill(status=500))
    gif.locator(".st-userimg-remove").click()
    expect(page.locator("#emote-toast")).to_have_text("删除图片失败，请重试。")
    expect(gif).to_be_visible()
    expect(gif.locator(".st-userimg-remove")).to_be_enabled()
    page.unroute(failed_remove)

    with page.expect_response(lambda response: "/library/textures/remove?" in response.url) as removal:
        gif.locator(".st-userimg-remove").click()
    assert removal.value.ok
    assert b".gif" in removal.value.request.post_data_buffer
    expect(gif).to_have_count(0)


def test_seeded_image_renders_and_click_assigns(page: Page, running_server: str) -> None:
    """Show image thumbnails and names, then assign the selected image as a source.image node."""
    images = [{"id": "img-mine", "label": "我的图片.png", "thumb": ""}]
    _open_stage(page, running_server, images=images)
    _switch_to_images(page)
    label = page.evaluate(
        "() => document.querySelector('.st-userimg-grid .st-pick-label')?.textContent || null")
    assert label == "我的图片.png", label
    assert page.evaluate("() => document.querySelector('.st-userimg-empty').hidden") is True
    assert page.evaluate(
        "() => document.querySelector('.st-drawer-count').textContent") == "1"

    page.evaluate("() => document.querySelector('.st-userimg-grid .st-userimg').click()")
    assigned = page.evaluate("""() => {
        const graph = window.__STAGE.getGraph();
        const image = graph.nodes.find((n) => n.type === 'source.image');
        return image ? image.params.imageId : '';
    }""")
    assert assigned == "img-mine", assigned

    # 副标题跟着换：绑了图片的格子不能再报"跟随默认"
    sub_after = page.evaluate(
        "() => document.querySelector('.st-tile.is-selected .st-tile-sub')?.textContent || ''")
    assert sub_after == "我的图片.png", sub_after

    _switch_to_library(page)
    page.locator('.st-pick[data-scene="anim-b"]').hover()
    expect(page.locator(".st-anim")).to_have_attribute("data-scene", "anim-b")
    expect(page.locator(".st-span")).to_have_text("0.00s – 12.00s")
    assert harness._upstream_scene(page, "idle_image")["type"] == "source.image"
    page.mouse.move(20, 20)
    expect(page.locator(".st-anim")).to_have_attribute("data-scene", "images:img-mine")

    # 删除钮：从清单里移除（赋格不受影响，节点还在图上）
    page.evaluate("""() => {
        window.__userImages.splice(0, 1);   // 同步清单
        const btns = [...document.querySelectorAll('.st-drawer-head .list-segment-btn')];
        btns.find((b) => b.textContent.includes('动作库')).click();
        btns[1].click();
    }""")
    page.wait_for_function(
        "() => document.querySelectorAll('.st-userimg-grid .st-userimg').length === 0",
        timeout=5000)
    empty_shown = page.evaluate(
        "() => !document.querySelector('.st-userimg-empty').hidden")
    assert empty_shown is True, "删完该显示空态"
    # 图上的 source.image 节点仍在（赋格已完成，删图不撤赋格）
    still = page.evaluate(
        "() => Boolean(window.__STAGE.getGraph().nodes.find((n) => n.type === 'source.image'))")
    assert still is True, "删图不该撤掉已赋的格"
