import pytest
from playwright.sync_api import Page


def _open_character_card_manager(page: Page, running_server: str) -> None:
    page.goto(f"{running_server}/character_card_manager")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("body")


def _mount_steam_preview_dom(page: Page) -> None:
    page.evaluate(
        """
        () => {
            const existing = document.getElementById('regression-steam-host');
            if (existing) {
                existing.remove();
            }

            const host = document.createElement('div');
            host.id = 'regression-steam-host';
            host.style.width = '960px';
            host.style.margin = '0 auto';
            document.body.appendChild(host);

            window.__messages = [];
            window.__consoleErrors = [];
            window.__consoleWarnings = [];
            window.showMessage = (message, type) => {
                window.__messages.push({
                    message: String(message || ''),
                    type: String(type || '')
                });
            };
            console.error = (...args) => {
                window.__consoleErrors.push(args.map(arg => String(arg)).join(' '));
            };
            console.warn = (...args) => {
                window.__consoleWarnings.push(args.map(arg => String(arg)).join(' '));
            };

            buildSteamTabContent('RegressionCard', {}, null, host);

            const previewContainer = document.getElementById('live2d-preview-container');
            const previewContent = document.getElementById('live2d-preview-content');
            const previewCanvas = document.getElementById('live2d-preview-canvas');

            if (previewContainer) {
                previewContainer.style.height = '360px';
            }
            if (previewContent) {
                previewContent.style.width = '360px';
                previewContent.style.height = '360px';
                Object.defineProperty(previewContent, 'clientWidth', {
                    configurable: true,
                    get: () => 360
                });
                Object.defineProperty(previewContent, 'clientHeight', {
                    configurable: true,
                    get: () => 360
                });
            }
            if (previewCanvas) {
                Object.defineProperty(previewCanvas, 'clientWidth', {
                    configurable: true,
                    get: () => 360
                });
                Object.defineProperty(previewCanvas, 'clientHeight', {
                    configurable: true,
                    get: () => 360
                });
            }
        }
        """
    )


def _install_preview_stubs(page: Page, load_delay_ms: int = 0) -> None:
    page.evaluate(
        """
        (loadDelayMs) => {
            const originalFetch = window.fetch.bind(window);
            live2dPreviewManager = null;
            currentPreviewModel = null;
            window._previewMotionFiles = [];

            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : input.url;

                if (url.includes('/api/live2d/model_files_by_id/steam123')) {
                    return new Response(JSON.stringify({
                        success: true,
                        motion_files: ['motions/idle.motion3.json', 'motions/cry.motion3.json'],
                        expression_files: ['expressions/smile.exp3.json', 'expressions/hide_tail.exp3.json']
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                if (url.endsWith('/workshop/steam123/ATLS/ATLS.model3.json')) {
                    return new Response(JSON.stringify({
                        FileReferences: {
                            Motions: {},
                            Expressions: []
                        }
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                return originalFetch(input, init);
            };

            class FakeLive2DManager {
                constructor() {
                    window.__managerSequence = (window.__managerSequence || 0) + 1;
                    this.instanceId = window.__managerSequence;
                    this.currentModel = null;
                    this.pixi_app = this._createPixiApp('live2d-preview-canvas');
                }

                _createPixiApp(canvasId) {
                    return {
                        view: document.getElementById(canvasId),
                        stage: {},
                        renderer: {
                            screen: { width: 360, height: 360 },
                            resize(width, height) {
                                this.screen = { width, height };
                            },
                            render() {}
                        },
                        destroy() {
                            this.destroyed = true;
                        }
                    };
                }

                async initPIXI(canvasId) {
                    this.pixi_app = this._createPixiApp(canvasId);
                }

                async ensurePIXIReady(canvasId) {
                    if (!this.pixi_app) {
                        this.pixi_app = this._createPixiApp(canvasId);
                        return;
                    }
                    this.pixi_app.view = document.getElementById(canvasId);
                }

                async rebuildPIXI(canvasId) {
                    this.pixi_app = this._createPixiApp(canvasId);
                }

                async removeModel() {
                    this.currentModel = null;
                }

                async loadModel() {
                    await new Promise(resolve => setTimeout(resolve, loadDelayMs));
                    const model = {
                        anchor: { set() {} },
                        scale: {
                            x: 1,
                            y: 1,
                            set(nextX, nextY) {
                                if (typeof nextY === 'number') {
                                    this.x = nextX;
                                    this.y = nextY;
                                    return;
                                }
                                this.x = nextX;
                                this.y = nextX;
                            }
                        },
                        x: 0,
                        y: 0,
                        motionCalls: [],
                        expressionCalls: [],
                        parent: {},
                        getBounds() {
                            return { x: 0, y: 0, width: 120, height: 220 };
                        },
                        motion(group, index, priority) {
                            this.motionCalls.push({ group, index, priority });
                        },
                        expression(name) {
                            this.expressionCalls.push(name);
                        }
                    };
                    this.currentModel = model;
                    return model;
                }

                applyModelSettings() {}
            }

            window.Live2DManager = FakeLive2DManager;
            Live2DManager = FakeLive2DManager;
            window.ensureVrmModulesLoaded = async () => true;
            window.ensureMmdModulesLoaded = async () => true;
            window.VRMManager = class FakeVrmManager {
                constructor() {
                    this.renderer = {
                        setSize() {}
                    };
                    this.camera = {
                        aspect: 1,
                        updateProjectionMatrix() {}
                    };
                }

                async initThreeJS() {}

                async loadModel() {
                    return { name: 'fake-vrm-model' };
                }

                async dispose() {}
            };
            window.MMDManager = class FakeMmdManager {
                constructor() {
                    this.renderer = {
                        setSize() {}
                    };
                    this.camera = {
                        aspect: 1,
                        updateProjectionMatrix() {}
                    };
                }

                async init() {}

                async loadModel() {
                    return { name: 'fake-mmd-model' };
                }

                async loadAnimation() {}

                playAnimation() {}

                async dispose() {}
            };
        }
        """,
        load_delay_ms,
    )


@pytest.mark.frontend
def test_character_card_manager_renders_subscribed_preview_image_url_fallback(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        () => {
            const subscriptionsList = document.getElementById('subscriptions-list')
                || (() => {
                    const element = document.createElement('div');
                    element.id = 'subscriptions-list';
                    document.body.appendChild(element);
                    return element;
                })();

            allSubscriptions = [{
                publishedFileId: '42',
                title: 'Workshop Asset',
                authorName: 'Alice',
                previewUrl: '',
                previewImageUrl: '/api/steam/proxy-image?image_path=preview.png',
                timeAdded: 1710000000,
                timeUpdated: 1710001000,
                fileSizeOnDisk: 2048,
                state: { installed: true }
            }];
            currentPage = 1;
            itemsPerPage = 10;
            totalPages = 1;

            renderSubscriptionsPage();

            const cardImage = subscriptionsList.querySelector('.workshop-card .card-image');
            const cardTitle = subscriptionsList.querySelector('.workshop-card .card-title');

            return {
                imageSrc: cardImage ? cardImage.getAttribute('src') : '',
                titleText: cardTitle ? cardTitle.textContent : ''
            };
        }
        """
    )

    assert state["imageSrc"] == "/api/steam/proxy-image?image_path=preview.png"
    assert "Workshop Asset" in state["titleText"]


@pytest.mark.frontend
def test_character_card_manager_creates_tag_scroll_buttons_for_dynamic_wrapper(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)
    _mount_steam_preview_dom(mock_page)

    state = mock_page.evaluate(
        """
        async () => {
            const wrapper = document.getElementById('character-card-tags-wrapper');
            const tagsContainer = document.getElementById('character-card-tags-container');
            const maxScrollLeft = 240;
            let scrollLeftValue = 0;

            Object.defineProperty(wrapper, 'clientWidth', {
                configurable: true,
                get: () => 180
            });
            Object.defineProperty(wrapper, 'scrollWidth', {
                configurable: true,
                get: () => 420
            });
            Object.defineProperty(wrapper, 'scrollLeft', {
                configurable: true,
                get: () => scrollLeftValue,
                set: value => {
                    scrollLeftValue = value;
                }
            });

            wrapper.scrollBy = ({ left }) => {
                scrollLeftValue = Math.max(0, Math.min(maxScrollLeft, scrollLeftValue + left));
            };

            ['tag-one', 'tag-two', 'tag-three', 'tag-four'].forEach(tag => {
                addCharacterCardTag('character-card', tag);
            });

            updateCharacterCardTagScrollControls();

            const leftButton = document.getElementById('character-card-tags-scroll-left');
            const rightButton = document.getElementById('character-card-tags-scroll-right');

            const snapshot = () => ({
                leftDisabled: !!leftButton.disabled,
                rightDisabled: !!rightButton.disabled,
                leftHidden: leftButton.classList.contains('is-hidden'),
                rightHidden: rightButton.classList.contains('is-hidden'),
                scrollLeft: scrollLeftValue
            });

            const start = snapshot();
            rightButton.click();
            await new Promise(resolve => setTimeout(resolve, 260));
            const afterFirstScroll = snapshot();
            rightButton.click();
            await new Promise(resolve => setTimeout(resolve, 260));
            const afterSecondScroll = snapshot();

            return {
                tagCount: tagsContainer.querySelectorAll('.tag').length,
                start,
                afterFirstScroll,
                afterSecondScroll
            };
        }
        """
    )

    assert state["tagCount"] == 4
    assert state["start"]["leftDisabled"] is True
    assert state["start"]["rightDisabled"] is False
    assert state["start"]["leftHidden"] is False
    assert state["start"]["rightHidden"] is False
    assert state["afterFirstScroll"]["scrollLeft"] > 0
    assert state["afterFirstScroll"]["leftDisabled"] is False
    assert state["afterSecondScroll"]["rightDisabled"] is True


@pytest.mark.frontend
def test_character_card_manager_renders_and_opens_cards_when_model_scan_never_resolves(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        async () => {
            const originalFetch = window.fetch.bind(window);
            let live2dModelScanRequests = 0;

            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : input.url;

                if (url.endsWith('/api/live2d/models')) {
                    live2dModelScanRequests += 1;
                    return new Promise(() => {});
                }

                if (url.endsWith('/api/model/vrm/models')) {
                    return new Response(JSON.stringify({ success: true, models: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                if (url.endsWith('/api/model/mmd/models')) {
                    return new Response(JSON.stringify({ success: true, models: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                if (url.endsWith('/api/characters/')) {
                    return new Response(JSON.stringify({
                        '主人': {},
                        '当前猫娘': '模拟猫娘',
                        '猫娘': {
                            '模拟猫娘': {
                                '档案名': '模拟猫娘',
                                'description': '迁移后角色管理应能直接显示',
                                '关键词': ['迁移', '回归']
                            }
                        }
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                if (url.endsWith('/api/characters/character-card/list')) {
                    return new Response(JSON.stringify({ success: true, character_cards: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                if (url.endsWith('/api/characters/current_catgirl')) {
                    return new Response(JSON.stringify({ current_catgirl: '模拟猫娘' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                if (url.endsWith('/api/characters/card-faces')) {
                    return new Response(JSON.stringify({ success: true, names: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                if (url.endsWith('/api/characters/card-metas')) {
                    return new Response(JSON.stringify({ success: true, metas: {} }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                return originalFetch(input, init);
            };

            const startedAt = performance.now();
            const loadResult = await Promise.race([
                loadCharacterCards().then(() => 'resolved'),
                new Promise(resolve => setTimeout(() => resolve('timeout'), 900))
            ]);
            const elapsedMs = performance.now() - startedAt;

            const card = document.querySelector('.chara-card-item');
            card?.click();
            await new Promise(resolve => setTimeout(resolve, 80));

            return {
                loadResult,
                elapsedMs,
                live2dModelScanRequests,
                cardCount: document.querySelectorAll('.chara-card-item').length,
                cardName: card?.querySelector('.card-name')?.textContent || '',
                selectExists: !!document.querySelector('#character-card-select'),
                selectOptions: Array.from(document.querySelectorAll('#character-card-select option'))
                    .map(option => option.textContent),
                panelOpen: !!document.querySelector('.catgirl-panel-overlay'),
                profileName: document.querySelector('.catgirl-panel-overlay input[name="档案名"]')?.value || '',
                saveButtonExists: !!document.querySelector('.catgirl-panel-overlay #save-button')
            };
        }
        """
    )

    assert state["loadResult"] == "resolved"
    assert state["elapsedMs"] < 900
    assert state["live2dModelScanRequests"] >= 1
    assert state["cardCount"] == 1
    assert state["cardName"] == "模拟猫娘"
    if state["selectExists"]:
        assert "模拟猫娘" in state["selectOptions"]
    assert state["panelOpen"] is True
    assert state["profileName"] == "模拟猫娘"
    assert state["saveButtonExists"] is True


@pytest.mark.frontend
def test_character_card_manager_live2d_preview_loads_after_regression_fixes(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)
    _mount_steam_preview_dom(mock_page)
    _install_preview_stubs(mock_page, load_delay_ms=30)

    state = mock_page.evaluate(
        """
        async () => {
            window._currentCardRawData = {
                _reserved: {
                    avatar: {
                        live2d: {
                            idle_animation: 'cry.motion3.json'
                        }
                    }
                }
            };

            await loadLive2DModelByName('ATLS', {
                name: 'ATLS',
                path: '/workshop/steam123/ATLS/ATLS.model3.json',
                item_id: 'steam123'
            });

            await new Promise(resolve => setTimeout(resolve, 180));

            const motionOptions = Array.from(
                document.querySelectorAll('#preview-motion-select option')
            ).map(option => option.value).filter(Boolean);
            const expressionOptions = Array.from(
                document.querySelectorAll('#preview-expression-select option')
            ).map(option => option.value).filter(Boolean);

            return {
                title: document.getElementById('model-preview-title')?.textContent || '',
                canvasDisplay: document.getElementById('live2d-preview-canvas')?.style.display || '',
                placeholderDisplay: document.querySelector('#live2d-preview-content .preview-placeholder')?.style.display || '',
                controlsDisplay: document.getElementById('live2d-preview-controls')?.style.display || '',
                hasCurrentModel: !!live2dPreviewManager?.currentModel,
                selectedModelName: selectedModelInfo?.name || '',
                refreshButtonDisplay: document.getElementById('live2d-refresh-btn')?.style.display || '',
                selectedMotion: document.getElementById('preview-motion-select')?.value || '',
                configuredIdleAnimations: live2dPreviewManager?._userIdleAnimations || [],
                motionCalls: currentPreviewModel?.motionCalls || [],
                motionOptions,
                expressionOptions,
                messages: window.__messages,
                consoleErrors: window.__consoleErrors
            };
        }
        """
    )

    assert state["title"] == "Live2D"
    assert state["canvasDisplay"] != "none"
    assert state["placeholderDisplay"] == "none"
    assert state["hasCurrentModel"] is True
    assert state["selectedModelName"] == "ATLS"
    assert state["refreshButtonDisplay"] == "flex"
    assert "motions/idle.motion3.json" in state["motionOptions"]
    assert "hide_tail" in state["expressionOptions"]
    assert state["selectedMotion"] == "motions/cry.motion3.json"
    assert state["configuredIdleAnimations"] == ["cry.motion3.json"]
    assert state["motionCalls"] == [{"group": "PreviewAll", "index": 1, "priority": 3}]
    assert not any(
        "Failed to load Live2D model by name" in entry
        or "Live2D preview is not ready" in entry
        for entry in state["consoleErrors"]
    )
    assert not [entry for entry in state["messages"] if entry["type"] == "error"]


@pytest.mark.frontend
def test_character_card_manager_preview_play_buttons_trigger_live2d_actions(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)
    _mount_steam_preview_dom(mock_page)
    _install_preview_stubs(mock_page, load_delay_ms=30)

    state = mock_page.evaluate(
        """
        async () => {
            await loadLive2DModelByName('ATLS', {
                name: 'ATLS',
                path: '/workshop/steam123/ATLS/ATLS.model3.json',
                item_id: 'steam123'
            });

            await new Promise(resolve => setTimeout(resolve, 180));

            window.__previewMotionCalls = [];
            window.__previewExpressionCalls = [];

            currentPreviewModel.motion = (group, index, priority) => {
                window.__previewMotionCalls.push({ group, index, priority });
            };
            currentPreviewModel.expression = (name) => {
                window.__previewExpressionCalls.push(name);
            };

            document.getElementById('preview-motion-select').value = 'motions/cry.motion3.json';
            document.getElementById('preview-expression-select').value = 'hide_tail';
            document.getElementById('preview-play-motion-btn').click();
            document.getElementById('preview-play-expression-btn').click();

            return {
                motionCalls: window.__previewMotionCalls || [],
                expressionCalls: window.__previewExpressionCalls || [],
                messages: window.__messages,
                consoleErrors: window.__consoleErrors
            };
        }
        """
    )

    assert state["motionCalls"] == [{"group": "PreviewAll", "index": 1, "priority": 3}]
    assert state["expressionCalls"] == ["hide_tail"]
    assert not any(
        "Failed to play motion:" in entry
        or "Failed to play expression:" in entry
        for entry in state["consoleErrors"]
    )
    assert not [entry for entry in state["messages"] if entry["type"] == "error"]


@pytest.mark.frontend
@pytest.mark.parametrize(
    ("switch_target", "model_path", "expected_title", "expected_visible_container"),
    (
        ("vrm", "/static/vrm/Fake/Fake.vrm", "VRM", "vrm-preview-container"),
        ("mmd", "/static/mmd/Fake/Fake.pmx", "MMD", "mmd-preview-container"),
    ),
)
def test_character_card_manager_cancels_stale_live2d_when_switching_to_3d_preview(
    mock_page: Page,
    running_server: str,
    switch_target: str,
    model_path: str,
    expected_title: str,
    expected_visible_container: str,
):
    _open_character_card_manager(mock_page, running_server)
    _mount_steam_preview_dom(mock_page)
    _install_preview_stubs(mock_page, load_delay_ms=180)

    state = mock_page.evaluate(
        """
        async ({ switchTarget, modelPath }) => {
            const live2dPromise = loadLive2DModelByName('ATLS', {
                name: 'ATLS',
                path: '/workshop/steam123/ATLS/ATLS.model3.json',
                item_id: 'steam123'
            });

            const switchPromise = switchTarget === 'vrm'
                ? loadVrmPreview(modelPath, {})
                : loadMmdPreview(modelPath, {});

            await Promise.allSettled([live2dPromise, switchPromise]);
            await new Promise(resolve => setTimeout(resolve, 260));

            return {
                title: document.getElementById('model-preview-title')?.textContent || '',
                live2dCanvasDisplay: document.getElementById('live2d-preview-canvas')?.style.display || '',
                refreshButtonDisplay: document.getElementById('live2d-refresh-btn')?.style.display || '',
                vrmDisplay: document.getElementById('vrm-preview-container')?.style.display || '',
                mmdDisplay: document.getElementById('mmd-preview-container')?.style.display || '',
                hasCurrentLive2dModel: !!live2dPreviewManager?.currentModel,
                hasCurrentPreviewModel: !!currentPreviewModel,
                selectedModelName: selectedModelInfo?.name || '',
                messages: window.__messages,
                consoleErrors: window.__consoleErrors
            };
        }
        """,
        {
            "switchTarget": switch_target,
            "modelPath": model_path,
        },
    )

    assert state["title"] == expected_title
    assert state["live2dCanvasDisplay"] == "none"
    assert state["refreshButtonDisplay"] == "none"
    assert state["hasCurrentLive2dModel"] is False
    assert state["hasCurrentPreviewModel"] is False
    assert state["selectedModelName"] == ""
    assert state["vrmDisplay"] == ("block" if expected_visible_container == "vrm-preview-container" else "none")
    assert state["mmdDisplay"] == ("block" if expected_visible_container == "mmd-preview-container" else "none")
    assert not any(
        "Failed to load Live2D model by name" in entry
        or "[Workshop VRM] 加载预览失败:" in entry
        or "[Workshop MMD] 加载预览失败:" in entry
        for entry in state["consoleErrors"]
    )


@pytest.mark.frontend
def test_character_card_manager_clear_preview_resets_refresh_state(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)
    _mount_steam_preview_dom(mock_page)
    _install_preview_stubs(mock_page, load_delay_ms=30)

    state = mock_page.evaluate(
        """
        async () => {
            await loadLive2DModelByName('ATLS', {
                name: 'ATLS',
                path: '/workshop/steam123/ATLS/ATLS.model3.json',
                item_id: 'steam123'
            });

            await new Promise(resolve => setTimeout(resolve, 180));
            await clearAllModelPreviews(true);

            return {
                canvasDisplay: document.getElementById('live2d-preview-canvas')?.style.display || '',
                placeholderDisplay: document.querySelector('#live2d-preview-content .preview-placeholder')?.style.display || '',
                refreshButtonDisplay: document.getElementById('live2d-refresh-btn')?.style.display || '',
                selectedModelName: selectedModelInfo?.name || '',
                hasCurrentPreviewModel: !!currentPreviewModel,
                messages: window.__messages,
                consoleErrors: window.__consoleErrors
            };
        }
        """
    )

    assert state["canvasDisplay"] == "none"
    assert state["placeholderDisplay"] == "flex"
    assert state["refreshButtonDisplay"] == "none"
    assert state["selectedModelName"] == ""
    assert state["hasCurrentPreviewModel"] is False
    assert not any(
        "清除Live2D预览失败:" in entry
        for entry in state["consoleErrors"]
    )
    assert not [entry for entry in state["messages"] if entry["type"] == "error"]


@pytest.mark.frontend
def test_character_card_manager_panel_close_recreates_live2d_preview_context(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)
    _install_preview_stubs(mock_page, load_delay_ms=0)

    state = mock_page.evaluate(
        """
        async () => {
            window.__messages = [];
            window.__consoleErrors = [];
            window.__consoleWarnings = [];
            window.showMessage = (message, type) => {
                window.__messages.push({
                    message: String(message || ''),
                    type: String(type || '')
                });
            };
            console.error = (...args) => {
                window.__consoleErrors.push(args.map(arg => String(arg)).join(' '));
            };
            console.warn = (...args) => {
                window.__consoleWarnings.push(args.map(arg => String(arg)).join(' '));
            };

            const mountPanelPreview = () => {
                const existing = document.querySelector('.catgirl-panel-overlay');
                if (existing) {
                    existing.remove();
                }

                const overlay = document.createElement('div');
                overlay.className = 'catgirl-panel-overlay active';
                const wrapper = document.createElement('div');
                wrapper.className = 'catgirl-panel-wrapper phase-expand';
                overlay.appendChild(wrapper);

                const host = document.createElement('div');
                host.id = 'regression-steam-host';
                host.style.width = '960px';
                host.style.margin = '0 auto';
                wrapper.appendChild(host);
                document.body.appendChild(overlay);

                buildSteamTabContent('RegressionCard', {}, null, host);

                const previewContainer = document.getElementById('live2d-preview-container');
                const previewContent = document.getElementById('live2d-preview-content');
                const previewCanvas = document.getElementById('live2d-preview-canvas');

                if (previewContainer) {
                    previewContainer.style.height = '360px';
                }
                if (previewContent) {
                    previewContent.style.width = '360px';
                    previewContent.style.height = '360px';
                    Object.defineProperty(previewContent, 'clientWidth', {
                        configurable: true,
                        get: () => 360
                    });
                    Object.defineProperty(previewContent, 'clientHeight', {
                        configurable: true,
                        get: () => 360
                    });
                }
                if (previewCanvas) {
                    Object.defineProperty(previewCanvas, 'clientWidth', {
                        configurable: true,
                        get: () => 360
                    });
                    Object.defineProperty(previewCanvas, 'clientHeight', {
                        configurable: true,
                        get: () => 360
                    });
                }
            };

            mountPanelPreview();
            await loadLive2DModelByName('ATLS', {
                name: 'ATLS',
                path: '/workshop/steam123/ATLS/ATLS.model3.json',
                item_id: 'steam123'
            });
            await new Promise(resolve => setTimeout(resolve, 120));

            const firstManagerId = live2dPreviewManager?.instanceId || null;
            const firstCanvas = document.getElementById('live2d-preview-canvas');
            const firstPixiApp = live2dPreviewManager?.pixi_app || null;

            await closeCatgirlPanel();

            const managerAfterClose = live2dPreviewManager;
            const firstCanvasConnectedAfterClose = firstCanvas ? firstCanvas.isConnected : null;
            const firstPixiAppDestroyed = firstPixiApp ? firstPixiApp.destroyed === true : null;

            mountPanelPreview();
            await loadLive2DModelByName('ATLS', {
                name: 'ATLS',
                path: '/workshop/steam123/ATLS/ATLS.model3.json',
                item_id: 'steam123'
            });
            await new Promise(resolve => setTimeout(resolve, 120));

            return {
                firstManagerId,
                secondManagerId: live2dPreviewManager?.instanceId || null,
                managerClearedOnClose: managerAfterClose === null,
                firstCanvasConnectedAfterClose,
                firstPixiAppDestroyed,
                hasCurrentModelAfterReopen: !!live2dPreviewManager?.currentModel,
                hasCurrentPreviewModelAfterReopen: !!currentPreviewModel,
                canvasDisplayAfterReopen: document.getElementById('live2d-preview-canvas')?.style.display || '',
                refreshButtonDisplayAfterReopen: document.getElementById('live2d-refresh-btn')?.style.display || '',
                consoleErrors: window.__consoleErrors,
                messages: window.__messages
            };
        }
        """
    )

    assert state["firstManagerId"] is not None
    assert state["secondManagerId"] is not None
    assert state["firstManagerId"] != state["secondManagerId"]
    assert state["managerClearedOnClose"] is True
    assert state["firstCanvasConnectedAfterClose"] is False
    assert state["firstPixiAppDestroyed"] is True
    assert state["hasCurrentModelAfterReopen"] is True
    assert state["hasCurrentPreviewModelAfterReopen"] is True
    assert state["canvasDisplayAfterReopen"] != "none"
    assert state["refreshButtonDisplayAfterReopen"] == "flex"
    assert not any(
        "清除Live2D预览失败:" in entry
        or "Failed to initialize Live2D preview:" in entry
        or "Failed to load Live2D model by name:" in entry
        for entry in state["consoleErrors"]
    )
    assert not [entry for entry in state["messages"] if entry["type"] == "error"]
