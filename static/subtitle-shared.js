(function() {
    'use strict';

    if (window.nekoSubtitleShared) {
        return;
    }

    var SETTINGS_EVENT = 'neko-subtitle-settings-change';
    var RENDER_EVENT = 'neko-subtitle-render-state';
    var DEFAULT_TEXT_OPACITY = 95;
    var DEFAULT_SIZE = 'medium';
    var DEFAULT_TRANSLATION_LANGUAGE = 'zh';
    var DEFAULT_UI_LOCALE = 'zh-CN';
    var SETTINGS_KEYS = {
        subtitleEnabled: 'subtitleEnabled',
        userLanguage: 'userLanguage',
        subtitleOpacity: 'subtitleOpacity',
        subtitleDragAnywhere: 'subtitleDragAnywhere',
        subtitleSize: 'subtitleSize'
    };
    var SIZE_PRESETS = {
        small: { width: 400, minHeight: 60 },
        medium: { width: 600, minHeight: 80 },
        large: { width: 800, minHeight: 100 }
    };
    var UI_KEY_MAP = {
        settingsBtn: 'subtitle.settings.settingsBtn',
        targetLang: 'subtitle.settings.targetLang',
        opacity: 'subtitle.settings.opacity',
        dragAnywhere: 'subtitle.settings.dragAnywhere',
        dragAria: 'subtitle.settings.dragAnywhere',
        size: 'subtitle.settings.size',
        sizeSmall: 'subtitle.settings.sizeSmall',
        sizeMedium: 'subtitle.settings.sizeMedium',
        sizeLarge: 'subtitle.settings.sizeLarge'
    };
    var UI_FALLBACK = {
        'zh-CN': {
            settingsBtn: '字幕设置',
            targetLang: '目标语言',
            opacity: '不透明度',
            dragAnywhere: '整体拖动',
            dragAria: '整体拖动开关',
            size: '大小',
            sizeSmall: '小',
            sizeMedium: '中',
            sizeLarge: '大'
        },
        'zh-TW': {
            settingsBtn: '字幕設定',
            targetLang: '目標語言',
            opacity: '不透明度',
            dragAnywhere: '整體拖動',
            dragAria: '整體拖動開關',
            size: '大小',
            sizeSmall: '小',
            sizeMedium: '中',
            sizeLarge: '大'
        },
        en: {
            settingsBtn: 'Subtitle Settings',
            targetLang: 'Target Language',
            opacity: 'Opacity',
            dragAnywhere: 'Drag Anywhere',
            dragAria: 'Drag Anywhere toggle',
            size: 'Size',
            sizeSmall: 'S',
            sizeMedium: 'M',
            sizeLarge: 'L'
        },
        ja: {
            settingsBtn: '字幕設定',
            targetLang: '翻訳先の言語',
            opacity: '不透明度',
            dragAnywhere: '全体ドラッグ',
            dragAria: '全体ドラッグの切り替え',
            size: 'サイズ',
            sizeSmall: '小',
            sizeMedium: '中',
            sizeLarge: '大'
        },
        ko: {
            settingsBtn: '자막 설정',
            targetLang: '대상 언어',
            opacity: '불투명도',
            dragAnywhere: '전체 드래그',
            dragAria: '전체 드래그 전환',
            size: '크기',
            sizeSmall: '소',
            sizeMedium: '중',
            sizeLarge: '대'
        },
        ru: {
            settingsBtn: 'Настройки субтитров',
            targetLang: 'Целевой язык',
            opacity: 'Непрозрачность',
            dragAnywhere: 'Перетаскивание везде',
            dragAria: 'Переключить перетаскивание',
            size: 'Размер',
            sizeSmall: 'М',
            sizeMedium: 'С',
            sizeLarge: 'Б'
        }
    };

    var settingsState = null;
    var renderState = null;

    function clone(obj) {
        return Object.assign({}, obj);
    }

    function hasOwn(obj, key) {
        return Object.prototype.hasOwnProperty.call(obj, key);
    }

    function normalizeTranslationLanguageCode(lang) {
        if (!lang) return DEFAULT_TRANSLATION_LANGUAGE;
        var value = String(lang).trim().toLowerCase();
        if (value.indexOf('ja') === 0) return 'ja';
        if (value.indexOf('en') === 0) return 'en';
        if (value.indexOf('ko') === 0) return 'ko';
        if (value.indexOf('ru') === 0) return 'ru';
        return 'zh';
    }

    function normalizeUiLocale(locale) {
        if (!locale) return DEFAULT_UI_LOCALE;
        var value = String(locale).trim();
        var lower = value.toLowerCase();
        if (lower.indexOf('zh') === 0) {
            if (/(tw|hk|hant)/i.test(value)) {
                return 'zh-TW';
            }
            return 'zh-CN';
        }
        if (lower.indexOf('ja') === 0) return 'ja';
        if (lower.indexOf('ko') === 0) return 'ko';
        if (lower.indexOf('ru') === 0) return 'ru';
        if (lower.indexOf('en') === 0) return 'en';
        return DEFAULT_UI_LOCALE;
    }

    function clampOpacity(value) {
        var number = parseInt(value, 10);
        if (!isFinite(number)) return DEFAULT_TEXT_OPACITY;
        return Math.max(20, Math.min(100, number));
    }

    function normalizeSizePreset(size) {
        return hasOwn(SIZE_PRESETS, size) ? size : DEFAULT_SIZE;
    }

    function getSizePreset(size) {
        return clone(SIZE_PRESETS[normalizeSizePreset(size)]);
    }

    function getCurrentUiLocale() {
        var source = '';
        try {
            if (window.i18next && window.i18next.language) {
                source = window.i18next.language;
            } else if (window.localStorage) {
                source = localStorage.getItem('i18nextLng') || '';
            }
        } catch (_) {}
        if (!source && document && document.documentElement) {
            source = document.documentElement.lang || '';
        }
        if (!source && navigator) {
            source = navigator.language || navigator.userLanguage || '';
        }
        return normalizeUiLocale(source);
    }

    function ensureSettingsState() {
        if (settingsState) {
            return settingsState;
        }
        settingsState = {
            subtitleEnabled: false,
            userLanguage: DEFAULT_TRANSLATION_LANGUAGE,
            subtitleOpacity: DEFAULT_TEXT_OPACITY,
            subtitleDragAnywhere: false,
            subtitleSize: DEFAULT_SIZE,
            uiLocale: getCurrentUiLocale()
        };
        try {
            settingsState.subtitleEnabled = localStorage.getItem(SETTINGS_KEYS.subtitleEnabled) === 'true';
            settingsState.userLanguage = normalizeTranslationLanguageCode(localStorage.getItem(SETTINGS_KEYS.userLanguage) || DEFAULT_TRANSLATION_LANGUAGE);
            settingsState.subtitleOpacity = clampOpacity(localStorage.getItem(SETTINGS_KEYS.subtitleOpacity));
            settingsState.subtitleDragAnywhere = localStorage.getItem(SETTINGS_KEYS.subtitleDragAnywhere) === 'true';
            settingsState.subtitleSize = normalizeSizePreset(localStorage.getItem(SETTINGS_KEYS.subtitleSize) || DEFAULT_SIZE);
        } catch (_) {}
        return settingsState;
    }

    function ensureRenderState() {
        if (renderState) {
            return renderState;
        }
        var current = ensureSettingsState();
        renderState = {
            text: '',
            visible: false,
            subtitleEnabled: current.subtitleEnabled,
            userLanguage: current.userLanguage,
            uiLocale: current.uiLocale,
            subtitleOpacity: current.subtitleOpacity,
            subtitleDragAnywhere: current.subtitleDragAnywhere,
            subtitleSize: current.subtitleSize
        };
        return renderState;
    }

    function writeSettingsToStorage(nextState, changedKeys) {
        try {
            if (changedKeys.indexOf('subtitleEnabled') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleEnabled, String(nextState.subtitleEnabled));
            }
            if (changedKeys.indexOf('userLanguage') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.userLanguage, nextState.userLanguage);
            }
            if (changedKeys.indexOf('subtitleOpacity') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleOpacity, String(nextState.subtitleOpacity));
            }
            if (changedKeys.indexOf('subtitleDragAnywhere') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleDragAnywhere, String(nextState.subtitleDragAnywhere));
            }
            if (changedKeys.indexOf('subtitleSize') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleSize, nextState.subtitleSize);
            }
        } catch (_) {}
    }

    function syncAppState(nextState, changedKeys) {
        if (!window.appState) return;
        if (changedKeys.indexOf('subtitleEnabled') !== -1) {
            window.appState.subtitleEnabled = nextState.subtitleEnabled;
        }
        if (changedKeys.indexOf('userLanguage') !== -1) {
            window.appState.userLanguage = nextState.userLanguage;
        }
    }

    function dispatchSettingsChange(nextState, changedKeys, source) {
        window.dispatchEvent(new CustomEvent(SETTINGS_EVENT, {
            detail: {
                state: clone(nextState),
                changedKeys: changedKeys.slice(),
                source: source || ''
            }
        }));
    }

    function updateRenderState(patch, options) {
        var current = ensureRenderState();
        var next = clone(current);
        var changedKeys = [];
        var keys = ['text', 'visible', 'subtitleEnabled', 'userLanguage', 'uiLocale', 'subtitleOpacity', 'subtitleDragAnywhere', 'subtitleSize'];
        var i;

        for (i = 0; i < keys.length; i++) {
            var key = keys[i];
            if (!hasOwn(patch, key)) continue;
            var value = patch[key];
            if (key === 'text') value = String(value || '');
            if (key === 'visible') value = !!value;
            if (key === 'subtitleEnabled') value = !!value;
            if (key === 'userLanguage') value = normalizeTranslationLanguageCode(value);
            if (key === 'uiLocale') value = normalizeUiLocale(value);
            if (key === 'subtitleOpacity') value = clampOpacity(value);
            if (key === 'subtitleDragAnywhere') value = !!value;
            if (key === 'subtitleSize') value = normalizeSizePreset(value);
            if (next[key] !== value) {
                next[key] = value;
                changedKeys.push(key);
            }
        }

        if (!changedKeys.length) {
            return clone(current);
        }

        renderState = next;
        window.dispatchEvent(new CustomEvent(RENDER_EVENT, {
            detail: {
                state: clone(next),
                changedKeys: changedKeys,
                source: options && options.source ? options.source : ''
            }
        }));
        return clone(next);
    }

    function updateSettings(patch, options) {
        var current = ensureSettingsState();
        var next = clone(current);
        var changedKeys = [];
        var uiLocale = hasOwn(patch, 'uiLocale')
            ? normalizeUiLocale(patch.uiLocale)
            : (options && options.refreshUiLocale ? getCurrentUiLocale() : current.uiLocale);

        if (hasOwn(patch, 'subtitleEnabled')) {
            next.subtitleEnabled = !!patch.subtitleEnabled;
        }
        if (hasOwn(patch, 'userLanguage')) {
            next.userLanguage = normalizeTranslationLanguageCode(patch.userLanguage);
        }
        if (hasOwn(patch, 'subtitleOpacity')) {
            next.subtitleOpacity = clampOpacity(patch.subtitleOpacity);
        }
        if (hasOwn(patch, 'subtitleDragAnywhere')) {
            next.subtitleDragAnywhere = !!patch.subtitleDragAnywhere;
        }
        if (hasOwn(patch, 'subtitleSize')) {
            next.subtitleSize = normalizeSizePreset(patch.subtitleSize);
        }
        next.uiLocale = uiLocale;

        var keys = ['subtitleEnabled', 'userLanguage', 'subtitleOpacity', 'subtitleDragAnywhere', 'subtitleSize', 'uiLocale'];
        for (var i = 0; i < keys.length; i++) {
            if (next[keys[i]] !== current[keys[i]]) {
                changedKeys.push(keys[i]);
            }
        }

        if (!changedKeys.length) {
            return clone(current);
        }

        settingsState = next;
        if (!options || options.persist !== false) {
            writeSettingsToStorage(next, changedKeys);
        }
        syncAppState(next, changedKeys);
        updateRenderState({
            subtitleEnabled: next.subtitleEnabled,
            userLanguage: next.userLanguage,
            uiLocale: next.uiLocale,
            subtitleOpacity: next.subtitleOpacity,
            subtitleDragAnywhere: next.subtitleDragAnywhere,
            subtitleSize: next.subtitleSize
        }, { source: options && options.source ? options.source : 'subtitle-settings' });
        if (!options || options.silent !== true) {
            dispatchSettingsChange(next, changedKeys, options && options.source);
        }
        return clone(next);
    }

    function getSettings() {
        return clone(ensureSettingsState());
    }

    function getRenderState() {
        return clone(ensureRenderState());
    }

    function subscribeToWindowEvent(eventName, listener, immediateState, immediateDetail) {
        function handler(evt) {
            if (!evt || !evt.detail) return;
            listener(evt.detail.state, evt.detail);
        }
        window.addEventListener(eventName, handler);
        if (immediateState) {
            listener(immediateState, immediateDetail || { changedKeys: [], source: 'init' });
        }
        return function unsubscribe() {
            window.removeEventListener(eventName, handler);
        };
    }

    function subscribeSettings(listener, options) {
        return subscribeToWindowEvent(
            SETTINGS_EVENT,
            listener,
            options && options.immediate === false ? null : getSettings(),
            { changedKeys: [], source: 'init' }
        );
    }

    function subscribeRenderState(listener, options) {
        return subscribeToWindowEvent(
            RENDER_EVENT,
            listener,
            options && options.immediate === false ? null : getRenderState(),
            { changedKeys: [], source: 'init' }
        );
    }

    function getUiText(key, uiLocale) {
        var i18nKey = UI_KEY_MAP[key];
        if (i18nKey && typeof window.t === 'function') {
            try {
                var translated = window.t(i18nKey);
                if (translated && translated !== i18nKey) {
                    return translated;
                }
            } catch (_) {}
        }
        var locale = normalizeUiLocale(uiLocale || ensureSettingsState().uiLocale || getCurrentUiLocale());
        var dictionary = UI_FALLBACK[locale] || UI_FALLBACK[DEFAULT_UI_LOCALE];
        return dictionary[key] || UI_FALLBACK[DEFAULT_UI_LOCALE][key] || key;
    }

    function query(root, selector) {
        if (!root) return null;
        if (typeof root.querySelector === 'function') {
            return root.querySelector(selector);
        }
        if (root.document && typeof root.document.querySelector === 'function') {
            return root.document.querySelector(selector);
        }
        return null;
    }

    function getSubtitleRefs(root) {
        return {
            display: query(root, '#subtitle-display'),
            scroll: query(root, '#subtitle-scroll'),
            text: query(root, '#subtitle-text'),
            settingsBtn: query(root, '#subtitle-settings-btn'),
            settingsPanel: query(root, '#subtitle-settings-panel'),
            labels: root && typeof root.querySelectorAll === 'function' ? root.querySelectorAll('.subtitle-settings-label') : [],
            langSelect: query(root, '#subtitle-lang-select'),
            opacitySlider: query(root, '#subtitle-opacity-slider'),
            opacityValue: query(root, '#subtitle-opacity-value'),
            dragModeToggle: query(root, '#subtitle-drag-mode-toggle'),
            sizeBtns: root && typeof root.querySelectorAll === 'function' ? root.querySelectorAll('.subtitle-size-btn') : [],
            dragHandle: query(root, '#subtitle-drag-handle')
        };
    }

    function applyBackgroundOpacity(display, opacity) {
        if (!display) return;
        var alpha = clampOpacity(opacity) / 100;
        display.style.background = 'linear-gradient(135deg, rgba(68,183,254,' + alpha + ') 0%, rgba(68,183,254,' + Math.max(0, alpha - 0.05) + ') 50%, rgba(100,200,255,' + alpha + ') 100%)';
    }

    function applySubtitlePreset(display, size, options) {
        if (!display) return getSizePreset(size);
        var preset = getSizePreset(size);
        display.dataset.subtitleSize = normalizeSizePreset(size);
        display.style.minHeight = preset.minHeight + 'px';
        if (!options || options.host !== 'window') {
            display.style.setProperty('--subtitle-max-width', preset.width + 'px');
        }
        return preset;
    }

    function applySettingsToUi(refs, state, options) {
        if (!refs || !refs.display) return;
        var host = options && options.host ? options.host : 'web';
        applyBackgroundOpacity(refs.display, state.subtitleOpacity);
        applySubtitlePreset(refs.display, state.subtitleSize, { host: host });
        refs.display.classList.toggle('drag-anywhere', !!state.subtitleDragAnywhere);
        if (refs.dragHandle) {
            refs.dragHandle.style.display = state.subtitleDragAnywhere ? 'none' : '';
        }
        if (refs.langSelect) {
            refs.langSelect.value = state.userLanguage;
        }
        if (refs.opacitySlider) {
            refs.opacitySlider.value = String(state.subtitleOpacity);
        }
        if (refs.opacityValue) {
            refs.opacityValue.textContent = state.subtitleOpacity + '%';
        }
        if (refs.dragModeToggle) {
            refs.dragModeToggle.checked = !!state.subtitleDragAnywhere;
        }
        if (refs.sizeBtns && refs.sizeBtns.length) {
            refs.sizeBtns.forEach(function(btn) {
                btn.classList.toggle('active', btn.dataset.size === state.subtitleSize);
            });
        }
    }

    function applyUiLabels(refs, state) {
        if (!refs) return;
        var locale = state && state.uiLocale ? state.uiLocale : getCurrentUiLocale();
        if (refs.labels && refs.labels.length) {
            refs.labels.forEach(function(label) {
                var key = label && label.dataset ? label.dataset.subtitleLabel : '';
                if (!key) return;
                label.textContent = getUiText(key, locale);
            });
        }
        if (refs.settingsBtn) {
            refs.settingsBtn.title = getUiText('settingsBtn', locale);
            refs.settingsBtn.setAttribute('aria-label', getUiText('settingsBtn', locale));
        }
        if (refs.langSelect) {
            refs.langSelect.title = getUiText('targetLang', locale);
        }
        if (refs.opacitySlider) {
            refs.opacitySlider.title = getUiText('opacity', locale);
        }
        if (refs.dragModeToggle) {
            refs.dragModeToggle.setAttribute('aria-label', getUiText('dragAria', locale));
        }
        if (refs.sizeBtns && refs.sizeBtns.length) {
            refs.sizeBtns.forEach(function(btn) {
                if (btn.dataset.size === 'small') btn.textContent = getUiText('sizeSmall', locale);
                if (btn.dataset.size === 'medium') btn.textContent = getUiText('sizeMedium', locale);
                if (btn.dataset.size === 'large') btn.textContent = getUiText('sizeLarge', locale);
            });
        }
    }

    function measureSubtitleLayout(options) {
        options = options || {};
        var text = String(options.text || '');
        var mode = options.mode || 'window';
        var preset = getSizePreset(options.presetKey);
        var baseFont = options.baseFont || 17;
        var minFont = options.minFont || 12;
        var fontFamily = options.fontFamily || 'Segoe UI, Arial, sans-serif';
        var maxWidth = options.maxWidth || preset.width;
        var minHeight = options.minHeight || preset.minHeight;
        var maxHeight = options.maxHeight || (mode === 'window' ? 280 : Math.max(minHeight, options.availableHeight || minHeight));
        var width = mode === 'window' ? maxWidth : (options.availableWidth || maxWidth);
        var node;
        var fontSize = baseFont;
        var finalHeight = minHeight;

        if (!document.body) {
            return { width: width, height: minHeight, fontSize: baseFont };
        }
        if (!text.trim()) {
            return { width: width, height: minHeight, fontSize: baseFont };
        }

        node = document.createElement(mode === 'window' ? 'div' : 'span');
        node.style.position = 'absolute';
        node.style.visibility = 'hidden';
        node.style.left = '-9999px';
        node.style.top = '-9999px';
        node.style.boxSizing = 'border-box';
        node.style.display = 'block';
        node.style.fontSize = baseFont + 'px';
        node.style.fontWeight = '500';
        node.style.lineHeight = '1.5';
        node.style.fontFamily = fontFamily;
        node.style.whiteSpace = 'nowrap';
        if (mode === 'window') {
            node.style.padding = '10px 18px';
        }
        node.textContent = text;
        document.body.appendChild(node);

        if (mode === 'window') {
            width = Math.max(200, Math.min(node.offsetWidth + 36, maxWidth));
            node.style.width = width + 'px';
        } else {
            width = Math.max(0, options.availableWidth || maxWidth);
            node.style.maxWidth = width + 'px';
            node.style.width = width + 'px';
        }
        node.style.whiteSpace = 'normal';
        node.style.overflowWrap = 'break-word';

        while (fontSize > minFont) {
            var overflowHeight = mode === 'window'
                ? node.offsetHeight + 60
                : node.offsetHeight;
            if (overflowHeight <= maxHeight) {
                break;
            }
            fontSize -= 1;
            node.style.fontSize = fontSize + 'px';
        }

        finalHeight = mode === 'window'
            ? Math.max(minHeight, Math.min(maxHeight, node.offsetHeight + 60))
            : Math.max(minHeight, node.offsetHeight);
        document.body.removeChild(node);

        return {
            width: mode === 'window' ? width : maxWidth,
            height: finalHeight,
            fontSize: fontSize
        };
    }

    function attachWebDrag(refs) {
        if (!refs.display || !refs.dragHandle) return function() {};

        var isDragging = false;
        var pendingDrag = false;
        var isManualPosition = false;
        var startX = 0;
        var startY = 0;
        var initialX = 0;
        var initialY = 0;

        function isDragAnywhereMode() {
            return refs.display.classList.contains('drag-anywhere');
        }

        function canStartDrag(target) {
            if (refs.settingsPanel && refs.settingsPanel.contains(target)) return false;
            if (refs.settingsBtn && refs.settingsBtn.contains(target)) return false;
            return true;
        }

        function handleMouseMove(e) {
            if (!pendingDrag && !isDragging) return;
            e.preventDefault();

            var dx = e.clientX - startX;
            var dy = e.clientY - startY;
            if (!isDragging) {
                if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
                isDragging = true;
                pendingDrag = false;
                isManualPosition = true;
                refs.display.style.animation = 'none';
                refs.display.style.transition = 'none';
                refs.display.classList.add('dragging');
                refs.display.style.transform = 'none';
                refs.display.style.left = initialX + 'px';
                refs.display.style.top = initialY + 'px';
                refs.display.style.bottom = 'auto';
            }

            var newX = initialX + dx;
            var newY = initialY + dy;
            var maxX = Math.max(0, window.innerWidth - refs.display.offsetWidth);
            var maxY = Math.max(0, window.innerHeight - refs.display.offsetHeight);

            refs.display.style.left = Math.max(0, Math.min(newX, maxX)) + 'px';
            refs.display.style.top = Math.max(0, Math.min(newY, maxY)) + 'px';
        }

        function handleMouseUp() {
            if (!pendingDrag && !isDragging) return;
            pendingDrag = false;
            isDragging = false;
            document.body.style.userSelect = '';
            refs.display.classList.remove('dragging');
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        }

        function beginDrag(e) {
            if (!canStartDrag(e.target)) return;
            if (typeof e.button === 'number' && e.button !== 0) return;
            pendingDrag = true;
            document.body.style.userSelect = 'none';
            var rect = refs.display.getBoundingClientRect();
            startX = e.clientX;
            startY = e.clientY;
            initialX = rect.left;
            initialY = rect.top;
            document.addEventListener('mousemove', handleMouseMove);
            document.addEventListener('mouseup', handleMouseUp);
        }

        function beginTouchDrag(e) {
            if (!e.touches || !e.touches.length) return;
            var touch = e.touches[0];
            beginDrag({
                target: e.target,
                button: 0,
                clientX: touch.clientX,
                clientY: touch.clientY
            });
        }

        function handleTouchMove(e) {
            if (!e.touches || !e.touches.length) return;
            var touch = e.touches[0];
            handleMouseMove({
                preventDefault: function() { e.preventDefault(); },
                clientX: touch.clientX,
                clientY: touch.clientY
            });
        }

        function clampManualPosition() {
            if (!isManualPosition) return;
            var rect = refs.display.getBoundingClientRect();
            var maxX = Math.max(0, window.innerWidth - refs.display.offsetWidth);
            var maxY = Math.max(0, window.innerHeight - refs.display.offsetHeight);

            if (rect.left < 0) refs.display.style.left = '0px';
            else if (rect.right > window.innerWidth) refs.display.style.left = maxX + 'px';
            if (rect.top < 0) refs.display.style.top = '0px';
            else if (rect.bottom > window.innerHeight) refs.display.style.top = maxY + 'px';
        }

        function onHandleMouseDown(e) {
            if (isDragAnywhereMode()) return;
            beginDrag(e);
        }

        function onHandleTouchStart(e) {
            if (isDragAnywhereMode()) return;
            beginTouchDrag(e);
        }

        function onDisplayMouseDown(e) {
            if (!isDragAnywhereMode()) return;
            beginDrag(e);
        }

        function onDisplayTouchStart(e) {
            if (!isDragAnywhereMode()) return;
            if (!canStartDrag(e.target)) return;
            beginTouchDrag(e);
        }

        refs.dragHandle.addEventListener('mousedown', onHandleMouseDown);
        refs.dragHandle.addEventListener('touchstart', onHandleTouchStart, { passive: false });
        refs.display.addEventListener('mousedown', onDisplayMouseDown);
        refs.display.addEventListener('touchstart', onDisplayTouchStart, { passive: false });
        document.addEventListener('touchmove', handleTouchMove, { passive: false });
        document.addEventListener('touchend', handleMouseUp);
        document.addEventListener('touchcancel', handleMouseUp);
        window.addEventListener('resize', clampManualPosition);

        return function detachWebDrag() {
            refs.dragHandle.removeEventListener('mousedown', onHandleMouseDown);
            refs.dragHandle.removeEventListener('touchstart', onHandleTouchStart, { passive: false });
            refs.display.removeEventListener('mousedown', onDisplayMouseDown);
            refs.display.removeEventListener('touchstart', onDisplayTouchStart, { passive: false });
            document.removeEventListener('touchmove', handleTouchMove, { passive: false });
            document.removeEventListener('touchend', handleMouseUp);
            document.removeEventListener('touchcancel', handleMouseUp);
            window.removeEventListener('resize', clampManualPosition);
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
    }

    function attachWindowDrag(refs, options) {
        var api = options && options.api;
        if (!refs.display || !refs.dragHandle || !api) return function() {};

        var isDragging = false;

        function isDragAnywhereMode() {
            return refs.display.classList.contains('drag-anywhere');
        }

        function bounceBackIfNeeded() {
            try {
                if (typeof window.screenX !== 'number') return;
                var margin = 30;
                var x = window.screenX;
                var y = window.screenY;
                var width = window.outerWidth;
                var height = window.outerHeight;
                var moved = false;

                if (!width || !height) return;
                if (x < 0) { x = 0; moved = true; }
                if (y < 0) { y = 0; moved = true; }
                if (x + width - margin > screen.availWidth) {
                    x = Math.max(0, screen.availWidth - width);
                    moved = true;
                }
                if (y + height - margin > screen.availHeight) {
                    y = Math.max(0, screen.availHeight - height);
                    moved = true;
                }
                if (moved) window.moveTo(x, y);
            } catch (_) {}
        }

        function canStartDrag(target) {
            if (refs.settingsPanel && refs.settingsPanel.contains(target)) return false;
            if (refs.settingsBtn && refs.settingsBtn.contains(target)) return false;
            return true;
        }

        function startDrag(e) {
            if (!canStartDrag(e.target)) return;
            isDragging = true;
            if (typeof api.dragStart === 'function') {
                api.dragStart();
            }
            if (e.preventDefault) e.preventDefault();
        }

        function stopDrag() {
            if (!isDragging) return;
            isDragging = false;
            refs.dragHandle.style.cursor = '';
            if (typeof api.dragStop === 'function') {
                api.dragStop();
            }
            bounceBackIfNeeded();
        }

        function onHandleMouseDown(e) {
            if (isDragAnywhereMode()) return;
            startDrag(e);
            refs.dragHandle.style.cursor = 'grabbing';
        }

        function onHandleTouchStart(e) {
            if (isDragAnywhereMode()) return;
            startDrag(e);
            refs.dragHandle.style.cursor = 'grabbing';
        }

        function onDisplayMouseDown(e) {
            if (!isDragAnywhereMode()) return;
            startDrag(e);
        }

        function onDisplayTouchStart(e) {
            if (!isDragAnywhereMode()) return;
            startDrag(e);
        }

        refs.dragHandle.addEventListener('mousedown', onHandleMouseDown);
        refs.dragHandle.addEventListener('touchstart', onHandleTouchStart, { passive: false });
        refs.display.addEventListener('mousedown', onDisplayMouseDown);
        refs.display.addEventListener('touchstart', onDisplayTouchStart, { passive: false });
        document.addEventListener('mouseup', stopDrag);
        document.addEventListener('touchend', stopDrag);
        document.addEventListener('touchcancel', stopDrag);
        window.addEventListener('focus', bounceBackIfNeeded);
        window.addEventListener('resize', bounceBackIfNeeded);

        return function detachWindowDrag() {
            refs.dragHandle.removeEventListener('mousedown', onHandleMouseDown);
            refs.dragHandle.removeEventListener('touchstart', onHandleTouchStart, { passive: false });
            refs.display.removeEventListener('mousedown', onDisplayMouseDown);
            refs.display.removeEventListener('touchstart', onDisplayTouchStart, { passive: false });
            document.removeEventListener('mouseup', stopDrag);
            document.removeEventListener('touchend', stopDrag);
            document.removeEventListener('touchcancel', stopDrag);
            window.removeEventListener('focus', bounceBackIfNeeded);
            window.removeEventListener('resize', bounceBackIfNeeded);
        };
    }

    function initSubtitleUI(options) {
        options = options || {};
        var refs = getSubtitleRefs(options.root || document);
        var cleanupFns = [];
        var state = getSettings();

        if (!refs.display) {
            return null;
        }

        function applyState(nextState, detail) {
            applySettingsToUi(refs, nextState, options);
            applyUiLabels(refs, nextState);
            if (typeof options.onSettingsApplied === 'function') {
                options.onSettingsApplied(nextState, refs, detail || { changedKeys: [], source: 'init' });
            }
        }

        applyState(state, { changedKeys: [], source: 'init' });
        cleanupFns.push(subscribeSettings(applyState, { immediate: false }));

        if (window.i18next && typeof window.i18next.on === 'function') {
            var onLanguageChanged = function(nextLocale) {
                updateSettings({ uiLocale: nextLocale }, {
                    persist: false,
                    source: 'subtitle-ui-locale'
                });
            };
            window.i18next.on('languageChanged', onLanguageChanged);
            cleanupFns.push(function() {
                if (window.i18next && typeof window.i18next.off === 'function') {
                    window.i18next.off('languageChanged', onLanguageChanged);
                }
            });
        }

        if (refs.settingsBtn && refs.settingsPanel) {
            var onSettingsClick = function(e) {
                e.stopPropagation();
                refs.settingsPanel.classList.toggle('hidden');
            };
            var onDocumentDown = function(e) {
                if (refs.settingsPanel.classList.contains('hidden')) return;
                if (refs.settingsPanel.contains(e.target)) return;
                if (refs.settingsBtn.contains(e.target)) return;
                refs.settingsPanel.classList.add('hidden');
            };
            refs.settingsBtn.addEventListener('click', onSettingsClick);
            document.addEventListener('mousedown', onDocumentDown);
            cleanupFns.push(function() {
                refs.settingsBtn.removeEventListener('click', onSettingsClick);
                document.removeEventListener('mousedown', onDocumentDown);
            });
        }

        if (refs.langSelect) {
            var onLanguageSelect = function() {
                var nextLanguage = normalizeTranslationLanguageCode(refs.langSelect.value);
                var nextState = updateSettings({ userLanguage: nextLanguage }, { source: 'subtitle-ui-language' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'language',
                        value: nextLanguage,
                        patch: { userLanguage: nextLanguage },
                        state: nextState
                    });
                }
                if (typeof options.onLanguageChange === 'function') {
                    options.onLanguageChange(nextLanguage, nextState);
                }
            };
            refs.langSelect.addEventListener('change', onLanguageSelect);
            cleanupFns.push(function() {
                refs.langSelect.removeEventListener('change', onLanguageSelect);
            });
        }

        if (refs.opacitySlider) {
            var onOpacityInput = function() {
                var nextOpacity = clampOpacity(refs.opacitySlider.value);
                var nextState = updateSettings({ subtitleOpacity: nextOpacity }, { source: 'subtitle-ui-opacity' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'opacity',
                        value: nextOpacity,
                        patch: { subtitleOpacity: nextOpacity },
                        state: nextState
                    });
                }
            };
            refs.opacitySlider.addEventListener('input', onOpacityInput);
            cleanupFns.push(function() {
                refs.opacitySlider.removeEventListener('input', onOpacityInput);
            });
        }

        if (refs.dragModeToggle) {
            var onDragModeChange = function() {
                var enabled = !!refs.dragModeToggle.checked;
                var nextState = updateSettings({ subtitleDragAnywhere: enabled }, { source: 'subtitle-ui-drag-mode' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'dragAnywhere',
                        value: enabled,
                        patch: { subtitleDragAnywhere: enabled },
                        state: nextState
                    });
                }
            };
            refs.dragModeToggle.addEventListener('change', onDragModeChange);
            cleanupFns.push(function() {
                refs.dragModeToggle.removeEventListener('change', onDragModeChange);
            });
        }

        if (refs.sizeBtns && refs.sizeBtns.length) {
            refs.sizeBtns.forEach(function(btn) {
                var onSizeClick = function() {
                    var nextSize = normalizeSizePreset(btn.dataset.size);
                    var nextState = updateSettings({ subtitleSize: nextSize }, { source: 'subtitle-ui-size' });
                    if (typeof options.propagateSetting === 'function') {
                        options.propagateSetting({
                            type: 'size',
                            value: nextSize,
                            patch: { subtitleSize: nextSize },
                            state: nextState
                        });
                    }
                };
                btn.addEventListener('click', onSizeClick);
                cleanupFns.push(function() {
                    btn.removeEventListener('click', onSizeClick);
                });
            });
        }

        cleanupFns.push(options.host === 'window' ? attachWindowDrag(refs, options) : attachWebDrag(refs));

        return {
            refs: refs,
            applyCurrentState: function() {
                applyState(getSettings(), { changedKeys: [], source: 'manual' });
            },
            destroy: function() {
                while (cleanupFns.length) {
                    var fn = cleanupFns.pop();
                    if (typeof fn === 'function') fn();
                }
            }
        };
    }

    ensureSettingsState();
    ensureRenderState();

    window.nekoSubtitleShared = {
        SETTINGS_EVENT: SETTINGS_EVENT,
        RENDER_EVENT: RENDER_EVENT,
        getSettings: getSettings,
        updateSettings: updateSettings,
        getRenderState: getRenderState,
        updateRenderState: updateRenderState,
        subscribeSettings: subscribeSettings,
        subscribeRenderState: subscribeRenderState,
        normalizeTranslationLanguageCode: normalizeTranslationLanguageCode,
        normalizeUiLocale: normalizeUiLocale,
        getCurrentUiLocale: getCurrentUiLocale,
        getUiText: getUiText,
        getSizePreset: getSizePreset,
        applySubtitlePreset: applySubtitlePreset,
        applyBackgroundOpacity: applyBackgroundOpacity,
        measureSubtitleLayout: measureSubtitleLayout,
        initSubtitleUI: initSubtitleUI
    };
})();
