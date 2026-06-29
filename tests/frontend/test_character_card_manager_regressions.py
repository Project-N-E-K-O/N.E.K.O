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
def test_character_card_manager_voice_dropdown_prefers_clone_prefix(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        async () => {
            const originalFetch = window.fetch.bind(window);
            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                const path = new URL(url, window.location.origin).pathname;

                if (path === '/api/characters/voices') {
                    return new Response(JSON.stringify({
                        voices: {
                            customabc123: {
                                voice_id: 'customabc123',
                                prefix: 'Sweet01',
                                name: 'customabc123',
                                provider: 'minimax'
                            },
                            customnameonly: {
                                voice_id: 'customnameonly',
                                name: 'Readable Name',
                                provider: 'cosyvoice'
                            }
                        },
                        free_voices: {},
                        native_voices: {},
                        voice_owners: {
                            customabc123: ['缓存猫娘']
                        }
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                if (path === '/api/characters/custom_tts_voices') {
                    return new Response(JSON.stringify({ success: true, voices: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }

                return originalFetch(input, init);
            };

            const select = document.createElement('select');
            document.body.appendChild(select);
            const ui = _panelCreateVoiceSelectUi(select);
            document.body.appendChild(ui.container);

            await _loadPanelVoices(select, 'customabc123');
            ui.refresh();

            const optionTexts = Array.from(select.options).map(option => ({
                value: option.value,
                text: option.textContent
            }));

            return {
                selectedText: ui.container.querySelector('.voice-select-selected')?.textContent || '',
                optionTexts
            };
        }
        """
    )

    assert state["selectedText"] == "Sweet01"
    assert {"value": "customabc123", "text": "Sweet01"} in state["optionTexts"]
    assert {"value": "customnameonly", "text": "Readable Name"} in state["optionTexts"]


@pytest.mark.frontend
def test_character_card_manager_voice_dropdown_groups_by_provider_source(
    mock_page: Page,
    running_server: str,
):
    """source-first voice picking (§5): voices grouped by "<Provider> · source".

    - registered clones grouped per provider (MiniMax / ElevenLabs clones do not mix);
    - free presets -> "Free · preset"; native -> "<Provider> · preset";
    - each source group carries data-voice-source-group and a label containing "·".
    """
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        async () => {
            const originalFetch = window.fetch.bind(window);
            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                const path = new URL(url, window.location.origin).pathname;
                if (path === '/api/characters/voices') {
                    return new Response(JSON.stringify({
                        voices: {
                            mm1: { voice_id: 'mm1', prefix: 'MM Voice', provider: 'minimax' },
                            el1: { voice_id: 'el1', prefix: 'EL Voice', provider: 'elevenlabs' }
                        },
                        free_voices: { playfulGirl: 'voice-tone-FREE1' },
                        native_voices: {
                            nativePuck: { prefix: 'Puck', provider: 'gemini', provider_label: 'Gemini' }
                        }
                    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
                }
                if (path === '/api/characters/custom_tts_voices') {
                    return new Response(JSON.stringify({ success: true, voices: [] }), {
                        status: 200, headers: { 'Content-Type': 'application/json' }
                    });
                }
                return originalFetch(input, init);
            };

            const select = document.createElement('select');
            document.body.appendChild(select);
            await _loadPanelVoices(select, '');

            const groups = Array.from(select.querySelectorAll('optgroup')).map(g => ({
                label: g.label,
                source: g.dataset.voiceSourceGroup || '',
                values: Array.from(g.querySelectorAll('option')).map(o => o.value)
            }));
            return { groups };
        }
        """
    )

    groups = state["groups"]
    # 克隆按 provider 分两组，互不混
    mm = next((g for g in groups if "MiniMax" in g["label"]), None)
    el = next((g for g in groups if "ElevenLabs" in g["label"]), None)
    assert mm and mm["source"] == "clone" and mm["values"] == ["mm1"]
    assert el and el["source"] == "clone" and el["values"] == ["el1"]
    assert "·" in mm["label"] and "·" in el["label"]
    # 免费预制组（值为 free voice_id）
    free = next((g for g in groups if "voice-tone-FREE1" in g["values"]), None)
    assert free and free["source"] == "preset" and "·" in free["label"]
    # native 预制组（Gemini · 预制）
    native = next((g for g in groups if "Gemini" in g["label"]), None)
    assert native and native["source"] == "preset" and "·" in native["label"]


@pytest.mark.frontend
def test_character_card_manager_localizes_free_api_native_voice_provider_label(
    mock_page: Page,
    running_server: str,
):
    """Backend catalog labels can be Chinese; the character voice picker localizes the provider name."""
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        async () => {
            window.t = (key) => ({
                'voice.providerFreeApi': 'Free API',
                'voice.providerFree': 'Free',
                'voice.providerUnknown': 'Other',
                'voice.sourcePreset': 'Preset',
                'voice.sourceClone': 'Clone',
                'voice.sourceDesign': 'Voice Design',
                'voice.nativeVoice.qingchunshaonv': 'Youthful Girl',
                'voice.nativeVoice.wenrounansheng': 'Gentle Male Voice'
            }[key] || key);

            const originalFetch = window.fetch.bind(window);
            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                const path = new URL(url, window.location.origin).pathname;
                if (path === '/api/characters/voices') {
                    return new Response(JSON.stringify({
                        voices: {},
                        free_voices: {},
                        native_voices: {
                            qingchunshaonv: {
                                prefix: '青春少女',
                                provider: 'free',
                                provider_label: '免费 API'
                            },
                            wenrounansheng: {
                                prefix: '温柔男声',
                                provider: 'free',
                                provider_label: '免费 API'
                            }
                        }
                    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
                }
                if (path === '/api/characters/custom_tts_voices') {
                    return new Response(JSON.stringify({ success: true, voices: [] }), {
                        status: 200, headers: { 'Content-Type': 'application/json' }
                    });
                }
                return originalFetch(input, init);
            };

            const select = document.createElement('select');
            document.body.appendChild(select);
            await _loadPanelVoices(select, '');
            const labels = Array.from(select.querySelectorAll('optgroup')).map(group => group.label);
            const optionTexts = Array.from(select.querySelectorAll('option')).map(option => option.textContent);
            return { labels, optionTexts };
        }
        """
    )

    assert "Free API · Preset" in state["labels"]
    assert all("免费 API" not in label for label in state["labels"])
    assert "Youthful Girl" in state["optionTexts"]
    assert "Gentle Male Voice" in state["optionTexts"]
    assert "青春少女" not in state["optionTexts"]
    assert "温柔男声" not in state["optionTexts"]


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

                if (url.endsWith('/api/characters/') || url.endsWith('/api/characters')) {
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
def test_character_card_manager_localizes_master_profile_builtin_field_labels(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        () => {
            window.t = (key) => {
                const translations = {
                    'character.profileName': 'Profile Name',
                    'character.required': '*',
                    'character.rename': 'Rename',
                    'character.renameMasterTitle': 'Rename My Profile',
                    'character.deleteField': 'Delete Field',
                    'character.addMasterField': 'Add Field',
                    'character.saveMaster': 'Save My Profile',
                    'character.cancel': 'Cancel',
                    'characterProfile.labels.昵称': 'Nickname',
                    'characterProfile.labels.性别': 'Gender'
                };
                return Object.prototype.hasOwnProperty.call(translations, key) ? translations[key] : key;
            };

            renderMasterForm({
                '档案名': 'Master',
                '昵称': 'Yuki',
                '性别': 'Female',
                '喜欢的食物': 'cookies'
            });

            const rows = Array.from(document.querySelectorAll('#master-form .field-row-wrapper'));
            return rows.map(row => ({
                label: row.querySelector('label')?.textContent || '',
                name: row.querySelector('input, textarea')?.getAttribute('name') || '',
                value: row.querySelector('input, textarea')?.value || ''
            }));
        }
        """
    )

    by_name = {row["name"]: row for row in state}
    assert by_name["档案名"]["label"].startswith("Profile Name")
    assert by_name["昵称"]["label"] == "Nickname"
    assert by_name["性别"]["label"] == "Gender"
    assert by_name["喜欢的食物"]["label"] == "喜欢的食物"
    assert by_name["昵称"]["value"] == "Yuki"
    assert by_name["喜欢的食物"]["value"] == "cookies"


@pytest.mark.frontend
def test_character_card_manager_saved_new_field_survives_immediate_reopen_with_stale_reload(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        async () => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const waitFor = async (predicate, timeout = 2500) => {
                const startedAt = Date.now();
                while (Date.now() - startedAt < timeout) {
                    if (predicate()) return true;
                    await sleep(25);
                }
                return false;
            };

            const originalFetch = window.fetch.bind(window);
            const staleCharacters = {
                '主人': {},
                '当前猫娘': '缓存猫娘',
                '猫娘': {
                    '缓存猫娘': {
                        '描述': '旧描述'
                    }
                }
            };
            const savedBodies = [];
            const characterFetchCaches = [];

            window.showMessage = () => {};
            window.showAutoSaveToast = () => {};
            window.showPrompt = async () => '追加设定';
            window.showAlert = async () => {};
            window.showAlertDialog = async () => {};
            window.fetch = async (input, init = {}) => {
                const rawUrl = typeof input === 'string' ? input : input.url;
                const url = new URL(rawUrl, window.location.origin);
                const path = decodeURIComponent(url.pathname);
                const method = String(init.method || 'GET').toUpperCase();

                if (path === '/api/characters/catgirl/缓存猫娘' && method === 'PUT') {
                    savedBodies.push(JSON.parse(init.body || '{}'));
                    return new Response(JSON.stringify({ success: true }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters' && method === 'GET') {
                    characterFetchCaches.push(init.cache || '');
                    return new Response(JSON.stringify(staleCharacters), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/character-card/list') {
                    return new Response(JSON.stringify({ success: true, character_cards: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/current_catgirl') {
                    return new Response(JSON.stringify({ current_catgirl: '缓存猫娘' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/card-faces') {
                    return new Response(JSON.stringify({ success: true, names: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/card-metas') {
                    return new Response(JSON.stringify({ success: true, metas: {} }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/voices') {
                    return new Response(JSON.stringify({ voices: {}, free_voices: {}, voice_owners: {} }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/custom_tts_voices') {
                    return new Response(JSON.stringify({ success: true, voices: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/live2d/models') {
                    return new Response(JSON.stringify([]), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/model/vrm/models' || path === '/api/model/mmd/models') {
                    return new Response(JSON.stringify({ success: true, models: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                return originalFetch(input, init);
            };

            window.characterCards = [{
                id: 1,
                name: '缓存猫娘',
                originalName: '缓存猫娘',
                description: '旧描述',
                tags: [],
                rawData: { '描述': '旧描述' }
            }];
            window._workshopCurrentCatgirl = '缓存猫娘';
            window._cardFaceNames = new Set();
            window._cardMetas = {};
            renderCharaCardsView();

            document.querySelector('.chara-card-item')?.click();
            await waitFor(() => !!document.querySelector('.catgirl-panel-overlay #panel-add-catgirl-field-btn'));

            document.querySelector('.catgirl-panel-overlay #panel-add-catgirl-field-btn').click();
            await waitFor(() => !!document.querySelector('.catgirl-panel-overlay textarea[name="追加设定"]'));
            const newField = document.querySelector('.catgirl-panel-overlay textarea[name="追加设定"]');
            newField.value = '保存后的内容';
            newField.dispatchEvent(new Event('input', { bubbles: true }));
            newField.dispatchEvent(new Event('change', { bubbles: true }));

            document.querySelector('.catgirl-panel-overlay #save-button').click();
            await waitFor(() => savedBodies.length > 0);
            await waitFor(() => {
                return !document.querySelector('.catgirl-panel-overlay form[data-submitting="true"]');
            });

            const valueAfterSave = document.querySelector('.catgirl-panel-overlay textarea[name="追加设定"]')?.value || '';
            await closeCatgirlPanel();
            await sleep(850);

            document.querySelector('.chara-card-item')?.click();
            await waitFor(() => !!document.querySelector('.catgirl-panel-overlay textarea[name="追加设定"]'));
            const valueAfterReopen = document.querySelector('.catgirl-panel-overlay textarea[name="追加设定"]')?.value || '';
            const cachedRawData = (window.characterCards || [])[0]?.rawData || {};

            return {
                savedBodies,
                characterFetchCaches,
                valueAfterSave,
                valueAfterReopen,
                cachedRawData
            };
        }
        """
    )

    assert state["savedBodies"][0]["追加设定"] == "保存后的内容"
    assert "no-store" in state["characterFetchCaches"]
    assert state["valueAfterSave"] == "保存后的内容"
    assert state["valueAfterReopen"] == "保存后的内容"
    assert state["cachedRawData"]["追加设定"] == "保存后的内容"


@pytest.mark.frontend
def test_character_card_manager_keeps_numeric_field_creation_order(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        async () => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const waitFor = async (predicate, timeout = 2500) => {
                const startedAt = Date.now();
                while (Date.now() - startedAt < timeout) {
                    if (predicate()) return true;
                    await sleep(25);
                }
                return false;
            };

            const originalFetch = window.fetch.bind(window);
            const savedBodies = [];
            window.showMessage = () => {};
            window.showAutoSaveToast = () => {};
            window.showAlertDialog = async () => {};

            window.fetch = async (input, init = {}) => {
                const rawUrl = typeof input === 'string' ? input : input.url;
                const url = new URL(rawUrl, window.location.origin);
                const path = decodeURIComponent(url.pathname);
                const method = String(init.method || 'GET').toUpperCase();

                if (path === '/api/characters/catgirl/顺序猫' && method === 'PUT') {
                    savedBodies.push(JSON.parse(init.body || '{}'));
                    return new Response(JSON.stringify({ success: true }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters' && method === 'GET') {
                    return new Response(JSON.stringify({
                        '主人': {},
                        '当前猫娘': '顺序猫',
                        '猫娘': {
                            '顺序猫': {
                                '1': '数字字段',
                                '喵喵喵': '文字字段',
                                '_reserved': { field_order: ['喵喵喵', '1'] }
                            }
                        }
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/current_catgirl') {
                    return new Response(JSON.stringify({ current_catgirl: '顺序猫' }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/character-card/list') {
                    return new Response(JSON.stringify({ success: true, character_cards: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/card-faces') {
                    return new Response(JSON.stringify({ success: true, names: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/card-metas') {
                    return new Response(JSON.stringify({ success: true, metas: {} }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/voices') {
                    return new Response(JSON.stringify({ voices: {}, free_voices: {}, voice_owners: {} }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/custom_tts_voices') {
                    return new Response(JSON.stringify({ success: true, voices: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/live2d/models') {
                    return new Response(JSON.stringify([]), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/model/vrm/models' || path === '/api/model/mmd/models') {
                    return new Response(JSON.stringify({ success: true, models: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                return originalFetch(input, init);
            };

            window.characterCards = [{
                id: 1,
                name: '顺序猫',
                originalName: '顺序猫',
                description: '',
                tags: [],
                rawData: {
                    '1': '数字字段',
                    '喵喵喵': '文字字段',
                    _reserved: { field_order: ['喵喵喵', '1'] }
                }
            }];
            window._workshopCurrentCatgirl = '顺序猫';
            window._cardFaceNames = new Set();
            window._cardMetas = {};
            renderCharaCardsView();

            document.querySelector('.chara-card-item')?.click();
            await waitFor(() => document.querySelectorAll('.catgirl-panel-overlay textarea[name]').length >= 2);
            const targetFieldNames = new Set(['喵喵喵', '1']);
            const beforeSaveOrder = Array.from(document.querySelectorAll('.catgirl-panel-overlay textarea[name]'))
                .map(el => el.getAttribute('name'))
                .filter(name => targetFieldNames.has(name));

            document.querySelector('.catgirl-panel-overlay #save-button').click();
            await waitFor(() => savedBodies.length > 0);

            return {
                beforeSaveOrder,
                savedOrder: savedBodies[0]._field_order || []
            };
        }
        """
    )

    assert state["beforeSaveOrder"] == ["喵喵喵", "1"]
    assert state["savedOrder"] == ["喵喵喵", "1"]


@pytest.mark.frontend
def test_character_card_manager_workshop_upload_preserves_field_order(
    mock_page: Page,
    running_server: str,
):
    """上传到创意工坊会剥掉系统保留字段（含承载顺序的 _reserved），需确保字段创建顺序
    以顶层 _field_order 幸存，否则下载方按对象枚举顺序渲染时数字 key 字段会再次乱序。"""
    _open_character_card_manager(mock_page, running_server)

    uploaded = mock_page.evaluate(
        """
        async () => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const waitFor = async (predicate, timeout = 2500) => {
                const startedAt = Date.now();
                while (Date.now() - startedAt < timeout) {
                    if (predicate()) return true;
                    await sleep(25);
                }
                return false;
            };

            const originalFetch = window.fetch.bind(window);
            const uploadBodies = [];
            window.showMessage = () => {};
            window.showAutoSaveToast = () => {};
            window.showAlertDialog = async () => {};
            // 模型判定走真实逻辑会牵扯大量全局状态，这里直接放行，把断言聚焦在字段顺序上。
            window.isDefaultModel = () => false;
            window.isStaticDefaultLive2DModel = () => false;

            window.fetch = async (input, init = {}) => {
                const rawUrl = typeof input === 'string' ? input : input.url;
                const url = new URL(rawUrl, window.location.origin);
                const path = decodeURIComponent(url.pathname);
                const method = String(init.method || 'GET').toUpperCase();

                if (path === '/api/steam/workshop/prepare-upload' && method === 'POST') {
                    uploadBodies.push(JSON.parse(init.body || '{}'));
                    return new Response(JSON.stringify({ success: true }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                return originalFetch(input, init);
            };

            // handleUploadToWorkshop 会读页面上的描述输入框；测试页面未必挂载它，按需补一个。
            if (!document.getElementById('character-card-description')) {
                const descInput = document.createElement('textarea');
                descInput.id = 'character-card-description';
                document.body.appendChild(descInput);
            }

            const card = {
                id: 9301,
                name: '顺序猫',
                originalName: '顺序猫',
                description: '用于工坊上传顺序回归',
                tags: [],
                rawData: {
                    '1': '数字字段',
                    '喵喵喵': '文字字段',
                    '描述': '用于工坊上传顺序回归',
                    _reserved: { field_order: ['喵喵喵', '1'] }
                }
            };
            window.characterCards = [card];
            // expandCharacterCardSection 第一行即设置 currentCharacterCardId；后续填表副作用与本用例无关，吞掉即可。
            try { expandCharacterCardSection(card); } catch (_) {}
            // 放在 expand 之后，避免被其填充逻辑覆盖，保证模型校验直接通过。
            window.currentCharacterCardModelType = 'live2d';
            window.currentCharacterCardModel = '顺序猫模型';

            await handleUploadToWorkshop();
            await waitFor(() => uploadBodies.length > 0);

            const charaData = uploadBodies.length ? uploadBodies[0].charaData : null;
            return {
                fieldOrder: (charaData && charaData._field_order) || null,
                hasReserved: !!(charaData && charaData._reserved)
            };
        }
        """
    )

    assert uploaded["fieldOrder"] == ["喵喵喵", "1"]
    assert uploaded["hasReserved"] is False


@pytest.mark.frontend
def test_character_card_manager_scan_import_keeps_model_fields_and_order(
    mock_page: Page,
    running_server: str,
):
    """从创意工坊导入角色卡（scanCharaFile）必须保留 live2d/model_type 等模型字段，
    同时按显式 _field_order 排列自定义字段。若误套渲染路径的系统保留名剔除，会丢掉模型绑定。"""
    _open_character_card_manager(mock_page, running_server)

    added = mock_page.evaluate(
        """
        async () => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const waitFor = async (predicate, timeout = 2500) => {
                const startedAt = Date.now();
                while (Date.now() - startedAt < timeout) {
                    if (predicate()) return true;
                    await sleep(25);
                }
                return false;
            };

            const originalFetch = window.fetch.bind(window);
            const addBodies = [];
            window.showMessage = () => {};
            window.showAlert = () => {};

            const charaJson = {
                '档案名': '顺序猫',
                'live2d': '测试模型',
                'model_type': 'live2d',
                '1': '数字字段',
                '喵喵喵': '文字字段',
                '_field_order': ['喵喵喵', '1']
            };

            window.fetch = async (input, init = {}) => {
                const rawUrl = typeof input === 'string' ? input : input.url;
                const url = new URL(rawUrl, window.location.origin);
                const path = decodeURIComponent(url.pathname);
                const method = String(init.method || 'GET').toUpperCase();

                if (path === '/api/steam/workshop/read-file') {
                    return new Response(JSON.stringify({ success: true, content: JSON.stringify(charaJson) }), {
                        status: 200, headers: { 'Content-Type': 'application/json' }
                    });
                }
                if (path === '/api/characters/catgirl' && method === 'POST') {
                    addBodies.push(JSON.parse(init.body || '{}'));
                    return new Response(JSON.stringify({ success: true }), {
                        status: 200, headers: { 'Content-Type': 'application/json' }
                    });
                }
                return originalFetch(input, init);
            };

            await scanCharaFile('顺序猫.chara.json', '99887', '顺序猫');
            await waitFor(() => addBodies.length > 0);
            return addBodies[0] || null;
        }
        """
    )

    assert added is not None
    # P1：模型字段必须保留，被误过滤会丢失模型绑定、开成错误或缺失的模型
    assert added["live2d"] == "测试模型"
    assert added["model_type"] == "live2d"
    assert added["live2d_item_id"] == "99887"
    # 自定义字段及其显式创建顺序一并保留
    assert added["1"] == "数字字段"
    assert added["喵喵喵"] == "文字字段"
    assert added["_field_order"] == ["喵喵喵", "1"]


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


@pytest.mark.frontend
def test_character_card_manager_card_assist_avatar_toggles_companion(
    mock_page: Page,
    running_server: str,
):
    _open_character_card_manager(mock_page, running_server)

    state = mock_page.evaluate(
        """
        async () => {
            if (window._cardCompanion) {
                _companionTeardown(window._cardCompanion);
                _companionDestroy(window._cardCompanion);
                window._cardCompanion = null;
            }

            const form = document.createElement('form');
            form.id = 'catgirl-form-card-assist-regression';
            const field = document.createElement('textarea');
            field.name = 'Personality';
            form.appendChild(field);
            document.body.appendChild(form);

            openCardAssistCompanion(form, 'RegressionCard', false);
            const panel = document.querySelector('.card-companion-panel');
            const avatar = panel ? panel.querySelector('.card-companion-avatar') : null;
            const avatarImg = avatar ? avatar.querySelector('img') : null;
            const minimize = panel ? panel.querySelector('.card-companion-minimize') : null;
            const avatarImgStyle = avatarImg ? window.getComputedStyle(avatarImg) : null;
            const avatarImgObjectPosition = avatarImgStyle ? avatarImgStyle.objectPosition : null;
            const avatarImgTransform = avatarImgStyle ? avatarImgStyle.transform : null;
            const panelTransitionBeforeCollapse = panel ? window.getComputedStyle(panel).transitionProperty : null;
            if (panel) panel.classList.add('card-companion-dragging');
            const draggingTransition = panel ? window.getComputedStyle(panel).transitionProperty : null;
            if (panel) panel.classList.remove('card-companion-dragging');

            const before = panel ? panel.classList.contains('card-companion-minimized') : null;
            const avatarRectBefore = avatar ? avatar.getBoundingClientRect() : null;
            if (avatar) avatar.click();
            const collapsingRightAfterClick = panel
                ? panel.classList.contains('card-companion-collapsing')
                : null;
            const collapsingTransition = panel ? window.getComputedStyle(panel).transitionProperty : null;
            const avatarRectDuringCollapse = avatar ? avatar.getBoundingClientRect() : null;
            await new Promise(resolve => setTimeout(resolve, 420));
            const afterFirstClick = panel ? panel.classList.contains('card-companion-minimized') : null;
            const ariaAfterFirstClick = avatar ? avatar.getAttribute('aria-expanded') : null;
            const minimizedRect = panel ? panel.getBoundingClientRect() : null;
            const minimizedBorderRadius = panel ? window.getComputedStyle(panel).borderRadius : null;
            const minimizedAnimationName = panel ? window.getComputedStyle(panel).animationName : null;
            const titleDisplayWhenMinimized = panel
                ? window.getComputedStyle(panel.querySelector('.card-companion-title')).display
                : null;
            const closeDisplayWhenMinimized = panel
                ? window.getComputedStyle(panel.querySelector('.card-companion-close')).display
                : null;
            const dragStartX = minimizedRect ? minimizedRect.left + minimizedRect.width / 2 : 0;
            const dragStartY = minimizedRect ? minimizedRect.top + minimizedRect.height / 2 : 0;
            if (avatar) {
                avatar.dispatchEvent(new PointerEvent('pointerdown', {
                    bubbles: true,
                    cancelable: true,
                    pointerId: 17,
                    button: 0,
                    clientX: dragStartX,
                    clientY: dragStartY
                }));
                window.dispatchEvent(new PointerEvent('pointermove', {
                    bubbles: true,
                    cancelable: true,
                    pointerId: 17,
                    clientX: dragStartX - 34,
                    clientY: dragStartY + 22
                }));
                await new Promise(resolve => setTimeout(resolve, 260));
                window.dispatchEvent(new PointerEvent('pointerup', {
                    bubbles: true,
                    cancelable: true,
                    pointerId: 17,
                    clientX: dragStartX - 34,
                    clientY: dragStartY + 22
                }));
                avatar.click();
            }
            await new Promise(resolve => setTimeout(resolve, 40));
            const afterMinimizedDrag = panel ? panel.classList.contains('card-companion-minimized') : null;
            const minimizedRectAfterDrag = panel ? panel.getBoundingClientRect() : null;
            const transformAfterMinimizedDrag = panel ? window.getComputedStyle(panel).transform : null;
            if (window._cardCompanion) {
                _companionSetMinimized(window._cardCompanion, false);
            }
            await new Promise(resolve => setTimeout(resolve, 420));
            const afterSecondClick = panel ? panel.classList.contains('card-companion-minimized') : null;
            const ariaAfterSecondClick = avatar ? avatar.getAttribute('aria-expanded') : null;

            if (window._cardCompanion) {
                _companionTeardown(window._cardCompanion);
                _companionDestroy(window._cardCompanion);
                window._cardCompanion = null;
            }
            form.remove();

            return {
                hasPanel: !!panel,
                hasAvatar: !!avatar,
                hasMinimize: !!minimize,
                panelTransitionBeforeCollapse,
                draggingTransition,
                collapsingTransition,
                before,
                afterFirstClick,
                afterSecondClick,
                collapsingRightAfterClick,
                avatarLeftBefore: avatarRectBefore ? Math.round(avatarRectBefore.left) : null,
                avatarTopBefore: avatarRectBefore ? Math.round(avatarRectBefore.top) : null,
                avatarWidthBefore: avatarRectBefore ? Math.round(avatarRectBefore.width) : null,
                avatarHeightBefore: avatarRectBefore ? Math.round(avatarRectBefore.height) : null,
                avatarWidthDuringCollapse: avatarRectDuringCollapse ? Math.round(avatarRectDuringCollapse.width) : null,
                avatarHeightDuringCollapse: avatarRectDuringCollapse ? Math.round(avatarRectDuringCollapse.height) : null,
                minimizedLeft: minimizedRect ? Math.round(minimizedRect.left) : null,
                minimizedTop: minimizedRect ? Math.round(minimizedRect.top) : null,
                minimizedWidth: minimizedRect ? Math.round(minimizedRect.width) : null,
                minimizedHeight: minimizedRect ? Math.round(minimizedRect.height) : null,
                minimizedBorderRadius,
                minimizedAnimationName,
                afterMinimizedDrag,
                transformAfterMinimizedDrag,
                minimizedTopAfterDrag: minimizedRectAfterDrag ? Math.round(minimizedRectAfterDrag.top) : null,
                titleDisplayWhenMinimized,
                closeDisplayWhenMinimized,
                avatarSrc: avatarImg ? avatarImg.getAttribute('src') : null,
                avatarImgObjectPosition,
                avatarImgTransform,
                avatarRole: avatar ? avatar.getAttribute('role') : null,
                avatarTabIndex: avatar ? avatar.getAttribute('tabindex') : null,
                ariaAfterFirstClick,
                ariaAfterSecondClick
            };
        }
        """
    )

    assert state["hasPanel"] is True
    assert state["hasAvatar"] is True
    assert state["hasMinimize"] is False
    assert "width" not in state["panelTransitionBeforeCollapse"].split(", ")
    assert "height" not in state["panelTransitionBeforeCollapse"].split(", ")
    assert state["draggingTransition"] == "none"
    assert "width" in state["collapsingTransition"].split(", ")
    assert "height" in state["collapsingTransition"].split(", ")
    assert state["before"] is False
    assert state["collapsingRightAfterClick"] is True
    assert state["afterFirstClick"] is True
    assert state["afterSecondClick"] is False
    assert abs(state["avatarWidthDuringCollapse"] - state["avatarWidthBefore"]) <= 1
    assert abs(state["avatarHeightDuringCollapse"] - state["avatarHeightBefore"]) <= 1
    assert abs(state["minimizedLeft"] - state["avatarLeftBefore"]) <= 1
    assert abs(state["minimizedTop"] - state["avatarTopBefore"]) <= 1
    assert abs(state["minimizedWidth"] - state["avatarWidthBefore"]) <= 1
    assert abs(state["minimizedHeight"] - state["avatarHeightBefore"]) <= 1
    assert state["minimizedBorderRadius"] == "50%"
    assert state["minimizedAnimationName"] == "cardCompanionBallGlow"
    assert state["afterMinimizedDrag"] is True
    assert state["transformAfterMinimizedDrag"] == "none"
    assert state["titleDisplayWhenMinimized"] == "none"
    assert state["closeDisplayWhenMinimized"] == "none"
    assert state["avatarSrc"].endswith("/api/characters/catgirl/YUI/card-face")
    assert state["avatarImgObjectPosition"] == "50% 8%"
    assert state["avatarImgTransform"] == "matrix(1.02, 0, 0, 1.02, 0, 3)"
    assert state["avatarRole"] == "button"
    assert state["avatarTabIndex"] == "0"
    assert state["ariaAfterFirstClick"] == "false"
    assert state["ariaAfterSecondClick"] == "true"
