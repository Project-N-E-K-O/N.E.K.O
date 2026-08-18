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
