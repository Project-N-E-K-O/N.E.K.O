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
                    if (typeof options.prepareCat1 === 'function' && options.prepareCat1() !== true) {
                        return false;
                    }
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


@pytest.mark.parametrize(
    ("tier", "roll", "finish", "action", "catnip_roll"),
    [
        ("cat1", 0.99, "pop-end", "mouse", None),
        ("cat1", 0.5, "early-end", "eat", None),
        ("cat1", 0.5, "pop-end", "eat", None),
        ("cat1", 0.1, "pop-end", "catnip1", 0.499999),
        ("cat1", 0.1, "early-end", "catnip2", 0.5),
        ("cat1", 0.1, "blocked", None, 0.5),
        ("cat1", 0.1, "cancelled", None, 0.5),
        ("cat1", 0.5, "blocked", None, None),  # Compact chat drag takes priority at pop end.
        ("cat1", 0.99, "cancelled", None, None),
        ("cat1", 0.5, "cleared", None, None),
        ("cat2", 0.99, "pop-end", None, None),  # Toy mouse in a nap bubble.
        ("cat2", 0.1, "early-end", None, None),  # Nap ZZZ must play the entire pop.
        ("cat3", 0.5, "pop-end", None, None),  # ZZZ hides the selected fish cookie.
        ("cat3", 0.99, "pop-end", None, None),  # Toy mouse in a deep sleep bubble.
    ],
)
def test_thought_bubble_pop_shares_audio_and_animation_deadline(page: Page, tier, roll, finish, action, catnip_roll):
    prefix, manager_name, backend_script = BACKENDS[0]
    if catnip_roll is not None:
        def serve_static(route):
            relative = route.request.url.split("http://neko.test/", 1)[1].split("?", 1)[0]
            asset = PROJECT_ROOT / relative
            if asset.is_file():
                route.fulfill(path=str(asset))
            else:
                route.abort()

        page.route("http://neko.test/static/**", serve_static)
        page.route("http://neko.test/", lambda route: route.fulfill(
            content_type="text/html", body="<main></main>",
        ))
        page.goto("http://neko.test/")
        page.add_style_tag(url="http://neko.test/static/css/index.css")
    else:
        page.set_content("<main></main>")
        page.add_style_tag(path=str(PROJECT_ROOT / "static/css/index.css"))
    page.add_script_tag(content=f"class {manager_name} {{}}; window.{manager_name} = {manager_name};")
    for part_name in AVATAR_UI_BUTTON_PART_NAMES:
        page.add_script_tag(path=str(AVATAR_UI_BUTTONS_DIR / part_name))
    page.add_script_tag(path=str(backend_script))
    page.clock.install()

    page.evaluate(
        """
        ({ managerName, prefix, tier, roll }) => {
            const Manager = window[managerName];
            window.nekoAutoGoodbye = { getState: () => ({ visualTier: tier }) };
            const container = new Manager().createReturnButton();
            Object.assign(container.style, {
                left: '120px', top: '160px', width: '96px', height: '96px', display: 'block',
            });
            const button = document.getElementById(`${prefix}-btn-return`);
            container.setAttribute('data-neko-idle-tier', tier);
            button.setAttribute('data-neko-idle-tier', tier);

            window.__bubbleCheck = { pops: 0, returns: 0, results: [] };
            window.addEventListener('neko:thought-bubble-pop', () => window.__bubbleCheck.pops++);
            window.addEventListener(`${prefix}-return-click`, () => window.__bubbleCheck.returns++);
            window.addEventListener('neko:cat-mind:action-result', event => {
                window.__bubbleCheck.results.push([event.detail.actionId, event.detail.result]);
            });
            window.Audio = class extends EventTarget {
                constructor(src) {
                    super(); this.src = src;
                    this.duration = src.includes('cat1-voice-eat.mp3') ? 0.936 : 7.18;
                    this.currentTime = 0;
                    this.ended = false; this.paused = false;
                }
                play() { return Promise.resolve(); }
                pause() { this.paused = true; }
            };
            Math.random = () => roll;
            if (tier === 'cat1') {
                _nekoIdleCat1AmbientSoundState.active = true;
                if (!_playNekoIdleCat1AmbientSound(_nekoIdleCat1AmbientSoundState.token)) {
                    throw new Error('Could not start the ambient response');
                }
                window.__bubbleAudio = _nekoIdleCat1AmbientSoundState.audio;
            } else {
                _nekoIdleSleepSoundState.tier = tier;
                if (!_playNekoIdleSleepSound(tier, _nekoIdleSleepSoundState.token)) {
                    throw new Error('Could not start the sleep response');
                }
                window.__bubbleAudio = _nekoIdleSleepSoundState.audio;
            }
            window.__bubbleCheck.initialVolume = window.__bubbleAudio.volume;
        }
        """,
        {"managerName": manager_name, "prefix": prefix, "tier": tier, "roll": roll},
    )

    button = page.locator(f"#{prefix}-btn-return")
    bubble = button.locator(".neko-idle-thought-bubble")
    if catnip_roll is not None:
        assert "catnip-pouch.png" in bubble.locator(".neko-idle-thought-bubble-item").get_attribute("src")
        page.evaluate("roll => { Math.random = () => roll; }", catnip_roll)
    bubble.click()
    assert button.evaluate("button => button.classList.contains('is-thought-bubble-popping')")
    expect(bubble).to_be_visible()
    bubble.dispatch_event("click")
    assert page.evaluate("window.__bubbleCheck.pops") == 1
    assert page.evaluate("window.__bubbleCheck.returns") == 0
    assert button.get_attribute("data-neko-cat1-visual-action") is None
    assert not button.evaluate("button => button.__nekoIdleCat1EatActionState?.active")
    assert page.evaluate("window.__bubbleCheck.results") == []

    page.clock.run_for(230)
    assert button.evaluate("button => button.classList.contains('is-thought-bubble-active')")
    assert page.evaluate("0 < window.__bubbleAudio.volume && window.__bubbleAudio.volume < window.__bubbleCheck.initialVolume")
    assert not page.evaluate("window.__bubbleAudio.paused")
    if finish == "early-end":
        page.evaluate("window.__bubbleAudio.ended = true; window.__bubbleAudio.dispatchEvent(new Event('ended'))")
        assert button.evaluate("button => button.classList.contains('is-thought-bubble-active')")
        assert button.get_attribute("data-neko-cat1-visual-action") is None
        assert not button.evaluate("button => button.__nekoIdleCat1EatActionState?.active")
    elif finish == "blocked":
        page.evaluate("_nekoIdleCompactSurfaceDragging = true")
    elif finish == "cancelled":
        page.evaluate("_stopNekoIdleCat1AmbientSound({ reason: 'tier-change' })")
    elif finish == "cleared":
        button.evaluate("button => _clearNekoIdleThoughtBubble(button)")

    page.clock.run_for(320)
    assert not button.evaluate("button => button.classList.contains('is-thought-bubble-active')")
    if finish in {"pop-end", "blocked"}:
        assert page.evaluate("window.__bubbleAudio.paused && window.__bubbleAudio.volume === 0")
    elif finish == "cleared":
        assert page.evaluate("!window.__bubbleAudio.paused && window.__bubbleAudio.volume === window.__bubbleCheck.initialVolume")

    if action in {"mouse", "catnip1", "catnip2"}:
        expect(button).to_have_attribute("data-neko-cat1-visual-action", action)
    else:
        assert button.get_attribute("data-neko-cat1-visual-action") is None
    if action in {"catnip1", "catnip2"}:
        page.wait_for_function(
            "(id) => Boolean(document.getElementById(id).__nekoIdleCat1VisualActionState?.timer)",
            arg=f"{prefix}-btn-return",
            timeout=3000,
        )
    if action == "eat":
        assert button.evaluate("button => button.__nekoIdleCat1EatActionState.assetUrl.split('?')[0]").endswith(
            "/cat-idle-cat1-eat-2.webp"
        )
        assert button.evaluate("button => button.__nekoIdleCat1EatActionState.audio.src.split('?')[0]").endswith(
            "/cat1-voice-eat.mp3"
        )
    else:
        assert not button.evaluate("button => button.__nekoIdleCat1EatActionState?.active")

    if action == "eat":
        page.clock.run_for(5200)
        assert not button.evaluate("button => button.__nekoIdleCat1EatActionState.active")
    action_id = {"cat1": "cat1_social_ping", "cat2": "cat2_nap_feedback", "cat3": "cat3_sleep_feedback"}[tier]
    result = {"early-end": "done", "cancelled": "interrupted"}.get(finish, "cancelled")
    results = [] if finish == "cleared" else [[action_id, result]]
    assert page.evaluate("window.__bubbleCheck.results") == results


def test_cat1_enter_is_available_to_keyboard_controls(page: Page):
    prefix, manager_name, backend_script = BACKENDS[0]
    page.route("http://neko.test/", lambda route: route.fulfill(
        content_type="text/html", body='<main><form><input aria-label="Chat"></form></main>',
    ))
    page.clock.install()
    page.goto("http://neko.test/")
    page.add_script_tag(content=f"class {manager_name} {{}}; window.{manager_name} = {manager_name};")
    for part_name in AVATAR_UI_BUTTON_PART_NAMES:
        page.add_script_tag(path=str(AVATAR_UI_BUTTONS_DIR / part_name))
    page.add_script_tag(path=str(backend_script))
    page.evaluate(
        """
        ({ managerName, prefix }) => {
            const Manager = window[managerName];
            const container = new Manager().createReturnButton();
            const button = document.getElementById(`${prefix}-btn-return`);
            container.style.display = 'block';
            window.nekoAutoGoodbye = { getState: () => ({ visualTier: 'cat1' }) };
            container.setAttribute('data-neko-idle-tier', 'cat1');
            button.setAttribute('data-neko-idle-tier', 'cat1');
            window.popCount = 0;
            window.submitCount = 0;
            window.addEventListener('neko:thought-bubble-pop', () => window.popCount++);
            document.querySelector('form').addEventListener('submit', event => {
                event.preventDefault();
                window.submitCount++;
            });
            setNekoIdleCatAudioEnabled(false);
        }
        """,
        {"managerName": manager_name, "prefix": prefix},
    )
    button = page.locator(f"#{prefix}-btn-return")
    page.keyboard.press("Enter")
    assert button.get_attribute("data-neko-cat1-visual-action") is None

    page.get_by_role("textbox", name="Chat").press("Enter")
    assert page.evaluate("window.submitCount") == 1
    assert button.get_attribute("data-neko-cat1-visual-action") is None

    page.evaluate("_showNekoIdleThoughtBubbleForSound('cat1')")
    bubble = button.locator(".neko-idle-thought-bubble")
    bubble.press("Enter")
    assert page.evaluate("window.popCount") == 1
    assert button.get_attribute("data-neko-cat1-visual-action") is None


def test_cat1_mouse_hover_selects_three_animations_and_restores_after_their_cycle(page: Page):
    prefix, manager_name, backend_script = BACKENDS[0]
    host = "http://neko.test/"

    def serve_static(route):
        relative = route.request.url.removeprefix(host).split("?", 1)[0]
        asset = PROJECT_ROOT / relative
        route.fulfill(path=str(asset)) if asset.is_file() else route.abort()

    page.route(host, lambda route: route.fulfill(content_type="text/html", body="<main></main>"))
    page.route(f"{host}static/**", serve_static)
    page.goto(host)
    page.add_style_tag(url=f"{host}static/css/index.css")
    page.add_script_tag(content=f"class {manager_name} {{}}; window.{manager_name} = {manager_name};")
    for part_name in AVATAR_UI_BUTTON_PART_NAMES:
        page.add_script_tag(path=str(AVATAR_UI_BUTTONS_DIR / part_name))
    page.add_script_tag(path=str(backend_script))
    page.clock.install()
    page.evaluate(
        """managerName => {
            window.nekoAutoGoodbye = { getState: () => ({ visualTier: 'cat1' }) };
            const Manager = window[managerName];
            const container = new Manager().createReturnButton();
            Object.assign(container.style, {display: 'block', left: '120px', top: '120px', width: '96px', height: '96px'});
        }""",
        manager_name,
    )
    page.clock.pause_at(page.evaluate("Date.now() + 100"))
    button = page.locator(f"#{prefix}-btn-return")
    art = button.locator(".neko-idle-return-art")
    expected_idle = art.get_attribute("src")
    rect = button.bounding_box()
    assert rect is not None
    for roll, filename, duration in (
        (0, "cat-idle-cat1-click.webp", 2430),
        (1 / 3, "cat-idle-cat1-wag-1.webp", 5040),
        (2 / 3, "cat-idle-cat1-wag-2.webp", 5040),
    ):
        page.evaluate("roll => { Math.random = () => roll; }", roll)
        page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
        src = art.get_attribute("src")
        assert src.split("?", 1)[0].endswith(filename)
        art.evaluate("img => img.decode()")
        assert button.evaluate("button => _nekoIdleCat1QuestionMarkKeyboardState.button === button")
        assert button.get_attribute("data-neko-cat1-visual-action") is None
        page.mouse.move(0, 0)
        elapsed = 0
        if filename == "cat-idle-cat1-wag-1.webp":
            started_at = art.evaluate("img => img.__nekoIdleHoverStartedAt")
            elapsed = 3000
            page.clock.fast_forward(elapsed)
            page.evaluate("() => { Math.random = () => 0.99; }")
            page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
            assert art.get_attribute("src") == src
            art.evaluate("img => _playNekoIdleHoverArt(img, 'cat1')")
            assert art.get_attribute("src") == src
            assert art.evaluate("img => img.__nekoIdleHoverStartedAt") == started_at
            page.mouse.move(0, 0)
        page.clock.fast_forward(duration - elapsed - 1)
        assert art.get_attribute("src") == src
        page.clock.fast_forward(1)
        assert art.get_attribute("src") == expected_idle

    for roll, duration in ((1 / 3, 5040), (0, 2430)):
        page.evaluate("roll => { Math.random = () => roll; }", roll)
        page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
        page.clock.fast_forward(duration + 250)
        button.evaluate("""button => {
            _getNekoIdleCat1Journey(button).targetKind = _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
            _playNekoIdleCat1SoundReaction();
        }""")
        src = art.get_attribute("src")
        assert src.split("?", 1)[0].endswith("cat-idle-cat1-click.webp")
        assert art.evaluate("img => img.__nekoIdleHoverStartedAt === Date.now()")
        page.clock.fast_forward(2429)
        assert art.get_attribute("src") == src
        page.clock.fast_forward(1)
        assert art.get_attribute("src") == expected_idle
        page.mouse.move(0, 0)


def test_animated_webp_appearance_assets_decode_and_advance_in_chromium(page: Page):
    assets = tuple(sorted(APPEARANCE_ROOT.rglob("*.webp")))
    assert len(assets) == 43

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
            "cat-idle-cat1-banana.webp",
            "cat-idle-cat1-mouse.webp",
            "cat-idle-cat1-ninjutsu.webp",
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
