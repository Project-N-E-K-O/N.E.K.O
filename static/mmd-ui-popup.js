/**
 * MMD UI Popup - 弹出框组件
 * 参考 vrm-ui-popup.js，适配 MMD 模式
 */

const MMD_POPUP_ANIMATION_DURATION_MS = 200;

// 注入 CSS 样式
(function () {
    if (document.getElementById('mmd-popup-styles')) return;
    const style = document.createElement('style');
    style.id = 'mmd-popup-styles';
    style.textContent = `
        .mmd-popup {
            position: absolute;
            left: 100%;
            top: 0;
            margin-left: 8px;
            z-index: 100001;
            background: var(--neko-popup-bg, rgba(255, 255, 255, 0.65));
            backdrop-filter: saturate(180%) blur(20px);
            border: var(--neko-popup-border, 1px solid rgba(255, 255, 255, 0.18));
            border-radius: 8px;
            padding: 8px;
            box-shadow: var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08));
            display: none;
            flex-direction: column;
            gap: 6px;
            min-width: 180px;
            max-height: 200px;
            overflow-y: auto;
            pointer-events: auto !important;
            opacity: 0;
            transform: translateX(-10px);
            transition: opacity 0.2s cubic-bezier(0.1, 0.9, 0.2, 1), transform 0.2s cubic-bezier(0.1, 0.9, 0.2, 1);
        }
        .mmd-popup.visible {
            display: flex;
            opacity: 1;
            transform: translateX(0);
        }
        .mmd-popup-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 8px;
            cursor: pointer;
            border-radius: 6px;
            transition: background 0.2s ease;
            font-size: 13px;
            white-space: nowrap;
        }
        .mmd-popup-item:hover {
            background: rgba(68, 183, 254, 0.08);
        }
        .mmd-popup-item.selected {
            background: rgba(68, 183, 254, 0.1);
        }
    `;
    document.head.appendChild(style);
})();

/**
 * 创建 MMD 弹出框
 */
function createMMDPopup(parentElement, items, options = {}) {
    const popup = document.createElement('div');
    popup.className = 'mmd-popup';

    items.forEach(item => {
        const el = document.createElement('div');
        el.className = 'mmd-popup-item';
        el.textContent = item.label;
        if (item.selected) el.classList.add('selected');
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            if (item.onClick) item.onClick();
            hideMMDPopup(popup);
        });
        popup.appendChild(el);
    });

    parentElement.style.position = 'relative';
    parentElement.appendChild(popup);

    return popup;
}

function showMMDPopup(popup) {
    if (!popup) return;
    popup.style.display = 'flex';
    requestAnimationFrame(() => {
        popup.classList.add('visible');
    });
}

function hideMMDPopup(popup) {
    if (!popup) return;
    popup.classList.remove('visible');
    setTimeout(() => {
        popup.style.display = 'none';
    }, MMD_POPUP_ANIMATION_DURATION_MS);
}

// 导出到全局
window.createMMDPopup = createMMDPopup;
window.showMMDPopup = showMMDPopup;
window.hideMMDPopup = hideMMDPopup;
