import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from PIL import Image


playwright_sync_api = pytest.importorskip("playwright.sync_api")
Page = playwright_sync_api.Page

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = PROJECT_ROOT / "tmp" / "phase11_visual_regression"
STATIC_ROOT = PROJECT_ROOT / "static"


def _has_playwright_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False

    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _has_playwright_browser(),
    reason="requires Playwright browser binaries",
)


def _install_static_routes(page: Page) -> None:
    def serve_static(route):
        parsed = urlparse(route.request.url)
        path = unquote(parsed.path)
        if not path.startswith("/static/"):
            route.abort()
            return
        local_path = PROJECT_ROOT / path.lstrip("/")
        try:
            local_path.relative_to(STATIC_ROOT)
        except ValueError:
            route.abort()
            return
        if not local_path.is_file():
            route.abort()
            return
        content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        route.fulfill(path=str(local_path), content_type=content_type)

    page.route("**/static/**", serve_static)


def _bootstrap_visual_harness(page: Page, *, pc_overlay: bool = False) -> None:
    _install_static_routes(page)
    page.route(
        "**/tutorial-phase11-visual",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Phase 11 Visual Harness</title>
  <style>
    html, body { margin: 0; min-height: 100%; background: #f7fbff; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .phase-shell { min-height: 100vh; display: grid; grid-template-columns: 260px 1fr; gap: 28px; padding: 32px; box-sizing: border-box; }
    .phase-sidebar { border-radius: 16px; background: #18243a; color: #eff7ff; padding: 18px; }
    .phase-main { position: relative; border-radius: 18px; background: #ffffff; box-shadow: 0 20px 50px rgba(24, 36, 58, 0.12); padding: 32px; }
    .phase-target { position: absolute; display: grid; place-items: center; border: 0; color: #0f172a; background: #dff7ef; box-shadow: inset 0 0 0 1px rgba(20, 184, 166, 0.25); }
    #phase-primary { left: 92px; top: 104px; width: 168px; height: 64px; border-radius: 18px; }
    #phase-secondary { right: 84px; top: 112px; width: 86px; height: 86px; border-radius: 50%; background: #e8edff; }
    #phase-persistent { left: 84px; bottom: 88px; width: min(520px, 54vw); height: 58px; border-radius: 999px; background: #fff5dd; }
    #phase-extra-a { right: 118px; bottom: 156px; width: 128px; height: 54px; border-radius: 14px; background: #fce8ee; }
    #phase-extra-b { right: 304px; bottom: 132px; width: 104px; height: 104px; border-radius: 28px; background: #e1f0ff; }
    @media (max-width: 700px) {
      .phase-shell { display: block; padding: 18px; }
      .phase-sidebar { min-height: 72px; margin-bottom: 16px; }
      .phase-main { min-height: 620px; padding: 20px; }
      #phase-primary { left: 32px; top: 132px; width: 144px; height: 58px; }
      #phase-secondary { right: 30px; top: 118px; width: 72px; height: 72px; }
      #phase-persistent { left: 28px; right: 28px; bottom: 86px; width: auto; }
      #phase-extra-a { right: 34px; bottom: 182px; width: 112px; }
      #phase-extra-b { left: 34px; bottom: 174px; width: 78px; height: 78px; }
    }
  </style>
</head>
<body>
  <div class="phase-shell">
    <aside class="phase-sidebar">Guide controls</aside>
    <main class="phase-main">
      <button id="phase-primary" class="phase-target">Primary</button>
      <button id="phase-secondary" class="phase-target" data-yui-spotlight-geometry="circle">Voice</button>
      <div id="phase-persistent" class="phase-target">Compact chat</div>
      <div id="phase-extra-a" class="phase-target">Tool</div>
      <div id="phase-extra-b" class="phase-target">Plugin</div>
    </main>
  </div>
</body>
</html>
            """,
        ),
    )
    page.goto("http://neko.test/tutorial-phase11-visual")
    page.add_style_tag(path=str(PROJECT_ROOT / "static" / "css" / "yui-guide.css"))
    if pc_overlay:
        page.evaluate(
            """
            () => {
                window.__pcOverlayBegins = [];
                window.__pcOverlayUpdates = [];
                window.__pcOverlayClears = [];
                window.nekoTutorialOverlay = {
                    capabilities: { petalTransition: true },
                    getWindowMetricsSync: () => ({
                        bounds: { x: 100, y: 50, width: window.innerWidth, height: window.innerHeight },
                        contentBounds: { x: 100, y: 50, width: window.innerWidth, height: window.innerHeight },
                        zoomFactor: 1,
                    }),
                    begin: (payload) => {
                        window.__pcOverlayBegins.push(payload);
                        return Promise.resolve({ ok: true });
                    },
                    update: (payload) => {
                        window.__pcOverlayUpdates.push(payload);
                        return Promise.resolve({ ok: true });
                    },
                    clear: (payload) => {
                        window.__pcOverlayClears.push(payload || {});
                        return Promise.resolve({ ok: true });
                    },
                };
            }
            """
        )
    page.add_script_tag(path=str(PROJECT_ROOT / "static" / "tutorial/visual/overlay-renderer.js"))
    page.add_script_tag(path=str(PROJECT_ROOT / "static" / "tutorial/avatar/yui-standin.js"))
    page.add_script_tag(path=str(PROJECT_ROOT / "static" / "tutorial/yui-guide/overlay.js"))


def _snapshot_has_visual_detail(path: Path) -> None:
    image = Image.open(path).convert("RGB")
    width, height = image.size
    assert width > 0 and height > 0
    sample_step = max(1, min(width, height) // 80)
    colors = {
        image.getpixel((x, y))
        for y in range(0, height, sample_step)
        for x in range(0, width, sample_step)
    }
    assert len(colors) > 16


def _exercise_dom_visual_state(page: Page, screenshot_name: str) -> dict:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    result = page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            const primary = document.getElementById('phase-primary');
            const secondary = document.getElementById('phase-secondary');
            const persistent = document.getElementById('phase-persistent');
            const extraA = document.getElementById('phase-extra-a');
            const extraB = document.getElementById('phase-extra-b');
            overlay.setTakingOver(true);
            overlay.setPersistentSpotlight(persistent);
            overlay.activateSpotlight(primary);
            overlay.activateSecondarySpotlight(secondary);
            overlay.setExtraSpotlights([extraA, extraB]);
            overlay.showBubble('Phase 11 visual regression text wraps without covering targets.', {
                title: 'Yui',
                meta: 'Phase 11',
                emotion: 'happy',
                anchorRect: primary.getBoundingClientRect(),
                preferredPlacement: 'bottom',
            });
            overlay.showPluginPreview(['Search', 'Vision', 'Memory'], { title: 'Tools' });
            await new Promise((resolve) => requestAnimationFrame(resolve));
            await new Promise((resolve) => setTimeout(resolve, 240));

            const rectOf = (selector) => {
                const element = document.querySelector(selector);
                if (!element) {
                    return null;
                }
                const rect = element.getBoundingClientRect();
                return {
                    left: Math.round(rect.left),
                    top: Math.round(rect.top),
                    right: Math.round(rect.right),
                    bottom: Math.round(rect.bottom),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    hidden: element.hidden,
                    visible: element.classList.contains('is-visible'),
                };
            };
            const frames = Array.from(document.querySelectorAll('.yui-guide-spotlight-frame.is-visible'))
                .map((element) => {
                    const rect = element.getBoundingClientRect();
                    return {
                        className: element.className,
                        left: Math.round(rect.left),
                        top: Math.round(rect.top),
                        right: Math.round(rect.right),
                        bottom: Math.round(rect.bottom),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                    };
                });
            const bubble = rectOf('.yui-guide-bubble');
            const preview = rectOf('.yui-guide-preview');
            const viewport = { width: window.innerWidth, height: window.innerHeight };
            return {
                frameCount: frames.length,
                frames,
                bubble,
                preview,
                viewport,
                takingOver: document.body.classList.contains('yui-taking-over'),
            };
        }
        """
    )
    path = ARTIFACT_DIR / screenshot_name
    page.screenshot(path=str(path), full_page=True)
    _snapshot_has_visual_detail(path)
    return result


@pytest.mark.frontend
def test_phase11_dom_fallback_visual_snapshot_desktop(mock_page: Page):
    mock_page.set_viewport_size({"width": 1280, "height": 820})
    _bootstrap_visual_harness(mock_page)

    result = _exercise_dom_visual_state(mock_page, "dom-fallback-desktop.png")

    assert result["frameCount"] == 5
    assert result["takingOver"] is True
    assert result["bubble"]["visible"] is True
    assert result["preview"]["visible"] is True
    for rect in [result["bubble"], result["preview"], *result["frames"]]:
        assert rect["width"] > 0
        assert rect["height"] > 0
        assert rect["left"] < result["viewport"]["width"]
        assert rect["top"] < result["viewport"]["height"]
        assert rect["right"] > 0
        assert rect["bottom"] > 0
    assert result["bubble"]["bottom"] <= result["viewport"]["height"]
    assert result["preview"]["right"] <= result["viewport"]["width"]


@pytest.mark.frontend
def test_phase11_dom_fallback_visual_snapshot_mobile(mock_page: Page):
    mock_page.set_viewport_size({"width": 390, "height": 844})
    _bootstrap_visual_harness(mock_page)

    result = _exercise_dom_visual_state(mock_page, "dom-fallback-mobile.png")

    assert result["frameCount"] == 5
    assert result["bubble"]["visible"] is True
    assert result["preview"]["visible"] is True
    assert result["bubble"]["left"] >= 0
    assert result["bubble"]["right"] <= result["viewport"]["width"]
    assert result["bubble"]["bottom"] <= result["viewport"]["height"]
    assert result["preview"]["left"] >= 0
    assert result["preview"]["right"] <= result["viewport"]["width"]


@pytest.mark.frontend
def test_phase11_pc_overlay_payload_covers_cursor_visuals_and_cleanup(mock_page: Page):
    mock_page.set_viewport_size({"width": 1180, "height": 760})
    _bootstrap_visual_harness(mock_page, pc_overlay=True)

    result = mock_page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            const primary = document.getElementById('phase-primary');
            const secondary = document.getElementById('phase-secondary');
            const persistent = document.getElementById('phase-persistent');
            overlay.setPersistentSpotlight(persistent);
            overlay.activateSpotlight(primary);
            overlay.activateSecondarySpotlight(secondary);
            overlay.showCursorAt(220, 180);
            await overlay.moveCursorTo(360, 224, { durationMs: 180 });
            overlay.clickCursor(360);
            overlay.showAvatarStandIn({
                resource: 'peek-left-border',
                position: 'left-bottom',
                durationMs: 400,
            });
            overlay.playPetalTransition({ x: 320, y: 240 }, { durationMs: 520, finalOpacity: 0.7 });
            await new Promise((resolve) => setTimeout(resolve, 40));
            const beforeDestroy = window.__pcOverlayUpdates.map((entry) => entry.payload);
            const domCursor = !!document.querySelector('#yui-guide-overlay .yui-guide-cursor-shell');
            overlay.destroy();
            await Promise.resolve();
            return {
                beforeDestroy,
                begins: window.__pcOverlayBegins.length,
                clearCount: window.__pcOverlayClears.length,
                domCursor,
                rootExists: !!document.getElementById('yui-guide-overlay'),
                takingOver: document.body.classList.contains('yui-taking-over'),
            };
        }
        """
    )

    payloads = result["beforeDestroy"]
    assert result["begins"] >= 1
    assert result["domCursor"] is False
    assert result["rootExists"] is False
    assert result["takingOver"] is False
    assert result["clearCount"] >= 1
    assert any(payload.get("cursor", {}).get("visible") is True for payload in payloads)
    assert any(payload.get("cursor", {}).get("effect") == "click" for payload in payloads)
    assert any(len(payload.get("spotlights", [])) == 3 for payload in payloads)
    assert any(payload.get("avatarStandIn", {}).get("resource") == "peek-left-border" for payload in payloads)
    assert any(payload.get("petal", {}).get("durationMs") == 520 for payload in payloads)


@pytest.mark.frontend
def test_phase11_spotlight_suppression_preserves_dom_fallback_until_destroy(mock_page: Page):
    _bootstrap_visual_harness(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            const primary = document.getElementById('phase-primary');
            overlay.activateSpotlight(primary);
            await new Promise((resolve) => requestAnimationFrame(resolve));
            const visibleBeforeSuppression = document.querySelectorAll('.yui-guide-spotlight-frame.is-visible').length;
            overlay.setSpotlightSuppressed(true);
            overlay.activateSpotlight(primary);
            await new Promise((resolve) => requestAnimationFrame(resolve));
            const visibleAfterSuppression = document.querySelectorAll('.yui-guide-spotlight-frame.is-visible').length;
            overlay.showBubble('Cleanup check', { anchorRect: primary.getBoundingClientRect() });
            overlay.destroy();
            return {
                visibleBeforeSuppression,
                visibleAfterSuppression,
                rootExists: !!document.getElementById('yui-guide-overlay'),
                takingOver: document.body.classList.contains('yui-taking-over'),
                highlighted: document.querySelectorAll('.yui-guide-highlighted').length,
            };
        }
        """
    )

    assert result == {
        "visibleBeforeSuppression": 1,
        "visibleAfterSuppression": 1,
        "rootExists": False,
        "takingOver": False,
        "highlighted": 0,
    }
