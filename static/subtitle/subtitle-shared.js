(function() {
    'use strict';

    if (window.nekoSubtitleShared) {
        return;
    }

    var SETTINGS_EVENT = 'neko-subtitle-settings-change';
    var RENDER_EVENT = 'neko-subtitle-render-state';
    var DEFAULT_BACKGROUND_OPACITY = 25;
    var DEFAULT_PANEL_BOUNDS = { width: 655, height: 109 };
    var MIN_SUBTITLE_CONTROL_SCALE = 1;
    var MAX_SUBTITLE_CONTROL_SCALE = 2;
    var DEFAULT_SUBTITLE_FONT_SIZE = 26;
    var SUBTITLE_FONT_SIZE_OPTIONS = [16, 21, 26, 34, 44];
    var DEFAULT_SUBTITLE_COLOR_SCHEME = 'default';
    var SUBTITLE_COLOR_SCHEME_OPTIONS = ['default', 'red', 'orange', 'yellow', 'green', 'blue', 'indigo', 'violet'];
    // Keep the three 22px controls and the corner decorations inside the panel,
    // with comfortable vertical space around the controls.
    var MIN_PANEL_WIDTH = 228;
    var MIN_PANEL_HEIGHT = 40;
    var DEFAULT_TRANSLATION_LANGUAGE = 'zh';
    var DEFAULT_UI_LOCALE = 'zh-CN';
    var CONTROLS_HIDE_DELAY_MS = 600;
    var PANEL_TEXT_HORIZONTAL_RESERVE = 110;
    var AUTO_SCROLL_DELAY_MS = 120;
    var AUTO_SCROLL_SPEED_PX_PER_SECOND = 90;
    var AUTO_SCROLL_USER_OVERRIDE_MS = 2500;
    var SETTINGS_KEYS = {
        subtitleEnabled: 'subtitleEnabled',
        userLanguage: 'userLanguage',
        subtitleOpacity: 'subtitleOpacity',
        subtitlePanelBounds: 'subtitlePanelBounds',
        subtitlePanelPosition: 'subtitlePanelPosition',
        subtitlePanelLocked: 'subtitlePanelLocked',
        subtitleInteractionPassthrough: 'subtitleInteractionPassthrough',
        subtitleDanmakuMode: 'subtitleDanmakuMode',
        subtitleFontSize: 'subtitleFontSize',
        subtitleColorScheme: 'subtitleColorScheme'
    };
    var UI_KEY_MAP = {
        settingsBtn: 'subtitle.settings.settingsBtn',
        lockPosition: 'subtitle.settings.lockPosition',
        unlockPosition: 'subtitle.settings.unlockPosition',
        closePanel: 'subtitle.settings.closePanel',
        targetLang: 'subtitle.settings.targetLang',
        opacity: 'subtitle.settings.opacity',
        fontSize: 'subtitle.settings.fontSize',
        fontSizeSmall: 'subtitle.settings.fontSizeSmall',
        fontSizeSmaller: 'subtitle.settings.fontSizeSmaller',
        fontSizeDefault: 'subtitle.settings.fontSizeDefault',
        fontSizeLarger: 'subtitle.settings.fontSizeLarger',
        fontSizeLarge: 'subtitle.settings.fontSizeLarge',
        colorScheme: 'subtitle.settings.colorScheme',
        colorSchemeDefault: 'subtitle.settings.colorSchemeDefault',
        colorSchemeRed: 'subtitle.settings.colorSchemeRed',
        colorSchemeOrange: 'subtitle.settings.colorSchemeOrange',
        colorSchemeYellow: 'subtitle.settings.colorSchemeYellow',
        colorSchemeGreen: 'subtitle.settings.colorSchemeGreen',
        colorSchemeBlue: 'subtitle.settings.colorSchemeBlue',
        colorSchemeIndigo: 'subtitle.settings.colorSchemeIndigo',
        colorSchemeViolet: 'subtitle.settings.colorSchemeViolet',
        danmakuMode: 'subtitle.settings.danmakuMode',
        emptyHint: 'subtitle.display.emptyHint'
    };
    var LOCK_ICON_PATH = 'M7 10V7a5 5 0 0110 0v3h1a1 1 0 011 1v9a1 1 0 01-1 1H6a1 1 0 01-1-1v-9a1 1 0 011-1h1zm2 0h6V7a3 3 0 00-6 0v3z';
    var UNLOCK_ICON_PATH = 'M12 17a2 2 0 100-4 2 2 0 000 4zm6-7h-8V7a3 3 0 015.64-1.44 1 1 0 001.73-1A5 5 0 008 7v3H6a1 1 0 00-1 1v9a1 1 0 001 1h12a1 1 0 001-1v-9a1 1 0 00-1-1z';
    var UI_FALLBACK = {
        'zh-CN': {
            settingsBtn: '字幕设置',
            lockPosition: '锁定位置',
            unlockPosition: '解锁位置',
            closePanel: '关闭翻译面板',
            targetLang: '语言',
            opacity: '不透明度',
            fontSize: '字体',
            fontSizeSmall: '小号',
            fontSizeSmaller: '较小',
            fontSizeDefault: '默认',
            fontSizeLarger: '较大',
            fontSizeLarge: '大号',
            colorScheme: '配色',
            colorSchemeDefault: '默认',
            colorSchemeRed: '红',
            colorSchemeOrange: '橙',
            colorSchemeYellow: '黄',
            colorSchemeGreen: '绿',
            colorSchemeBlue: '蓝',
            colorSchemeIndigo: '靛',
            colorSchemeViolet: '紫',
            danmakuMode: '弹幕模式',
            emptyHint: '暂无翻译内容'
        },
        'zh-TW': {
            settingsBtn: '字幕設定',
            lockPosition: '鎖定位置',
            unlockPosition: '解鎖位置',
            closePanel: '關閉翻譯面板',
            targetLang: '語言',
            opacity: '不透明度',
            fontSize: '字體',
            fontSizeSmall: '小號',
            fontSizeSmaller: '較小',
            fontSizeDefault: '預設',
            fontSizeLarger: '較大',
            fontSizeLarge: '大號',
            colorScheme: '配色',
            colorSchemeDefault: '預設',
            colorSchemeRed: '紅',
            colorSchemeOrange: '橙',
            colorSchemeYellow: '黃',
            colorSchemeGreen: '綠',
            colorSchemeBlue: '藍',
            colorSchemeIndigo: '靛',
            colorSchemeViolet: '紫',
            danmakuMode: '彈幕模式',
            emptyHint: '暫無翻譯內容'
        },
        en: {
            settingsBtn: 'Subtitle Settings',
            lockPosition: 'Lock position',
            unlockPosition: 'Unlock position',
            closePanel: 'Close translation panel',
            targetLang: 'Language',
            opacity: 'Opacity',
            fontSize: 'Font',
            fontSizeSmall: 'Small',
            fontSizeSmaller: 'Smaller',
            fontSizeDefault: 'Default',
            fontSizeLarger: 'Larger',
            fontSizeLarge: 'Large',
            colorScheme: 'Color',
            colorSchemeDefault: 'Default',
            colorSchemeRed: 'Red',
            colorSchemeOrange: 'Orange',
            colorSchemeYellow: 'Yellow',
            colorSchemeGreen: 'Green',
            colorSchemeBlue: 'Blue',
            colorSchemeIndigo: 'Indigo',
            colorSchemeViolet: 'Violet',
            danmakuMode: 'Danmaku mode',
            emptyHint: 'No translation yet'
        },
        es: {
            settingsBtn: 'Configuración de subtítulos',
            lockPosition: 'Bloquear posición',
            unlockPosition: 'Desbloquear posición',
            closePanel: 'Cerrar panel de traducción',
            targetLang: 'Idioma',
            opacity: 'Opacidad',
            fontSize: 'Fuente',
            fontSizeSmall: 'Pequeño',
            fontSizeSmaller: 'Más pequeño',
            fontSizeDefault: 'Predeterminado',
            fontSizeLarger: 'Más grande',
            fontSizeLarge: 'Grande',
            colorScheme: 'Color',
            colorSchemeDefault: 'Predeterminado',
            colorSchemeRed: 'Rojo',
            colorSchemeOrange: 'Naranja',
            colorSchemeYellow: 'Amarillo',
            colorSchemeGreen: 'Verde',
            colorSchemeBlue: 'Azul',
            colorSchemeIndigo: 'Índigo',
            colorSchemeViolet: 'Violeta',
            danmakuMode: 'Modo Danmaku',
            emptyHint: 'Sin traducción todavía'
        },
        pt: {
            settingsBtn: 'Configurações de legenda',
            lockPosition: 'Bloquear posição',
            unlockPosition: 'Desbloquear posição',
            closePanel: 'Fechar painel de tradução',
            targetLang: 'Idioma',
            opacity: 'Opacidade',
            fontSize: 'Fonte',
            fontSizeSmall: 'Pequeno',
            fontSizeSmaller: 'Menor',
            fontSizeDefault: 'Padrão',
            fontSizeLarger: 'Maior',
            fontSizeLarge: 'Grande',
            colorScheme: 'Cor',
            colorSchemeDefault: 'Padrão',
            colorSchemeRed: 'Vermelho',
            colorSchemeOrange: 'Laranja',
            colorSchemeYellow: 'Amarelo',
            colorSchemeGreen: 'Verde',
            colorSchemeBlue: 'Azul',
            colorSchemeIndigo: 'Índigo',
            colorSchemeViolet: 'Violeta',
            danmakuMode: 'Modo Danmaku',
            emptyHint: 'Sem tradução ainda'
        },
        ja: {
            settingsBtn: '字幕設定',
            lockPosition: '位置をロック',
            unlockPosition: '位置ロックを解除',
            closePanel: '翻訳パネルを閉じる',
            targetLang: '言語',
            opacity: '不透明度',
            fontSize: '文字',
            fontSizeSmall: '小さめ',
            fontSizeSmaller: 'やや小さめ',
            fontSizeDefault: '標準',
            fontSizeLarger: 'やや大きめ',
            fontSizeLarge: '大きめ',
            colorScheme: '配色',
            colorSchemeDefault: '標準',
            colorSchemeRed: '赤',
            colorSchemeOrange: '橙',
            colorSchemeYellow: '黄',
            colorSchemeGreen: '緑',
            colorSchemeBlue: '青',
            colorSchemeIndigo: '藍',
            colorSchemeViolet: '紫',
            danmakuMode: '弾幕モード',
            emptyHint: '翻訳はまだありません'
        },
        ko: {
            settingsBtn: '자막 설정',
            lockPosition: '위치 잠금',
            unlockPosition: '위치 잠금 해제',
            closePanel: '번역 패널 닫기',
            targetLang: '언어',
            opacity: '불투명도',
            fontSize: '글자',
            fontSizeSmall: '작게',
            fontSizeSmaller: '조금 작게',
            fontSizeDefault: '기본',
            fontSizeLarger: '조금 크게',
            fontSizeLarge: '크게',
            colorScheme: '색상',
            colorSchemeDefault: '기본',
            colorSchemeRed: '빨강',
            colorSchemeOrange: '주황',
            colorSchemeYellow: '노랑',
            colorSchemeGreen: '초록',
            colorSchemeBlue: '파랑',
            colorSchemeIndigo: '남색',
            colorSchemeViolet: '보라',
            danmakuMode: '탄막 모드',
            emptyHint: '아직 번역이 없습니다'
        },
        ru: {
            settingsBtn: 'Настройки субтитров',
            lockPosition: 'Заблокировать положение',
            unlockPosition: 'Разблокировать положение',
            closePanel: 'Закрыть панель перевода',
            targetLang: 'Язык',
            opacity: 'Непрозрачность',
            fontSize: 'Шрифт',
            fontSizeSmall: 'Малый',
            fontSizeSmaller: 'Меньше',
            fontSizeDefault: 'По умолчанию',
            fontSizeLarger: 'Больше',
            fontSizeLarge: 'Крупный',
            colorScheme: 'Цвет',
            colorSchemeDefault: 'По умолчанию',
            colorSchemeRed: 'Красный',
            colorSchemeOrange: 'Оранжевый',
            colorSchemeYellow: 'Желтый',
            colorSchemeGreen: 'Зеленый',
            colorSchemeBlue: 'Синий',
            colorSchemeIndigo: 'Индиго',
            colorSchemeViolet: 'Фиолетовый',
            danmakuMode: 'Режим данмаку',
            emptyHint: 'Перевода пока нет'
        }
    };

    var settingsState = null;
    var renderState = null;

    function clonePanelPosition(position) {
        if (!position) return null;
        return {
            left: position.left,
            top: position.top,
            coordinateSpace: position.coordinateSpace
        };
    }

    function clonePanelBounds(bounds) {
        if (!bounds) return null;
        return {
            width: bounds.width,
            height: bounds.height
        };
    }

    function clone(obj) {
        var next = Object.assign({}, obj);
        if (obj && hasOwn(obj, 'subtitlePanelPosition')) {
            next.subtitlePanelPosition = clonePanelPosition(obj.subtitlePanelPosition);
        }
        if (obj && hasOwn(obj, 'subtitlePanelBounds')) {
            next.subtitlePanelBounds = clonePanelBounds(obj.subtitlePanelBounds);
        }
        return next;
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
        if (value.indexOf('es') === 0) return 'es';
        if (value.indexOf('pt') === 0) return 'pt';
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
        if (lower.indexOf('es') === 0) return 'es';
        if (lower.indexOf('pt') === 0) return 'pt';
        if (lower.indexOf('en') === 0) return 'en';
        return DEFAULT_UI_LOCALE;
    }

    function clampOpacity(value) {
        var number = parseInt(value, 10);
        if (!isFinite(number)) return DEFAULT_BACKGROUND_OPACITY;
        return Math.max(0, Math.min(100, number));
    }

    function normalizeSubtitleFontSize(value) {
        var number = parseInt(value, 10);
        if (!isFinite(number)) return DEFAULT_SUBTITLE_FONT_SIZE;
        for (var i = 0; i < SUBTITLE_FONT_SIZE_OPTIONS.length; i += 1) {
            if (number === SUBTITLE_FONT_SIZE_OPTIONS[i]) {
                return number;
            }
        }
        return DEFAULT_SUBTITLE_FONT_SIZE;
    }

    function normalizeSubtitleColorScheme(value) {
        var scheme = String(value || '').trim().toLowerCase();
        for (var i = 0; i < SUBTITLE_COLOR_SCHEME_OPTIONS.length; i += 1) {
            if (scheme === SUBTITLE_COLOR_SCHEME_OPTIONS[i]) {
                return scheme;
            }
        }
        return DEFAULT_SUBTITLE_COLOR_SCHEME;
    }

    function formatAlpha(value) {
        return String(Math.round(value * 100) / 100);
    }

    function normalizePanelPosition(position) {
        var value = position;
        if (!value) return null;
        if (typeof value === 'string') {
            try {
                value = JSON.parse(value);
            } catch (_) {
                return null;
            }
        }
        if (!value || typeof value !== 'object') return null;
        var rawLeft = hasOwn(value, 'left') ? value.left : value.x;
        var rawTop = hasOwn(value, 'top') ? value.top : value.y;
        var left = Number(rawLeft);
        var top = Number(rawTop);
        if (!isFinite(left) || !isFinite(top)) return null;
        return {
            left: Math.max(0, left),
            top: Math.max(0, top),
            coordinateSpace: 'viewport'
        };
    }

    function samePanelPosition(a, b) {
        if (!a && !b) return true;
        if (!a || !b) return false;
        return a.left === b.left &&
            a.top === b.top &&
            a.coordinateSpace === b.coordinateSpace;
    }

    function normalizePanelBounds(bounds) {
        var value = bounds;
        if (!value) return null;
        if (typeof value === 'string') {
            try {
                value = JSON.parse(value);
            } catch (_) {
                return null;
            }
        }
        if (!value || typeof value !== 'object') return null;
        var rawWidth = hasOwn(value, 'width') ? value.width : value.w;
        var rawHeight = hasOwn(value, 'height') ? value.height : value.h;
        var width = Number(rawWidth);
        var height = Number(rawHeight);
        if (!isFinite(width) || !isFinite(height)) return null;
        return {
            width: Math.max(MIN_PANEL_WIDTH, Math.round(width)),
            height: Math.max(MIN_PANEL_HEIGHT, Math.round(height))
        };
    }

    function getPanelBounds(bounds) {
        return normalizePanelBounds(bounds) || clonePanelBounds(DEFAULT_PANEL_BOUNDS);
    }

    function samePanelBounds(a, b) {
        if (!a && !b) return true;
        if (!a || !b) return false;
        return a.width === b.width && a.height === b.height;
    }

    function normalizePanelState(state) {
        var value = String(state || '').trim().toLowerCase();
        if (value === 'controls' || value === 'settings') return value;
        return 'clean';
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
            subtitleOpacity: DEFAULT_BACKGROUND_OPACITY,
            subtitlePanelBounds: clonePanelBounds(DEFAULT_PANEL_BOUNDS),
            subtitlePanelPosition: null,
            subtitlePanelLocked: false,
            subtitleInteractionPassthrough: false,
            subtitleDanmakuMode: false,
            subtitleFontSize: DEFAULT_SUBTITLE_FONT_SIZE,
            subtitleColorScheme: DEFAULT_SUBTITLE_COLOR_SCHEME,
            uiLocale: getCurrentUiLocale()
        };
        try {
            settingsState.subtitleEnabled = localStorage.getItem(SETTINGS_KEYS.subtitleEnabled) === 'true';
            settingsState.userLanguage = normalizeTranslationLanguageCode(localStorage.getItem(SETTINGS_KEYS.userLanguage) || DEFAULT_TRANSLATION_LANGUAGE);
            settingsState.subtitleOpacity = clampOpacity(localStorage.getItem(SETTINGS_KEYS.subtitleOpacity));
            settingsState.subtitlePanelBounds = getPanelBounds(localStorage.getItem(SETTINGS_KEYS.subtitlePanelBounds));
            settingsState.subtitlePanelPosition = normalizePanelPosition(localStorage.getItem(SETTINGS_KEYS.subtitlePanelPosition));
            var storedLocked = localStorage.getItem(SETTINGS_KEYS.subtitlePanelLocked);
            var storedPassthrough = localStorage.getItem(SETTINGS_KEYS.subtitleInteractionPassthrough);
            if (storedLocked !== null) {
                settingsState.subtitlePanelLocked = storedLocked === 'true';
                settingsState.subtitleInteractionPassthrough = settingsState.subtitlePanelLocked;
            } else if (storedPassthrough !== null) {
                settingsState.subtitlePanelLocked = false;
                settingsState.subtitleInteractionPassthrough = storedPassthrough !== 'false';
            } else {
                settingsState.subtitleInteractionPassthrough = settingsState.subtitlePanelLocked;
            }
            settingsState.subtitleDanmakuMode = localStorage.getItem(SETTINGS_KEYS.subtitleDanmakuMode) === 'true';
            settingsState.subtitleFontSize = normalizeSubtitleFontSize(localStorage.getItem(SETTINGS_KEYS.subtitleFontSize));
            settingsState.subtitleColorScheme = normalizeSubtitleColorScheme(localStorage.getItem(SETTINGS_KEYS.subtitleColorScheme));
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
            subtitlePanelBounds: clonePanelBounds(current.subtitlePanelBounds),
            subtitlePanelPosition: clonePanelPosition(current.subtitlePanelPosition),
            subtitlePanelLocked: current.subtitlePanelLocked,
            subtitleInteractionPassthrough: current.subtitleInteractionPassthrough,
            subtitleDanmakuMode: current.subtitleDanmakuMode,
            subtitleFontSize: current.subtitleFontSize,
            subtitleColorScheme: current.subtitleColorScheme,
            subtitlePanelState: 'clean'
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
            if (changedKeys.indexOf('subtitlePanelBounds') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitlePanelBounds, JSON.stringify(nextState.subtitlePanelBounds));
            }
            if (changedKeys.indexOf('subtitlePanelPosition') !== -1) {
                if (nextState.subtitlePanelPosition) {
                    localStorage.setItem(SETTINGS_KEYS.subtitlePanelPosition, JSON.stringify(nextState.subtitlePanelPosition));
                } else {
                    localStorage.removeItem(SETTINGS_KEYS.subtitlePanelPosition);
                }
            }
            if (changedKeys.indexOf('subtitlePanelLocked') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitlePanelLocked, String(nextState.subtitlePanelLocked));
            }
            if (changedKeys.indexOf('subtitleInteractionPassthrough') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleInteractionPassthrough, String(nextState.subtitleInteractionPassthrough));
            }
            if (changedKeys.indexOf('subtitleDanmakuMode') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleDanmakuMode, String(nextState.subtitleDanmakuMode));
            }
            if (changedKeys.indexOf('subtitleFontSize') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleFontSize, String(nextState.subtitleFontSize));
            }
            if (changedKeys.indexOf('subtitleColorScheme') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleColorScheme, nextState.subtitleColorScheme);
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
        var keys = [
            'text', 'visible', 'subtitleEnabled', 'userLanguage', 'uiLocale',
            'subtitleOpacity', 'subtitlePanelBounds',
            'subtitlePanelPosition', 'subtitlePanelLocked',
            'subtitleInteractionPassthrough', 'subtitleDanmakuMode', 'subtitleFontSize',
            'subtitleColorScheme', 'subtitlePanelState'
        ];
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
            if (key === 'subtitlePanelBounds') value = getPanelBounds(value);
            if (key === 'subtitlePanelPosition') value = normalizePanelPosition(value);
            if (key === 'subtitlePanelLocked') value = !!value;
            if (key === 'subtitleInteractionPassthrough') value = value !== false;
            if (key === 'subtitleDanmakuMode') value = !!value;
            if (key === 'subtitleFontSize') value = normalizeSubtitleFontSize(value);
            if (key === 'subtitleColorScheme') value = normalizeSubtitleColorScheme(value);
            if (key === 'subtitlePanelState') value = normalizePanelState(value);
            var changed = key === 'subtitlePanelPosition'
                ? !samePanelPosition(next[key], value)
                : (key === 'subtitlePanelBounds' ? !samePanelBounds(next[key], value) : next[key] !== value);
            if (changed) {
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
        if (hasOwn(patch, 'subtitlePanelBounds')) {
            next.subtitlePanelBounds = getPanelBounds(patch.subtitlePanelBounds);
        }
        if (hasOwn(patch, 'subtitlePanelPosition')) {
            next.subtitlePanelPosition = normalizePanelPosition(patch.subtitlePanelPosition);
        }
        if (hasOwn(patch, 'subtitlePanelLocked') || hasOwn(patch, 'subtitleInteractionPassthrough')) {
            var hasPanelLocked = hasOwn(patch, 'subtitlePanelLocked');
            var hasInteractionPassthrough = hasOwn(patch, 'subtitleInteractionPassthrough');
            if (hasPanelLocked) {
                next.subtitlePanelLocked = !!patch.subtitlePanelLocked;
            }
            if (hasInteractionPassthrough) {
                next.subtitleInteractionPassthrough = patch.subtitleInteractionPassthrough !== false;
            } else if (hasPanelLocked) {
                next.subtitleInteractionPassthrough = next.subtitlePanelLocked;
            }
        }
        if (hasOwn(patch, 'subtitleDanmakuMode')) {
            next.subtitleDanmakuMode = !!patch.subtitleDanmakuMode;
        }
        if (hasOwn(patch, 'subtitleFontSize')) {
            next.subtitleFontSize = normalizeSubtitleFontSize(patch.subtitleFontSize);
        }
        if (hasOwn(patch, 'subtitleColorScheme')) {
            next.subtitleColorScheme = normalizeSubtitleColorScheme(patch.subtitleColorScheme);
        }
        next.uiLocale = uiLocale;

        var keys = [
            'subtitleEnabled', 'userLanguage', 'subtitleOpacity',
            'subtitlePanelBounds', 'subtitlePanelPosition',
            'subtitlePanelLocked', 'subtitleInteractionPassthrough',
            'subtitleDanmakuMode', 'subtitleFontSize', 'subtitleColorScheme', 'uiLocale'
        ];
        for (var i = 0; i < keys.length; i++) {
            var key = keys[i];
            var changed = key === 'subtitlePanelPosition'
                ? !samePanelPosition(next[key], current[key])
                : (key === 'subtitlePanelBounds' ? !samePanelBounds(next[key], current[key]) : next[key] !== current[key]);
            if (changed) {
                changedKeys.push(key);
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
            subtitlePanelBounds: next.subtitlePanelBounds,
            subtitlePanelPosition: next.subtitlePanelPosition,
            subtitlePanelLocked: next.subtitlePanelLocked,
            subtitleInteractionPassthrough: next.subtitleInteractionPassthrough,
            subtitleDanmakuMode: next.subtitleDanmakuMode,
            subtitleFontSize: next.subtitleFontSize,
            subtitleColorScheme: next.subtitleColorScheme
        }, { source: options && options.source ? options.source : 'subtitle-settings' });
        if (!options || options.silent !== true) {
            dispatchSettingsChange(next, changedKeys, options && options.source);
        }
        return clone(next);
    }

    function getStorageSyncPatch(evt) {
        var key = evt && evt.key;
        var value = evt ? evt.newValue : null;
        var patch = {};
        if (!key) return patch;
        if (key === SETTINGS_KEYS.subtitleEnabled) {
            patch.subtitleEnabled = value === 'true';
        } else if (key === SETTINGS_KEYS.userLanguage) {
            patch.userLanguage = value || DEFAULT_TRANSLATION_LANGUAGE;
        } else if (key === SETTINGS_KEYS.subtitleOpacity) {
            patch.subtitleOpacity = clampOpacity(value);
        } else if (key === SETTINGS_KEYS.subtitlePanelBounds) {
            patch.subtitlePanelBounds = getPanelBounds(value);
        } else if (key === SETTINGS_KEYS.subtitlePanelPosition) {
            patch.subtitlePanelPosition = normalizePanelPosition(value);
        } else if (key === SETTINGS_KEYS.subtitlePanelLocked) {
            patch.subtitlePanelLocked = value === 'true';
        } else if (key === SETTINGS_KEYS.subtitleInteractionPassthrough) {
            patch.subtitleInteractionPassthrough = value !== 'false';
        } else if (key === SETTINGS_KEYS.subtitleDanmakuMode) {
            patch.subtitleDanmakuMode = value === 'true';
        } else if (key === SETTINGS_KEYS.subtitleFontSize) {
            patch.subtitleFontSize = normalizeSubtitleFontSize(value);
        } else if (key === SETTINGS_KEYS.subtitleColorScheme) {
            patch.subtitleColorScheme = normalizeSubtitleColorScheme(value);
        }
        return patch;
    }

    function syncSettingsFromStorage(evt) {
        if (!evt || (evt.storageArea && evt.storageArea !== window.localStorage)) return;
        var patch = getStorageSyncPatch(evt);
        if (!Object.keys(patch).length) return;
        updateSettings(patch, {
            persist: false,
            source: 'subtitle-storage-sync'
        });
    }

    window.addEventListener('storage', syncSettingsFromStorage);

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
                if (typeof translated === 'string' && translated && translated !== i18nKey) {
                    return translated;
                }
            } catch (_) {}
        }
        return getUiFallbackText(key, uiLocale);
    }

    function getUiFallbackText(key, uiLocale) {
        var locale = normalizeUiLocale(uiLocale || ensureSettingsState().uiLocale || getCurrentUiLocale());
        var dictionary = UI_FALLBACK[locale] || UI_FALLBACK[DEFAULT_UI_LOCALE];
        return dictionary[key] || UI_FALLBACK[DEFAULT_UI_LOCALE][key] || key;
    }

    function getSettingsLabelSvgWidth(text, fontSize) {
        var width = 0;
        var source = typeof text === 'string' ? text : String(text || '');
        for (var i = 0; i < source.length; i += 1) {
            width += /[\u1100-\uFFFF]/.test(source.charAt(i)) ? fontSize : fontSize * 0.58;
        }
        return Math.max(1, Math.ceil(width));
    }

    function renderSettingsLabelText(container, text) {
        if (!container) return;
        if (typeof document === 'undefined' || !document.createElementNS) {
            container.textContent = text;
            return;
        }

        var source = typeof text === 'string' ? text : String(text || '');
        var computed = typeof window !== 'undefined' && window.getComputedStyle ? window.getComputedStyle(container) : null;
        var fontSize = computed ? parseFloat(computed.fontSize) : 13;
        if (!Number.isFinite(fontSize) || fontSize <= 0) {
            fontSize = 13;
        }
        var lineHeight = computed ? parseFloat(computed.lineHeight) : NaN;
        if (!Number.isFinite(lineHeight) || lineHeight <= 0) {
            lineHeight = fontSize * 1.25;
        }
        var width = getSettingsLabelSvgWidth(source, fontSize);
        var height = Math.ceil(lineHeight);
        var gradientId = container.dataset.subtitleGradientId;
        if (!gradientId) {
            gradientId = 'subtitle-settings-label-gradient-' + Math.random().toString(36).slice(2);
            container.dataset.subtitleGradientId = gradientId;
        }

        container.textContent = '';
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'subtitle-settings-label-svg');
        svg.setAttribute('width', String(width));
        svg.setAttribute('height', String(height));
        svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
        svg.setAttribute('aria-label', source);

        var defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        var gradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        gradient.setAttribute('id', gradientId);
        gradient.setAttribute('x1', '0');
        gradient.setAttribute('y1', '0');
        gradient.setAttribute('x2', String(width));
        gradient.setAttribute('y2', '0');
        gradient.setAttribute('gradientUnits', 'userSpaceOnUse');

        var start = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        start.setAttribute('offset', '0');
        start.setAttribute('stop-color', '#4fa9ee');
        var end = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        end.setAttribute('offset', '1');
        end.setAttribute('stop-color', '#82caff');
        gradient.appendChild(start);
        gradient.appendChild(end);
        defs.appendChild(gradient);
        svg.appendChild(defs);

        var svgText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        svgText.setAttribute('class', 'subtitle-settings-label-svg-text');
        svgText.setAttribute('x', '0');
        svgText.setAttribute('y', String(height / 2));
        svgText.setAttribute('dominant-baseline', 'central');
        svgText.setAttribute('fill', 'url(#' + gradientId + ')');
        svgText.textContent = source;
        svg.appendChild(svgText);
        container.appendChild(svg);
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

    function queryAll(root, selector) {
        if (!root) return [];
        if (typeof root.querySelectorAll === 'function') {
            return Array.prototype.slice.call(root.querySelectorAll(selector));
        }
        if (root.document && typeof root.document.querySelectorAll === 'function') {
            return Array.prototype.slice.call(root.document.querySelectorAll(selector));
        }
        return [];
    }

    function getSubtitleRefs(root) {
        return {
            display: query(root, '#subtitle-display'),
            scroll: query(root, '#subtitle-scroll'),
            text: query(root, '#subtitle-text'),
            panelControls: query(root, '#subtitle-panel-controls'),
            lockBtn: query(root, '#subtitle-lock-btn'),
            settingsBtn: query(root, '#subtitle-settings-btn'),
            closeBtn: query(root, '#subtitle-close-btn'),
            settingsPanel: query(root, '#subtitle-settings-panel'),
            labels: root && typeof root.querySelectorAll === 'function' ? root.querySelectorAll('.subtitle-settings-label') : [],
            langSelect: query(root, '#subtitle-lang-select'),
            opacitySlider: query(root, '#subtitle-opacity-slider'),
            opacityValue: query(root, '#subtitle-opacity-value'),
            fontSizeSelect: query(root, '#subtitle-font-size-select'),
            colorSchemeSelect: query(root, '#subtitle-color-scheme-select'),
            danmakuModeBtn: query(root, '#subtitle-danmaku-mode-btn'),
            resizeHandles: queryAll(root, '.subtitle-resize-edge')
        };
    }

    function isDarkThemeActive() {
        return !!(
            document.documentElement &&
            document.documentElement.getAttribute('data-theme') === 'dark'
        );
    }

    function applyBackgroundOpacity(display, opacity) {
        if (!display) return;
        var opacityValue = clampOpacity(opacity);
        var alpha = opacityValue / 100;
        display.style.removeProperty('background');
        display.style.setProperty('--subtitle-panel-alpha', String(alpha));
        display.style.setProperty('--subtitle-panel-soft-alpha', formatAlpha(alpha));
        display.style.setProperty('--subtitle-panel-soft-mid-alpha', formatAlpha(alpha));
        display.style.setProperty('--subtitle-panel-soft-edge-alpha', formatAlpha(alpha));
        display.dataset.subtitleBackgroundOpacity = String(opacityValue);
    }

    function getSubtitleControlScale(bounds) {
        var resolved = getPanelBounds(bounds);
        var widthRatio = resolved.width / DEFAULT_PANEL_BOUNDS.width;
        var heightRatio = resolved.height / DEFAULT_PANEL_BOUNDS.height;
        var scale = Math.max(widthRatio, heightRatio);
        scale = Math.max(MIN_SUBTITLE_CONTROL_SCALE, Math.min(MAX_SUBTITLE_CONTROL_SCALE, scale));
        return formatAlpha(scale);
    }

    function applySubtitleControlScale(display, bounds) {
        var controlScale = getSubtitleControlScale(bounds);
        if (!display) return controlScale;
        display.dataset.subtitleControlScale = controlScale;
        display.style.setProperty('--subtitle-control-scale', controlScale);
        return controlScale;
    }

    function applySubtitlePanelBounds(display, bounds, options) {
        var resolved = getPanelBounds(bounds);
        var rendered = resolved;
        if (options && options.host === 'web') {
            var viewportWidth = Math.max(1, Math.floor(Number(window.innerWidth) || resolved.width));
            var viewportHeight = Math.max(1, Math.floor(Number(window.innerHeight) || resolved.height));
            rendered = {
                width: Math.min(resolved.width, viewportWidth),
                height: Math.min(resolved.height, viewportHeight)
            };
        }
        var fontSize = normalizeSubtitleFontSize(options && hasOwn(options, 'fontSize')
            ? options.fontSize
            : getSettings().subtitleFontSize);
        if (!display) return resolved;
        display.dataset.subtitlePanelWidth = String(rendered.width);
        display.dataset.subtitlePanelHeight = String(rendered.height);
        display.style.width = rendered.width + 'px';
        display.style.height = rendered.height + 'px';
        display.style.minWidth = Math.min(MIN_PANEL_WIDTH, rendered.width) + 'px';
        display.style.minHeight = Math.min(MIN_PANEL_HEIGHT, rendered.height) + 'px';
        display.style.maxHeight = 'none';
        display.style.fontSize = fontSize + 'px';
        display.dataset.subtitleFontSize = String(fontSize);
        display.style.setProperty('--subtitle-font-size', fontSize + 'px');
        display.style.setProperty('--subtitle-panel-width', rendered.width + 'px');
        display.style.setProperty('--subtitle-panel-height', rendered.height + 'px');
        applySubtitleControlScale(display, rendered);
        display.style.setProperty('--subtitle-content-max-height', Math.max(24, rendered.height - 24) + 'px');
        if (!options || options.host !== 'window') {
            display.style.setProperty('--subtitle-max-width', rendered.width + 'px');
        }
        return resolved;
    }

    function isDanmakuBoundaryPunctuation(ch) {
        return ',，.。!！?？;；:：、…'.indexOf(ch) !== -1;
    }

    function isDanmakuClosingPunctuation(ch) {
        return '"\'”’）)]}》」』】'.indexOf(ch) !== -1;
    }

    function splitSubtitleDanmakuSegments(text) {
        var normalized = String(text || '').replace(/\s+/g, ' ').trim();
        var segments = [];
        var start = 0;
        var punctuationCount = 0;
        var i;
        var end;
        var segment;

        if (!normalized) return segments;

        for (i = 0; i < normalized.length; i += 1) {
            if (!isDanmakuBoundaryPunctuation(normalized.charAt(i))) continue;
            punctuationCount += 1;
            if (punctuationCount < 2) continue;

            end = i + 1;
            while (end < normalized.length && isDanmakuClosingPunctuation(normalized.charAt(end))) {
                end += 1;
            }
            segment = normalized.slice(start, end).trim();
            if (segment) segments.push(segment);
            start = end;
            i = end - 1;
            punctuationCount = 0;
        }

        segment = normalized.slice(start).trim();
        if (segment) segments.push(segment);
        return segments;
    }

    function clearSubtitleDanmakuText(refs) {
        var display = refs && refs.display;
        var scroll = refs && refs.scroll;
        var layer = scroll && scroll.querySelector ? scroll.querySelector('.subtitle-danmaku-layer') : null;
        if (layer && layer.parentNode) {
            layer.parentNode.removeChild(layer);
        }
        if (scroll && scroll.classList) {
            scroll.classList.remove('subtitle-danmaku-scroll');
        }
        if (display && display.dataset) {
            delete display.dataset.subtitleDanmakuActive;
            delete display.dataset.subtitleDanmakuCount;
        }
    }

    function isSubtitleDanmakuScrollActive(scroll) {
        var display;
        if (!scroll) return false;
        if (scroll.classList && scroll.classList.contains('subtitle-danmaku-scroll')) {
            return true;
        }
        display = scroll.closest ? scroll.closest('#subtitle-display') : null;
        return !!(display && display.dataset && display.dataset.subtitleDanmakuActive === 'true');
    }

    function resetSubtitleDanmakuScroll(scroll) {
        if (!scroll) return 0;
        cancelSubtitleAutoScroll(scroll);
        scroll.scrollTop = 0;
        if (scroll.dataset) {
            scroll.dataset.subtitleScrollable = 'false';
        }
        return 0;
    }

    function renderSubtitleDanmakuText(refs, text, options) {
        var display = refs && refs.display;
        var scroll = refs && refs.scroll;
        var enabled = !!(options && options.enabled);
        var segments = enabled ? splitSubtitleDanmakuSegments(text) : [];
        var layer;
        var laneCount;
        var lanes;
        var i;
        var segment;
        var item;
        var lane;
        var duration;
        var delay;

        if (!display || !scroll || !enabled || !segments.length) {
            clearSubtitleDanmakuText(refs);
            return segments;
        }

        layer = scroll.querySelector ? scroll.querySelector('.subtitle-danmaku-layer') : null;
        if (!layer) {
            layer = document.createElement('div');
            layer.className = 'subtitle-danmaku-layer';
            layer.setAttribute('aria-hidden', 'true');
            scroll.appendChild(layer);
        }
        layer.textContent = '';
        laneCount = Math.min(2, Math.max(1, segments.length));
        lanes = [];

        for (i = 0; i < laneCount; i += 1) {
            lane = document.createElement('div');
            lane.className = 'subtitle-danmaku-lane';
            lane.dataset.subtitleDanmakuLane = String(i);
            lane.style.setProperty('--subtitle-danmaku-top', ((i + 0.5) * 100 / laneCount) + '%');
            layer.appendChild(lane);
            lanes.push({
                element: lane,
                textLength: 0,
                itemCount: 0
            });
        }

        for (i = 0; i < segments.length; i += 1) {
            segment = segments[i];
            lane = i % laneCount;
            item = document.createElement('span');
            item.className = 'subtitle-danmaku-item';
            item.textContent = segment;
            item.dataset.subtitleDanmakuIndex = String(i);
            item.dataset.subtitleDanmakuLane = String(lane);
            lanes[lane].element.appendChild(item);
            lanes[lane].textLength += segment.length;
            lanes[lane].itemCount += 1;
        }

        for (i = 0; i < lanes.length; i += 1) {
            duration = Math.max(8, Math.min(20, 8 + lanes[i].textLength * 0.12 + lanes[i].itemCount * 0.8));
            delay = i * 1.4;
            lanes[i].element.style.setProperty('--subtitle-danmaku-duration', duration.toFixed(2) + 's');
            lanes[i].element.style.setProperty('--subtitle-danmaku-delay', '-' + (delay % duration).toFixed(2) + 's');
        }

        scroll.classList.add('subtitle-danmaku-scroll');
        display.dataset.subtitleDanmakuActive = 'true';
        display.dataset.subtitleDanmakuCount = String(segments.length);
        resetSubtitleDanmakuScroll(scroll);
        return segments;
    }

    function getSubtitleScrollNode(target) {
        if (target && typeof target.scrollTop === 'number' &&
            typeof target.scrollHeight === 'number') {
            return target;
        }
        if (target && target.scroll) return target.scroll;
        return query(document, '#subtitle-scroll');
    }

    function getSubtitleScrollMax(scroll) {
        if (!scroll) return 0;
        return Math.max(0, (scroll.scrollHeight || 0) - (scroll.clientHeight || 0));
    }

    function isSubtitleScrollScrollable(scroll) {
        return getSubtitleScrollMax(scroll) > 1;
    }

    function updateSubtitleScrollState(target) {
        var scroll = getSubtitleScrollNode(target);
        if (!scroll || !scroll.dataset) return false;
        if (isSubtitleDanmakuScrollActive(scroll)) {
            resetSubtitleDanmakuScroll(scroll);
            return false;
        }
        var scrollable = isSubtitleScrollScrollable(scroll);
        scroll.dataset.subtitleScrollable = scrollable ? 'true' : 'false';
        return scrollable;
    }

    function cancelSubtitleAutoScroll(target) {
        var scroll = getSubtitleScrollNode(target);
        var state = scroll && scroll._nekoSubtitleAutoScroll;
        if (!state) return;
        if (state.timerId !== null && typeof state.timerId !== 'undefined') {
            clearTimeout(state.timerId);
        }
        if (state.frameId !== null && typeof state.frameId !== 'undefined') {
            cancelAnimationFrame(state.frameId);
        }
        scroll._nekoSubtitleAutoScroll = null;
    }

    function scrollSubtitleToBottom(target) {
        var scroll = getSubtitleScrollNode(target);
        if (!scroll) return 0;
        if (isSubtitleDanmakuScrollActive(scroll)) {
            return resetSubtitleDanmakuScroll(scroll);
        }
        cancelSubtitleAutoScroll(scroll);
        updateSubtitleScrollState(scroll);
        scroll.scrollTop = getSubtitleScrollMax(scroll);
        return scroll.scrollTop;
    }

    function requestSubtitleAutoScroll(target, options) {
        var scroll = getSubtitleScrollNode(target);
        if (!scroll) return 0;
        if (isSubtitleDanmakuScrollActive(scroll)) {
            return resetSubtitleDanmakuScroll(scroll);
        }
        if (!updateSubtitleScrollState(scroll)) {
            cancelSubtitleAutoScroll(scroll);
            scroll.scrollTop = 0;
            return 0;
        }

        var lastUserScrollAt = Number(scroll._nekoSubtitleLastUserScrollAt) || 0;
        if (!(options && options.force) &&
            lastUserScrollAt &&
            Date.now() - lastUserScrollAt < AUTO_SCROLL_USER_OVERRIDE_MS) {
            return scroll.scrollTop;
        }

        var maxScrollTop = getSubtitleScrollMax(scroll);
        if (scroll.scrollTop >= maxScrollTop - 1) {
            return scroll.scrollTop;
        }

        var activeState = scroll._nekoSubtitleAutoScroll;
        var speed = Math.max(1, Number(options && options.speedPixelsPerSecond) || AUTO_SCROLL_SPEED_PX_PER_SECOND);
        if (activeState) {
            activeState.speed = speed;
            return scroll.scrollTop;
        }

        var delayMs = Math.max(0, Number(options && options.delayMs));
        if (!Number.isFinite(delayMs)) {
            delayMs = AUTO_SCROLL_DELAY_MS;
        }

        var state = {
            frameId: null,
            timerId: null,
            lastTimestamp: 0,
            speed: speed
        };
        scroll._nekoSubtitleAutoScroll = state;

        function step(timestamp) {
            if (scroll._nekoSubtitleAutoScroll !== state) return;
            if (isSubtitleDanmakuScrollActive(scroll)) {
                resetSubtitleDanmakuScroll(scroll);
                return;
            }
            if (!updateSubtitleScrollState(scroll)) {
                cancelSubtitleAutoScroll(scroll);
                return;
            }
            var targetTop = getSubtitleScrollMax(scroll);
            if (scroll.scrollTop >= targetTop - 1) {
                scroll.scrollTop = targetTop;
                cancelSubtitleAutoScroll(scroll);
                return;
            }
            if (!state.lastTimestamp) {
                state.lastTimestamp = timestamp || 0;
                state.frameId = requestAnimationFrame(step);
                return;
            }
            var elapsedMs = Math.max(0, Math.min(120, (timestamp || 0) - state.lastTimestamp));
            state.lastTimestamp = timestamp || state.lastTimestamp;
            var distance = state.speed * elapsedMs / 1000;
            scroll.scrollTop = Math.min(targetTop, scroll.scrollTop + distance);
            state.frameId = requestAnimationFrame(step);
        }

        state.timerId = setTimeout(function() {
            if (scroll._nekoSubtitleAutoScroll !== state) return;
            state.timerId = null;
            state.frameId = requestAnimationFrame(step);
        }, delayMs);

        return scroll.scrollTop;
    }

    function getWheelScrollDelta(e, scroll) {
        if (!e) return 0;
        var delta = Number(e.deltaY) || 0;
        if (!delta && e.shiftKey) {
            delta = Number(e.deltaX) || 0;
        }
        if (!delta) return 0;
        if (e.deltaMode === 1) {
            delta *= 32;
        } else if (e.deltaMode === 2) {
            delta *= Math.max(1, scroll && scroll.clientHeight ? scroll.clientHeight : 1);
        }
        return delta;
    }

    function attachSubtitleScroll(refs) {
        var scroll = refs && refs.scroll;
        if (!scroll) return function() {};
        var mutationObserver = null;
        var resizeObserver = null;
        var scheduleUpdateId = null;

        function scheduleScrollStateUpdate() {
            if (scheduleUpdateId !== null) return;
            scheduleUpdateId = setTimeout(function() {
                scheduleUpdateId = null;
                updateSubtitleScrollState(scroll);
            }, 0);
        }

        var onWheel = function(e) {
            if (!updateSubtitleScrollState(scroll)) return;
            var delta = getWheelScrollDelta(e, scroll);
            if (!delta) return;
            var maxScrollTop = getSubtitleScrollMax(scroll);
            var nextScrollTop = Math.max(0, Math.min(maxScrollTop, scroll.scrollTop + delta));
            if (Math.abs(nextScrollTop - scroll.scrollTop) < 1) return;
            cancelSubtitleAutoScroll(scroll);
            scroll._nekoSubtitleLastUserScrollAt = Date.now();
            scroll.scrollTop = nextScrollTop;
            if (e.preventDefault) e.preventDefault();
            if (e.stopPropagation) e.stopPropagation();
        };

        updateSubtitleScrollState(scroll);
        if (window.MutationObserver) {
            mutationObserver = new MutationObserver(scheduleScrollStateUpdate);
            mutationObserver.observe(scroll, {
                childList: true,
                characterData: true,
                subtree: true
            });
        }
        if (window.ResizeObserver) {
            resizeObserver = new ResizeObserver(scheduleScrollStateUpdate);
            resizeObserver.observe(scroll);
        }
        scroll.addEventListener('wheel', onWheel, { passive: false });
        return function() {
            if (scheduleUpdateId !== null) {
                clearTimeout(scheduleUpdateId);
                scheduleUpdateId = null;
            }
            cancelSubtitleAutoScroll(scroll);
            if (mutationObserver) mutationObserver.disconnect();
            if (resizeObserver) resizeObserver.disconnect();
            scroll.removeEventListener('wheel', onWheel, { passive: false });
        };
    }

    function applySettingsToUi(refs, state, options) {
        if (!refs || !refs.display) return;
        var host = options && options.host ? options.host : 'web';
        var passthroughEnabled = state.subtitleInteractionPassthrough !== false;
        refs.display.dataset.subtitlePanelLocked = state.subtitlePanelLocked ? 'true' : 'false';
        refs.display.dataset.subtitleInteractionPassthrough = passthroughEnabled ? 'true' : 'false';
        refs.display.dataset.subtitleColorScheme = normalizeSubtitleColorScheme(state.subtitleColorScheme);
        if (!refs.display.dataset.subtitlePanelState) {
            refs.display.dataset.subtitlePanelState = 'clean';
        }
        applyBackgroundOpacity(refs.display, state.subtitleOpacity);
        applySubtitlePanelBounds(refs.display, state.subtitlePanelBounds, {
            host: host,
            fontSize: state.subtitleFontSize
        });
        if (host === 'web') {
            applyWebPanelPosition(refs, state.subtitlePanelPosition);
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
        if (refs.fontSizeSelect) {
            refs.fontSizeSelect.value = String(normalizeSubtitleFontSize(state.subtitleFontSize));
        }
        if (refs.colorSchemeSelect) {
            refs.colorSchemeSelect.value = normalizeSubtitleColorScheme(state.subtitleColorScheme);
        }
        if (refs.danmakuModeBtn) {
            refs.danmakuModeBtn.checked = !!state.subtitleDanmakuMode;
        }
    }

    function applyUiLabels(refs, state) {
        if (!refs) return;
        var locale = state && state.uiLocale ? state.uiLocale : getCurrentUiLocale();
        if (refs.labels && refs.labels.length) {
            refs.labels.forEach(function(label) {
                var key = label && label.dataset ? label.dataset.subtitleLabel : '';
                if (!key) return;
                var text = getUiText(key, locale);
                var textNode = label.querySelector ? label.querySelector('.subtitle-settings-label-text') : null;
                if (textNode) {
                    renderSettingsLabelText(textNode, text);
                } else {
                    label.textContent = text;
                }
            });
        }
        if (refs.settingsBtn) {
            refs.settingsBtn.title = getUiText('settingsBtn', locale);
            refs.settingsBtn.setAttribute('aria-label', getUiText('settingsBtn', locale));
        }
        if (refs.lockBtn) {
            var lockKey = state && state.subtitlePanelLocked ? 'unlockPosition' : 'lockPosition';
            refs.lockBtn.title = getUiText(lockKey, locale);
            refs.lockBtn.setAttribute('aria-label', getUiText(lockKey, locale));
        }
        if (refs.closeBtn) {
            refs.closeBtn.title = getUiText('closePanel', locale);
            refs.closeBtn.setAttribute('aria-label', getUiText('closePanel', locale));
        }
        if (refs.langSelect) {
            refs.langSelect.title = getUiText('targetLang', locale);
        }
        if (refs.opacitySlider) {
            refs.opacitySlider.title = getUiText('opacity', locale);
        }
        if (refs.fontSizeSelect) {
            refs.fontSizeSelect.title = getUiText('fontSize', locale);
            Array.prototype.forEach.call(refs.fontSizeSelect.options || [], function(option) {
                var key = option && option.dataset ? option.dataset.subtitleFontSizeLabel : '';
                if (!key) return;
                option.textContent = getUiText(key, locale);
            });
        }
        if (refs.colorSchemeSelect) {
            refs.colorSchemeSelect.title = getUiText('colorScheme', locale);
            Array.prototype.forEach.call(refs.colorSchemeSelect.options || [], function(option) {
                var key = option && option.dataset ? option.dataset.subtitleColorSchemeLabel : '';
                if (!key) return;
                option.textContent = getUiText(key, locale);
            });
        }
        if (refs.danmakuModeBtn) {
            var danmakuText = getUiText('danmakuMode', locale);
            refs.danmakuModeBtn.title = danmakuText;
            refs.danmakuModeBtn.setAttribute('aria-label', danmakuText);
        }
        if (refs.text) {
            var placeholderLocale = normalizeUiLocale(state && state.userLanguage ? state.userLanguage : locale);
            refs.text.setAttribute('data-subtitle-placeholder', getUiFallbackText('emptyHint', placeholderLocale));
        }
    }

    function applyLockButtonIcon(lockBtn, locked) {
        if (!lockBtn) return;
        var svg = lockBtn.querySelector ? lockBtn.querySelector('svg') : null;
        var path = svg && svg.querySelector ? svg.querySelector('path') : null;
        if (!svg) {
            svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'currentColor');
            svg.setAttribute('width', '14');
            svg.setAttribute('height', '14');
            svg.setAttribute('aria-hidden', 'true');
            lockBtn.appendChild(svg);
        }
        if (!path) {
            path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            svg.appendChild(path);
        }
        lockBtn.dataset.subtitleLockIcon = locked ? 'locked' : 'unlocked';
        path.setAttribute('d', locked ? LOCK_ICON_PATH : UNLOCK_ICON_PATH);
    }

    function measureSubtitleLayout(options) {
        options = options || {};
        var text = String(options.text || '');
        var mode = options.mode || 'window';
        var bounds = getPanelBounds(options.panelBounds);
        var baseFont = options.baseFont || DEFAULT_SUBTITLE_FONT_SIZE;
        var minFont = options.minFont || 12;
        var fontFamily = options.fontFamily || 'Segoe UI, Arial, sans-serif';
        var maxWidth = options.maxWidth || bounds.width;
        var minHeight = options.minHeight || bounds.height;
        var maxHeight = options.maxHeight || Math.max(minHeight, options.availableHeight || bounds.height);
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
            node.style.padding = '12px 86px 12px 24px';
        }
        node.textContent = text;
        document.body.appendChild(node);

        if (mode === 'window') {
            width = Math.max(MIN_PANEL_WIDTH, Math.min(node.offsetWidth + 8, maxWidth));
            node.style.width = width + 'px';
        } else {
            width = Math.max(0, options.availableWidth || Math.max(0, maxWidth - PANEL_TEXT_HORIZONTAL_RESERVE));
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

    function clampPanelPosition(refs, position) {
        if (!refs || !refs.display || !position) return null;
        var rect = refs.display.getBoundingClientRect ? refs.display.getBoundingClientRect() : null;
        var state = getSettings();
        var bounds = getPanelBounds(state.subtitlePanelBounds);
        var targetWidth = Number(refs.display.dataset ? refs.display.dataset.subtitlePanelWidth : 0);
        var targetHeight = Number(refs.display.dataset ? refs.display.dataset.subtitlePanelHeight : 0);
        var width = (Number.isFinite(targetWidth) && targetWidth > 0 ? targetWidth : 0) ||
            refs.display.offsetWidth || (rect ? rect.width : 0) || Math.min(bounds.width, window.innerWidth);
        var height = (Number.isFinite(targetHeight) && targetHeight > 0 ? targetHeight : 0) ||
            refs.display.offsetHeight || (rect ? rect.height : 0) || bounds.height;
        var maxX = Math.max(0, window.innerWidth - width);
        var maxY = Math.max(0, window.innerHeight - height);
        return {
            left: Math.max(0, Math.min(Number(position.left) || 0, maxX)),
            top: Math.max(0, Math.min(Number(position.top) || 0, maxY)),
            coordinateSpace: 'viewport'
        };
    }

    function clearWebPanelPosition(refs) {
        if (!refs || !refs.display) return;
        refs.display.style.left = '';
        refs.display.style.top = '';
        refs.display.style.bottom = '';
        refs.display.style.transform = '';
        refs.display.style.animation = '';
        delete refs.display.dataset.subtitlePositioned;
    }

    function applyWebPanelPosition(refs, position) {
        if (!refs || !refs.display) return null;
        var clamped = clampPanelPosition(refs, position);
        if (!clamped) {
            clearWebPanelPosition(refs);
            return null;
        }
        refs.display.style.left = clamped.left + 'px';
        refs.display.style.top = clamped.top + 'px';
        refs.display.style.bottom = 'auto';
        refs.display.style.transform = 'none';
        refs.display.style.animation = 'none';
        refs.display.dataset.subtitlePositioned = 'true';
        return clamped;
    }

    function attachWebDrag(refs) {
        if (!refs.display) return function() {};

        var isDragging = false;
        var pendingDrag = false;
        var currentPosition = null;
        var startX = 0;
        var startY = 0;
        var initialX = 0;
        var initialY = 0;

        function isPanelLocked() {
            return !!getSettings().subtitlePanelLocked;
        }

        function canStartDrag(target) {
            if (isPanelLocked()) return false;
            if (target && target.dataset && target.dataset.resizeDir) return false;
            if (refs.settingsPanel && refs.settingsPanel.contains(target)) return false;
            if (refs.panelControls && refs.panelControls.contains(target)) return false;
            if (refs.settingsBtn && refs.settingsBtn.contains(target)) return false;
            return true;
        }

        function handleMouseMove(e) {
            if (!pendingDrag && !isDragging) return;
            e.preventDefault();
            if (isPanelLocked()) {
                handleMouseUp();
                return;
            }

            var dx = e.clientX - startX;
            var dy = e.clientY - startY;
            if (!isDragging) {
                if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
                isDragging = true;
                pendingDrag = false;
                refs.display.style.animation = 'none';
                refs.display.style.transition = 'none';
                refs.display.classList.add('dragging');
                refs.display.style.transform = 'none';
                refs.display.style.left = initialX + 'px';
                refs.display.style.top = initialY + 'px';
                refs.display.style.bottom = 'auto';
            }

            currentPosition = applyWebPanelPosition(refs, {
                left: initialX + dx,
                top: initialY + dy,
                coordinateSpace: 'viewport'
            });
        }

        function handleMouseUp() {
            if (!pendingDrag && !isDragging) return;
            var shouldPersist = isDragging && currentPosition;
            pendingDrag = false;
            isDragging = false;
            document.body.style.userSelect = '';
            refs.display.classList.remove('dragging');
            refs.display.style.transition = '';
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
            if (shouldPersist) {
                updateSettings({ subtitlePanelPosition: currentPosition }, { source: 'subtitle-ui-drag' });
            }
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
            currentPosition = null;
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
            var state = getSettings();
            if (!state.subtitlePanelPosition) return;
            var clamped = applyWebPanelPosition(refs, state.subtitlePanelPosition);
            if (clamped && !samePanelPosition(clamped, state.subtitlePanelPosition)) {
                updateSettings({ subtitlePanelPosition: clamped }, { source: 'subtitle-ui-position-clamp' });
            }
        }

        function onDisplayMouseDown(e) {
            beginDrag(e);
        }

        function onDisplayTouchStart(e) {
            if (!canStartDrag(e.target)) return;
            beginTouchDrag(e);
        }

        clampManualPosition();
        refs.display.addEventListener('mousedown', onDisplayMouseDown);
        refs.display.addEventListener('touchstart', onDisplayTouchStart, { passive: false });
        document.addEventListener('touchmove', handleTouchMove, { passive: false });
        document.addEventListener('touchend', handleMouseUp);
        document.addEventListener('touchcancel', handleMouseUp);
        window.addEventListener('resize', clampManualPosition);
        window.addEventListener('orientationchange', clampManualPosition);

        return function detachWebDrag() {
            handleMouseUp();
            refs.display.removeEventListener('mousedown', onDisplayMouseDown);
            refs.display.removeEventListener('touchstart', onDisplayTouchStart, { passive: false });
            document.removeEventListener('touchmove', handleTouchMove, { passive: false });
            document.removeEventListener('touchend', handleMouseUp);
            document.removeEventListener('touchcancel', handleMouseUp);
            window.removeEventListener('resize', clampManualPosition);
            window.removeEventListener('orientationchange', clampManualPosition);
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
    }

    function attachWindowDrag(refs, options) {
        var api = options && options.api;
        if (!refs.display || !api) return function() {};

        var isDragging = false;

        function isPanelLocked() {
            return !!getSettings().subtitlePanelLocked;
        }

        function isLinuxSubtitleHost() {
            return document.body && document.body.classList.contains('subtitle-linux-host');
        }

        function bounceBackIfNeeded() {
            try {
                if (isLinuxSubtitleHost()) return;
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
            if (isPanelLocked()) return false;
            if (target && target.dataset && target.dataset.resizeDir) return false;
            if (refs.settingsPanel && refs.settingsPanel.contains(target)) return false;
            if (refs.panelControls && refs.panelControls.contains(target)) return false;
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
            if (typeof api.dragStop === 'function') {
                api.dragStop();
            }
            bounceBackIfNeeded();
        }

        function onDisplayMouseDown(e) {
            startDrag(e);
        }

        function onDisplayTouchStart(e) {
            startDrag(e);
        }

        refs.display.addEventListener('mousedown', onDisplayMouseDown);
        refs.display.addEventListener('touchstart', onDisplayTouchStart, { passive: false });
        document.addEventListener('mouseup', stopDrag);
        document.addEventListener('touchend', stopDrag);
        document.addEventListener('touchcancel', stopDrag);
        window.addEventListener('focus', bounceBackIfNeeded);
        window.addEventListener('resize', bounceBackIfNeeded);

        return function detachWindowDrag() {
            stopDrag();
            refs.display.classList.remove('dragging');
            refs.display.removeEventListener('mousedown', onDisplayMouseDown);
            refs.display.removeEventListener('touchstart', onDisplayTouchStart, { passive: false });
            document.removeEventListener('mouseup', stopDrag);
            document.removeEventListener('touchend', stopDrag);
            document.removeEventListener('touchcancel', stopDrag);
            window.removeEventListener('focus', bounceBackIfNeeded);
            window.removeEventListener('resize', bounceBackIfNeeded);
        };
    }

    function getResizeCursor(dir) {
        if (dir === 'n' || dir === 's') return 'ns-resize';
        if (dir === 'e' || dir === 'w') return 'ew-resize';
        if (dir === 'ne' || dir === 'sw') return 'nesw-resize';
        return 'nwse-resize';
    }

    function calculateResizeBounds(start, dir, clientX, clientY, limits) {
        var dx = clientX - start.x;
        var dy = clientY - start.y;
        var left = start.left;
        var top = start.top;
        var width = start.width;
        var height = start.height;

        if (dir.indexOf('e') !== -1) {
            width = Math.max(MIN_PANEL_WIDTH, start.width + dx);
        }
        if (dir.indexOf('s') !== -1) {
            height = Math.max(MIN_PANEL_HEIGHT, start.height + dy);
        }
        if (dir.indexOf('w') !== -1) {
            width = Math.max(MIN_PANEL_WIDTH, start.width - dx);
            left = start.left + start.width - width;
        }
        if (dir.indexOf('n') !== -1) {
            height = Math.max(MIN_PANEL_HEIGHT, start.height - dy);
            top = start.top + start.height - height;
        }

        if (limits && limits.clampToViewport) {
            if (left < 0) {
                width = Math.max(MIN_PANEL_WIDTH, width + left);
                left = 0;
            }
            if (top < 0) {
                height = Math.max(MIN_PANEL_HEIGHT, height + top);
                top = 0;
            }
            if (left + width > limits.width) {
                width = Math.max(MIN_PANEL_WIDTH, limits.width - left);
            }
            if (top + height > limits.height) {
                height = Math.max(MIN_PANEL_HEIGHT, limits.height - top);
            }
        }

        return {
            bounds: getPanelBounds({ width: width, height: height }),
            position: {
                left: Math.max(0, Math.round(left)),
                top: Math.max(0, Math.round(top)),
                coordinateSpace: 'viewport'
            }
        };
    }

    function attachPanelResize(refs, options) {
        if (!refs.display || !refs.resizeHandles || !refs.resizeHandles.length) {
            return function() {};
        }

        var api = options && options.api;
        var host = options && options.host ? options.host : 'web';
        var windowEdgeInset = host === 'window' ? Math.max(0, Number(options && options.windowEdgeInset) || 0) : 0;
        var resizeState = null;

        function isPanelLocked() {
            return !!getSettings().subtitlePanelLocked;
        }

        function getStartMetrics(e, dir) {
            var rect = refs.display.getBoundingClientRect();
            var bounds = getPanelBounds({
                width: rect.width || refs.display.offsetWidth,
                height: rect.height || refs.display.offsetHeight
            });
            if (host === 'window') {
                return {
                    dir: dir,
                    x: e.clientX,
                    y: e.clientY,
                    left: typeof window.screenX === 'number' ? window.screenX : 0,
                    top: typeof window.screenY === 'number' ? window.screenY : 0,
                    width: bounds.width,
                    height: bounds.height
                };
            }
            return {
                dir: dir,
                x: e.clientX,
                y: e.clientY,
                left: rect.left,
                top: rect.top,
                width: bounds.width,
                height: bounds.height
            };
        }

        function applyResize(result, persist) {
            applySubtitlePanelBounds(refs.display, result.bounds, { host: host });
            if (host === 'web') {
                applyWebPanelPosition(refs, result.position);
            } else if (api) {
                if (typeof api.setPosition === 'function' &&
                    (resizeState.dir.indexOf('n') !== -1 || resizeState.dir.indexOf('w') !== -1)) {
                    api.setPosition(result.position.left, result.position.top);
                }
                if (typeof api.setSize === 'function') {
                    api.setSize(
                        result.bounds.width + windowEdgeInset * 2,
                        result.bounds.height + windowEdgeInset * 2,
                        {
                            panelBounds: result.bounds
                        }
                    );
                }
            }
            if (!persist) return;
            var patch = { subtitlePanelBounds: result.bounds };
            if (host === 'web') {
                patch.subtitlePanelPosition = result.position;
            }
            var nextState = updateSettings(patch, { source: 'subtitle-ui-resize' });
            if (typeof options.propagateSetting === 'function') {
                options.propagateSetting({
                    type: 'bounds',
                    value: result.bounds,
                    patch: { subtitlePanelBounds: result.bounds },
                    state: nextState
                });
            }
        }

        function updateResize(clientX, clientY, persist) {
            if (!resizeState) return;
            resizeState.lastX = clientX;
            resizeState.lastY = clientY;
            var result = calculateResizeBounds(resizeState, resizeState.dir, clientX, clientY, {
                clampToViewport: host === 'web',
                width: window.innerWidth,
                height: window.innerHeight
            });
            applyResize(result, persist);
        }

        function onMove(e) {
            if (!resizeState) return;
            e.preventDefault();
            updateResize(e.clientX, e.clientY, false);
        }

        function onTouchMove(e) {
            if (!resizeState || !e.touches || !e.touches.length) return;
            e.preventDefault();
            updateResize(e.touches[0].clientX, e.touches[0].clientY, false);
        }

        function endResize(e) {
            if (!resizeState) return;
            var clientX = e && typeof e.clientX === 'number' ? e.clientX : resizeState.lastX;
            var clientY = e && typeof e.clientY === 'number' ? e.clientY : resizeState.lastY;
            if ((typeof clientX !== 'number' || typeof clientY !== 'number') &&
                e && e.changedTouches && e.changedTouches.length) {
                clientX = e.changedTouches[0].clientX;
                clientY = e.changedTouches[0].clientY;
            }
            if (typeof clientX !== 'number') clientX = resizeState.x;
            if (typeof clientY !== 'number') clientY = resizeState.y;
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
            refs.display.classList.remove('resizing');
            refs.display.style.transition = '';
            updateResize(clientX, clientY, true);
            resizeState = null;
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', endResize);
            document.removeEventListener('touchmove', onTouchMove, { passive: false });
            document.removeEventListener('touchend', endResize);
            document.removeEventListener('touchcancel', endResize);
        }

        function beginResize(e, dir) {
            if (isPanelLocked()) return;
            if (typeof e.button === 'number' && e.button !== 0) return;
            resizeState = getStartMetrics(e, dir);
            resizeState.lastX = resizeState.x;
            resizeState.lastY = resizeState.y;
            refs.display.classList.add('resizing');
            refs.display.style.transition = 'none';
            document.body.style.userSelect = 'none';
            document.body.style.cursor = getResizeCursor(dir);
            if (e.preventDefault) e.preventDefault();
            if (e.stopPropagation) e.stopPropagation();
            document.addEventListener('mousemove', onMove);
            document.addEventListener('touchmove', onTouchMove, { passive: false });
            document.addEventListener('mouseup', endResize);
            document.addEventListener('touchend', endResize);
            document.addEventListener('touchcancel', endResize);
        }

        refs.resizeHandles.forEach(function(handle) {
            var dir = handle.dataset.resizeDir || 'se';
            var onMouseDown = function(e) { beginResize(e, dir); };
            var onTouchStart = function(e) {
                if (!e.touches || !e.touches.length) return;
                beginResize({
                    target: e.target,
                    button: 0,
                    clientX: e.touches[0].clientX,
                    clientY: e.touches[0].clientY,
                    preventDefault: function() { e.preventDefault(); },
                    stopPropagation: function() { e.stopPropagation(); }
                }, dir);
            };
            handle.addEventListener('mousedown', onMouseDown);
            handle.addEventListener('touchstart', onTouchStart, { passive: false });
            handle._nekoSubtitleResizeCleanup = function() {
                handle.removeEventListener('mousedown', onMouseDown);
                handle.removeEventListener('touchstart', onTouchStart, { passive: false });
            };
        });

        return function detachPanelResize() {
            if (resizeState) {
                endResize({
                    clientX: resizeState.lastX,
                    clientY: resizeState.lastY
                });
            } else {
                document.body.style.userSelect = '';
                document.body.style.cursor = '';
                refs.display.classList.remove('resizing');
                refs.display.style.transition = '';
            }
            refs.resizeHandles.forEach(function(handle) {
                if (typeof handle._nekoSubtitleResizeCleanup === 'function') {
                    handle._nekoSubtitleResizeCleanup();
                    delete handle._nekoSubtitleResizeCleanup;
                }
            });
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', endResize);
            document.removeEventListener('touchmove', onTouchMove, { passive: false });
            document.removeEventListener('touchend', endResize);
            document.removeEventListener('touchcancel', endResize);
        };
    }

    function initSubtitleUI(options) {
        options = options || {};
        var refs = getSubtitleRefs(options.root || document);
        var cleanupFns = [];
        var state = getSettings();
        var controlsHideTimer = null;
        var panelState = normalizePanelState((getRenderState() || {}).subtitlePanelState);
        var externalSettingsOpen = false;

        if (!refs.display) {
            return null;
        }

        function notifyPanelStateChanged(source) {
            if (typeof options.onSettingsApplied === 'function') {
                options.onSettingsApplied(getSettings(), refs, {
                    changedKeys: ['subtitlePanelState'],
                    source: source || 'subtitle-ui-panel-state'
                });
            }
        }

        function applyPanelState(nextState, source) {
            var previousState = panelState;
            panelState = normalizePanelState(nextState);
            refs.display.dataset.subtitlePanelState = panelState;
            if (refs.panelControls) {
                refs.panelControls.setAttribute('aria-hidden', panelState === 'clean' ? 'true' : 'false');
            }
            if (refs.settingsBtn) {
                refs.settingsBtn.setAttribute('aria-expanded', panelState === 'settings' ? 'true' : 'false');
            }
            if (previousState === panelState && source !== 'subtitle-ui-init') {
                return;
            }
            updateRenderState({ subtitlePanelState: panelState }, {
                source: source || 'subtitle-ui-panel-state'
            });
            notifyPanelStateChanged(source);
        }

        function clearControlsHideTimer() {
            if (controlsHideTimer) {
                clearTimeout(controlsHideTimer);
                controlsHideTimer = null;
            }
        }

        function isInlineSettingsOpen() {
            return !!(refs.settingsPanel && !refs.settingsPanel.classList.contains('hidden'));
        }

        function isSettingsOpen() {
            return externalSettingsOpen || isInlineSettingsOpen();
        }

        function hasFocusWithinPanel() {
            return !!(document.activeElement && refs.display.contains(document.activeElement));
        }

        function blurFocusWithinPanel() {
            var active = document.activeElement;
            if (active && refs.display.contains(active) && typeof active.blur === 'function') {
                active.blur();
            }
        }

        function isPointInsidePanel(e) {
            if (!e || !refs.display || typeof refs.display.getBoundingClientRect !== 'function') return false;
            var x = Number(e.clientX);
            var y = Number(e.clientY);
            if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
            var rect = refs.display.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 &&
                x >= rect.left && x <= rect.right &&
                y >= rect.top && y <= rect.bottom;
        }

        function showControls(source) {
            clearControlsHideTimer();
            if (panelState !== 'settings') {
                applyPanelState('controls', source || 'subtitle-ui-controls');
            }
        }

        function scheduleClean(source) {
            clearControlsHideTimer();
            if (isSettingsOpen()) return;
            controlsHideTimer = setTimeout(function() {
                controlsHideTimer = null;
                if (isSettingsOpen()) return;
                if (panelPointerInside) return;
                if (refs.display.matches && refs.display.matches(':hover')) return;
                if (hasFocusWithinPanel()) return;
                applyPanelState('clean', source || 'subtitle-ui-clean');
            }, CONTROLS_HIDE_DELAY_MS);
        }

        function openSettings(source) {
            if (typeof options.openExternalSettings === 'function') {
                clearControlsHideTimer();
                externalSettingsOpen = true;
                applyPanelState('settings', source || 'subtitle-ui-settings-open');
                options.openExternalSettings(getSettings(), refs, {
                    source: source || 'subtitle-ui-settings-open'
                });
                if (refs.settingsBtn && typeof refs.settingsBtn.blur === 'function') {
                    refs.settingsBtn.blur();
                }
                return;
            }
            if (!refs.settingsPanel) return;
            clearControlsHideTimer();
            refs.settingsPanel.classList.remove('hidden');
            applyPanelState('settings', source || 'subtitle-ui-settings-open');
        }

        function closeSettings(source, nextPanelState) {
            var wasExternalSettingsOpen = externalSettingsOpen;
            if (wasExternalSettingsOpen && typeof options.closeExternalSettings === 'function') {
                externalSettingsOpen = false;
                options.closeExternalSettings({
                    source: source || 'subtitle-ui-settings-close'
                });
            } else {
                externalSettingsOpen = false;
            }
            if (refs.settingsPanel) {
                refs.settingsPanel.classList.add('hidden');
            }
            applyPanelState(nextPanelState || 'controls', source || 'subtitle-ui-settings-close');
            blurFocusWithinPanel();
            if (wasExternalSettingsOpen && nextPanelState !== 'clean') {
                scheduleClean(source || 'subtitle-ui-settings-close');
            }
        }

        function hasHostCloseBridge() {
            return !!(options.api && typeof options.api.changeSettings === 'function');
        }

        function hideLocalPanelAfterClose(source) {
            refs.display.classList.remove('show');
            refs.display.classList.add('hidden');
            updateRenderState({
                visible: false,
                subtitleEnabled: false
            }, {
                source: source || 'subtitle-ui-close-local'
            });
        }

        function applyState(nextState, detail) {
            applySettingsToUi(refs, nextState, options);
            applyUiLabels(refs, nextState);
            if (!refs.display.dataset.subtitlePanelState) {
                refs.display.dataset.subtitlePanelState = panelState;
            }
            if (refs.lockBtn) {
                refs.lockBtn.setAttribute('aria-pressed', nextState.subtitlePanelLocked ? 'true' : 'false');
                applyLockButtonIcon(refs.lockBtn, !!nextState.subtitlePanelLocked);
            }
            if (typeof options.onSettingsApplied === 'function') {
                options.onSettingsApplied(nextState, refs, detail || { changedKeys: [], source: 'init' });
            }
        }

        applyState(state, { changedKeys: [], source: 'init' });
        applyPanelState(panelState, 'subtitle-ui-init');
        cleanupFns.push(attachSubtitleScroll(refs));
        cleanupFns.push(subscribeSettings(applyState, { immediate: false }));

        function setPanelLocked(nextLocked, source) {
            var locked = !!nextLocked;
            var nextState = updateSettings({
                subtitlePanelLocked: locked,
                subtitleInteractionPassthrough: locked
            }, {
                source: source || 'subtitle-ui-lock'
            });
            if (typeof options.propagateSetting === 'function') {
                options.propagateSetting({
                    type: 'lock',
                    value: locked,
                    patch: {
                        subtitlePanelLocked: locked,
                        subtitleInteractionPassthrough: locked
                    },
                    state: nextState
                });
            }
            return nextState;
        }

        var observedThemeDark = isDarkThemeActive();
        var applyThemeStateIfChanged = function(source) {
            var nextThemeDark = isDarkThemeActive();
            if (nextThemeDark === observedThemeDark) return;
            observedThemeDark = nextThemeDark;
            applyState(getSettings(), { changedKeys: ['theme'], source: source });
        };
        var onThemeChanged = function() {
            applyThemeStateIfChanged('subtitle-ui-theme-event');
        };
        window.addEventListener('neko-theme-changed', onThemeChanged);
        cleanupFns.push(function() {
            window.removeEventListener('neko-theme-changed', onThemeChanged);
        });
        if (window.MutationObserver && document.documentElement) {
            var themeObserver = new MutationObserver(function(mutations) {
                for (var i = 0; i < mutations.length; i += 1) {
                    if (mutations[i].attributeName === 'data-theme') {
                        applyThemeStateIfChanged('subtitle-ui-theme-attribute');
                        break;
                    }
                }
            });
            themeObserver.observe(document.documentElement, {
                attributes: true,
                attributeFilter: ['data-theme']
            });
            cleanupFns.push(function() {
                themeObserver.disconnect();
            });
        }

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

        var panelPointerInside = false;

        if (refs.display) {
            var onPanelPointerEnter = function() {
                panelPointerInside = true;
                showControls('subtitle-ui-pointerenter');
            };
            var onPanelPointerLeave = function() {
                panelPointerInside = false;
                scheduleClean('subtitle-ui-pointerleave');
            };
            var onDocumentMouseMove = function(e) {
                if (refs.display.classList.contains('hidden')) return;
                var inside = isPointInsidePanel(e);
                if (inside) {
                    panelPointerInside = true;
                    showControls('subtitle-ui-mousemove');
                    return;
                }
                if (panelPointerInside || panelState === 'controls') {
                    panelPointerInside = false;
                    scheduleClean('subtitle-ui-mousemove-leave');
                }
            };
            var onPanelClick = function(e) {
                if (refs.settingsPanel && refs.settingsPanel.contains(e.target)) return;
                if (refs.panelControls && refs.panelControls.contains(e.target)) return;
                showControls('subtitle-ui-click');
            };
            var onPanelFocusIn = function() {
                showControls('subtitle-ui-focusin');
            };
            var onPanelFocusOut = function() {
                setTimeout(function() {
                    if (!hasFocusWithinPanel()) {
                        scheduleClean('subtitle-ui-focusout');
                    }
                }, 0);
            };
            var onPanelKeyDown = function(e) {
                if (e.key !== 'Escape') return;
                if (isSettingsOpen()) {
                    closeSettings('subtitle-ui-escape-settings', 'controls');
                    e.stopPropagation();
                    return;
                }
                applyPanelState('clean', 'subtitle-ui-escape-clean');
            };

            refs.display.addEventListener('pointerenter', onPanelPointerEnter);
            refs.display.addEventListener('pointerleave', onPanelPointerLeave);
            document.addEventListener('mousemove', onDocumentMouseMove, true);
            refs.display.addEventListener('click', onPanelClick);
            refs.display.addEventListener('focusin', onPanelFocusIn);
            refs.display.addEventListener('focusout', onPanelFocusOut);
            refs.display.addEventListener('keydown', onPanelKeyDown);
            cleanupFns.push(function() {
                refs.display.removeEventListener('pointerenter', onPanelPointerEnter);
                refs.display.removeEventListener('pointerleave', onPanelPointerLeave);
                document.removeEventListener('mousemove', onDocumentMouseMove, true);
                refs.display.removeEventListener('click', onPanelClick);
                refs.display.removeEventListener('focusin', onPanelFocusIn);
                refs.display.removeEventListener('focusout', onPanelFocusOut);
                refs.display.removeEventListener('keydown', onPanelKeyDown);
            });
        }

        if (refs.lockBtn) {
            var onLockClick = function(e) {
                e.stopPropagation();
                showControls('subtitle-ui-lock');
                var nextLocked = !getSettings().subtitlePanelLocked;
                setPanelLocked(nextLocked, 'subtitle-ui-lock');
                blurFocusWithinPanel();
            };
            refs.lockBtn.addEventListener('click', onLockClick);
            cleanupFns.push(function() {
                refs.lockBtn.removeEventListener('click', onLockClick);
            });
        }

        if (refs.closeBtn) {
            var onCloseClick = function(e) {
                e.stopPropagation();
                closeSettings('subtitle-ui-close', 'clean');
                if (typeof options.onClose === 'function') {
                    options.onClose();
                } else if (typeof options.propagateSetting === 'function') {
                    var nextState = updateSettings({ subtitleEnabled: false }, { source: 'subtitle-ui-close' });
                    options.propagateSetting({
                        type: 'toggle',
                        value: false,
                        patch: { subtitleEnabled: false },
                        state: nextState
                    });
                    if (!hasHostCloseBridge()) {
                        hideLocalPanelAfterClose('subtitle-ui-close-fallback');
                    }
                } else {
                    updateSettings({ subtitleEnabled: false }, { source: 'subtitle-ui-close' });
                    hideLocalPanelAfterClose('subtitle-ui-close-fallback');
                }
            };
            refs.closeBtn.addEventListener('click', onCloseClick);
            cleanupFns.push(function() {
                refs.closeBtn.removeEventListener('click', onCloseClick);
            });
        }

        if (refs.settingsBtn) {
            var onSettingsClick = function(e) {
                e.stopPropagation();
                if (externalSettingsOpen) {
                    closeSettings('subtitle-ui-panel', 'controls');
                } else if (typeof options.openExternalSettings === 'function') {
                    openSettings('subtitle-ui-panel');
                } else if (isSettingsOpen()) {
                    closeSettings('subtitle-ui-panel', 'controls');
                } else {
                    openSettings('subtitle-ui-panel');
                }
            };
            var onDocumentDown = function(e) {
                if (!isSettingsOpen()) return;
                if (refs.settingsPanel && refs.settingsPanel.contains(e.target)) return;
                if (refs.settingsBtn && refs.settingsBtn.contains(e.target)) return;
                closeSettings('subtitle-ui-panel-outside', 'clean');
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

        if (refs.fontSizeSelect) {
            var onFontSizeSelect = function() {
                var nextFontSize = normalizeSubtitleFontSize(refs.fontSizeSelect.value);
                var nextState = updateSettings({ subtitleFontSize: nextFontSize }, { source: 'subtitle-ui-font-size' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'fontSize',
                        value: nextFontSize,
                        patch: { subtitleFontSize: nextFontSize },
                        state: nextState
                    });
                }
            };
            refs.fontSizeSelect.addEventListener('change', onFontSizeSelect);
            cleanupFns.push(function() {
                refs.fontSizeSelect.removeEventListener('change', onFontSizeSelect);
            });
        }

        if (refs.colorSchemeSelect) {
            var onColorSchemeSelect = function() {
                var nextColorScheme = normalizeSubtitleColorScheme(refs.colorSchemeSelect.value);
                var nextState = updateSettings({ subtitleColorScheme: nextColorScheme }, { source: 'subtitle-ui-color-scheme' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'colorScheme',
                        value: nextColorScheme,
                        patch: { subtitleColorScheme: nextColorScheme },
                        state: nextState
                    });
                }
            };
            refs.colorSchemeSelect.addEventListener('change', onColorSchemeSelect);
            cleanupFns.push(function() {
                refs.colorSchemeSelect.removeEventListener('change', onColorSchemeSelect);
            });
        }

        if (refs.danmakuModeBtn) {
            var onDanmakuModeChange = function() {
                var nextDanmakuMode = !!refs.danmakuModeBtn.checked;
                var nextState = updateSettings({ subtitleDanmakuMode: nextDanmakuMode }, { source: 'subtitle-ui-danmaku-mode' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'danmakuMode',
                        value: nextDanmakuMode,
                        patch: { subtitleDanmakuMode: nextDanmakuMode },
                        state: nextState
                    });
                }
            };
            refs.danmakuModeBtn.addEventListener('change', onDanmakuModeChange);
            cleanupFns.push(function() {
                refs.danmakuModeBtn.removeEventListener('change', onDanmakuModeChange);
            });
        }

        if (options.windowInteractions === 'external') {
            refs.display.dataset.subtitleWindowInteractions = 'external';
        } else {
            cleanupFns.push(attachPanelResize(refs, options));
            cleanupFns.push(options.host === 'window' ? attachWindowDrag(refs, options) : attachWebDrag(refs));
        }

        return {
            refs: refs,
            applyCurrentState: function() {
                applyState(getSettings(), { changedKeys: [], source: 'manual' });
            },
            closeSettingsForExternalInteraction: function(nextPanelState) {
                closeSettings('subtitle-ui-external-interaction', nextPanelState || 'controls');
            },
            destroy: function() {
                clearControlsHideTimer();
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
        normalizeSubtitleFontSize: normalizeSubtitleFontSize,
        normalizeSubtitleColorScheme: normalizeSubtitleColorScheme,
        normalizeUiLocale: normalizeUiLocale,
        getCurrentUiLocale: getCurrentUiLocale,
        getUiText: getUiText,
        applyBackgroundOpacity: applyBackgroundOpacity,
        applySubtitleControlScale: applySubtitleControlScale,
        measureSubtitleLayout: measureSubtitleLayout,
        getPanelBounds: getPanelBounds,
        applySubtitlePanelBounds: applySubtitlePanelBounds,
        splitSubtitleDanmakuSegments: splitSubtitleDanmakuSegments,
        renderSubtitleDanmakuText: renderSubtitleDanmakuText,
        clearSubtitleDanmakuText: clearSubtitleDanmakuText,
        scrollSubtitleToBottom: scrollSubtitleToBottom,
        requestSubtitleAutoScroll: requestSubtitleAutoScroll,
        cancelSubtitleAutoScroll: cancelSubtitleAutoScroll,
        initSubtitleUI: initSubtitleUI
    };
})();
