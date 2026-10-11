/**
 * visit-settings.js — 设置弹窗「串门」分组的辅助（docs/design/visit-infrastructure.md
 * §5 PR-13、§3.7.6；OD-09 v3 / OD-06 v2）。
 *
 * 两个开关本身（visitEnabled 默认关、visitVoiceEnabled 默认开）由
 * avatar-ui-popup.js 的通用开关项渲染、经 saveNEKOSettings 持久化；本文件只管：
 * - 总闸探测：`GET /api/visit/state?catgirl=` 的 `enabled`（NEKO_VISIT_ENABLED）为真
 *   才显示分组入口，`default_tier` 作只读档位显示；
 * - 构图偏好「上半身 / 全身」（本机偏好，建房时带上，不进对话设置）；
 * - 串门人设面板（PR-12 的 `openVisitPersonaPreview`）的入口与 visitEnabled
 *   首次打开时的确认引导；面板未加载时入口不显示。
 * 记忆开关是隐藏配置，不在设置页出现；清除入口在记忆浏览器的串门面板。
 */
(function () {
    'use strict';

    if (window.nekoVisitSettings) return;

    var CROP_STORAGE_KEY = 'nekoVisitCrop';
    var PERSONA_PROMPTED_KEY = 'nekoVisitPersonaPrompted';
    var CROPS = ['upper', 'full'];
    var ENTRY_ATTR = 'data-neko-visit-settings-entry';

    var probePromise = null;
    var probed = { enabled: false, defaultTier: '' };

    function t(key, fallback) {
        if (typeof window.t === 'function') {
            var text = window.t(key);
            if (text && text !== key) return text;
        }
        return fallback;
    }

    function currentCatgirl() {
        var cfg = window.lanlan_config;
        var name = cfg && typeof cfg.lanlan_name === 'string' ? cfg.lanlan_name : '';
        return name;
    }

    async function requestHeaders() {
        var security = window.nekoLocalMutationSecurity;
        if (security && typeof security.getMutationHeaders === 'function') {
            try {
                return await security.getMutationHeaders();
            } catch (_) { /* 拿不到 token：后端会 403，按总闸关处理 */ }
        }
        return {};
    }

    async function isStaleCsrf(response) {
        if (response.status !== 403) return false;
        try {
            var body = await response.clone().json();
            return !!(body && body.error_code === 'csrf_validation_failed');
        } catch (_) {
            return false;
        }
    }

    async function refreshCsrfToken() {
        var security = window.nekoLocalMutationSecurity;
        if (!security || typeof security.refreshToken !== 'function') return false;
        try {
            return !!(await security.refreshToken());
        } catch (_) {
            return false;
        }
    }

    async function fetchState(catgirl) {
        var url = '/api/visit/state?catgirl=' + encodeURIComponent(catgirl);
        var response = await fetch(url, { cache: 'no-store', headers: await requestHeaders() });
        // 后端重启后页面缓存的 CSRF token 失效：刷新一次再试，不能把 403 当成总闸关
        if (await isStaleCsrf(response) && await refreshCsrfToken()) {
            response = await fetch(url, { cache: 'no-store', headers: await requestHeaders() });
        }
        return response;
    }

    // 每次打开设置弹窗都重新探（下面监听 neko-avatar-popup-opening）：总闸可能在页面开着期间
    // 被关掉（后端换了配置重启），加载时那次探测也可能失败。只合并同时在途的探测；拿不到确切的
    // 「开」一律按关处理
    function probe() {
        if (probePromise) return probePromise;
        probePromise = (async function () {
            probed = { enabled: false, defaultTier: probed.defaultTier };
            var catgirl = currentCatgirl();
            if (!catgirl) return probed;
            try {
                var response = await fetchState(catgirl);
                if (!response.ok) return probed;
                var body = await response.json();
                probed = {
                    enabled: !!(body && body.enabled === true),
                    defaultTier: body && typeof body.default_tier === 'string' ? body.default_tier : ''
                };
            } catch (_) { /* 后端没起或网络错：分组保持隐藏 */ }
            return probed;
        })();
        var inFlight = probePromise;
        inFlight.then(function () {
            if (probePromise === inFlight) probePromise = null;
        });
        return inFlight;
    }

    function applyTier(root) {
        var nodes = (root || document).querySelectorAll('[data-neko-visit-tier]');
        Array.prototype.forEach.call(nodes, function (node) {
            node.textContent = probed.defaultTier || '—';
        });
    }

    /**
     * 总闸开时显示所有「串门」分组入口：每次打开设置弹窗（neko-avatar-popup-opening）都调；
     * 弹窗创建时与本脚本加载时也各调一次，让第一次打开时入口已经就位、不闪一下。
     */
    function revealEntries() {
        return probe().then(function (result) {
            var entries = document.querySelectorAll('[' + ENTRY_ATTR + ']');
            Array.prototype.forEach.call(entries, function (entry) {
                entry.style.display = result.enabled ? '' : 'none';
            });
            applyTier(document);
            return result.enabled;
        });
    }

    function readCrop() {
        try {
            var value = localStorage.getItem(CROP_STORAGE_KEY);
            if (CROPS.indexOf(value) !== -1) return value;
        } catch (_) { /* noop */ }
        return 'upper';
    }

    function writeCrop(value) {
        if (CROPS.indexOf(value) === -1) return;
        try { localStorage.setItem(CROP_STORAGE_KEY, value); } catch (_) { /* noop */ }
        try {
            window.dispatchEvent(new CustomEvent('neko-visit-crop-changed', { detail: { crop: value } }));
        } catch (_) { /* noop */ }
    }

    function personaPanelAvailable() {
        return typeof window.openVisitPersonaPreview === 'function';
    }

    function openPersona(requireConfirm) {
        var catgirl = currentCatgirl();
        if (!catgirl || !personaPanelAvailable()) return false;
        try {
            window.openVisitPersonaPreview(catgirl, { requireConfirm: !!requireConfirm });
            return true;
        } catch (error) {
            console.warn('[VisitSettings] openVisitPersonaPreview failed:', error);
            return false;
        }
    }

    /**
     * visitEnabled 被打开：首次打开时弹串门人设确认（requireConfirm）。没确认开关
     * 也保持打开，出门 / 建房会被后端 409 VISIT_PERSONA_UNREVIEWED 引导回来。
     */
    function onVisitEnabledChanged(enabled) {
        if (!enabled) return;
        var prompted = false;
        try { prompted = localStorage.getItem(PERSONA_PROMPTED_KEY) === '1'; } catch (_) { /* noop */ }
        if (prompted) return;
        if (openPersona(true)) {
            try { localStorage.setItem(PERSONA_PROMPTED_KEY, '1'); } catch (_) { /* noop */ }
        }
    }

    function makeRow(labelText, labelKey) {
        var row = document.createElement('div');
        Object.assign(row.style, {
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '4px 8px',
            padding: '6px 12px',
            fontSize: '12px',
            color: 'var(--neko-popup-text, #333)'
        });
        var label = document.createElement('span');
        label.textContent = labelText;
        if (labelKey) label.setAttribute('data-i18n', labelKey);
        row.appendChild(label);
        return row;
    }

    function buildCropRow() {
        var row = makeRow(t('visit.settings.crop', '构图'), 'visit.settings.crop');
        var group = document.createElement('div');
        group.setAttribute('role', 'radiogroup');
        group.setAttribute('aria-label', t('visit.settings.crop', '构图'));
        Object.assign(group.style, { display: 'flex', gap: '4px' });
        var buttons = [];
        var paint = function () {
            var current = readCrop();
            buttons.forEach(function (btn) {
                var on = btn.dataset.crop === current;
                btn.setAttribute('aria-checked', on ? 'true' : 'false');
                btn.style.background = on ? 'var(--neko-popup-accent, #44b7fe)' : 'transparent';
                btn.style.color = on ? '#fff' : 'var(--neko-popup-text, #333)';
            });
        };
        [['upper', 'visit.settings.cropUpper', '上半身'], ['full', 'visit.settings.cropFull', '全身']].forEach(function (spec) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.dataset.crop = spec[0];
            btn.setAttribute('role', 'radio');
            btn.textContent = t(spec[1], spec[2]);
            btn.setAttribute('data-i18n', spec[1]);
            Object.assign(btn.style, {
                border: '1px solid var(--neko-popup-indicator-border, #ccc)',
                borderRadius: '6px',
                padding: '2px 8px',
                fontSize: '12px',
                whiteSpace: 'nowrap',
                cursor: 'pointer'
            });
            btn.addEventListener('click', function (event) {
                event.stopPropagation();
                writeCrop(spec[0]);
                paint();
            });
            buttons.push(btn);
            group.appendChild(btn);
        });
        paint();
        row.appendChild(group);
        return row;
    }

    function buildTierRow() {
        var row = makeRow(t('visit.settings.tier', '画质'), 'visit.settings.tier');
        var value = document.createElement('span');
        value.setAttribute('data-neko-visit-tier', '');
        value.textContent = probed.defaultTier || '—';
        value.style.opacity = '0.75';
        row.appendChild(value);
        return row;
    }

    function buildPersonaRow() {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = t('visit.settings.persona', '串门自我介绍');
        btn.setAttribute('data-i18n', 'visit.settings.persona');
        Object.assign(btn.style, {
            margin: '4px 12px',
            padding: '4px 8px',
            fontSize: '12px',
            border: '1px solid var(--neko-popup-indicator-border, #ccc)',
            borderRadius: '6px',
            background: 'transparent',
            color: 'var(--neko-popup-text, #333)',
            cursor: 'pointer',
            textAlign: 'left'
        });
        btn.addEventListener('click', function (event) {
            event.stopPropagation();
            openPersona(false);
        });
        return btn;
    }

    /** 往「串门」侧边面板里补上开关之外的几行：构图、只读档位、人设入口。 */
    function appendPanelExtras(container) {
        if (!container) return;
        container.appendChild(buildCropRow());
        container.appendChild(buildTierRow());
        if (personaPanelAvailable()) container.appendChild(buildPersonaRow());
    }

    window.nekoVisitSettings = Object.freeze({
        ENTRY_ATTR: ENTRY_ATTR,
        probe: probe,
        revealEntries: revealEntries,
        getCrop: readCrop,
        setCrop: writeCrop,
        appendPanelExtras: appendPanelExtras,
        onVisitEnabledChanged: onVisitEnabledChanged
    });

    // 设置弹窗建好之后每次打开只走 showPopup，不会再调 createSettingsPopupContent：
    // 所有模型类型的 showPopup 都派发这个生命周期事件，在这里重新探
    window.addEventListener('neko-avatar-popup-opening', function (event) {
        var detail = event && event.detail;
        if (detail && detail.buttonId === 'settings') void revealEntries();
    });

    // 设置弹窗可能比本脚本先建好：加载完补一次
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { void revealEntries(); });
    } else {
        void revealEntries();
    }
})();
