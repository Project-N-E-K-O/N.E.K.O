import base64
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from playwright.sync_api import Page, expect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_DIR = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"
APPEARANCE_ROOT = PROJECT_ROOT / "static" / "assets" / "cat-resources" / "appearance" / "dev_neko"
AVATAR_UI_BUTTON_PART_NAMES = (
    "cat-resource-registry.js",
    "core.js",
    "idle-assets-and-question.js",
    "idle-playground.js",
    "idle-actions-and-audio.js",
    "idle-drag-and-subactions.js",
    "idle-journey-and-presentation.js",
    "idle-cat-mind-observations.js",
    "methods-setup.js",
    "methods-buttons.js",
    "methods-return.js",
    "methods-state-and-cleanup.js",
)
BACKENDS = (
    ("live2d", "Live2DManager", PROJECT_ROOT / "static/live2d/live2d-ui-buttons.js"),
    ("vrm", "VRMManager", PROJECT_ROOT / "static/vrm/vrm-ui-buttons.js"),
    ("mmd", "MMDManager", PROJECT_ROOT / "static/mmd/mmd-ui-buttons.js"),
)
VIEWPORTS = (
    pytest.param({"width": 1440, "height": 900}, id="wide"),
    pytest.param({"width": 390, "height": 844}, id="narrow"),
)


def _gif_frame_png_data_url(path: Path, frame_index: int) -> str:
    with Image.open(path) as image:
        image.seek(frame_index if frame_index >= 0 else image.n_frames + frame_index)
        frame = image.convert("RGBA")
        buffer = BytesIO()
        frame.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _non_background_bbox(page: Page, background: tuple[int, int, int]) -> tuple[int, int, int, int]:
    screenshot = Image.open(BytesIO(page.screenshot())).convert("RGB")
    backdrop = Image.new("RGB", screenshot.size, background)
    box = ImageChops.difference(screenshot, backdrop).getbbox()
    assert box is not None
    return box


AVATAR_UI_BUTTON_TEMPLATE_PATHS = (
    PROJECT_ROOT / "templates/index.html",
    PROJECT_ROOT / "templates/card_maker.html",
    PROJECT_ROOT / "templates/character_card_manager.html",
    PROJECT_ROOT / "templates/drawing_guess.html",
    PROJECT_ROOT / "templates/live2d_parameter_editor.html",
    PROJECT_ROOT / "templates/model_manager.html",
    PROJECT_ROOT / "templates/soccer_demo.html",
    PROJECT_ROOT / "templates/viewer.html",
)


@pytest.mark.parametrize("template_path", AVATAR_UI_BUTTON_TEMPLATE_PATHS)
def test_avatar_button_parts_load_in_dependency_order(template_path: Path):
    template = template_path.read_text(encoding="utf-8")
    script_positions = [
        template.index(f"/static/avatar/avatar-ui-buttons/{part_name}")
        for part_name in AVATAR_UI_BUTTON_PART_NAMES
    ]
    assert script_positions == sorted(script_positions)


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("prefix,manager_name,backend_script", BACKENDS)
def test_avatar_button_parts_smoke_each_backend_and_layout(
    page: Page,
    viewport: dict[str, int],
    prefix: str,
    manager_name: str,
    backend_script: Path,
):
    page.set_viewport_size(viewport)
    page.set_content("<main id='smoke-root'></main>")
    page.evaluate(
        """
        () => {
            window.APP_VERSION = 'split-smoke';
            window.t = (key) => key;
            window.__NEKO_MULTI_WINDOW__ = true;
        }
        """
    )
    page.add_script_tag(content=f"class {manager_name} {{}}; window.{manager_name} = {manager_name};")

    part_paths = tuple(AVATAR_UI_BUTTONS_DIR / name for name in AVATAR_UI_BUTTON_PART_NAMES)
    assert all(path.is_file() for path in part_paths)
    for part_path in part_paths:
        page.add_script_tag(path=str(part_path))
    page.add_script_tag(path=str(backend_script))

    page.evaluate(
        """
        ({ prefix, managerName }) => {
            const Manager = window[managerName];
            const manager = new Manager();
            const toolbar = manager.setupFloatingButtonsBase(null);
            const config = manager.getDefaultButtonConfigs()[0];
            const buttonData = manager.createButtonElement(config, toolbar, 0);
            toolbar.appendChild(buttonData.btnWrapper);
            buttonData.btnWrapper.appendChild(buttonData.btn);
            toolbar.style.left = '16px';
            toolbar.style.top = '16px';
            toolbar.style.display = 'flex';

            const returnContainer = manager.createReturnButton();
            returnContainer.style.left = '80px';
            returnContainer.style.top = '16px';
            returnContainer.style.width = '48px';
            returnContainer.style.height = '48px';
            returnContainer.style.display = 'block';

            window.__avatarReturnClicks = 0;
            window.addEventListener(`${prefix}-return-click`, () => {
                window.__avatarReturnClicks += 1;
            });
        }
        """,
        {"prefix": prefix, "managerName": manager_name},
    )

    toolbar = page.locator(f"#{prefix}-floating-buttons")
    main_button = page.locator(f"#{prefix}-btn-mic")
    return_button = page.locator(f"#{prefix}-btn-return")
    expect(toolbar).to_be_visible()
    expect(return_button).to_be_visible()

    # Cat return recomputes the toolbar with a fractional scale. The raster
    # icon must share the button's exact box/origin instead of introducing a
    # second translate layer which Chromium may round to another device pixel.
    page.evaluate(
        """prefix => {
            const toolbar = document.getElementById(`${prefix}-floating-buttons`);
            toolbar.style.transformOrigin = 'left top';
            toolbar.style.transform = 'scale(0.537)';
        }""",
        prefix,
    )
    icon_geometry = main_button.evaluate(
        """button => {
            const icon = button.querySelector('img');
            const buttonRect = button.getBoundingClientRect();
            const iconRect = icon.getBoundingClientRect();
            return {
                centerDelta: {
                    x: iconRect.x + iconRect.width / 2 - buttonRect.x - buttonRect.width / 2,
                    y: iconRect.y + iconRect.height / 2 - buttonRect.y - buttonRect.height / 2,
                },
                sizeDelta: {
                    width: iconRect.width - buttonRect.width,
                    height: iconRect.height - buttonRect.height,
                },
                transform: getComputedStyle(icon).transform,
                imageRendering: getComputedStyle(icon).imageRendering,
            };
        }"""
    )
    assert abs(icon_geometry["centerDelta"]["x"]) < 0.001
    assert abs(icon_geometry["centerDelta"]["y"]) < 0.001
    assert abs(icon_geometry["sizeDelta"]["width"]) < 0.001
    assert abs(icon_geometry["sizeDelta"]["height"]) < 0.001
    assert icon_geometry["transform"] == "none"
    assert icon_geometry["imageRendering"] == "auto"

    return_button.click()
    assert page.evaluate("window.__avatarReturnClicks") == 1

    page.evaluate(
        "prefix => { document.getElementById(`${prefix}-floating-buttons`).style.display = 'none'; }",
        prefix,
    )
    expect(toolbar).to_be_hidden()


def test_non_square_visual_actions_keep_return_surfaces_pixel_anchor(page: Page):
    prefix, manager_name, backend_script = BACKENDS[0]
    background = (1, 2, 3)
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content("<main id='visual-action-layout-smoke'></main>")
    page.add_style_tag(path=str(PROJECT_ROOT / "static/css/index.css"))
    page.evaluate(
        """
        background => {
            document.documentElement.style.background = `rgb(${background.join(',')})`;
            document.body.style.background = `rgb(${background.join(',')})`;
            document.body.style.margin = '0';
            window.APP_VERSION = 'visual-action-layout-smoke';
            window.t = (key) => key;
            window.__NEKO_MULTI_WINDOW__ = false;
            window.nekoAutoGoodbye = { getState: () => ({ visualTier: 'cat1' }) };
        }
        """,
        list(background),
    )
    page.add_script_tag(content=f"class {manager_name} {{}}; window.{manager_name} = {manager_name};")
    for part_name in AVATAR_UI_BUTTON_PART_NAMES:
        page.add_script_tag(path=str(AVATAR_UI_BUTTONS_DIR / part_name))
    page.add_script_tag(path=str(backend_script))
    page.evaluate(
        """
        ({ prefix, managerName }) => {
            const manager = new window[managerName]();
            const container = manager.createReturnButton();
            container.style.left = '250px';
            container.style.top = '200px';
            container.style.display = 'block';
            const button = document.getElementById(`${prefix}-btn-return`);
            button.style.setProperty('--neko-idle-return-size', '112px');
            const art = button.querySelector('.neko-idle-return-art');
            art.style.transition = 'none';
        }
        """,
        {"prefix": prefix, "managerName": manager_name},
    )

    assets = {
        "normal": APPEARANCE_ROOT / "idle/cat-idle-cat1.webp",
        "wide-centered": APPEARANCE_ROOT / "action/cat-idle-cat1-mouse.webp",
        "tall-bottom": APPEARANCE_ROOT / "action/cat-idle-cat1-lion_dance.webp",
    }

    def render_bbox(layout: str, src: str) -> tuple[int, int, int, int]:
        page.evaluate(
            """
            async ({ prefix, layout, src }) => {
                const button = document.getElementById(`${prefix}-btn-return`);
                const art = button.querySelector('.neko-idle-return-art');
                if (layout === 'normal') button.removeAttribute('data-neko-cat1-visual-layout');
                else button.setAttribute('data-neko-cat1-visual-layout', layout);
                art.src = src;
                await art.decode();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }
            """,
            {"prefix": prefix, "layout": layout, "src": src},
        )
        return _non_background_bbox(page, background)

    def assert_surface_anchors(surface_name: str, render):
        for frame_name, frame_index in (("first", 0), ("last", -1)):
            idle_src = _gif_frame_png_data_url(assets["normal"], frame_index)
            idle_box = render("normal", idle_src)
            for layout in ("wide-centered", "tall-bottom"):
                staged_idle_box = render(layout, idle_src)
                staged_deltas = tuple(
                    abs(actual - expected)
                    for actual, expected in zip(staged_idle_box, idle_box, strict=True)
                )
                assert max(staged_deltas) <= 1, (
                    f"{surface_name} {layout} moves the previous idle frame before the new image "
                    f"is ready: idle={idle_box}, staged={staged_idle_box}"
                )
                action_box = render(
                    layout,
                    _gif_frame_png_data_url(assets[layout], frame_index),
                )
                deltas = tuple(
                    abs(actual - expected)
                    for actual, expected in zip(action_box, idle_box, strict=True)
                )
                assert max(deltas) <= 1, (
                    f"{surface_name} {layout} {frame_name} frame visibly jumps: "
                    f"idle={idle_box}, action={action_box}"
                )

    assert_surface_anchors("production flex return button", render_bbox)

    page.evaluate(
        """
        prefix => {
            document.getElementById(`${prefix}-btn-return`).parentElement.style.display = 'none';
            const mirror = document.createElement('div');
            mirror.className = 'neko-idle-cat1-compact-mirror';
            mirror.style.left = '250px';
            mirror.style.top = '200px';
            mirror.style.width = '112px';
            mirror.style.height = '112px';
            const art = document.createElement('img');
            art.className = 'neko-idle-cat1-compact-mirror-art';
            art.alt = '';
            mirror.appendChild(art);
            document.body.appendChild(mirror);
        }
        """,
        prefix,
    )

    def render_mirror_bbox(layout: str, src: str) -> tuple[int, int, int, int]:
        page.evaluate(
            """
            async ({ layout, src }) => {
                const mirror = document.querySelector('.neko-idle-cat1-compact-mirror');
                const art = mirror.querySelector('.neko-idle-cat1-compact-mirror-art');
                if (layout === 'normal') mirror.removeAttribute('data-neko-cat1-art-layout');
                else mirror.setAttribute('data-neko-cat1-art-layout', layout);
                art.src = src;
                await art.decode();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }
            """,
            {"layout": layout, "src": src},
        )
        return _non_background_bbox(page, background)

    assert_surface_anchors("compact mirror", render_mirror_bbox)


def test_cat_right_click_routes_cat1_weighted_pool_and_sleep_tiers_to_scared_demotion(page: Page):
    prefix, manager_name, backend_script = BACKENDS[0]
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content("<main id='visual-action-weighted-contextmenu-smoke'></main>")
    page.evaluate(
        """
        () => {
            window.APP_VERSION = 'visual-action-weighted-contextmenu-smoke';
            window.t = (key) => key;
            window.__NEKO_MULTI_WINDOW__ = false;
            window.__rightClickRoll = 0;
            Math.random = () => window.__rightClickRoll;
            window.__visualTier = 'cat1';
            window.__setVisualTier = (tier, source = 'test') => {
                window.__visualTier = tier;
                window.dispatchEvent(new CustomEvent('neko:auto-goodbye:state-change', {
                    detail: { type: 'visual-tier', tier, source },
                }));
            };
            window.nekoAutoGoodbye = {
                getState: () => ({ visualTier: window.__visualTier }),
                demoteVisualTierToCat1: (source, reason) => {
                    if (!['cat2', 'cat3'].includes(window.__visualTier)) return false;
                    window.__sleepTierDemotion = { source, reason, from: window.__visualTier };
                    window.__setVisualTier('cat1', source);
                    return true;
                },
            };
        }
        """
    )
    page.add_script_tag(content=f"class {manager_name} {{}}; window.{manager_name} = {manager_name};")
    for part_name in AVATAR_UI_BUTTON_PART_NAMES:
        page.add_script_tag(path=str(AVATAR_UI_BUTTONS_DIR / part_name))
    page.add_script_tag(path=str(backend_script))

    page.evaluate(
        """
        ({ prefix, managerName }) => {
            const Manager = window[managerName];
            const manager = new Manager();
            const returnContainer = manager.createReturnButton();
            returnContainer.style.left = '80px';
            returnContainer.style.top = '80px';
            returnContainer.style.width = '96px';
            returnContainer.style.height = '96px';
            returnContainer.style.display = 'block';

            window.__cat1VisualActionRequests = [];
            window.__cat1VisualActionTerminal = null;
            window.__scaredRequestOptions = null;
            window.__avatarReturnClicks = 0;
            window.NekoCatIdlePresentation = {
                requestCat1Stroke: (options = {}) => {
                    window.__cat1VisualActionRequests.push('stroke');
                    return true;
                },
                requestCat1Cosplay1: (options = {}) => {
                    window.__cat1VisualActionRequests.push('cosplay1');
                    return true;
                },
                requestCat1Scared: (options = {}) => {
                    window.__cat1VisualActionRequests.push('scared');
                    window.__scaredRequestOptions = options;
                    return true;
                },
            };
            window.addEventListener(`${prefix}-return-click`, () => {
                window.__avatarReturnClicks += 1;
            });
        }
        """,
        {"prefix": prefix, "managerName": manager_name},
    )

    return_button = page.locator(f"#{prefix}-btn-return")
    expect(return_button).to_be_visible()
    return_button.click(button="right")
    assert page.evaluate("window.__cat1VisualActionRequests") == ["stroke"]
    assert page.evaluate("window.__avatarReturnClicks") == 0

    page.evaluate("window.__rightClickRoll = 0.5")
    return_button.click(button="right")
    assert page.evaluate("window.__cat1VisualActionRequests") == ["stroke", "cosplay1"]

    page.evaluate("window.__setVisualTier('cat2')")
    return_button.click(button="right")
    assert page.evaluate("window.__cat1VisualActionRequests") == ["stroke", "cosplay1", "scared"]
    assert page.evaluate("window.__visualTier") == "cat1"
    assert page.evaluate("window.__scaredRequestOptions.animateEntry") is True
    assert page.evaluate("window.__sleepTierDemotion") == {
        "source": "cat-contextmenu-demotion",
        "reason": "cat1-scared-action",
        "from": "cat2",
    }

    page.evaluate(
        """
        prefix => {
            const bubble = document.querySelector(`#${prefix}-btn-return .neko-idle-thought-bubble`);
            bubble.dispatchEvent(new MouseEvent('contextmenu', {
                button: 2,
                bubbles: true,
                cancelable: true,
            }));
        }
        """,
        prefix,
    )
    assert page.evaluate("window.__cat1VisualActionRequests") == ["stroke", "cosplay1", "scared"]

    return_button.click()
    assert page.evaluate("window.__cat1VisualActionRequests") == ["stroke", "cosplay1", "scared"]
    assert page.evaluate("window.__avatarReturnClicks") == 1


def test_animated_webp_appearance_assets_decode_and_advance_in_chromium(page: Page):
    assets = tuple(sorted(APPEARANCE_ROOT.rglob("*.webp")))
    assert len(assets) == 29

    def serve_asset(route):
        relative = route.request.url.split("/appearance/", 1)[-1].split("?", 1)[0]
        asset = APPEARANCE_ROOT / relative
        assert asset.is_file(), asset
        route.fulfill(body=asset.read_bytes(), content_type="image/webp")

    page.route("http://neko.test/appearance/**", serve_asset)
    page.set_content(
        "<main style='background:#808080;padding:8px;display:inline-block'>"
        "<img id='asset' alt='' style='display:block;object-fit:contain'>"
        "</main>"
    )
    image = page.locator("#asset")
    for asset in assets:
        relative = asset.relative_to(APPEARANCE_ROOT).as_posix()
        width, height = (214, 122) if asset.name in {
            "cat-idle-cat1-mouse.webp",
            "cat-idle-cat-play-1.webp",
        } else ((122, 168) if asset.name == "cat-idle-cat1-lion_dance.webp" else (122, 122))
        page.evaluate(
            """
            async ({ filename, width, height }) => {
                const image = document.getElementById('asset');
                image.style.width = `${width}px`;
                image.style.height = `${height}px`;
                image.src = `http://neko.test/appearance/${filename}?run=${Date.now()}`;
                await image.decode();
            }
            """,
            {"filename": relative, "width": width, "height": height},
        )
        first_hash = hashlib.sha256(image.screenshot()).digest()
        advanced = False
        for _ in range(8):
            page.wait_for_timeout(100)
            if hashlib.sha256(image.screenshot()).digest() != first_hash:
                advanced = True
                break
        assert advanced, f"animated WebP did not advance in Chromium: {relative}"
