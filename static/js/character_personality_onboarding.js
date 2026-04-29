(function () {
    'use strict';

    const STYLE_ID = 'character-personality-onboarding-style';
    const TUTORIAL_PROMPT_POLL_INTERVAL_MS = 120;
    const TYPEWRITER_BASE_DELAY_MS = 18;
    const TYPEWRITER_PUNCTUATION_DELAY_MS = 110;

    function interpolateTemplate(template, options) {
        return String(template || '').replace(/{{\s*(\w+)\s*}}/g, (_, name) => {
            if (options && Object.prototype.hasOwnProperty.call(options, name)) {
                return String(options[name]);
            }
            return '';
        });
    }

    function translate(key, fallback, options) {
        if (typeof window.t === 'function') {
            const translated = options && typeof options === 'object'
                ? window.t(key, {
                    ...options,
                    defaultValue: fallback,
                })
                : window.t(key, fallback);
            if (translated && translated !== key) {
                return translated;
            }
        }
        const template = fallback || key;
        return options && typeof options === 'object'
            ? interpolateTemplate(template, options)
            : template;
    }

    async function requestJson(url, options) {
        const response = await fetch(url, options);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload && payload.error ? payload.error : `Request failed: ${response.status}`);
        }
        return payload;
    }

    function ensureStyles() {
        if (document.getElementById(STYLE_ID)) {
            return;
        }

        const styleLink = document.createElement('link');
        styleLink.id = STYLE_ID;
        styleLink.rel = 'stylesheet';
        styleLink.href = '/static/css/character_personality_onboarding.css';
        document.head.appendChild(styleLink);
    }

    function splitHighlightText(value) {
        return String(value || '')
            .split(/[、,，/]/)
            .map((item) => item.trim())
            .filter(Boolean);
    }

    function createElement(tagName, className, textContent) {
        const element = document.createElement(tagName);
        if (className) {
            element.className = className;
        }
        if (typeof textContent === 'string') {
            element.textContent = textContent;
        }
        return element;
    }

    class CharacterPersonalityOnboardingManager {
        constructor() {
            this.overlay = null;
            this.currentCharacterName = '';
            this.presets = [];
            this.selectedPresetId = '';
            this.bootstrapStarted = false;
            this.pendingResumeAfterTutorial = false;
            this.restoreBodyPointerEventsNeeded = false;
            this.originalBodyPointerEvents = '';
            this.openReason = 'onboarding';
            this.typewriterRunId = 0;
            this.typewriterTimer = null;
            this.bindTutorialLifecycleEvents();
        }

        async bootstrap() {
            if (this.bootstrapStarted) {
                return;
            }
            this.bootstrapStarted = true;
            await this.waitForStartupBarrier();
            if (await this.openIfManualReselectPending()) {
                return;
            }
            await this.waitForTutorialFlowToSettle();
            await this.openIfPending();
        }

        async openFromSettings(characterName) {
            this.openReason = 'settings';
            this.currentCharacterName = String(characterName || '').trim() || await this.fetchCurrentCharacterName();
            this.presets = await this.fetchPresets();
            this.ensureOverlay();
            this.renderStageOne();
            this.showOverlay();
        }

        async waitForStartupBarrier() {
            if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
                await window.waitForStorageLocationStartupBarrier();
            }
        }

        async waitForTutorialManagerReady() {
            if (window.universalTutorialManager) {
                return;
            }

            await new Promise((resolve) => {
                let settled = false;
                const finish = () => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    clearInterval(intervalId);
                    clearTimeout(timeoutId);
                    window.removeEventListener('neko:tutorial-started', finish);
                    resolve();
                };
                const intervalId = window.setInterval(() => {
                    if (window.universalTutorialManager) {
                        finish();
                    }
                }, 50);
                const timeoutId = window.setTimeout(finish, 3000);
                window.addEventListener('neko:tutorial-started', finish, { once: true });
            });
        }

        async waitForTutorialCompletion() {
            await this.waitForTutorialManagerReady();
            if (!window.universalTutorialManager || !window.universalTutorialManager.isTutorialRunning) {
                return;
            }
            await new Promise((resolve) => {
                let settled = false;
                const cleanup = () => {
                    window.removeEventListener('neko:tutorial-completed', handleCompleted);
                    window.removeEventListener('neko:tutorial-skipped', handleSkipped);
                };
                const finish = () => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    cleanup();
                    resolve();
                };
                const handleCompleted = () => finish();
                const handleSkipped = () => finish();
                window.addEventListener('neko:tutorial-completed', handleCompleted, { once: true });
                window.addEventListener('neko:tutorial-skipped', handleSkipped, { once: true });
            });
        }

        async fetchTutorialPromptState() {
            try {
                const payload = await requestJson('/api/tutorial-prompt/state', {
                    cache: 'no-store',
                });
                return payload && payload.state ? payload.state : null;
            } catch (error) {
                console.warn('[CharacterPersonalityOnboarding] failed to fetch tutorial prompt state:', error);
                return null;
            }
        }

        normalizeTutorialPromptStatus(state) {
            if (!state || typeof state !== 'object') {
                return '';
            }
            return String(state.status || '').trim().toLowerCase();
        }

        normalizeTutorialPromptUserCohort(state) {
            if (!state || typeof state !== 'object') {
                return '';
            }
            return String(state.user_cohort || '').trim().toLowerCase();
        }

        isTutorialPromptSettled(state) {
            if (!state || typeof state !== 'object') {
                return false;
            }
            const status = this.normalizeTutorialPromptStatus(state);
            if (status === 'completed' || status === 'deferred' || status === 'never' || status === 'error') {
                return true;
            }
            if (status === 'started') {
                return false;
            }

            const userCohort = this.normalizeTutorialPromptUserCohort(state);
            if (status === 'observing' || status === 'prompted') {
                return userCohort === 'existing';
            }

            return false;
        }

        async waitForTutorialFlowToSettle() {
            await this.waitForTutorialCompletion();

            while (true) {
                if (window.universalTutorialManager && window.universalTutorialManager.isTutorialRunning) {
                    await this.waitForTutorialCompletion();
                    continue;
                }

                const tutorialPromptState = await this.fetchTutorialPromptState();
                if (!tutorialPromptState) {
                    await new Promise((resolve) => {
                        window.setTimeout(resolve, TUTORIAL_PROMPT_POLL_INTERVAL_MS);
                    });
                    continue;
                }
                const status = this.normalizeTutorialPromptStatus(tutorialPromptState);
                if (this.isTutorialPromptSettled(tutorialPromptState)) {
                    return;
                }

                if (status === 'started') {
                    await new Promise((resolve) => {
                        window.setTimeout(resolve, TUTORIAL_PROMPT_POLL_INTERVAL_MS);
                    });
                    continue;
                }

                await new Promise((resolve) => {
                    window.setTimeout(resolve, TUTORIAL_PROMPT_POLL_INTERVAL_MS);
                });
            }
        }

        async fetchOnboardingState() {
            return requestJson('/api/characters/persona-onboarding-state', {
                cache: 'no-store',
            });
        }

        async openIfManualReselectPending() {
            const statePayload = await this.fetchOnboardingState();
            const state = statePayload && statePayload.state ? statePayload.state : null;
            const manualCharacterName = String(state && state.manual_reselect_character_name || '').trim();
            if (!manualCharacterName) {
                return false;
            }

            this.currentCharacterName = await this.fetchCurrentCharacterName();
            if (!this.currentCharacterName || this.currentCharacterName !== manualCharacterName) {
                return false;
            }

            this.openReason = 'manual_reselect';
            this.presets = await this.fetchPresets();
            if (!this.presets.length) {
                return false;
            }

            this.ensureOverlay();
            this.renderStageOne();
            this.showOverlay();
            return true;
        }

        async openIfPending() {
            const statePayload = await this.fetchOnboardingState();
            if (!statePayload || !statePayload.state || statePayload.state.status !== 'pending') {
                return;
            }

            this.openReason = 'onboarding';
            this.currentCharacterName = await this.fetchCurrentCharacterName();
            this.presets = await this.fetchPresets();
            if (!this.currentCharacterName || !this.presets.length) {
                return;
            }

            this.ensureOverlay();
            this.renderStageOne();
            this.showOverlay();
        }

        async fetchCurrentCharacterName() {
            const payload = await requestJson('/api/characters/current_catgirl');
            return String(payload.current_catgirl || '').trim();
        }

        async fetchPresets() {
            const payload = await requestJson('/api/characters/persona-presets');
            return Array.isArray(payload.presets) ? payload.presets : [];
        }

        ensureOverlay() {
            ensureStyles();
            if (this.overlay && document.body.contains(this.overlay)) {
                return;
            }

            const overlay = createElement('div', 'character-personality-overlay');
            overlay.className = 'character-personality-overlay';
            overlay.dataset.testid = 'character-personality-overlay';
            overlay.hidden = true;

            const shell = createElement('div', 'character-personality-shell');
            shell.className = 'character-personality-shell';
            shell.id = 'modalShell';
            shell.setAttribute('role', 'dialog');
            shell.setAttribute('aria-modal', 'true');
            overlay.appendChild(shell);

            const decoBar = createElement('div', 'shell-deco-bar');
            shell.appendChild(decoBar);

            const body = createElement('div', 'character-personality-body');
            shell.appendChild(body);

            const stageOne = createElement(
                'div',
                'character-personality-stage character-personality-stage-one'
            );
            stageOne.className = 'character-personality-stage character-personality-stage-one';
            body.appendChild(stageOne);

            const stageTwo = createElement(
                'div',
                'character-personality-stage character-personality-stage-two'
            );
            stageTwo.className = 'character-personality-stage character-personality-stage-two';
            stageTwo.hidden = true;
            body.appendChild(stageTwo);

            document.body.appendChild(overlay);
            this.overlay = overlay;
        }

        bindTutorialLifecycleEvents() {
            const queueResume = (event) => {
                if (!event || !event.detail || event.detail.page !== 'home') {
                    return;
                }
                if (!this.bootstrapStarted || this.openReason !== 'onboarding') {
                    return;
                }
                this.pendingResumeAfterTutorial = true;
                if (this.overlay && !this.overlay.hidden) {
                    this.hideOverlay();
                }
            };

            const resumeIfNeeded = (event) => {
                if (!event || !event.detail || event.detail.page !== 'home') {
                    return;
                }
                if (!this.pendingResumeAfterTutorial || this.openReason !== 'onboarding') {
                    return;
                }
                this.pendingResumeAfterTutorial = false;
                void this.openIfPending();
            };

            window.addEventListener('neko:tutorial-started', queueResume);
            window.addEventListener('neko:tutorial-completed', resumeIfNeeded);
            window.addEventListener('neko:tutorial-skipped', resumeIfNeeded);
        }

        getEyebrowText() {
            if (this.openReason === 'manual_reselect') {
                return translate('memory.characterSelection.manualEyebrow', '当前角色人格重选');
            }
            if (this.openReason === 'settings') {
                return translate('memory.characterSelection.settingsEyebrow', '角色人格设置');
            }
            return translate('memory.characterSelection.onboardingEyebrow', '初始人格选择');
        }

        getTitleText() {
            if (this.openReason === 'manual_reselect') {
                return translate('memory.characterSelection.manualTitle', '这一次，想让我用哪种性格陪着你呀喵？');
            }
            if (this.openReason === 'settings') {
                return translate('memory.characterSelection.settingsTitle', '来帮我换一种陪着你的语气吧喵');
            }
            return translate('memory.characterSelection.chooseTitle', '你想让我变成哪种陪着你的样子喵？');
        }

        getHintText() {
            if (this.openReason === 'manual_reselect') {
                return translate(
                    'memory.characterSelection.manualHint',
                    '这是当前角色的人格重选喵，确认后会立刻写回这只角色现在生效的人格。'
                );
            }
            if (this.openReason === 'settings') {
                return translate(
                    'memory.characterSelection.settingsHint',
                    '不用担心喵，这里不会改坏角色卡原本设定，只会覆盖当前角色现在生效的人格。'
                );
            }
            return translate('memory.characterSelection.chooseHint', '先挑现在最喜欢的感觉就好喵，之后也能在设置里再改。');
        }

        shouldShowContextWarning() {
            return this.openReason === 'settings' || this.openReason === 'manual_reselect';
        }

        getContextWarningText() {
            return translate(
                'memory.characterSelection.contextWarning',
                '小提醒喵，切换人格后，我会先清空当前角色最近这段聊天上下文，再用新的语气继续陪你。'
            );
        }

        createContextWarning(extraClassName) {
            const className = extraClassName
                ? `character-personality-warning ${extraClassName}`
                : 'character-personality-warning';
            const warning = createElement('div', className, this.getContextWarningText());
            warning.dataset.testid = 'character-personality-warning';
            return warning;
        }

        updateHeaderCopy() {
            if (!this.overlay) {
                return;
            }

            const eyebrow = this.overlay.querySelector("[data-role='eyebrow']");
            const title = this.overlay.querySelector("[data-role='title']");
            const hint = this.overlay.querySelector("[data-role='hint']");
            const currentCharacter = this.overlay.querySelector("[data-role='current-character']");

            if (eyebrow) eyebrow.textContent = this.getEyebrowText();
            if (title) title.textContent = this.getTitleText();
            if (hint) hint.textContent = this.getHintText();
            if (currentCharacter) {
                currentCharacter.textContent = this.currentCharacterName
                    ? this.currentCharacterName
                    : translate('memory.characterSelection.currentCharacterEmpty', '当前角色');
            }
        }

        prepareOverlayPointerEvents() {
            this.originalBodyPointerEvents = document.body.style.pointerEvents;
            this.restoreBodyPointerEventsNeeded = getComputedStyle(document.body).pointerEvents === 'none';
            if (this.restoreBodyPointerEventsNeeded) {
                document.body.style.pointerEvents = 'auto';
            }
        }

        restoreOverlayPointerEvents() {
            if (!this.restoreBodyPointerEventsNeeded) {
                return;
            }
            document.body.style.pointerEvents = this.originalBodyPointerEvents;
            this.restoreBodyPointerEventsNeeded = false;
            this.originalBodyPointerEvents = '';
        }

        stopTypewriter() {
            this.typewriterRunId += 1;
            if (this.typewriterTimer) {
                clearTimeout(this.typewriterTimer);
                this.typewriterTimer = null;
            }
        }

        scheduleNextTypeCharacter(target, text, index, runId) {
            if (runId !== this.typewriterRunId) {
                return;
            }

            target.textContent = text.slice(0, index);
            if (index >= text.length) {
                target.classList.add('is-complete');
                target.dataset.typing = 'done';
                this.typewriterTimer = null;
                return;
            }

            target.dataset.typing = 'active';
            const currentCharacter = text.charAt(index - 1);
            const extraDelay = /[,.!?;:，。！？；：]/.test(currentCharacter) ? TYPEWRITER_PUNCTUATION_DELAY_MS : 0;
            this.typewriterTimer = window.setTimeout(() => {
                this.scheduleNextTypeCharacter(target, text, index + 1, runId);
            }, TYPEWRITER_BASE_DELAY_MS + extraDelay);
        }

        playTypewriter(target, text) {
            this.stopTypewriter();
            const runId = this.typewriterRunId;
            target.textContent = '';
            target.classList.remove('is-complete');
            this.scheduleNextTypeCharacter(target, text, 1, runId);
        }

        getPresetHighlights(preset) {
            const highlightMap = {
                classic_genki: ['高共情', '贴贴型', '情绪充电'],
                tsundere_helper: ['嘴硬心软', '高可靠', '吐槽式偏爱'],
                elegant_butler: ['稳妥周全', '优雅克制', '先你一步'],
            };
            const presetId = preset && preset.preset_id;
            const fallbacks = highlightMap[presetId] || [];
            return fallbacks.map((fallback, index) => translate(
                `memory.characterSelection.${presetId}.tag${index + 1}`,
                fallback
            ));
        }

        getPresetTranslationBase(preset) {
            return preset && preset.preset_id
                ? `memory.characterSelection.${preset.preset_id}`
                : '';
        }

        getPresetName(preset) {
            const baseKey = this.getPresetTranslationBase(preset);
            const fallback = preset?.display_name || preset?.profile?.['性格原型'] || preset?.preset_id || '';
            return baseKey ? translate(`${baseKey}.name`, fallback) : fallback;
        }

        getPresetSummary(preset) {
            const fallback = preset?.summary_fallback || '';
            const key = preset?.summary_key || `${this.getPresetTranslationBase(preset)}.desc`;
            return key ? translate(key, fallback) : fallback;
        }

        getPresetPreviewLine(preset) {
            const baseKey = this.getPresetTranslationBase(preset);
            const fallback = preset?.preview_line || preset?.profile?.['一句话台词'] || '';
            return baseKey ? translate(`${baseKey}.previewLine`, fallback) : fallback;
        }

        getPresetDetailItems(preset, keySuffix, fallback) {
            return splitHighlightText(this.getPresetProfileValue(preset, keySuffix, fallback));
        }

        getPresetProfileValue(preset, keySuffix, fallback) {
            const baseKey = this.getPresetTranslationBase(preset);
            return baseKey ? translate(`${baseKey}.${keySuffix}`, fallback) : fallback;
        }

        buildPreviewCopy(preset) {
            const profile = preset && preset.profile && typeof preset.profile === 'object' ? preset.profile : {};
            const presetName = this.getPresetName(preset);
            const profileSummary = this.getPresetProfileValue(
                preset,
                'profileSummary',
                String(profile['性格'] || '').trim()
            );
            const profileVoice = this.getPresetPreviewLine(preset);
            const hiddenRule = this.getPresetProfileValue(
                preset,
                'hiddenRule',
                String(profile['隐藏设定'] || '').trim()
            );

            return [
                translate(
                    'memory.characterSelection.previewLead',
                    '如果把现在的我调成「{{name}}」，我大概会这样陪着你喵。',
                    { name: presetName }
                ),
                profileSummary,
                profileVoice,
                hiddenRule,
            ].filter(Boolean).join('\n\n');
        }

        renderStageOne() {
            if (!this.overlay) {
                return;
            }

            this.stopTypewriter();
            this.selectedPresetId = '';

            const stageOne = this.overlay.querySelector('.character-personality-stage-one');
            const stageTwo = this.overlay.querySelector('.character-personality-stage-two');
            stageOne.replaceChildren();

            const header = createElement('div', 'character-personality-header');
            const headerTop = createElement('div', 'character-personality-header-top');
            const eyebrow = createElement('div', 'character-personality-eyebrow', this.getEyebrowText());
            eyebrow.dataset.role = 'eyebrow';
            headerTop.appendChild(eyebrow);

            const badge = createElement(
                'div',
                'cph-badge character-personality-current-character',
                this.currentCharacterName || translate('memory.characterSelection.currentCharacterEmpty', '当前角色')
            );
            badge.dataset.role = 'current-character';
            headerTop.appendChild(badge);
            header.appendChild(headerTop);

            const title = createElement('h2', 'cph-title character-personality-title', this.getTitleText());
            title.dataset.role = 'title';
            header.appendChild(title);

            const hint = createElement('p', 'character-personality-hint', this.getHintText());
            hint.dataset.role = 'hint';
            header.appendChild(hint);
            stageOne.appendChild(header);

            const intro = createElement(
                'div',
                'character-personality-intro',
                translate(
                    'memory.characterSelection.stageOneIntro',
                    '先挑一个最对味的气质喵，再听听我开口是不是你想要的感觉。'
                )
            );
            if (this.shouldShowContextWarning()) {
                stageOne.appendChild(this.createContextWarning());
            }
            stageOne.appendChild(intro);

            const grid = createElement('div', 'character-personality-grid');
            stageOne.appendChild(grid);

            this.presets.forEach((preset) => {
                const card = createElement('button', 'character-personality-card');
                card.type = 'button';
                card.dataset.testid = `character-personality-preset-${preset.preset_id}`;
                card.dataset.presetId = preset.preset_id;

                const watermark = createElement('div', 'card-watermark', this.currentCharacterName || '');
                card.appendChild(watermark);

                const presetBadge = createElement('span', 'cpc-pill', this.getPresetName(preset));
                card.appendChild(presetBadge);

                const name = createElement(
                    'h3',
                    'cpc-title character-personality-card-name',
                    this.getPresetName(preset)
                );
                card.appendChild(name);

                const desc = createElement(
                    'p',
                    'cpc-desc character-personality-card-desc',
                    this.getPresetSummary(preset)
                );
                card.appendChild(desc);

                const quote = createElement(
                    'div',
                    'cpc-quote character-personality-card-quote',
                    this.getPresetPreviewLine(preset)
                );
                card.appendChild(quote);

                card.addEventListener('click', () => this.renderStageTwo(preset));
                grid.appendChild(card);
            });

            const actions = createElement('div', 'character-personality-actions');
            const skipButton = createElement(
                'button',
                'character-personality-button btn-ghost-jelly',
                translate('memory.characterSelection.skip', '先跳过喵')
            );
            skipButton.type = 'button';
            skipButton.dataset.testid = 'character-personality-skip';
            skipButton.addEventListener('click', async () => {
                if (this.openReason === 'manual_reselect') {
                    await requestJson('/api/characters/persona-reselect-current', {
                        method: 'DELETE',
                    });
                } else if (this.openReason === 'onboarding') {
                    await requestJson('/api/characters/persona-onboarding-state', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: 'skipped' }),
                    });
                }
                this.hideOverlay();
            });
            actions.appendChild(skipButton);
            stageOne.appendChild(actions);

            stageOne.hidden = false;
            stageTwo.hidden = true;
        }

        renderStageTwo(preset) {
            if (!this.overlay || !preset) {
                return;
            }

            this.selectedPresetId = preset.preset_id;

            const stageOne = this.overlay.querySelector('.character-personality-stage-one');
            const stageTwo = this.overlay.querySelector('.character-personality-stage-two');
            stageTwo.replaceChildren();

            const header = createElement('div', 'stage-two-header');
            const iconBack = createElement('button', 'btn-back-icon');
            iconBack.type = 'button';
            iconBack.setAttribute('aria-label', translate('memory.characterSelection.back', '返回重选'));
            iconBack.innerHTML = (
                '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" '
                + 'stroke="currentColor" stroke-width="3" stroke-linecap="round" '
                + 'stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>'
            );
            iconBack.addEventListener('click', () => this.renderStageOne());
            header.appendChild(iconBack);

            const stageTitle = createElement(
                'h2',
                'stage-two-title',
                translate('memory.characterSelection.previewLabel', '开口预览喵')
            );
            stageTitle.dataset.role = 'stage-two-title';
            header.appendChild(stageTitle);

            const titleBadge = createElement('div', 'stage-two-subtitle', this.getPresetName(preset));
            titleBadge.id = 'previewTitleBadge';
            header.appendChild(titleBadge);
            stageTwo.appendChild(header);

            const content = createElement('div', 'stage-two-content');
            stageTwo.appendChild(content);

            const previewSection = createElement('div', 'preview-section');
            const previewLabel = createElement(
                'div',
                'preview-label character-personality-preview-label',
                translate('memory.characterSelection.previewLabel', '开口预览喵')
            );
            previewSection.appendChild(previewLabel);

            const bubbleWrapper = createElement('div', 'preview-bubble-wrapper');
            const avatar = createElement(
                'div',
                'preview-avatar',
                this.currentCharacterName || translate('memory.characterSelection.currentCharacterEmpty', '当前角色')
            );
            bubbleWrapper.appendChild(avatar);

            const bubble = createElement('div', 'preview-bubble');
            const stream = createElement('span', 'character-personality-preview-stream');
            stream.dataset.testid = 'character-personality-preview-stream';
            bubble.appendChild(stream);
            bubbleWrapper.appendChild(bubble);
            previewSection.appendChild(bubbleWrapper);
            content.appendChild(previewSection);

            const detailsSection = createElement('div', 'details-section');
            content.appendChild(detailsSection);

            const profile = preset.profile && typeof preset.profile === 'object' ? preset.profile : {};
            [
                {
                    containerId: 'detailCatchphrases',
                    groupClassName: 'detail-group',
                    label: translate('memory.characterSelection.detailSpeechHabits', '常挂嘴边的话喵'),
                    items: this.getPresetDetailItems(
                        preset,
                        'speechHabits',
                        String(profile['口癖'] || '')
                    ),
                },
                {
                    containerId: 'detailAtmosphere',
                    groupClassName: 'detail-group',
                    label: translate('memory.characterSelection.detailHobbies', '最喜欢的气氛喵'),
                    items: this.getPresetDetailItems(
                        preset,
                        'hobbies',
                        String(profile['爱好'] || '')
                    ),
                },
                {
                    containerId: 'detailBottomLines',
                    groupClassName: 'detail-group danger',
                    label: translate('memory.characterSelection.detailBoundaries', '不能踩的尾巴喵'),
                    items: this.getPresetDetailItems(
                        preset,
                        'boundaries',
                        String(profile['雷点'] || '')
                    ),
                },
            ].forEach((groupConfig) => {
                if (!groupConfig.items.length) {
                    return;
                }

                const group = createElement('div', groupConfig.groupClassName);
                const label = createElement('div', 'detail-group-title', groupConfig.label);
                group.appendChild(label);

                const pills = createElement('div', 'detail-pills');
                pills.id = groupConfig.containerId;
                groupConfig.items.forEach((itemText) => {
                    const pill = createElement('span', 'detail-pill', itemText);
                    pills.appendChild(pill);
                });
                group.appendChild(pills);
                detailsSection.appendChild(group);
            });

            if (this.shouldShowContextWarning()) {
                stageTwo.appendChild(this.createContextWarning('character-personality-warning-stage-two'));
            }

            const actions = createElement('div', 'character-personality-actions');
            const backButton = createElement(
                'button',
                'character-personality-button btn-ghost-jelly',
                translate('memory.characterSelection.back', '再挑挑看喵')
            );
            backButton.type = 'button';
            backButton.dataset.testid = 'character-personality-back';
            backButton.addEventListener('click', () => this.renderStageOne());
            actions.appendChild(backButton);

            const confirmButton = createElement(
                'button',
                'character-personality-button btn-primary-jelly',
                translate('memory.characterSelection.confirmGreeting', '就用这个喵')
            );
            confirmButton.type = 'button';
            confirmButton.id = 'confirmBtn';
            confirmButton.dataset.testid = 'character-personality-confirm';
            confirmButton.addEventListener('click', async () => {
                if (!this.currentCharacterName || !this.selectedPresetId || confirmButton.disabled) {
                    return;
                }
                const selectedPresetId = this.selectedPresetId;
                const currentCharacterName = this.currentCharacterName;
                const openReason = this.openReason;
                confirmButton.disabled = true;
                confirmButton.classList.add('success');
                try {
                    await requestJson(
                        `/api/characters/character/${encodeURIComponent(currentCharacterName)}/persona-selection`,
                        {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                preset_id: selectedPresetId,
                                source: openReason,
                            }),
                        }
                    );
                    window.dispatchEvent(new CustomEvent('neko:character-personality-updated', {
                        detail: {
                            characterName: currentCharacterName,
                            presetId: selectedPresetId,
                        },
                    }));
                    this.hideOverlay();
                } catch (error) {
                    confirmButton.disabled = false;
                    confirmButton.classList.remove('success');
                    throw error;
                }
            });
            actions.appendChild(confirmButton);
            stageTwo.appendChild(actions);

            stageOne.hidden = true;
            stageTwo.hidden = false;
            this.playTypewriter(stream, this.buildPreviewCopy(preset));
        }

        showOverlay() {
            if (!this.overlay) {
                return;
            }
            this.prepareOverlayPointerEvents();
            this.updateHeaderCopy();
            this.overlay.hidden = false;
        }

        hideOverlay() {
            if (this.overlay) {
                this.overlay.hidden = true;
            }
            this.stopTypewriter();
            this.restoreOverlayPointerEvents();
        }
    }

    window.CharacterPersonalityOnboarding = new CharacterPersonalityOnboardingManager();
})();
