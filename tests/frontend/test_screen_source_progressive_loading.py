from pathlib import Path

import pytest
from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parents[2]
APP_SCREEN = ROOT / "static" / "app" / "app-screen.js"
DESKTOP_CAPTURE_PROVIDER = ROOT / "static" / "app" / "desktop-capture-provider.js"


def _install_screen_source_harness(
    page: Page,
    *,
    thumbnail_timeout_ms: int = 15_000,
    source_enumeration_may_prompt: bool = False,
    initial_storage: dict[str, str] | None = None,
) -> None:
    page.set_content(
        '<div id="live2d-popup-screen" '
        'style="display:flex;opacity:1"></div>'
    )
    page.evaluate(
        """(options) => {
            const storedValues = new Map(Object.entries(options.initialStorage));
            Object.defineProperty(window, 'localStorage', {
                configurable: true,
                value: {
                    getItem(key) {
                        return storedValues.has(key) ? storedValues.get(key) : null;
                    },
                    setItem(key, value) {
                        storedValues.set(key, String(value));
                    },
                    removeItem(key) {
                        storedValues.delete(key);
                    },
                },
            });
            window.__storedValues = storedValues;
            window.appState = { selectedScreenSourceId: null };
            window.appConst = {
                SCREEN_SOURCE_THUMBNAIL_TIMEOUT: options.thumbnailTimeoutMs,
            };
            window.appUtils = { isMobile: () => false };
            window.safeT = (_key, fallback) => fallback;
            window.t = (key, options = {}) => {
                if (key === 'app.screenSource.loading') return 'Loading...';
                if (key === 'app.screenSource.screenLabel') {
                    return `Screen ${options.index}`;
                }
                if (key === 'app.screenSource.titleFilterPlaceholder') {
                    return 'Filter window titles';
                }
                if (key === 'app.screenSource.titleFilterAriaLabel') {
                    return 'Filter windows by title';
                }
                if (key === 'app.screenSource.noWindowMatches') {
                    return 'No matching windows';
                }
                return key;
            };
            window.showStatusToast = () => {};
            window.__captureCalls = [];
            window.__metadataThumbnailReads = 0;
            window.__thumbnailResolve = null;
            const thumbnailPromise = new Promise((resolve) => {
                window.__thumbnailResolve = resolve;
            });
            const emptyMetadataThumbnail = {
                isEmpty() { return true; },
                toDataURL() {
                    window.__metadataThumbnailReads += 1;
                    return '';
                },
            };
            window.__metadataSources = [
                { id: 'screen:1', name: 'Entire Screen', display_id: '1', thumbnail: emptyMetadataThumbnail },
                { id: 'window:2', name: 'Editor', display_id: '', thumbnail: emptyMetadataThumbnail },
            ];
            window.__selectedSourceCalls = [];
            window.__desktopProvider = {
                sourceEnumerationMayPrompt: options.sourceEnumerationMayPrompt,
                getSources(options) {
                    window.__captureCalls.push(options);
                    if (options.thumbnailSize.width === 0) {
                        return Promise.resolve(window.__metadataSources);
                    }
                    return thumbnailPromise;
                },
                setSelectedSource(sourceId) {
                    window.__selectedSourceCalls.push(sourceId);
                    return Promise.resolve();
                },
            };
            window.electronDesktopCapturer = window.__desktopProvider;
        }""",
        {
            "thumbnailTimeoutMs": thumbnail_timeout_ms,
            "sourceEnumerationMayPrompt": source_enumeration_may_prompt,
            "initialStorage": initial_storage or {},
        },
    )
    page.add_script_tag(path=str(DESKTOP_CAPTURE_PROVIDER))
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
            metadataThumbnailReads: window.__metadataThumbnailReads,
            calls: window.__captureCalls,
        })"""
    )
    assert before_thumbnails == {
        "labels": ["Screen 1", "Editor"],
        "loadingCount": 2,
        "imageCount": 0,
        "metadataThumbnailReads": 0,
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


@pytest.mark.frontend
def test_screen_source_hung_thumbnail_request_falls_back_after_timeout(
    page: Page,
) -> None:
    _install_screen_source_harness(page, thumbnail_timeout_ms=25)

    rendered = page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    )
    assert rendered is True
    page.wait_for_function(
        "document.querySelectorAll('.screen-source-thumbnail-fallback').length === 2"
    )

    state = page.evaluate(
        """() => ({
            calls: window.__captureCalls,
            loadingCount: document.querySelectorAll(
                '.screen-source-thumbnail-loading'
            ).length,
            fallbackCount: document.querySelectorAll(
                '.screen-source-thumbnail-fallback'
            ).length,
        })"""
    )
    assert state == {
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
        "loadingCount": 0,
        "fallbackCount": 2,
    }


@pytest.mark.frontend
def test_window_title_filter_is_local_and_keeps_screens_visible(page: Page) -> None:
    _install_screen_source_harness(page, source_enumeration_may_prompt=True)
    page.evaluate(
        """() => {
            window.__metadataSources.push({
                id: 'window:3',
                name: 'Browser Preview',
                display_id: '',
                thumbnail: null,
            });
        }"""
    )

    assert page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    ) is True

    result = page.evaluate(
        """() => {
            const input = document.querySelector('.screen-source-title-filter');
            input.value = '  EDIT  ';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            const filtered = Object.fromEntries(
                Array.from(document.querySelectorAll('.screen-source-option'))
                    .map((option) => [option.dataset.sourceName, option.hidden])
            );
            const editorDisplay = getComputedStyle(document.querySelector(
                '.screen-source-option[data-source-id="window:2"]'
            )).display;
            const browserDisplay = getComputedStyle(document.querySelector(
                '.screen-source-option[data-source-id="window:3"]'
            )).display;
            input.value = 'missing title';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            return {
                filtered,
                editorDisplay,
                browserDisplay,
                filterBeforeScreens: Boolean(
                    input.compareDocumentPosition(document.querySelector(
                        '.screen-source-screen-label'
                    )) & Node.DOCUMENT_POSITION_FOLLOWING
                ),
                screenHiddenAfterNoMatch: document.querySelector(
                    '.screen-source-option[data-source-id="screen:1"]'
                ).hidden,
                noMatchHidden: document.querySelector(
                    '.screen-source-no-window-matches'
                ).hidden,
                captureCalls: window.__captureCalls,
            };
        }"""
    )
    assert result == {
        "filtered": {
            "Entire Screen": False,
            "Editor": False,
            "Browser Preview": True,
        },
        "editorDisplay": "flex",
        "browserDisplay": "none",
        "filterBeforeScreens": True,
        "screenHiddenAfterNoMatch": False,
        "noMatchHidden": False,
        "captureCalls": [
            {
                "types": ["window", "screen"],
                "thumbnailSize": {"width": 0, "height": 0},
            }
        ],
    }


@pytest.mark.frontend
def test_remembered_title_restores_only_one_normalized_exact_match(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        source_enumeration_may_prompt=True,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "EDITOR",
            "selectedScreenSourceId": "window:stale",
        },
    )
    page.evaluate(
        """() => {
            window.__metadataSources[1].id = 'window:new';
            window.__metadataSources[1].name = '  Editor  ';
        }"""
    )

    assert page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    ) is True

    result = page.evaluate(
        """() => ({
            selectedId: window.appState.selectedScreenSourceId,
            storedId: window.__storedValues.get('selectedScreenSourceId'),
            rememberedTitle: window.__storedValues.get('selectedScreenWindowTitle'),
            selectedSourceCalls: window.__selectedSourceCalls,
            selectedOptions: Array.from(document.querySelectorAll(
                '.screen-source-option.selected'
            )).map((option) => option.dataset.sourceId),
        })"""
    )
    assert result == {
        "selectedId": "window:new",
        "storedId": "window:new",
        "rememberedTitle": "EDITOR",
        "selectedSourceCalls": ["window:stale", "window:new"],
        "selectedOptions": ["window:new"],
    }


@pytest.mark.frontend
def test_remembered_title_does_not_guess_between_duplicate_windows(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        source_enumeration_may_prompt=True,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:stale",
        },
    )
    page.evaluate(
        """() => {
            window.__metadataSources.push({
                id: 'window:3',
                name: ' editor ',
                display_id: '',
                thumbnail: null,
            });
        }"""
    )

    assert page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    ) is True

    result = page.evaluate(
        """() => ({
            selectedId: window.appState.selectedScreenSourceId,
            hasStoredId: window.__storedValues.has('selectedScreenSourceId'),
            rememberedTitle: window.__storedValues.get('selectedScreenWindowTitle'),
            selectedSourceCalls: window.__selectedSourceCalls,
        })"""
    )
    assert result == {
        "selectedId": None,
        "hasStoredId": False,
        "rememberedTitle": "Editor",
        "selectedSourceCalls": ["window:stale", None],
    }


@pytest.mark.frontend
def test_current_explicit_selection_survives_duplicate_window_titles(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
        },
    )
    page.evaluate(
        """() => {
            window.__metadataSources.push({
                id: 'window:3',
                name: ' editor ',
                display_id: '',
                thumbnail: null,
            });
            window.selectScreenSource('window:2', 'Editor', 'Editor');
        }"""
    )

    result = page.evaluate(
        """async () => {
            const reconciliation = await window.appScreen
                .reconcileRememberedWindowSource(window.__metadataSources);
            return {
                status: reconciliation.status,
                sourceId: reconciliation.sourceId,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId'),
                selectedSourceCalls: window.__selectedSourceCalls,
            };
        }"""
    )
    assert result == {
        "status": "matched",
        "sourceId": "window:2",
        "selectedId": "window:2",
        "storedId": "window:2",
        "selectedSourceCalls": [None, "window:2"],
    }


@pytest.mark.frontend
def test_remembered_title_wins_when_an_old_source_id_is_reused(page: Page) -> None:
    _install_screen_source_harness(
        page,
        source_enumeration_may_prompt=True,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Browser Preview",
            "selectedScreenSourceId": "window:2",
        },
    )
    page.evaluate(
        """() => {
            window.__metadataSources.push({
                id: 'window:new-browser',
                name: 'Browser Preview',
                display_id: '',
                thumbnail: null,
            });
        }"""
    )

    assert page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    ) is True

    assert page.evaluate("window.appState.selectedScreenSourceId") == (
        "window:new-browser"
    )
    assert page.evaluate("window.__selectedSourceCalls") == [
        "window:2",
        "window:new-browser",
    ]


@pytest.mark.frontend
def test_window_selection_and_toggle_bound_the_remembered_title(page: Page) -> None:
    _install_screen_source_harness(page, source_enumeration_may_prompt=True)
    assert page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    ) is True

    result = page.evaluate(
        """async () => {
            document.querySelector(
                '.screen-source-option[data-source-id="window:2"]'
            ).click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const hasTitleBeforeEnable = window.__storedValues.has(
                'selectedScreenWindowTitle'
            );
            window.setScreenSourceTitleMatchEnabled(true);
            const rememberedAfterEnable = window.__storedValues.get(
                'selectedScreenWindowTitle'
            );
            document.querySelector(
                '.screen-source-option[data-source-id="screen:1"]'
            ).click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const hasTitleAfterScreen = window.__storedValues.has(
                'selectedScreenWindowTitle'
            );
            document.querySelector(
                '.screen-source-option[data-source-id="window:2"]'
            ).click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const rememberedAfterWindow = window.__storedValues.get(
                'selectedScreenWindowTitle'
            );
            window.setScreenSourceTitleMatchEnabled(false);
            return {
                hasTitleBeforeEnable,
                rememberedAfterEnable,
                hasTitleAfterScreen,
                rememberedAfterWindow,
                enabledAfterDisable: window.isScreenSourceTitleMatchEnabled(),
                hasRememberedTitleAfterDisable: window.__storedValues.has(
                    'selectedScreenWindowTitle'
                ),
            };
        }"""
    )
    assert result == {
        "hasTitleBeforeEnable": False,
        "rememberedAfterEnable": "Editor",
        "hasTitleAfterScreen": False,
        "rememberedAfterWindow": "Editor",
        "enabledAfterDisable": False,
        "hasRememberedTitleAfterDisable": False,
    }


@pytest.mark.frontend
def test_remember_toggle_uses_current_explicit_title_not_hidden_picker(
    page: Page,
) -> None:
    _install_screen_source_harness(page, source_enumeration_may_prompt=True)
    assert page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    ) is True

    result = page.evaluate(
        """async () => {
            const hiddenPicker = document.createElement('div');
            hiddenPicker.style.display = 'none';
            hiddenPicker.innerHTML = `
                <button class="screen-source-option"
                    data-source-id="window:2"
                    data-source-name="Old Editor"></button>
            `;
            document.getElementById('live2d-popup-screen')
                .insertAdjacentElement('beforebegin', hiddenPicker);

            document.querySelector(
                '#live2d-popup-screen '
                + '.screen-source-option[data-source-id="window:2"]'
            ).click();
            await new Promise((resolve) => setTimeout(resolve, 0));
            window.setScreenSourceTitleMatchEnabled(true);
            const rememberedAfterEnable = window.__storedValues.get(
                'selectedScreenWindowTitle'
            );
            const resolution = window.appScreen.reconcileRememberedWindowSource([
                { id: 'window:2', name: 'Editor' },
                { id: 'window:3', name: 'Old Editor' },
            ]);
            return {
                rememberedAfterEnable,
                status: resolution.status,
                selectedId: window.appState.selectedScreenSourceId,
            };
        }"""
    )

    assert result == {
        "rememberedAfterEnable": "Editor",
        "status": "matched",
        "selectedId": "window:2",
    }


@pytest.mark.frontend
def test_screen_source_prompt_provider_skips_thumbnail_reenumeration(
    page: Page,
) -> None:
    _install_screen_source_harness(page, source_enumeration_may_prompt=True)

    rendered = page.evaluate(
        """async () => window.renderFloatingScreenSourceList(
            document.getElementById('live2d-popup-screen')
        )"""
    )
    assert rendered is True

    state = page.evaluate(
        """() => ({
            calls: window.__captureCalls,
            metadataThumbnailReads: window.__metadataThumbnailReads,
            loadingCount: document.querySelectorAll(
                '.screen-source-thumbnail-loading'
            ).length,
            fallbackCount: document.querySelectorAll(
                '.screen-source-thumbnail-fallback'
            ).length,
        })"""
    )
    assert state == {
        "calls": [
            {
                "types": ["window", "screen"],
                "thumbnailSize": {"width": 0, "height": 0},
            }
        ],
        "metadataThumbnailReads": 0,
        "loadingCount": 0,
        "fallbackCount": 2,
    }


@pytest.mark.frontend
def test_remembered_title_reconciles_reused_id_before_stream_capture(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:stale",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__metadataSources = [
                { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
                { id: 'window:stale', name: 'Unrelated Browser', display_id: '' },
                { id: 'window:new', name: 'Editor', display_id: '' },
            ];
            window.__desktopProvider.getSources = async () => window.__metadataSources;
            const capturedSourceIds = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const stream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        capturedSourceIds.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return stream;
                    },
                },
            });

            const acquired = await window.appScreen.acquireOrReuseCachedStream({
                allowPrompt: false,
            });
            const state = {
                capturedSourceIds,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId'),
                returnedExpectedStream: acquired === stream,
            };
            track.stop();
            window.appState.screenCaptureStream = null;
            return state;
        }"""
    )

    assert result == {
        "capturedSourceIds": ["window:new"],
        "selectedId": "window:new",
        "storedId": "window:new",
        "returnedExpectedStream": True,
    }


@pytest.mark.frontend
def test_missing_remembered_window_does_not_fall_back_to_entire_screen(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:stale",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__metadataSources = [
                { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
                { id: 'window:other', name: 'Unrelated Browser', display_id: '' },
            ];
            window.__desktopProvider.getSources = async () => window.__metadataSources;
            const capturedSourceIds = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const stream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        capturedSourceIds.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return stream;
                    },
                },
            });

            const acquired = await window.appScreen.acquireOrReuseCachedStream({
                allowPrompt: false,
            });
            const state = {
                capturedSourceIds,
                selectedId: window.appState.selectedScreenSourceId,
                hasStoredId: window.__storedValues.has('selectedScreenSourceId'),
                rememberedTitle: window.__storedValues.get(
                    'selectedScreenWindowTitle'
                ),
                returnedNull: acquired === null,
            };
            if (acquired) acquired.getTracks().forEach((item) => item.stop());
            window.appState.screenCaptureStream = null;
            return state;
        }"""
    )

    assert result == {
        "capturedSourceIds": [],
        "selectedId": None,
        "hasStoredId": False,
        "rememberedTitle": "Editor",
        "returnedNull": True,
    }


@pytest.mark.frontend
def test_screenshot_preflight_remaps_reused_source_id_by_remembered_title(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:reused",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__desktopProvider.getSources = async () => [
                { id: 'window:reused', name: 'Unrelated Browser', display_id: '' },
                { id: 'window:correct', name: 'Editor', display_id: '' },
            ];
            const prepared = await window.appScreen.prepareRememberedWindowCapture();
            return {
                required: prepared.required,
                allowed: prepared.allowed,
                sourceId: prepared.sourceId,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
            };
        }"""
    )

    assert result == {
        "required": True,
        "allowed": True,
        "sourceId": "window:correct",
        "selectedId": "window:correct",
        "storedId": "window:correct",
    }


@pytest.mark.frontend
def test_screenshot_preflight_blocks_missing_remembered_title(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:reused",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__desktopProvider.getSources = async () => [
                { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
                { id: 'window:reused', name: 'Unrelated Browser', display_id: '' },
            ];
            const prepared = await window.appScreen.prepareRememberedWindowCapture();
            return {
                required: prepared.required,
                allowed: prepared.allowed,
                sourceId: prepared.sourceId,
                selectedId: window.appState.selectedScreenSourceId,
                hasStoredId: window.__storedValues.has('selectedScreenSourceId'),
            };
        }"""
    )

    assert result == {
        "required": True,
        "allowed": False,
        "sourceId": None,
        "selectedId": None,
        "hasStoredId": False,
    }


@pytest.mark.frontend
def test_screenshot_preflight_keeps_selected_window_bounded_when_title_store_fails(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenSourceId": "window:2",
        },
    )

    result = page.evaluate(
        """async () => {
            const originalSetItem = window.localStorage.setItem.bind(window.localStorage);
            window.localStorage.setItem = (key, value) => {
                if (key === 'selectedScreenWindowTitle') {
                    throw new Error('simulated title storage failure');
                }
                originalSetItem(key, value);
            };
            await window.selectScreenSource('window:2', 'Editor', 'Editor');
            window.__desktopProvider.getSources = async () => [
                { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
                { id: 'window:2', name: 'Editor', display_id: '' },
            ];
            const prepared = await window.appScreen.prepareRememberedWindowCapture();
            return {
                required: prepared.required,
                allowed: prepared.allowed,
                status: prepared.status ?? null,
                sourceId: prepared.sourceId,
                rememberedTitle:
                    window.__storedValues.get('selectedScreenWindowTitle') ?? null,
            };
        }"""
    )

    assert result == {
        "required": True,
        "allowed": True,
        "status": "adopted-current-window",
        "sourceId": "window:2",
        "rememberedTitle": None,
    }


@pytest.mark.frontend
def test_restored_window_id_without_trusted_title_is_not_adopted(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenSourceId": "window:reused",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__desktopProvider.getSources = async () => [
                { id: 'window:reused', name: 'Unrelated Browser', display_id: '' },
            ];
            const captureCalls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const unrelatedStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return unrelatedStream;
                    },
                },
            });

            const prepared = await window.appScreen.prepareRememberedWindowCapture();
            let acquired = null;
            if (prepared.allowed) {
                acquired = await window.appScreen.acquireOrReuseCachedStream({
                    allowPrompt: false,
                });
            }
            const state = {
                required: prepared.required,
                allowed: prepared.allowed,
                status: prepared.status ?? null,
                captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
                rememberedTitle:
                    window.__storedValues.get('selectedScreenWindowTitle') ?? null,
                unrelatedStreamInstalled:
                    window.appState.screenCaptureStream === unrelatedStream,
                trackStoppedBeforeCleanup: track.stopped,
            };
            if (acquired) acquired.getTracks().forEach((item) => item.stop());
            window.appState.screenCaptureStream = null;
            return state;
        }"""
    )

    assert result == {
        "required": True,
        "allowed": False,
        "status": "untrusted-restored-window",
        "captureCalls": [],
        "selectedId": None,
        "storedId": None,
        "rememberedTitle": None,
        "unrelatedStreamInstalled": False,
        "trackStoppedBeforeCleanup": False,
    }


@pytest.mark.frontend
def test_screenshot_preflight_bounds_a_stalled_source_enumeration(page: Page) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:2",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__desktopProvider.getSources = () => new Promise(() => {});
            return Promise.race([
                window.appScreen.prepareRememberedWindowCapture().then((prepared) => ({
                    hung: false,
                    required: prepared.required,
                    allowed: prepared.allowed,
                })),
                new Promise((resolve) => setTimeout(() => resolve({ hung: true }), 3500)),
            ]);
        }"""
    )

    assert result == {
        "hung": False,
        "required": True,
        "allowed": False,
    }


@pytest.mark.frontend
def test_late_stream_for_old_selection_is_discarded_after_source_change(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__metadataSources = [
                { id: 'window:old', name: 'Editor', display_id: '' },
                { id: 'window:new', name: 'Browser', display_id: '' },
            ];
            window.__desktopProvider.getSources = async () => window.__metadataSources;
            let resolveGetUserMedia;
            let getUserMediaStarted = false;
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const oldStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    getUserMedia() {
                        getUserMediaStarted = true;
                        return new Promise((resolve) => {
                            resolveGetUserMedia = resolve;
                        });
                    },
                },
            });

            const acquisition = window.appScreen.acquireOrReuseCachedStream({
                allowPrompt: false,
            });
            const getUserMediaDeadline = performance.now() + 5000;
            while (!getUserMediaStarted && performance.now() < getUserMediaDeadline) {
                await new Promise((resolve) => setTimeout(resolve, 0));
            }
            if (!getUserMediaStarted) throw new Error('getUserMedia did not start');
            await window.selectScreenSource('window:new', 'Browser', 'Browser');
            resolveGetUserMedia(oldStream);
            const acquired = await acquisition;
            const state = {
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId'),
                returnedNull: acquired === null,
                oldStreamStopped: track.stopped,
                oldStreamInstalled: window.appState.screenCaptureStream === oldStream,
            };
            if (acquired) acquired.getTracks().forEach((item) => item.stop());
            window.appState.screenCaptureStream = null;
            return state;
        }"""
    )

    assert result == {
        "selectedId": "window:new",
        "storedId": "window:new",
        "returnedNull": True,
        "oldStreamStopped": True,
        "oldStreamInstalled": False,
    }


@pytest.mark.frontend
def test_stale_title_enumeration_does_not_clear_a_newer_selection(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            let resolveSources;
            let enumerationStarted = false;
            window.__desktopProvider.getSources = () => {
                enumerationStarted = true;
                return new Promise((resolve) => { resolveSources = resolve; });
            };
            let getUserMediaCalls = 0;
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia() {
                        getUserMediaCalls += 1;
                        throw new Error('stale acquisition must not continue');
                    },
                },
            });

            const acquisition = window.appScreen.acquireOrReuseCachedStream({
                allowPrompt: false,
            });
            const enumerationDeadline = performance.now() + 5000;
            while (!enumerationStarted && performance.now() < enumerationDeadline) {
                await new Promise((resolve) => setTimeout(resolve, 0));
            }
            if (!enumerationStarted) throw new Error('source enumeration did not start');
            await window.selectScreenSource('window:new', 'Browser', 'Browser');
            resolveSources([
                { id: 'window:old', name: 'Editor', display_id: '' },
            ]);
            const acquired = await acquisition;
            return {
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId'),
                rememberedTitle: window.__storedValues.get(
                    'selectedScreenWindowTitle'
                ),
                returnedNull: acquired === null,
                getUserMediaCalls,
            };
        }"""
    )

    assert result == {
        "selectedId": "window:new",
        "storedId": "window:new",
        "rememberedTitle": "Browser",
        "returnedNull": True,
        "getUserMediaCalls": 0,
    }


@pytest.mark.frontend
def test_manual_share_stale_metadata_does_not_clear_a_newer_selection(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )
    page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__manualEnumerationStarted = false;
            window.__manualGetUserMediaCalls = 0;
            window.__desktopProvider.getSources = () => {
                window.__manualEnumerationStarted = true;
                return new Promise((resolve) => { window.__resolveManualSources = resolve; });
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia() {
                        window.__manualGetUserMediaCalls += 1;
                        throw new Error('stale manual start must not continue');
                    },
                },
            });
            window.__manualStartPromise = window.startScreenSharing();
        }"""
    )
    page.wait_for_function("window.__manualEnumerationStarted === true")

    result = page.evaluate(
        """async () => {
            await window.selectScreenSource('window:new', 'Browser', 'Browser');
            window.__resolveManualSources([
                { id: 'window:old', name: 'Editor', display_id: '' },
            ]);
            await window.__manualStartPromise;
            return {
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
                rememberedTitle: window.__storedValues.get('selectedScreenWindowTitle') ?? null,
                getUserMediaCalls: window.__manualGetUserMediaCalls,
            };
        }"""
    )

    assert result == {
        "selectedId": "window:new",
        "storedId": "window:new",
        "rememberedTitle": "Browser",
        "getUserMediaCalls": 0,
    }


@pytest.mark.frontend
def test_manual_share_discards_late_stream_after_source_change(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )
    page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__desktopProvider.getSources = async () => [
                { id: 'window:old', name: 'Editor', display_id: '' },
                { id: 'window:new', name: 'Browser', display_id: '' },
            ];
            window.__manualGetUserMediaStarted = false;
            window.__manualCaptureCalls = [];
            window.__oldTrack = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            window.__oldStream = {
                active: true,
                getVideoTracks() { return [window.__oldTrack]; },
                getTracks() { return [window.__oldTrack]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    getUserMedia(constraints) {
                        window.__manualCaptureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        window.__manualGetUserMediaStarted = true;
                        return new Promise((resolve) => {
                            window.__resolveManualGetUserMedia = resolve;
                        });
                    },
                },
            });
            window.__manualStartPromise = window.startScreenSharing();
        }"""
    )
    page.wait_for_function("window.__manualGetUserMediaStarted === true")

    result = page.evaluate(
        """async () => {
            await window.selectScreenSource('window:new', 'Browser', 'Browser');
            window.__resolveManualGetUserMedia(window.__oldStream);
            await window.__manualStartPromise;
            const state = {
                captureCalls: window.__manualCaptureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
                rememberedTitle:
                    window.__storedValues.get('selectedScreenWindowTitle') ?? null,
                oldStreamInstalled:
                    window.appState.screenCaptureStream === window.__oldStream,
                oldTrackStoppedBeforeCleanup: window.__oldTrack.stopped,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "captureCalls": ["window:old"],
        "selectedId": "window:new",
        "storedId": "window:new",
        "rememberedTitle": "Browser",
        "oldStreamInstalled": False,
        "oldTrackStoppedBeforeCleanup": True,
    }


@pytest.mark.frontend
def test_manual_share_rejected_stale_metadata_does_not_capture_old_selection(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )
    page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__manualEnumerationStarted = false;
            window.__manualCaptureCalls = [];
            window.__oldTrack = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            window.__oldStream = {
                active: true,
                getVideoTracks() { return [window.__oldTrack]; },
                getTracks() { return [window.__oldTrack]; },
            };
            window.__desktopProvider.getSources = () => {
                window.__manualEnumerationStarted = true;
                return new Promise((_resolve, reject) => {
                    window.__rejectManualSources = reject;
                });
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        window.__manualCaptureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return window.__oldStream;
                    },
                },
            });
            window.__manualStartPromise = window.startScreenSharing();
        }"""
    )
    page.wait_for_function("window.__manualEnumerationStarted === true")

    result = page.evaluate(
        """async () => {
            await window.selectScreenSource('window:new', 'Browser', 'Browser');
            window.__rejectManualSources(new Error('metadata unavailable'));
            await window.__manualStartPromise;
            const state = {
                captureCalls: window.__manualCaptureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
                rememberedTitle: window.__storedValues.get('selectedScreenWindowTitle') ?? null,
                oldStreamInstalled: window.appState.screenCaptureStream === window.__oldStream,
            };
            await window.stopScreenSharing(true);
            state.oldTrackStopped = window.__oldTrack.stopped;
            return state;
        }"""
    )

    assert result == {
        "captureCalls": [],
        "selectedId": "window:new",
        "storedId": "window:new",
        "rememberedTitle": "Browser",
        "oldStreamInstalled": False,
        "oldTrackStopped": False,
    }


@pytest.mark.frontend
def test_manual_share_rejected_remembered_validation_does_not_capture_reused_id(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:stale",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__desktopProvider.getSources = async () => {
                throw new Error('metadata unavailable');
            };
            const captureCalls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const unrelatedStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return unrelatedStream;
                    },
                },
            });
            await window.startScreenSharing();
            const state = {
                captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                unrelatedStreamInstalled:
                    window.appState.screenCaptureStream === unrelatedStream,
            };
            await window.stopScreenSharing(true);
            state.trackStopped = track.stopped;
            return state;
        }"""
    )

    assert result == {
        "captureCalls": [],
        "selectedId": "window:stale",
        "unrelatedStreamInstalled": False,
        "trackStopped": False,
    }


@pytest.mark.frontend
def test_adopted_remembered_window_capture_failure_does_not_fallback_to_screen(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            const calls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const screenStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            window.__desktopProvider.getSources = async (options) => {
                if (options.types.length === 1 && options.types[0] === 'screen') {
                    return [{ id: 'screen:1', name: 'Entire Screen', display_id: '1' }];
                }
                return [
                    { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
                    { id: 'window:old', name: 'Editor', display_id: '' },
                ];
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        const sourceId = constraints.video.mandatory.chromeMediaSourceId;
                        calls.push(sourceId);
                        if (sourceId === 'window:old') {
                            throw new Error('window acquisition failed');
                        }
                        return screenStream;
                    },
                    async getDisplayMedia() {
                        calls.push('getDisplayMedia');
                        return screenStream;
                    },
                },
            });
            await window.selectScreenSource('window:old', 'Editor', 'Editor');
            await window.startScreenSharing();
            const state = {
                calls,
                rememberedTitle:
                    window.__storedValues.get('selectedScreenWindowTitle') ?? null,
                screenStreamInstalled: window.appState.screenCaptureStream === screenStream,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "calls": ["window:old"],
        "rememberedTitle": "Editor",
        "screenStreamInstalled": False,
    }


@pytest.mark.frontend
def test_failed_adopted_title_storage_does_not_widen_capture_to_screen(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            const originalSetItem = window.localStorage.setItem.bind(window.localStorage);
            window.localStorage.setItem = (key, value) => {
                if (key === 'selectedScreenWindowTitle') {
                    throw new Error('simulated title storage failure');
                }
                originalSetItem(key, value);
            };
            await window.selectScreenSource('window:old', 'Editor', 'Editor');
            const calls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const screenStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            window.__desktopProvider.getSources = async (options) => {
                if (options.types.length === 1 && options.types[0] === 'screen') {
                    return [{ id: 'screen:1', name: 'Entire Screen', display_id: '1' }];
                }
                return [
                    { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
                    { id: 'window:old', name: 'Editor', display_id: '' },
                ];
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        const sourceId = constraints.video.mandatory.chromeMediaSourceId;
                        calls.push(sourceId);
                        if (sourceId === 'window:old') {
                            throw new Error('window acquisition failed');
                        }
                        return screenStream;
                    },
                    async getDisplayMedia() {
                        calls.push('getDisplayMedia');
                        return screenStream;
                    },
                },
            });
            await window.startScreenSharing();
            const state = {
                calls,
                rememberedTitle:
                    window.__storedValues.get('selectedScreenWindowTitle') ?? null,
                screenStreamInstalled: window.appState.screenCaptureStream === screenStream,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "calls": ["window:old"],
        "rememberedTitle": None,
        "screenStreamInstalled": False,
    }


@pytest.mark.frontend
def test_canonical_unicode_title_keeps_the_explicit_window_selection(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Café",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """() => {
            const resolution = window.appScreen.reconcileRememberedWindowSource([
                { id: 'window:old', name: 'Cafe\\u0301', display_id: '' },
            ]);
            return {
                status: resolution.status,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
                rememberedTitle:
                    window.__storedValues.get('selectedScreenWindowTitle') ?? null,
            };
        }"""
    )

    assert result == {
        "status": "matched",
        "selectedId": "window:old",
        "storedId": "window:old",
        "rememberedTitle": "Café",
    }


@pytest.mark.frontend
def test_remembered_window_capture_failure_does_not_fallback_to_a_screen(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            const calls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
                getSettings() { return { displaySurface: 'monitor' }; },
            };
            const fallbackStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            window.__desktopProvider.getSources = async (options) => {
                if (options.types.length === 1 && options.types[0] === 'screen') {
                    return [{ id: 'screen:1', name: 'Entire Screen', display_id: '1' }];
                }
                return [
                    { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
                    { id: 'window:old', name: 'Editor', display_id: '' },
                ];
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        const sourceId = constraints.video.mandatory.chromeMediaSourceId;
                        calls.push(sourceId);
                        if (sourceId === 'window:old') {
                            const error = new Error('window acquisition failed');
                            error.name = 'NotReadableError';
                            throw error;
                        }
                        return fallbackStream;
                    },
                    async getDisplayMedia() {
                        calls.push('getDisplayMedia');
                        return fallbackStream;
                    },
                },
            });
            await window.startScreenSharing();
            const state = {
                calls,
                selectedId: window.appState.selectedScreenSourceId,
                fallbackInstalled: window.appState.screenCaptureStream === fallbackStream,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "calls": ["window:old"],
        "selectedId": "window:old",
        "fallbackInstalled": False,
    }


@pytest.mark.frontend
def test_non_remembered_stale_source_can_fallback_to_the_first_screen(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={"selectedScreenSourceId": "window:stale"},
    )

    result = page.evaluate(
        """async () => {
            window.__metadataSources = [
                { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
            ];
            window.__desktopProvider.getSources = async () => window.__metadataSources;
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const stream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            const calls = [];
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        calls.push(constraints.video.mandatory.chromeMediaSourceId);
                        return stream;
                    },
                },
            });

            const acquired = await window.appScreen.acquireOrReuseCachedStream({
                allowPrompt: false,
            });
            const state = {
                calls,
                returnedStream: acquired === stream,
                trackStopped: track.stopped,
            };
            if (acquired) acquired.getTracks().forEach((item) => item.stop());
            window.appState.screenCaptureStream = null;
            return state;
        }"""
    )

    assert result == {
        "calls": ["screen:1"],
        "returnedStream": True,
        "trackStopped": False,
    }


@pytest.mark.frontend
def test_remembered_cached_acquisition_failure_does_not_open_display_picker(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__desktopProvider.getSources = async () => [
                { id: 'window:old', name: 'Editor', display_id: '' },
            ];
            const calls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const pickerStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        calls.push(constraints.video.mandatory.chromeMediaSourceId);
                        throw new Error('bound window acquisition failed');
                    },
                    async getDisplayMedia() {
                        calls.push('getDisplayMedia');
                        return pickerStream;
                    },
                },
            });

            const acquired = await window.appScreen.acquireOrReuseCachedStream({
                allowPrompt: true,
            });
            const state = {
                calls,
                returnedPickerStream: acquired === pickerStream,
                pickerStreamInstalled:
                    window.appState.screenCaptureStream === pickerStream,
                pickerTrackStopped: track.stopped,
            };
            if (acquired) acquired.getTracks().forEach((item) => item.stop());
            window.appState.screenCaptureStream = null;
            return state;
        }"""
    )

    assert result == {
        "calls": ["window:old"],
        "returnedPickerStream": False,
        "pickerStreamInstalled": False,
        "pickerTrackStopped": False,
    }


@pytest.mark.frontend
def test_prompting_provider_rejects_unowned_restored_window_id(page: Page) -> None:
    _install_screen_source_harness(
        page,
        source_enumeration_may_prompt=True,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:reused",
        },
    )

    result = page.evaluate(
        """async () => {
            const prepared = await window.appScreen.prepareRememberedWindowCapture();
            return {
                required: prepared.required,
                allowed: prepared.allowed,
                status: prepared.status,
                selectedId: window.appState.selectedScreenSourceId,
            };
        }"""
    )

    assert result == {
        "required": True,
        "allowed": False,
        "status": "untrusted-prompt-source",
        "selectedId": "window:reused",
    }


@pytest.mark.frontend
def test_prompting_provider_keeps_current_renderer_explicit_window(page: Page) -> None:
    _install_screen_source_harness(
        page,
        source_enumeration_may_prompt=True,
        initial_storage={"screenSourceTitleMatchEnabled": "true"},
    )

    result = page.evaluate(
        """async () => {
            await window.selectScreenSource('window:2', 'Editor', 'Editor');
            const prepared = await window.appScreen.prepareRememberedWindowCapture();
            return {
                required: prepared.required,
                allowed: prepared.allowed,
                status: prepared.status,
                selectedId: window.appState.selectedScreenSourceId,
            };
        }"""
    )

    assert result == {
        "required": True,
        "allowed": True,
        "status": "prompt-required",
        "selectedId": "window:2",
    }


@pytest.mark.frontend
def test_manual_share_rejects_unowned_restored_window_on_prompting_provider(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        source_enumeration_may_prompt=True,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:reused",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            const captureCalls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const unrelatedStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return unrelatedStream;
                    },
                },
            });

            await window.startScreenSharing();
            const state = {
                captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                unrelatedStreamInstalled:
                    window.appState.screenCaptureStream === unrelatedStream,
                trackStoppedBeforeCleanup: track.stopped,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "captureCalls": [],
        "selectedId": "window:reused",
        "unrelatedStreamInstalled": False,
        "trackStoppedBeforeCleanup": False,
    }


@pytest.mark.frontend
def test_manual_share_rejects_unowned_titleless_window_when_enumeration_fails(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenSourceId": "window:reused",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__desktopProvider.getSources = async () => {
                throw new Error('enumeration failed');
            };
            const captureCalls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const unrelatedStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return unrelatedStream;
                    },
                    async getDisplayMedia() {
                        captureCalls.push('getDisplayMedia');
                        return unrelatedStream;
                    },
                },
            });

            await window.startScreenSharing();
            const state = {
                captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                unrelatedStreamInstalled:
                    window.appState.screenCaptureStream === unrelatedStream,
                trackStoppedBeforeCleanup: track.stopped,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "captureCalls": [],
        "selectedId": "window:reused",
        "unrelatedStreamInstalled": False,
        "trackStoppedBeforeCleanup": False,
    }


@pytest.mark.frontend
def test_manual_share_fails_closed_when_owned_window_enumeration_fails(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            await window.selectScreenSource('window:reused', 'Editor', 'Editor');
            window.__desktopProvider.getSources = async () => {
                throw new Error('enumeration failed after source-id reuse');
            };
            const captureCalls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const unrelatedStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return unrelatedStream;
                    },
                },
            });

            await window.startScreenSharing();
            const state = {
                captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                unrelatedStreamInstalled:
                    window.appState.screenCaptureStream === unrelatedStream,
                trackStoppedBeforeCleanup: track.stopped,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "captureCalls": [],
        "selectedId": "window:reused",
        "unrelatedStreamInstalled": False,
        "trackStoppedBeforeCleanup": False,
    }


@pytest.mark.frontend
def test_remembered_source_rejection_releases_proactive_cached_stream(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:stale",
        },
    )

    result = page.evaluate(
        """async () => {
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const proactiveStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            window.appState.proactiveVisionEnabled = true;
            window.appState.isRecording = true;
            window.appState.screenCaptureStream = proactiveStream;
            window.appState.screenCaptureStreamLastUsed = Date.now();
            window.__metadataSources = [
                {
                    id: 'screen:1',
                    name: 'Entire Screen',
                    display_id: '1',
                    thumbnail: null,
                },
            ];

            await window.renderFloatingScreenSourceList(
                document.getElementById('live2d-popup-screen')
            );
            return {
                selectedId: window.appState.selectedScreenSourceId,
                streamRetained:
                    window.appState.screenCaptureStream === proactiveStream,
                trackStopped: track.stopped,
                lastUsed: window.appState.screenCaptureStreamLastUsed,
            };
        }"""
    )

    assert result == {
        "selectedId": None,
        "streamRetained": False,
        "trackStopped": True,
        "lastUsed": None,
    }


@pytest.mark.frontend
def test_remembered_cached_reenumeration_is_bounded(page: Page) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__desktopProvider.getSources = () => new Promise(() => {});
            return Promise.race([
                window.appScreen.acquireOrReuseCachedStream({ allowPrompt: false })
                    .then((stream) => ({ hung: false, returnedStream: !!stream })),
                new Promise((resolve) => {
                    setTimeout(() => resolve({ hung: true }), 3500);
                }),
            ]);
        }"""
    )

    assert result == {
        "hung": False,
        "returnedStream": False,
    }


@pytest.mark.frontend
def test_manual_screen_fallback_discards_late_stream_after_source_change(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={"selectedScreenSourceId": "window:old"},
    )
    page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__desktopProvider.getSources = async (options) => {
                if (options.types.length === 1 && options.types[0] === 'screen') {
                    return [{ id: 'screen:1', name: 'Entire Screen', display_id: '1' }];
                }
                return [
                    { id: 'window:old', name: 'Editor', display_id: '' },
                    { id: 'window:new', name: 'Browser', display_id: '' },
                    { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
                ];
            };
            window.__fallbackStarted = false;
            window.__captureCalls = [];
            window.__fallbackTrack = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            window.__fallbackStream = {
                active: true,
                getVideoTracks() { return [window.__fallbackTrack]; },
                getTracks() { return [window.__fallbackTrack]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        const sourceId =
                            constraints.video.mandatory.chromeMediaSourceId;
                        window.__captureCalls.push(sourceId);
                        if (sourceId === 'window:old') {
                            throw new Error('selected source failed');
                        }
                        window.__fallbackStarted = true;
                        return new Promise((resolve) => {
                            window.__resolveFallback = resolve;
                        });
                    },
                },
            });
            window.__manualStartPromise = window.startScreenSharing();
        }"""
    )
    page.wait_for_function("window.__fallbackStarted === true")

    result = page.evaluate(
        """async () => {
            await window.selectScreenSource('window:new', 'Browser', 'Browser');
            window.__resolveFallback(window.__fallbackStream);
            await window.__manualStartPromise;
            const state = {
                captureCalls: window.__captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
                fallbackInstalled:
                    window.appState.screenCaptureStream === window.__fallbackStream,
                fallbackTrackStoppedBeforeCleanup: window.__fallbackTrack.stopped,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "captureCalls": ["window:old", "screen:1"],
        "selectedId": "window:new",
        "storedId": "window:new",
        "fallbackInstalled": False,
        "fallbackTrackStoppedBeforeCleanup": True,
    }


@pytest.mark.frontend
def test_manual_picker_fallback_discards_late_stream_after_source_change(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        source_enumeration_may_prompt=True,
        initial_storage={"selectedScreenSourceId": "window:old"},
    )
    page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__pickerStarted = false;
            window.__captureCalls = [];
            window.__pickerTrack = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            window.__pickerStream = {
                active: true,
                getVideoTracks() { return [window.__pickerTrack]; },
                getTracks() { return [window.__pickerTrack]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        window.__captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        throw new Error('selected source failed');
                    },
                    getDisplayMedia() {
                        window.__captureCalls.push('getDisplayMedia');
                        window.__pickerStarted = true;
                        return new Promise((resolve) => {
                            window.__resolvePicker = resolve;
                        });
                    },
                },
            });
            window.__manualStartPromise = window.startScreenSharing();
        }"""
    )
    page.wait_for_function("window.__pickerStarted === true")

    result = page.evaluate(
        """async () => {
            await window.selectScreenSource('window:new', 'Browser', 'Browser');
            window.__resolvePicker(window.__pickerStream);
            await window.__manualStartPromise;
            const state = {
                captureCalls: window.__captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
                pickerInstalled:
                    window.appState.screenCaptureStream === window.__pickerStream,
                pickerTrackStoppedBeforeCleanup: window.__pickerTrack.stopped,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "captureCalls": ["window:old", "getDisplayMedia"],
        "selectedId": "window:new",
        "storedId": "window:new",
        "pickerInstalled": False,
        "pickerTrackStoppedBeforeCleanup": True,
    }


@pytest.mark.frontend
@pytest.mark.parametrize("capture_path", ["selected-source", "display-picker"])
@pytest.mark.parametrize(
    "invalidation",
    ["current", "cancel", "source-change", "confirmation-error"],
)
def test_wgc_restart_approval_requires_current_attempt(
    page: Page,
    capture_path: str,
    invalidation: str,
) -> None:
    _install_screen_source_harness(
        page,
        source_enumeration_may_prompt=True,
        initial_storage=(
            {"selectedScreenSourceId": "window:old"}
            if capture_path == "selected-source"
            else None
        ),
    )
    page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__wgcRequests = [];
            window.__wgcRestartCalls = [];
            window.__statusToasts = [];
            window.showStatusToast = (message) => {
                window.__statusToasts.push(String(message));
            };
            window.__wgcRequestStarted = false;
            window.__desktopProvider.requestWindowsGraphicsCaptureFallback = (payload) => {
                window.__wgcRequests.push(payload);
                window.__wgcRequestStarted = true;
                return new Promise((resolve) => {
                    window.__resolveWgcRequest = resolve;
                });
            };
            window.__desktopProvider.restartWindowsGraphicsCaptureFallback = (token) => {
                window.__wgcRestartCalls.push(token);
                if (window.__rejectWgcRestart) {
                    return Promise.reject(new Error('restart IPC unavailable'));
                }
                return Promise.resolve({ restarting: true, reason: 'restarting' });
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia() {
                        const error = new Error('Could not start video source');
                        error.name = 'NotReadableError';
                        throw error;
                    },
                    async getDisplayMedia() {
                        throw new Error('fallback picker failed');
                    },
                },
            });
            window.__manualStartPromise = window.startScreenSharing();
        }"""
    )
    page.wait_for_function("window.__wgcRequestStarted === true")

    result = page.evaluate(
        """async (invalidation) => {
            if (invalidation === 'cancel') {
                window.appScreen.cancelPendingScreenSharingStart();
            } else if (invalidation === 'source-change') {
                await window.selectScreenSource('window:new', 'Browser', 'Browser');
            }
            window.__rejectWgcRestart = invalidation === 'confirmation-error';
            window.__resolveWgcRequest({
                prompted: true,
                restarting: false,
                restartApproved: true,
                restartToken: 'approval-1',
                reason: 'restart-approved',
            });
            await window.__manualStartPromise;
            return {
                pending: window.isScreenSharingStartPending(),
                restartCalls: window.__wgcRestartCalls,
                requestCount: window.__wgcRequests.length,
                deferred: window.__wgcRequests[0].deferRestartUntilConfirmed,
                selectedId: window.appState.selectedScreenSourceId,
                captureFailureToasts: window.__statusToasts.filter(
                    (message) => message.startsWith('NotReadableError:')
                ).length,
            };
        }""",
        invalidation,
    )

    expected_selected_id = None
    if capture_path == "selected-source":
        expected_selected_id = "window:old"
    if invalidation == "source-change":
        expected_selected_id = "window:new"
    assert result == {
        "pending": False,
        "restartCalls": (
            ["approval-1"]
            if invalidation in {"current", "confirmation-error"}
            else []
        ),
        "requestCount": 1,
        "deferred": True,
        "selectedId": expected_selected_id,
        "captureFailureToasts": 1 if invalidation == "confirmation-error" else 0,
    }


@pytest.mark.frontend
def test_cached_acquisition_without_trusted_title_does_not_widen_to_screen(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenSourceId": "window:stale",
        },
    )

    result = page.evaluate(
        """async () => {
            window.__desktopProvider.getSources = async () => [
                { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
            ];
            const captureCalls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const screenStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return screenStream;
                    },
                },
            });

            const acquired = await window.appScreen.acquireOrReuseCachedStream({
                allowPrompt: false,
            });
            const state = {
                captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                returnedNull: acquired === null,
                screenStreamInstalled:
                    window.appState.screenCaptureStream === screenStream,
                trackStoppedBeforeCleanup: track.stopped,
            };
            if (acquired) acquired.getTracks().forEach((item) => item.stop());
            window.appState.screenCaptureStream = null;
            return state;
        }"""
    )

    assert result == {
        "captureCalls": [],
        "selectedId": None,
        "returnedNull": True,
        "screenStreamInstalled": False,
        "trackStoppedBeforeCleanup": False,
    }


@pytest.mark.frontend
def test_manual_remembered_source_enumeration_is_bounded(page: Page) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            let resolveSources;
            window.__desktopProvider.getSources = () => new Promise((resolve) => {
                resolveSources = resolve;
            });

            const startPromise = window.startScreenSharing();
            const outcome = await Promise.race([
                startPromise.then(() => ({
                    hung: false,
                    pending: window.isScreenSharingStartPending(),
                })),
                new Promise((resolve) => {
                    setTimeout(() => resolve({
                        hung: true,
                        pending: window.isScreenSharingStartPending(),
                    }), 3500);
                }),
            ]);
            if (outcome.hung) {
                window.appScreen.cancelPendingScreenSharingStart();
                if (typeof resolveSources === 'function') {
                    resolveSources([
                        { id: 'window:old', name: 'Editor', display_id: '' },
                    ]);
                }
                await startPromise;
            }
            return outcome;
        }"""
    )

    assert result == {
        "hung": False,
        "pending": False,
    }


@pytest.mark.frontend
def test_manual_share_revalidates_cached_stream_before_transmission(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            const oldTrack = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const oldStream = {
                active: true,
                getVideoTracks() { return [oldTrack]; },
                getTracks() { return [oldTrack]; },
            };
            const replacementTrack = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const replacementStream = {
                active: true,
                getVideoTracks() { return [replacementTrack]; },
                getTracks() { return [replacementTrack]; },
            };
            window.appState.screenCaptureStream = oldStream;
            let enumerationCalls = 0;
            const captureCalls = [];
            window.__desktopProvider.getSources = async () => {
                enumerationCalls += 1;
                return [
                    { id: 'window:old', name: 'Unrelated Browser', display_id: '' },
                    { id: 'window:new', name: 'Editor', display_id: '' },
                ];
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return replacementStream;
                    },
                },
            });

            await window.startScreenSharing();
            const state = {
                enumerated: enumerationCalls > 0,
                captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                oldStreamInstalled:
                    window.appState.screenCaptureStream === oldStream,
                oldTrackStoppedBeforeCleanup: oldTrack.stopped,
            };
            await window.stopScreenSharing(true);
            if (!oldTrack.stopped) oldTrack.stop();
            if (!replacementTrack.stopped) replacementTrack.stop();
            return state;
        }"""
    )

    assert result == {
        "enumerated": True,
        "captureCalls": ["window:new"],
        "selectedId": "window:new",
        "oldStreamInstalled": False,
        "oldTrackStoppedBeforeCleanup": True,
    }


@pytest.mark.frontend
def test_manual_share_does_not_widen_after_rejecting_untrusted_restored_window(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenSourceId": "window:reused",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__desktopProvider.getSources = async () => [
                { id: 'window:reused', name: 'Unrelated Browser', display_id: '' },
                { id: 'screen:1', name: 'Entire Screen', display_id: '1' },
            ];
            const captureCalls = [];
            const track = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const screenStream = {
                active: true,
                getVideoTracks() { return [track]; },
                getTracks() { return [track]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return screenStream;
                    },
                },
            });

            await window.startScreenSharing();
            const state = {
                captureCalls,
                selectedId: window.appState.selectedScreenSourceId,
                screenStreamInstalled:
                    window.appState.screenCaptureStream === screenStream,
                trackStoppedBeforeCleanup: track.stopped,
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "captureCalls": [],
        "selectedId": None,
        "screenStreamInstalled": False,
        "trackStoppedBeforeCleanup": False,
    }


@pytest.mark.frontend
def test_manual_timeout_cleanup_does_not_mask_pre_enumeration_hang(page: Page) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.innerHTML = `
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `;
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.showCurrentModel = () => new Promise(() => {});
            let resolveSources;
            let enumerationStarted = false;
            window.__desktopProvider.getSources = () => {
                enumerationStarted = true;
                return new Promise((resolve) => { resolveSources = resolve; });
            };

            window.startScreenSharing();
            await new Promise((resolve) => setTimeout(resolve, 20));
            const outcome = {
                enumerationStarted,
                pendingBeforeCancel: window.isScreenSharingStartPending(),
            };
            window.appScreen.cancelPendingScreenSharingStart();
            if (typeof resolveSources === 'function') {
                resolveSources([
                    { id: 'window:old', name: 'Editor', display_id: '' },
                ]);
            }
            outcome.pendingAfterCancel = window.isScreenSharingStartPending();
            return outcome;
        }"""
    )

    assert result == {
        "enumerationStarted": False,
        "pendingBeforeCancel": True,
        "pendingAfterCancel": False,
    }


@pytest.mark.frontend
def test_shared_picker_discards_stream_after_source_change(page: Page) -> None:
    _install_screen_source_harness(page)

    result = page.evaluate(
        """async () => {
            window.__desktopProvider.getSources = async () => [];
            let resolvePicker;
            let pickerStarted = false;
            const pickerTrack = {
                readyState: 'live',
                stopped: false,
                stop() { this.stopped = true; this.readyState = 'ended'; },
                addEventListener() {},
            };
            const pickerStream = {
                active: true,
                getVideoTracks() { return [pickerTrack]; },
                getTracks() { return [pickerTrack]; },
            };
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    getDisplayMedia() {
                        pickerStarted = true;
                        return new Promise((resolve) => { resolvePicker = resolve; });
                    },
                },
            });

            const acquisition = window.appScreen.acquireOrReuseCachedStream({
                allowPrompt: true,
            });
            const pickerDeadline = performance.now() + 5000;
            while (!pickerStarted && performance.now() < pickerDeadline) {
                await new Promise((resolve) => setTimeout(resolve, 0));
            }
            if (!pickerStarted) throw new Error('getDisplayMedia did not start');
            await window.selectScreenSource('window:new', 'Browser', 'Browser');
            resolvePicker(pickerStream);
            const acquired = await acquisition;
            const state = {
                selectedId: window.appState.selectedScreenSourceId,
                storedId: window.__storedValues.get('selectedScreenSourceId') ?? null,
                returnedNull: acquired === null,
                pickerInstalled: window.appState.screenCaptureStream === pickerStream,
                pickerTrackStopped: pickerTrack.stopped,
            };
            if (acquired) acquired.getTracks().forEach((track) => track.stop());
            window.appState.screenCaptureStream = null;
            return state;
        }"""
    )

    assert result == {
        "selectedId": "window:new",
        "storedId": "window:new",
        "returnedNull": True,
        "pickerInstalled": False,
        "pickerTrackStopped": True,
    }


@pytest.mark.frontend
def test_manual_preflight_adopts_newer_owned_replacement_stream(page: Page) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )
    page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            function makeTrack() {
                return {
                    readyState: 'live',
                    stopped: false,
                    stop() { this.stopped = true; this.readyState = 'ended'; },
                    addEventListener() {},
                };
            }
            function makeStream(track) {
                return {
                    active: true,
                    getVideoTracks() { return [track]; },
                    getTracks() { return [track]; },
                };
            }
            window.__oldTrack = makeTrack();
            window.__oldStream = makeStream(window.__oldTrack);
            window.__replacementTrack = makeTrack();
            window.__replacementStream = makeStream(window.__replacementTrack);
            window.__freshTrack = makeTrack();
            window.__freshStream = makeStream(window.__freshTrack);
            window.appState.screenCaptureStream = window.__oldStream;
            window.__enumerationCalls = 0;
            window.__firstEnumerationStarted = false;
            window.__desktopProvider.getSources = () => {
                window.__enumerationCalls += 1;
                if (window.__enumerationCalls === 1) {
                    window.__firstEnumerationStarted = true;
                    return new Promise((resolve) => {
                        window.__resolveFirstEnumeration = resolve;
                    });
                }
                return Promise.resolve([
                    { id: 'window:old', name: 'Editor', display_id: '' },
                ]);
            };
            window.__captureCalls = [];
            Object.defineProperty(navigator, 'mediaDevices', {
                configurable: true,
                value: {
                    async getUserMedia(constraints) {
                        window.__captureCalls.push(
                            constraints.video.mandatory.chromeMediaSourceId
                        );
                        return window.__freshStream;
                    },
                },
            });
            window.__manualStartPromise = window.startScreenSharing();
        }"""
    )
    page.wait_for_function("window.__firstEnumerationStarted === true")

    result = page.evaluate(
        """async () => {
            window.appState.screenCaptureStream = window.__replacementStream;
            window.__resolveFirstEnumeration([
                { id: 'window:old', name: 'Editor', display_id: '' },
            ]);
            await window.__manualStartPromise;
            const state = {
                captureCalls: window.__captureCalls,
                oldTrackStopped: window.__oldTrack.stopped,
                replacementInstalled:
                    window.appState.screenCaptureStream === window.__replacementStream,
                replacementTrackStopped: window.__replacementTrack.stopped,
                freshInstalled: window.appState.screenCaptureStream === window.__freshStream,
            };
            await window.stopScreenSharing(true);
            if (!window.__oldTrack.stopped) window.__oldTrack.stop();
            if (!window.__replacementTrack.stopped) window.__replacementTrack.stop();
            if (!window.__freshTrack.stopped) window.__freshTrack.stop();
            return state;
        }"""
    )

    assert result == {
        "captureCalls": [],
        "oldTrackStopped": True,
        "replacementInstalled": True,
        "replacementTrackStopped": False,
        "freshInstalled": False,
    }


@pytest.mark.frontend
def test_native_remap_during_first_frame_keeps_manual_controls_owned(page: Page) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
            "selectedScreenSourceId": "window:old",
        },
    )
    page.evaluate(
        """() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__nativeSources = [
                { id: 'window:old', name: 'Editor', display_id: '' },
            ];
            window.__nativeCaptureCalls = [];
            window.__oldNativeCaptureStarted = false;
            window.__desktopProvider.nativeFrameCapture = true;
            window.__desktopProvider.getSources = async () => window.__nativeSources;
            window.__desktopProvider.captureSourceAsDataUrl = (sourceId) => {
                window.__nativeCaptureCalls.push(sourceId);
                if (sourceId === 'window:old') {
                    window.__oldNativeCaptureStarted = true;
                    return new Promise((resolve) => {
                        window.__resolveOldNativeCapture = resolve;
                    });
                }
                return Promise.resolve({
                    success: true,
                    dataUrl: 'data:image/jpeg;base64,AA==',
                });
            };
            window.__nativeSent = [];
            window.appState.socket = {
                readyState: WebSocket.OPEN,
                send(payload) { window.__nativeSent.push(JSON.parse(payload)); },
            };
            window.__nativeStartPromise = window.startScreenSharing();
        }"""
    )
    page.wait_for_function("window.__oldNativeCaptureStarted === true")

    result = page.evaluate(
        """async () => {
            window.__nativeSources = [
                { id: 'window:old', name: 'Unrelated Browser', display_id: '' },
                { id: 'window:new', name: 'Editor', display_id: '' },
            ];
            window.appScreen.reconcileRememberedWindowSource(window.__nativeSources);
            window.__resolveOldNativeCapture({
                success: true,
                dataUrl: 'data:image/jpeg;base64,AA==',
            });
            await window.__nativeStartPromise;
            const controlDeadline = performance.now() + 2000;
            while (document.getElementById('stopButton').disabled
                && performance.now() < controlDeadline) {
                await new Promise((resolve) => setTimeout(resolve, 0));
            }
            const state = {
                firstCaptureId: window.__nativeCaptureCalls[0] ?? null,
                newCaptureCount: window.__nativeCaptureCalls.filter(
                    (sourceId) => sourceId === 'window:new'
                ).length,
                selectedId: window.appState.selectedScreenSourceId,
                sentCount: window.__nativeSent.length,
                senderScheduled: window.appState.videoSenderInterval != null,
                stopDisabled: document.getElementById('stopButton').disabled,
                screenActive: document.getElementById('screenButton').classList.contains(
                    'active'
                ),
            };
            await window.stopScreenSharing(true);
            return state;
        }"""
    )

    assert result == {
        "firstCaptureId": "window:old",
        "newCaptureCount": 1,
        "selectedId": "window:new",
        "sentCount": 1,
        "senderScheduled": True,
        "stopDisabled": False,
        "screenActive": True,
    }


@pytest.mark.frontend
def test_native_manual_share_bounds_default_lookup_for_remembered_title(
    page: Page,
) -> None:
    _install_screen_source_harness(
        page,
        initial_storage={
            "screenSourceTitleMatchEnabled": "true",
            "selectedScreenWindowTitle": "Editor",
        },
    )

    result = page.evaluate(
        """async () => {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="live2d-container"></div>
                <button id="micButton"></button><button id="muteButton"></button>
                <button id="screenButton"></button><button id="stopButton" disabled></button>
                <button id="resetSessionButton"></button>
            `);
            window.appState.isRecording = true;
            window.appState.voiceChatActive = true;
            window.appState.audioPlayerContext = { state: 'running' };
            window.__desktopProvider.nativeFrameCapture = true;
            window.__desktopProvider.captureSourceAsDataUrl = async () => ({
                success: true,
                dataUrl: 'data:image/jpeg;base64,AA==',
            });
            let defaultLookupStarted = false;
            window.__desktopProvider.getSources = () => {
                defaultLookupStarted = true;
                return new Promise(() => {});
            };
            let boundedValidationCalls = 0;
            window.invokeDesktopCaptureWithTimeout = async () => {
                boundedValidationCalls += 1;
                return [];
            };
            let settled = false;
            window.startScreenSharing().finally(() => { settled = true; });
            await new Promise((resolve) => setTimeout(resolve, 30));
            const state = {
                defaultLookupStarted,
                boundedValidationCalls,
                settled,
                pending: window.isScreenSharingStartPending(),
            };
            if (!settled) window.appScreen.cancelPendingScreenSharingStart();
            return state;
        }"""
    )

    assert result == {
        "defaultLookupStarted": False,
        "boundedValidationCalls": 1,
        "settled": True,
        "pending": False,
    }
