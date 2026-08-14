from pathlib import Path

import pytest
from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parents[2]
APP_SCREEN = ROOT / "static" / "app" / "app-screen.js"


def _install_screen_source_harness(page: Page) -> None:
    page.set_content(
        '<div id="live2d-popup-screen" '
        'style="display:flex;opacity:1"></div>'
    )
    page.evaluate(
        """() => {
            window.appState = { selectedScreenSourceId: null };
            window.appConst = {};
            window.appUtils = { isMobile: () => false };
            window.safeT = (_key, fallback) => fallback;
            window.t = (key, options = {}) => {
                if (key === 'app.screenSource.loading') return 'Loading...';
                if (key === 'app.screenSource.screenLabel') {
                    return `Screen ${options.index}`;
                }
                return key;
            };
            window.showStatusToast = () => {};
            window.__captureCalls = [];
            window.__thumbnailResolve = null;
            const thumbnailPromise = new Promise((resolve) => {
                window.__thumbnailResolve = resolve;
            });
            const metadataSources = [
                { id: 'screen:1', name: 'Entire Screen', display_id: '1', thumbnail: null },
                { id: 'window:2', name: 'Editor', display_id: '', thumbnail: null },
            ];
            window.__desktopProvider = {
                getSources(options) {
                    window.__captureCalls.push(options);
                    if (options.thumbnailSize.width === 0) {
                        return Promise.resolve(metadataSources);
                    }
                    return thumbnailPromise;
                },
                setSelectedSource() { return Promise.resolve(); },
            };
            window.getDesktopCaptureProvider = () => window.__desktopProvider;
        }"""
    )
    page.add_script_tag(path=str(APP_SCREEN))


@pytest.mark.frontend
def test_screen_source_names_render_before_cached_thumbnails(page: Page) -> None:
    _install_screen_source_harness(page)

    rendered = page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    )
    assert rendered is True
    page.wait_for_function("window.__captureCalls.length === 2")

    before_thumbnails = page.evaluate(
        """() => ({
            labels: Array.from(document.querySelectorAll('.screen-source-option span'))
                .map((node) => node.textContent),
            loadingCount: document.querySelectorAll(
                '.screen-source-thumbnail-loading'
            ).length,
            imageCount: document.querySelectorAll(
                '.screen-source-thumbnail-ready img'
            ).length,
            calls: window.__captureCalls,
        })"""
    )
    assert before_thumbnails == {
        "labels": ["Screen 1", "Editor"],
        "loadingCount": 2,
        "imageCount": 0,
        "calls": [
            {
                "types": ["window", "screen"],
                "thumbnailSize": {"width": 0, "height": 0},
            },
            {
                "types": ["window", "screen"],
                "thumbnailSize": {"width": 160, "height": 100},
                "thumbnailCache": True,
            },
        ],
    }

    page.evaluate(
        """() => window.__thumbnailResolve([
            {
                id: 'screen:1',
                name: 'Entire Screen',
                display_id: '1',
                thumbnail: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
            },
            {
                id: 'window:2',
                name: 'Editor',
                display_id: '',
                thumbnail: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
            },
            {
                id: 'window:stale',
                name: 'Closed Window',
                display_id: '',
                thumbnail: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
            },
        ])"""
    )
    page.wait_for_function(
        "document.querySelectorAll('.screen-source-thumbnail-ready img').length === 2"
    )

    after_thumbnails = page.evaluate(
        """() => ({
            optionCount: document.querySelectorAll('.screen-source-option').length,
            loadingCount: document.querySelectorAll(
                '.screen-source-thumbnail-loading'
            ).length,
            imageCount: document.querySelectorAll(
                '.screen-source-thumbnail-ready img'
            ).length,
        })"""
    )
    assert after_thumbnails == {
        "optionCount": 2,
        "loadingCount": 0,
        "imageCount": 2,
    }
