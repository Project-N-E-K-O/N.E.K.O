/**
 * MMD UI Debug - 渲染调试面板
 * 提供环境光、主光源、曝光、色调映射等渲染参数的实时调节
 */

// ═══════════════════ 默认值 & 常量 ═══════════════════

const MMD_DEBUG_DEFAULTS = {
    ambientLightIntensity: 3,
    ambientLightColor: '#aaaaaa',
    directionalLightIntensity: 2,
    directionalLightColor: '#ffffff',
    toneMapping: 0,       // NoToneMapping (与 hime-display 默认一致)
    exposure: 1.0,
    pixelRatio: 0,        // 0 = auto (follow performance mode)
    useOutlineEffect: true
};

const MMD_TONE_MAPPING_OPTIONS = [
    { value: 0, label: 'NoToneMapping' },
    { value: 1, label: 'LinearToneMapping' },
    { value: 2, label: 'ReinhardToneMapping' },
    { value: 3, label: 'CineonToneMapping' },
    { value: 4, label: 'ACESFilmicToneMapping' },
    { value: 6, label: 'AgXToneMapping' },
    { value: 7, label: 'NeutralToneMapping' }
];

const MMD_DEBUG_STORAGE_KEY = 'mmd_debug_settings';

// ═══════════════════ CSS 注入 ═══════════════════

(function injectDebugStyles() {
    if (document.getElementById('mmd-debug-styles')) return;
    const style = document.createElement('style');
    style.id = 'mmd-debug-styles';
    style.textContent = `
        .mmd-debug-panel {
            position: fixed;
            right: 70px;
            top: 50%;
            transform: translateY(-50%);
            z-index: 100002;
            background: var(--neko-popup-bg, rgba(255, 255, 255, 0.72));
            backdrop-filter: saturate(180%) blur(20px);
            border: var(--neko-popup-border, 1px solid rgba(255, 255, 255, 0.2));
            border-radius: 14px;
            padding: 16px;
            box-shadow: var(--neko-popup-shadow, 0 4px 24px rgba(0,0,0,0.10));
            min-width: 280px;
            max-width: 340px;
            max-height: 80vh;
            overflow-y: auto;
            display: none;
            flex-direction: column;
            gap: 10px;
            pointer-events: auto !important;
            opacity: 0;
            transition: opacity 0.25s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.25s cubic-bezier(0.1, 0.9, 0.2, 1);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 13px;
            color: #333;
        }
        .mmd-debug-panel.visible {
            display: flex;
            opacity: 1;
        }
        .mmd-debug-panel::-webkit-scrollbar {
            width: 4px;
        }
        .mmd-debug-panel::-webkit-scrollbar-thumb {
            background: rgba(0,0,0,0.15);
            border-radius: 2px;
        }
        .mmd-debug-title {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .mmd-debug-title-text {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .mmd-debug-section {
            border-top: 1px solid rgba(0,0,0,0.06);
            padding-top: 8px;
        }
        .mmd-debug-section-label {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #888;
            margin-bottom: 6px;
        }
        .mmd-debug-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            min-height: 28px;
        }
        .mmd-debug-row label {
            flex-shrink: 0;
            font-size: 12px;
            color: #555;
            min-width: 70px;
        }
        .mmd-debug-row input[type="range"] {
            flex: 1;
            height: 4px;
            -webkit-appearance: none;
            appearance: none;
            background: rgba(0,0,0,0.1);
            border-radius: 2px;
            outline: none;
            cursor: pointer;
        }
        .mmd-debug-row input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 14px;
            height: 14px;
            border-radius: 50%;
            background: #44b7fe;
            border: 2px solid #fff;
            box-shadow: 0 1px 4px rgba(0,0,0,0.15);
            cursor: pointer;
        }
        .mmd-debug-row input[type="color"] {
            width: 28px;
            height: 22px;
            border: 1px solid rgba(0,0,0,0.1);
            border-radius: 4px;
            padding: 0;
            cursor: pointer;
            background: transparent;
        }
        .mmd-debug-row select {
            flex: 1;
            padding: 3px 6px;
            border: 1px solid rgba(0,0,0,0.1);
            border-radius: 6px;
            background: rgba(255,255,255,0.6);
            font-size: 12px;
            outline: none;
            cursor: pointer;
        }
        .mmd-debug-value {
            font-size: 11px;
            color: #999;
            min-width: 32px;
            text-align: right;
            font-variant-numeric: tabular-nums;
        }
        .mmd-debug-actions {
            display: flex;
            gap: 8px;
            margin-top: 4px;
        }
        .mmd-debug-btn {
            flex: 1;
            padding: 6px 0;
            border: 1px solid rgba(0,0,0,0.08);
            border-radius: 8px;
            background: rgba(255,255,255,0.5);
            font-size: 12px;
            cursor: pointer;
            text-align: center;
            transition: background 0.15s, transform 0.1s;
        }
        .mmd-debug-btn:hover {
            background: rgba(68, 183, 254, 0.1);
        }
        .mmd-debug-btn:active {
            transform: scale(0.97);
        }
        .mmd-debug-btn.primary {
            background: rgba(68, 183, 254, 0.15);
            border-color: rgba(68, 183, 254, 0.2);
            font-weight: 500;
        }
        .mmd-debug-toggle {
            position: relative;
            width: 36px;
            height: 20px;
            background: rgba(0,0,0,0.12);
            border-radius: 10px;
            cursor: pointer;
            transition: background 0.2s;
            flex-shrink: 0;
        }
        .mmd-debug-toggle.active {
            background: #44b7fe;
        }
        .mmd-debug-toggle::after {
            content: '';
            position: absolute;
            top: 2px;
            left: 2px;
            width: 16px;
            height: 16px;
            background: #fff;
            border-radius: 50%;
            box-shadow: 0 1px 3px rgba(0,0,0,0.15);
            transition: transform 0.2s;
        }
        .mmd-debug-toggle.active::after {
            transform: translateX(16px);
        }
        .mmd-debug-toast {
            position: fixed;
            bottom: 60px;
            left: 50%;
            transform: translateX(-50%) translateY(10px);
            background: rgba(0,0,0,0.7);
            color: #fff;
            padding: 8px 18px;
            border-radius: 8px;
            font-size: 13px;
            z-index: 200000;
            opacity: 0;
            transition: opacity 0.2s, transform 0.2s;
            pointer-events: none;
        }
        .mmd-debug-toast.show {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }
        @media (max-width: 768px) {
            .mmd-debug-panel {
                right: 10px;
                top: auto;
                bottom: 180px;
                transform: none;
                min-width: 240px;
                max-width: 90vw;
            }
        }
    `;
    document.head.appendChild(style);
})();

// ═══════════════════ 持久化 ═══════════════════

function loadMMDDebugSettings() {
    try {
        const raw = localStorage.getItem(MMD_DEBUG_STORAGE_KEY);
        if (raw) {
            const parsed = JSON.parse(raw);
            return { ...MMD_DEBUG_DEFAULTS, ...parsed };
        }
    } catch (e) {
        console.warn('[MMD Debug] 读取设置失败:', e);
    }
    return { ...MMD_DEBUG_DEFAULTS };
}

function saveMMDDebugSettings(settings) {
    try {
        localStorage.setItem(MMD_DEBUG_STORAGE_KEY, JSON.stringify(settings));
    } catch (e) {
        console.warn('[MMD Debug] 保存设置失败:', e);
    }
}

// ═══════════════════ 实时应用 ═══════════════════

function applyMMDDebugSetting(key, value) {
    const manager = window.mmdManager;
    if (!manager) return;

    const THREE = window.THREE;
    if (!THREE) return;

    switch (key) {
        case 'ambientLightIntensity':
            if (manager.ambientLight) manager.ambientLight.intensity = value;
            break;
        case 'ambientLightColor':
            if (manager.ambientLight) manager.ambientLight.color.set(value);
            break;
        case 'directionalLightIntensity':
            if (manager.directionalLight) manager.directionalLight.intensity = value;
            break;
        case 'directionalLightColor':
            if (manager.directionalLight) manager.directionalLight.color.set(value);
            break;
        case 'toneMapping':
            if (manager.renderer) {
                manager.renderer.toneMapping = Number(value);
                // toneMapping 变更后需要重新编译 shader 程序。
                // 对于 MMDToonMaterial（ShaderMaterial 子类），直接设置 needsUpdate = true
                // 虽然会触发 shader 重编译，但 Three.js 的 refreshMaterialUniforms 对
                // isShaderMaterial 不会调用 refreshUniformsCommon，导致纹理 uniform 丢失。
                // 跳过 ShaderMaterial，它们会在 toneMapping 变化时由 Three.js 内部
                // 的 program cache key 变化自动触发重编译。
                if (manager.currentModel && manager.currentModel.mesh) {
                    manager.currentModel.mesh.traverse(child => {
                        if (child.isMesh && child.material) {
                            const mats = Array.isArray(child.material) ? child.material : [child.material];
                            mats.forEach(m => {
                                if (!m.isShaderMaterial) {
                                    m.needsUpdate = true;
                                }
                            });
                        }
                    });
                }
            }
            break;
        case 'exposure':
            if (manager.renderer) manager.renderer.toneMappingExposure = value;
            break;
        case 'useOutlineEffect':
            manager.useOutlineEffect = value;
            break;
        case 'pixelRatio':
            if (manager.renderer) {
                if (value === 0) {
                    // auto: 让 mmd-core 的 applyPerformanceSettings 决定
                    if (manager.core) manager.core.applyPerformanceSettings();
                } else {
                    manager.renderer.setPixelRatio(value);
                }
            }
            break;
    }
}

/**
 * 将已保存的调试设置应用到当前渲染器
 */
function applyMMDSavedDebugSettings() {
    const settings = loadMMDDebugSettings();
    for (const [key, value] of Object.entries(settings)) {
        applyMMDDebugSetting(key, value);
    }
}

// ═══════════════════ Toast 提示 ═══════════════════

function showMMDDebugToast(message) {
    let toast = document.getElementById('mmd-debug-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'mmd-debug-toast';
        toast.className = 'mmd-debug-toast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toast._hideTimer);
    toast._hideTimer = setTimeout(() => {
        toast.classList.remove('show');
    }, 1500);
}

// ═══════════════════ 构建面板 ═══════════════════

function buildMMDDebugPanel() {
    const existing = document.getElementById('mmd-debug-panel');
    if (existing) existing.remove();

    const settings = loadMMDDebugSettings();

    const panel = document.createElement('div');
    panel.id = 'mmd-debug-panel';
    panel.className = 'mmd-debug-panel';

    // 阻止事件穿透
    ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'wheel'].forEach(evt => {
        panel.addEventListener(evt, e => e.stopPropagation());
    });

    // ── 标题 ──
    const title = document.createElement('div');
    title.className = 'mmd-debug-title';
    const titleText = document.createElement('span');
    titleText.className = 'mmd-debug-title-text';
    titleText.textContent = '🎨 渲染调试';
    const closeBtn = document.createElement('span');
    closeBtn.textContent = '✕';
    closeBtn.style.cssText = 'cursor:pointer; font-size:16px; color:#aaa; padding:2px 4px;';
    closeBtn.addEventListener('click', () => hideMMDDebugPanel());
    title.appendChild(titleText);
    title.appendChild(closeBtn);
    panel.appendChild(title);

    // ── 环境光部分 ──
    panel.appendChild(createDebugSection('环境光', [
        createSliderRow('强度', 'ambientLightIntensity', settings.ambientLightIntensity, 0, 10, 0.1),
        createColorRow('颜色', 'ambientLightColor', settings.ambientLightColor)
    ]));

    // ── 主光源部分 ──
    panel.appendChild(createDebugSection('主光源', [
        createSliderRow('强度', 'directionalLightIntensity', settings.directionalLightIntensity, 0, 10, 0.1),
        createColorRow('颜色', 'directionalLightColor', settings.directionalLightColor)
    ]));

    // ── 色调映射部分 ──
    panel.appendChild(createDebugSection('色调映射', [
        createSelectRow('模式', 'toneMapping', settings.toneMapping, MMD_TONE_MAPPING_OPTIONS),
        createSliderRow('曝光', 'exposure', settings.exposure, 0, 5, 0.05)
    ]));

    // ── 描边效果部分 ──
    panel.appendChild(createDebugSection('描边效果', [
        createToggleRow('描边', 'useOutlineEffect', settings.useOutlineEffect)
    ]));

    // ── 像素比部分 ──
    panel.appendChild(createDebugSection('像素比', [
        createSelectRow('像素比', 'pixelRatio', settings.pixelRatio, [
            { value: 0, label: '自动 (性能优先)' },
            { value: 1, label: '1x' },
            { value: 1.5, label: '1.5x' },
            { value: 2, label: '2x' }
        ])
    ]));

    // ── 渲染预设 ──
    panel.appendChild(createDebugSection('预设', [
        createPresetRow()
    ]));

    // ── 底部操作按钮 ──
    const actions = document.createElement('div');
    actions.className = 'mmd-debug-actions';

    const resetBtn = document.createElement('div');
    resetBtn.className = 'mmd-debug-btn';
    resetBtn.textContent = '重置默认';
    resetBtn.addEventListener('click', () => {
        saveMMDDebugSettings(MMD_DEBUG_DEFAULTS);
        for (const [key, value] of Object.entries(MMD_DEBUG_DEFAULTS)) {
            applyMMDDebugSetting(key, value);
        }
        // 刷新面板
        buildMMDDebugPanel();
        showMMDDebugPanel();
        showMMDDebugToast('已重置为默认值');
    });

    const saveBtn = document.createElement('div');
    saveBtn.className = 'mmd-debug-btn primary';
    saveBtn.textContent = '保存设置';
    saveBtn.addEventListener('click', () => {
        const currentSettings = collectCurrentSettings();
        saveMMDDebugSettings(currentSettings);
        showMMDDebugToast('设置已保存');
    });

    actions.appendChild(resetBtn);
    actions.appendChild(saveBtn);
    panel.appendChild(actions);

    document.body.appendChild(panel);
    return panel;
}

// ═══════════════════ UI 行构建器 ═══════════════════

function createDebugSection(label, rows) {
    const section = document.createElement('div');
    section.className = 'mmd-debug-section';
    const sectionLabel = document.createElement('div');
    sectionLabel.className = 'mmd-debug-section-label';
    sectionLabel.textContent = label;
    section.appendChild(sectionLabel);
    rows.forEach(row => section.appendChild(row));
    return section;
}

function createSliderRow(label, key, value, min, max, step) {
    const row = document.createElement('div');
    row.className = 'mmd-debug-row';

    const lbl = document.createElement('label');
    lbl.textContent = label;

    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min = min;
    slider.max = max;
    slider.step = step;
    slider.value = value;
    slider.dataset.key = key;

    const valueDisplay = document.createElement('span');
    valueDisplay.className = 'mmd-debug-value';
    valueDisplay.textContent = Number(value).toFixed(step < 1 ? (step < 0.1 ? 2 : 1) : 0);

    slider.addEventListener('input', () => {
        const v = parseFloat(slider.value);
        valueDisplay.textContent = v.toFixed(step < 1 ? (step < 0.1 ? 2 : 1) : 0);
        applyMMDDebugSetting(key, v);
    });

    row.appendChild(lbl);
    row.appendChild(slider);
    row.appendChild(valueDisplay);
    return row;
}

function createColorRow(label, key, value) {
    const row = document.createElement('div');
    row.className = 'mmd-debug-row';

    const lbl = document.createElement('label');
    lbl.textContent = label;

    const colorInput = document.createElement('input');
    colorInput.type = 'color';
    colorInput.value = value;
    colorInput.dataset.key = key;

    const valueDisplay = document.createElement('span');
    valueDisplay.className = 'mmd-debug-value';
    valueDisplay.textContent = value;

    colorInput.addEventListener('input', () => {
        valueDisplay.textContent = colorInput.value;
        applyMMDDebugSetting(key, colorInput.value);
    });

    row.appendChild(lbl);
    row.appendChild(colorInput);
    row.appendChild(valueDisplay);
    return row;
}

function createSelectRow(label, key, value, options) {
    const row = document.createElement('div');
    row.className = 'mmd-debug-row';

    const lbl = document.createElement('label');
    lbl.textContent = label;

    const select = document.createElement('select');
    select.dataset.key = key;
    options.forEach(opt => {
        const option = document.createElement('option');
        option.value = opt.value;
        option.textContent = opt.label;
        if (Number(opt.value) === Number(value)) option.selected = true;
        select.appendChild(option);
    });

    select.addEventListener('change', () => {
        applyMMDDebugSetting(key, Number(select.value));
    });

    row.appendChild(lbl);
    row.appendChild(select);
    return row;
}

function createToggleRow(label, key, value) {
    const row = document.createElement('div');
    row.className = 'mmd-debug-row';

    const lbl = document.createElement('label');
    lbl.textContent = label;

    const toggle = document.createElement('div');
    toggle.className = 'mmd-debug-toggle' + (value ? ' active' : '');
    toggle.dataset.key = key;

    toggle.addEventListener('click', () => {
        const isActive = toggle.classList.toggle('active');
        applyMMDDebugSetting(key, isActive);
    });

    row.appendChild(lbl);
    row.appendChild(toggle);
    return row;
}

// ═══════════════════ 渲染预设 ═══════════════════

const MMD_RENDER_PRESETS = {
    default: {
        label: '默认',
        ambientLightIntensity: 3, ambientLightColor: '#aaaaaa',
        directionalLightIntensity: 2, directionalLightColor: '#ffffff',
        toneMapping: 0, exposure: 1.0, pixelRatio: 0, useOutlineEffect: true
    },
    hime: {
        label: 'Hime 风格',
        ambientLightIntensity: 3, ambientLightColor: '#aaaaaa',
        directionalLightIntensity: 2, directionalLightColor: '#ffffff',
        toneMapping: 0, exposure: 1.0, pixelRatio: 0, useOutlineEffect: true
    },
    bright: {
        label: '明亮',
        ambientLightIntensity: 3.5, ambientLightColor: '#bbbbbb',
        directionalLightIntensity: 2.5, directionalLightColor: '#ffffff',
        toneMapping: 1, exposure: 1.0, pixelRatio: 0, useOutlineEffect: true
    },
    cinematic: {
        label: '电影',
        ambientLightIntensity: 2, ambientLightColor: '#b0b0c0',
        directionalLightIntensity: 2.5, directionalLightColor: '#fff5e6',
        toneMapping: 4, exposure: 0.85, pixelRatio: 0, useOutlineEffect: true
    },
    highContrast: {
        label: '高对比度',
        ambientLightIntensity: 2, ambientLightColor: '#999999',
        directionalLightIntensity: 3, directionalLightColor: '#ffffff',
        toneMapping: 1, exposure: 1.2, pixelRatio: 0, useOutlineEffect: true
    }
};

function createPresetRow() {
    const row = document.createElement('div');
    row.className = 'mmd-debug-row';

    const lbl = document.createElement('label');
    lbl.textContent = '风格';

    const select = document.createElement('select');
    select.dataset.key = '_preset';
    Object.entries(MMD_RENDER_PRESETS).forEach(([key, preset]) => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = preset.label;
        select.appendChild(option);
    });

    select.addEventListener('change', () => {
        const preset = MMD_RENDER_PRESETS[select.value];
        if (!preset) return;
        // 应用预设的所有设置
        const settings = { ...preset };
        delete settings.label;
        saveMMDDebugSettings(settings);
        for (const [key, value] of Object.entries(settings)) {
            applyMMDDebugSetting(key, value);
        }
        // 刷新面板以反映新值
        buildMMDDebugPanel();
        showMMDDebugPanel();
        showMMDDebugToast(`已应用预设: ${preset.label}`);
    });

    row.appendChild(lbl);
    row.appendChild(select);
    return row;
}

// ═══════════════════ 设置收集 ═══════════════════

function collectCurrentSettings() {
    const panel = document.getElementById('mmd-debug-panel');
    if (!panel) return loadMMDDebugSettings();

    const settings = {};

    panel.querySelectorAll('input[type="range"]').forEach(el => {
        settings[el.dataset.key] = parseFloat(el.value);
    });
    panel.querySelectorAll('input[type="color"]').forEach(el => {
        settings[el.dataset.key] = el.value;
    });
    panel.querySelectorAll('select').forEach(el => {
        if (el.dataset.key && !el.dataset.key.startsWith('_')) {
            settings[el.dataset.key] = Number(el.value);
        }
    });
    panel.querySelectorAll('.mmd-debug-toggle').forEach(el => {
        settings[el.dataset.key] = el.classList.contains('active');
    });

    return settings;
}

// ═══════════════════ 显示 / 隐藏 ═══════════════════

function showMMDDebugPanel() {
    let panel = document.getElementById('mmd-debug-panel');
    if (!panel) panel = buildMMDDebugPanel();
    panel.style.display = 'flex';
    requestAnimationFrame(() => {
        panel.classList.add('visible');
    });
}

function hideMMDDebugPanel() {
    const panel = document.getElementById('mmd-debug-panel');
    if (!panel) return;
    panel.classList.remove('visible');
    setTimeout(() => {
        panel.style.display = 'none';
    }, 250);
}

function toggleMMDDebugPanel() {
    const panel = document.getElementById('mmd-debug-panel');
    if (panel && panel.classList.contains('visible')) {
        hideMMDDebugPanel();
    } else {
        showMMDDebugPanel();
    }
}

function cleanupMMDDebugPanel() {
    const panel = document.getElementById('mmd-debug-panel');
    if (panel) panel.remove();
    const toast = document.getElementById('mmd-debug-toast');
    if (toast) toast.remove();
}

// ═══════════════════ 导出 ═══════════════════

window.showMMDDebugPanel = showMMDDebugPanel;
window.hideMMDDebugPanel = hideMMDDebugPanel;
window.toggleMMDDebugPanel = toggleMMDDebugPanel;
window.cleanupMMDDebugPanel = cleanupMMDDebugPanel;
window.applyMMDSavedDebugSettings = applyMMDSavedDebugSettings;
