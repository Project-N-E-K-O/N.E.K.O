/**
 * Live2D UI HUD - Agent任务HUD组件
 * 包含任务面板、任务卡片、HUD拖拽功能
 */

window.AgentHUD = window.AgentHUD || {};

var PLUGIN_DASHBOARD_REDIRECT_URL = '/api/agent/user_plugin/dashboard';
const STANDALONE_HUD_POSITION = Object.freeze({
    top: '0',
    left: '0',
    right: 'auto',
    transform: 'none'
});
const AGENT_TASK_HUD_COLLAPSED_KEY = 'agent-task-hud-collapsed-v2';

function appendPluginDashboardOpenerOrigin(url) {
    const target = new URL(url, document.baseURI || window.location.href);
    if (window.location && window.location.origin) {
        target.searchParams.set('yui_opener_origin', window.location.origin);
    }
    return target.toString();
}

function getPluginDashboardRedirectUrl() {
    return appendPluginDashboardOpenerOrigin(PLUGIN_DASHBOARD_REDIRECT_URL);
}

function appendCacheBuster(url) {
    var separator = typeof url === 'string' && url.indexOf('?') >= 0 ? '&' : '?';
    return `${url}${separator}v=${Date.now()}`;
}

function isStandaloneAgentHudPage() {
    try {
        return !!(document.body && document.body.classList.contains('agent-hud-standalone-page'));
    } catch (_) {
        return false;
    }
}

function isAgentHudSuppressedByGoodbye() {
    if (isStandaloneAgentHudPage()) return false;
    try {
        if (typeof window.isNekoGoodbyeResourceSuspendingOrSuspended === 'function' &&
            window.isNekoGoodbyeResourceSuspendingOrSuspended()) {
            return true;
        }
        if (typeof window.isNekoGoodbyeModeActive === 'function' && window.isNekoGoodbyeModeActive()) {
            return true;
        }
    } catch (_) { /* ignore */ }
    return false;
}

function setAgentHudDraggingState(active) {
    try {
        if (document.body) {
            document.body.classList.toggle('neko-agent-hud-dragging', !!active);
        }
        window.dispatchEvent(new CustomEvent('neko-agent-hud-drag-state', {
            detail: { dragging: !!active }
        }));
    } catch (_) {}
}

/**
 * 精简 AI 生成的冗长任务描述为用户友好的短文本
 * 例: "设置一个15分钟后的一次性提醒，内容为'起来活动'" → "15分钟后 起来活动"
 * 例: "打开浏览器搜索今天的天气" → "搜索今天的天气"
 */
window.AgentHUD._shortenDesc = function (desc) {
    if (!desc) return desc;
    let s = desc.trim();
    // 去掉开头的冗余动词
    s = s.replace(/^(请|帮我?|帮忙|设置一个?|创建一个?|添加一个?|发送一[条个]?|执行|进行|打开|启动|调用|运行)\s*/, '');
    // 去掉"的一次性提醒"
    s = s.replace(/的一次性提醒/g, '');
    // "，内容为'xxx'" → " xxx"
    s = s.replace(/[，,]\s*(内容[为是]|提醒内容[为是])\s*['""\u2018\u2019\u201C\u201D「」]?/g, ' ');
    // "提醒用户" → ""
    s = s.replace(/提醒用户/g, '');
    // 去掉引号
    s = s.replace(/['""\u2018\u2019\u201C\u201D「」]/g, '');
    // 去掉尾部的"的提醒"
    s = s.replace(/[的地得]?提醒$/, '');
    s = s.trim().replace(/^[，,。.、\s]+|[，,。.、\s]+$/g, '');
    return s.slice(0, 50) || desc.slice(0, 50);
};

window.AgentHUD._getTaskRawDesc = function (task) {
    const params = task && task.params ? task.params : {};
    return params.description || params.instruction || '';
};

// New cards and differential updates share this path so the visible text and
// tooltip source cannot drift apart while task_update events reuse DOM nodes.
window.AgentHUD._syncTaskDescriptionRow = function (card, task, isRunning) {
    if (!card) return;
    const rawDesc = this._getTaskRawDesc(task);
    const descText = rawDesc ? window.AgentHUD._shortenDesc(rawDesc) : '';
    let descRow = card.querySelector('.task-card-desc');

    if (!descText) {
        if (descRow) {
            if (window.NekoTooltip && typeof window.NekoTooltip.destroyFor === 'function') {
                window.NekoTooltip.destroyFor(descRow);
            }
            descRow.removeAttribute('title');
            descRow.remove();
        }
        return;
    }

    if (!descRow) {
        descRow = document.createElement('div');
        descRow.className = 'task-card-desc';
        Object.assign(descRow.style, {
            color: 'var(--neko-popup-text-sub, #888)',
            fontSize: '11px',
            lineHeight: '1.3',
            marginTop: '3px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
        });
        const progressRow = card.querySelector('.task-progress-row');
        if (progressRow) {
            card.insertBefore(descRow, progressRow);
        } else {
            card.appendChild(descRow);
        }
    }

    if (descRow.dataset.rawDesc !== rawDesc) {
        descRow.textContent = descText;
        descRow.dataset.rawDesc = rawDesc;
        // Bind only the description text, not the whole card, so cancel and drag
        // targets keep their existing pointer behavior.
        if (window.NekoTooltip && typeof window.NekoTooltip.attach === 'function') {
            window.NekoTooltip.attach(descRow, { text: rawDesc });
        } else {
            descRow.title = rawDesc;
        }
    }

    const expectedMargin = isRunning ? '3px' : '0';
    if (descRow.style.marginBottom !== expectedMargin) {
        descRow.style.marginBottom = expectedMargin;
    }
};

// 缓存当前显示器边界信息（多屏幕支持）
let cachedDisplayHUD = {
    x: 0,
    y: 0,
    width: window.innerWidth,
    height: window.innerHeight
};

function getAgentHudViewportBounds() {
    return {
        left: 0,
        top: 0,
        width: Math.max(1, Math.round(Number(window.innerWidth) || 1)),
        height: Math.max(1, Math.round(Number(window.innerHeight) || 1))
    };
}

function clampAgentHudViewportPosition(left, top, width, height) {
    const viewport = getAgentHudViewportBounds();
    const safeWidth = Math.max(1, Number(width) || 1);
    const safeHeight = Math.max(1, Number(height) || 1);
    const maxLeft = Math.max(viewport.left, viewport.left + viewport.width - safeWidth);
    const maxTop = Math.max(viewport.top, viewport.top + viewport.height - safeHeight);
    return {
        left: Math.max(viewport.left, Math.min(Number(left) || 0, maxLeft)),
        top: Math.max(viewport.top, Math.min(Number(top) || 0, maxTop))
    };
}

function getAgentHudPixelCoordinate(value, fallback) {
    const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
    const numeric = parseFloat(normalized);
    if (normalized.endsWith('px') && Number.isFinite(numeric)) {
        return numeric;
    }
    const safeFallback = Number(fallback);
    return Number.isFinite(safeFallback) ? safeFallback : 0;
}

function clampAgentHudElementToViewport(hud, options = {}) {
    if (!hud) return null;
    const rect = hud.getBoundingClientRect();
    const currentLeft = getAgentHudPixelCoordinate(hud.style.left, rect.left);
    const currentTop = getAgentHudPixelCoordinate(hud.style.top, rect.top);
    const clamped = clampAgentHudViewportPosition(currentLeft, currentTop, rect.width, rect.height);
    hud.style.left = clamped.left + 'px';
    hud.style.top = clamped.top + 'px';
    hud.style.right = 'auto';
    hud.style.transform = 'none';

    if (options.persist) {
        try {
            localStorage.setItem('agent-task-hud-position', JSON.stringify({
                left: hud.style.left,
                top: hud.style.top,
                right: hud.style.right,
                transform: hud.style.transform
            }));
        } catch (error) {
            console.warn('Failed to save position to localStorage:', error);
        }
    }
    return clamped;
}

// 更新显示器边界信息
async function updateDisplayBounds(centerX, centerY) {
    if (!window.electronScreen || !window.electronScreen.getAllDisplays) {
        // 非 Electron 环境，使用窗口大小
        cachedDisplayHUD = {
            x: 0,
            y: 0,
            width: window.innerWidth,
            height: window.innerHeight
        };
        return;
    }

    try {
        const displays = await window.electronScreen.getAllDisplays();
        if (!displays || displays.length === 0) {
            // 没有显示器信息，使用窗口大小
            cachedDisplayHUD = {
                x: 0,
                y: 0,
                width: window.innerWidth,
                height: window.innerHeight
            };
            return;
        }

        // 如果提供了中心点坐标，找到包含该点的显示器
        if (typeof centerX === 'number' && typeof centerY === 'number') {
            for (const display of displays) {
                if (centerX >= display.x && centerX < display.x + display.width &&
                    centerY >= display.y && centerY < display.y + display.height) {
                    cachedDisplayHUD = {
                        x: display.x,
                        y: display.y,
                        width: display.width,
                        height: display.height
                    };
                    return;
                }
            }
        }

        // 否则使用主显示器或第一个显示器
        const primaryDisplay = displays.find(d => d.primary) || displays[0];
        cachedDisplayHUD = {
            x: primaryDisplay.x,
            y: primaryDisplay.y,
            width: primaryDisplay.width,
            height: primaryDisplay.height
        };
    } catch (error) {
        console.warn('Failed to update display bounds:', error);
        // 失败时使用窗口大小
        cachedDisplayHUD = {
            x: 0,
            y: 0,
            width: window.innerWidth,
            height: window.innerHeight
        };
    }
}

// 将 updateDisplayBounds 暴露到全局，确保其他脚本或模块可以调用（兼容不同加载顺序）
try {
    if (typeof window !== 'undefined') window.updateDisplayBounds = updateDisplayBounds;
} catch (e) {
    // 忽略不可用的全局对象情形
}

// 创建Agent弹出框内容
window.AgentHUD._createAgentPopupContent = function (popup) {
    popup.style.gap = '0';
    const avatarPrefix = this && typeof this._avatarPrefix === 'string' && this._avatarPrefix
        ? this._avatarPrefix
        : 'live2d';

    // 添加状态显示栏 - Fluent Design
    const statusDiv = document.createElement('div');
    statusDiv.id = `${avatarPrefix}-agent-status`;
    Object.assign(statusDiv.style, {
        fontSize: '12px',
        color: 'var(--neko-popup-accent, #2a7bc4)',
        padding: '6px 8px',
        borderRadius: '4px',
        background: 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.05))',
        marginBottom: '0',
        minHeight: '20px',
        textAlign: 'center'
    });
    // 【状态机】初始显示"查询中..."，由状态机更新
    statusDiv.textContent = window.t ? window.t('settings.toggles.checking') : '查询中...';
    statusDiv.setAttribute('data-i18n', 'settings.toggles.checking');
    popup.appendChild(statusDiv);

    // 【状态机严格控制】所有 agent 开关默认禁用，title显示查询中
    // 只有状态机检测到可用性后才逐个恢复交互
    const agentToggles = [
        {
            id: 'agent-master',
            label: window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关',
            labelKey: 'settings.toggles.agentMaster',
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        },
        {
            id: 'agent-taskhud',
            label: window.t ? window.t('settings.toggles.showTaskHud') : '显示猫爪任务HUD',
            labelKey: 'settings.toggles.showTaskHud',
            initialDisabled: false,
            initialTitle: window.t ? window.t('settings.toggles.showTaskHud') : '显示猫爪任务HUD'
        },
        {
            id: 'agent-keyboard',
            label: window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制',
            labelKey: 'settings.toggles.keyboardControl',
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        },
        {
            id: 'agent-browser',
            label: window.t ? window.t('settings.toggles.browserUse') : 'Browser Control',
            labelKey: 'settings.toggles.browserUse',
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        },
        {
            id: 'agent-openfang',
            label: window.t ? window.t('settings.toggles.openfang') : '专属桌面',
            labelKey: 'settings.toggles.openfang',
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        },
        {
            id: 'agent-user-plugin',
            label: window.t ? window.t('settings.toggles.userPlugin') : '用户插件',
            labelKey: 'settings.toggles.userPlugin',
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        },
        {
            id: 'agent-openclaw',
            label: window.t ? window.t('settings.toggles.openclawConnect') : 'OpenClaw',
            labelKey: 'settings.toggles.openclawConnect',
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        }
    ];

    agentToggles.forEach(toggle => {
        const toggleItem = this._createToggleItem(toggle, popup);
        popup.appendChild(toggleItem);

        // 侧边快捷入口（用户插件管理面板 / OpenClaw 接入教程）
        if ((toggle.id === 'agent-user-plugin' || toggle.id === 'agent-openclaw') && typeof this._createSidePanelContainer === 'function') {
            document.querySelectorAll(`[data-neko-sidepanel-type="${toggle.id}-actions"]`).forEach((element) => {
                if (element && typeof element.remove === 'function') {
                    element.remove();
                }
            });
            const existingSidePanelById = document.getElementById(`${toggle.id}-actions`);
            if (existingSidePanelById && typeof existingSidePanelById.remove === 'function') {
                existingSidePanelById.remove();
            }
            const sidePanel = this._createSidePanelContainer();
            sidePanel.id = `${toggle.id}-actions`;
            sidePanel.setAttribute('data-neko-sidepanel-type', `${toggle.id}-actions`);
            sidePanel.style.flexDirection = 'column';
            sidePanel.style.alignItems = 'stretch';
            sidePanel.style.gap = '4px';
            sidePanel.style.padding = '6px 10px';
            sidePanel._anchorElement = toggleItem;
            sidePanel._popupElement = popup;

            const configBtn = document.createElement('div');
            const actionConfig = toggle.id === 'agent-user-plugin'
                ? {
                    actionId: 'management-panel',
                    labelKey: 'settings.toggles.pluginManagementPanel',
                    labelFallback: '管理面板',
                    icon: '⚙',
                    url: getPluginDashboardRedirectUrl,
                    windowName: 'neko_plugin_dashboard',
                    forceReloadOnReuse: true
                }
                : {
                    actionId: 'openclaw-guide',
                    labelKey: 'settings.toggles.openclawGuide',
                    labelFallback: 'OpenClaw 接入教程',
                    icon: '📘',
                    url: '/api/agent/openclaw/guide',
                    windowName: 'neko_openclaw_guide',
                    forceReloadOnReuse: true
                };
            const existingActionButton = document.getElementById(`neko-sidepanel-action-${toggle.id}-${actionConfig.actionId}`);
            if (existingActionButton && typeof existingActionButton.remove === 'function') {
                existingActionButton.remove();
            }
            configBtn.id = `neko-sidepanel-action-${toggle.id}-${actionConfig.actionId}`;
            configBtn.setAttribute('data-neko-sidepanel-action', actionConfig.actionId);
            configBtn.setAttribute('data-neko-sidepanel-toggle', toggle.id);
            Object.assign(configBtn.style, {
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '5px 8px',
                cursor: 'pointer',
                borderRadius: '6px',
                fontSize: '12px',
                whiteSpace: 'nowrap',
                color: 'var(--neko-popup-text, #333)',
                transition: 'background 0.15s ease'
            });
            const configIcon = document.createElement('span');
            configIcon.textContent = actionConfig.icon;
            configIcon.style.fontSize = '13px';
            const configLabel = document.createElement('span');
            configLabel.setAttribute('data-i18n', actionConfig.labelKey);
            configLabel.style.userSelect = 'none';
            configBtn.setAttribute('data-i18n-title', actionConfig.labelKey);
            const updateConfigI18n = () => {
                const translated = window.t ? window.t(actionConfig.labelKey) : actionConfig.labelFallback;
                configLabel.textContent = translated;
                configBtn.title = translated;
                configBtn.setAttribute('aria-label', translated);
            };
            configLabel._updateLabelText = updateConfigI18n;
            updateConfigI18n();
            const configArrow = document.createElement('span');
            configArrow.textContent = '↗';
            configArrow.style.marginLeft = 'auto';
            configArrow.style.opacity = '0.5';
            configArrow.style.fontSize = '11px';
            configBtn.appendChild(configIcon);
            configBtn.appendChild(configLabel);
            configBtn.appendChild(configArrow);

            configBtn.addEventListener('mouseenter', () => {
                configBtn.style.background = 'var(--neko-popup-hover, rgba(68,183,254,0.1))';
            });
            configBtn.addEventListener('mouseleave', () => {
                configBtn.style.background = 'transparent';
            });

            let isOpening = false;
            configBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (isOpening) return;
                isOpening = true;
                const width = Math.min(1280, Math.round(screen.width * 0.8));
                const height = Math.min(900, Math.round(screen.height * 0.8));
                const left = Math.max(0, Math.floor((screen.width - width) / 2));
                const top = Math.max(0, Math.floor((screen.height - height) / 2));
                const features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;
                const rawUrl = typeof actionConfig.url === 'function'
                    ? actionConfig.url()
                    : actionConfig.url;
                const absoluteUrl = new URL(rawUrl, document.baseURI || window.location.href).toString();
                const targetUrl = actionConfig.forceReloadOnReuse
                    ? appendCacheBuster(absoluteUrl)
                    : absoluteUrl;
                const existingWindow = window._openedWindows && window._openedWindows[actionConfig.windowName];
                let openedWindow = null;
                if (actionConfig.forceReloadOnReuse && existingWindow && !existingWindow.closed) {
                    try {
                        existingWindow.location.replace(targetUrl);
                    } catch (_) {
                        existingWindow.location.href = targetUrl;
                    }
                    if (typeof window.requestOpenedWindowRestore === 'function') {
                        window.requestOpenedWindowRestore(existingWindow);
                    }
                    existingWindow.focus();
                    openedWindow = existingWindow;
                } else if (typeof window.openOrFocusWindow === 'function') {
                    openedWindow = window.openOrFocusWindow(targetUrl, actionConfig.windowName, features, {
                        navigateOnReuse: !!actionConfig.forceReloadOnReuse
                    });
                } else {
                    openedWindow = window.open(targetUrl, actionConfig.windowName, features);
                }
                if (openedWindow) {
                    if (!window._openedWindows) {
                        window._openedWindows = {};
                    }
                    window._openedWindows[actionConfig.windowName] = openedWindow;
                    try {
                        openedWindow.focus();
                    } catch (_) {}
                }
                setTimeout(() => { isOpening = false; }, 500);
            });

            sidePanel.appendChild(configBtn);
            document.body.appendChild(sidePanel);
            this._attachSidePanelHover(toggleItem, sidePanel);
        }

    });

    // Init taskhud toggle from localStorage
    const taskhudCheckbox = document.getElementById(`${avatarPrefix}-agent-taskhud`);
    if (taskhudCheckbox) {
        const stored = (() => { try { return localStorage.getItem('neko-agent-taskhud-visible'); } catch (_) { return null; } })();
        taskhudCheckbox.checked = stored === null ? true : stored === 'true';
    }
};

// 创建 Agent 任务 HUD（屏幕正中右侧）
window.AgentHUD.createAgentTaskHUD = function () {
    // 如果已存在则不重复创建
    if (document.getElementById('agent-task-hud')) {
        return document.getElementById('agent-task-hud');
    }

    if (this._cleanupDragging) {
        this._cleanupDragging();
        this._cleanupDragging = null;
    }

    const hud = document.createElement('div');
    hud.id = 'agent-task-hud';

    // 获取保存的位置或使用默认位置
    const standaloneAgentHud = isStandaloneAgentHudPage();
    const savedPos = standaloneAgentHud ? null : localStorage.getItem('agent-task-hud-position');
    let position = { top: '50%', right: '20px', transform: 'translateY(-50%)' };

    if (standaloneAgentHud) {
        position = STANDALONE_HUD_POSITION;
    }

    if (!standaloneAgentHud && savedPos) {
        try {
            const parsed = JSON.parse(savedPos);
            position = {
                top: parsed.top || '50%',
                left: parsed.left || null,
                right: parsed.right || '20px',
                transform: parsed.transform || 'translateY(-50%)'
            };
        } catch (e) {
            console.warn('Failed to parse saved position:', e);
        }
    }

    Object.assign(hud.style, {
        position: 'fixed',
        width: standaloneAgentHud ? '100%' : '320px',
        maxHeight: standaloneAgentHud ? '100vh' : '60vh',
        height: standaloneAgentHud ? '100%' : '',
        background: 'var(--neko-popup-bg, rgba(255, 255, 255, 0.65))',
        backdropFilter: 'saturate(180%) blur(20px)',
        WebkitBackdropFilter: 'saturate(180%) blur(20px)',
        borderRadius: '8px',
        padding: '0',
        border: 'var(--neko-popup-border, 1px solid rgba(255, 255, 255, 0.18))',
        boxShadow: 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))',
        color: 'var(--neko-popup-text, #333)',
        fontFamily: "'Segoe UI', 'SF Pro Display', -apple-system, sans-serif",
        fontSize: '13px',
        zIndex: '9999',
        display: 'none',
        flexDirection: 'column',
        gap: '12px',
        pointerEvents: 'auto',
        overflowY: 'auto',
        transition: 'opacity 0.4s cubic-bezier(0.16, 1, 0.3, 1), transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.3s ease, width 0.4s cubic-bezier(0.16, 1, 0.3, 1), padding 0.4s ease, max-height 0.4s ease',
        cursor: standaloneAgentHud ? 'default' : 'move',
        userSelect: 'none',
        willChange: 'transform, width',
        contain: 'layout style paint'
    });

    // 应用保存的位置
    if (position.top) hud.style.top = position.top;
    if (position.left) hud.style.left = position.left;
    if (position.right) hud.style.right = position.right;
    if (position.transform) hud.style.transform = position.transform;

    // HUD 标题栏
    const header = document.createElement('div');
    header.id = 'agent-task-hud-header';
    Object.assign(header.style, {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 16px',
        margin: '0',
        backgroundColor: 'var(--neko-hud-header-bg, rgba(255, 255, 255, 0.85))',
        borderTopLeftRadius: '8px',
        borderTopRightRadius: '8px',
        borderBottom: '1px solid var(--neko-popup-separator, rgba(0, 0, 0, 0.08))',
        touchAction: 'none',
        transition: 'padding 0.4s ease, margin 0.4s ease, border-color 0.4s ease, border-radius 0.4s ease, background-color 0.4s ease'
    });

    const title = document.createElement('div');
    title.id = 'agent-task-hud-title';
    title.innerHTML = `<span style="color: var(--neko-popup-accent, #2a7bc4); margin-right: 8px;">⚡</span>${window.t ? window.t('agent.taskHud.title') : 'Agent 任务'}`;
    Object.assign(title.style, {
        fontWeight: '600',
        fontSize: '15px',
        color: 'var(--neko-popup-text, #333)',
        transition: 'width 0.3s ease, opacity 0.3s ease',
        overflow: 'hidden',
        whiteSpace: 'nowrap'
    });

    // 统计信息
    const stats = document.createElement('div');
    stats.id = 'agent-task-hud-stats';
    Object.assign(stats.style, {
        display: 'flex',
        gap: '12px',
        fontSize: '11px'
    });
    stats.innerHTML = `
        <span style="color: var(--neko-popup-accent, #2a7bc4);" title="${window.t ? window.t('agent.taskHud.running') : '运行中'}">● <span id="hud-running-count">0</span></span>
        <span style="color: var(--neko-popup-text-sub, #666);" title="${window.t ? window.t('agent.taskHud.queued') : '队列中'}">◐ <span id="hud-queued-count">0</span></span>
    `;

    // 右侧容器（stats + minimize）
    const headerRight = document.createElement('div');
    Object.assign(headerRight.style, {
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        flexShrink: '0'
    });

    // 最小化按钮
    const minimizeBtn = document.createElement('div');
    minimizeBtn.id = 'agent-task-hud-minimize';
    minimizeBtn.innerHTML = '▼';
    Object.assign(minimizeBtn.style, {
        width: '22px',
        height: '22px',
        borderRadius: '6px',
        background: 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.12))',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '10px',
        fontWeight: 'bold',
        color: 'var(--neko-popup-accent, #2a7bc4)',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        flexShrink: '0'
    });
    minimizeBtn.title = window.t ? window.t('agent.taskHud.minimize') : '折叠/展开';

    // 终止按钮
    const cancelBtn = document.createElement('div');
    cancelBtn.id = 'agent-task-hud-cancel';
    cancelBtn.innerHTML = '✕';
    Object.assign(cancelBtn.style, {
        width: '22px',
        height: '22px',
        borderRadius: '6px',
        background: 'var(--neko-popup-error-bg, rgba(220, 53, 69, 0.12))',
        display: 'none',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '11px',
        fontWeight: 'bold',
        color: 'var(--neko-popup-error, #dc3545)',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        flexShrink: '0'
    });
    cancelBtn.title = window.t ? window.t('agent.taskHud.cancelAll') : '终止所有任务';
    cancelBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const msg = window.t ? window.t('agent.taskHud.cancelConfirm') : '确定要终止所有正在进行的任务吗？';
        const title = window.t ? window.t('agent.taskHud.cancelAll') : '终止所有任务';
        const confirmed = await window.showConfirm(msg, title, { danger: true });
        if (!confirmed) return;
        try {
            cancelBtn.style.opacity = '0.5';
            cancelBtn.style.pointerEvents = 'none';
            const r = await fetch('/api/agent/admin/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'end_all' })
            });
            // 2xx → end_all ran; 504 → the proxy timed out AFTER forwarding,
            // so the tool server keeps executing end_all to completion.
            // Either way the backend registry ends up cleared — apply the
            // terminal state locally in case its task_update events never
            // arrive (stuck dispatch). Other failures (500 connect error,
            // 501 remote block) mean the request never landed: don't lie.
            if (r.ok || r.status === 504) {
                if (window.AgentHUD._markTasksCancelledLocally) {
                    window.AgentHUD._markTasksCancelledLocally();
                }
            }
        } catch (err) {
            console.error('[AgentHUD] Cancel all tasks failed:', err);
        } finally {
            cancelBtn.style.opacity = '1';
            cancelBtn.style.pointerEvents = 'auto';
        }
    });

    headerRight.appendChild(stats);
    headerRight.appendChild(cancelBtn);
    headerRight.appendChild(minimizeBtn);
    header.appendChild(title);
    header.appendChild(headerRight);
    hud.appendChild(header);

    // 任务列表容器
    const taskList = document.createElement('div');
    taskList.id = 'agent-task-list';
    Object.assign(taskList.style, {
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        padding: '0 16px 16px 16px',
        maxHeight: 'calc(60vh - 80px)',
        overflowY: 'auto',
        transition: 'max-height 0.3s ease, opacity 0.3s ease, padding 0.3s ease',
        contain: 'layout style'
    });

    // 整体折叠逻辑 (key v2: reset stale collapsed state)
    const applyHudCollapsed = (collapsed) => {
        if (!collapsed && hud.style.display !== 'none') {
            // Check edge collision for smooth unfolding direction towards the left
            const rect = hud.getBoundingClientRect();
            if (hud.style.left && hud.style.left !== 'auto') {
                const currentLeft = parseFloat(hud.style.left) || rect.left;
                if (currentLeft + 320 > window.innerWidth) {
                    // It will overflow right. Convert left anchor to right anchor
                    const currentRight = window.innerWidth - rect.right;
                    if (window.innerWidth - currentRight - 320 > 0) {
                        hud.style.right = currentRight + 'px';
                        hud.style.left = 'auto'; // let it expand to the left
                    } else {
                        hud.style.left = '0px';
                        hud.style.right = 'auto';
                    }
                }
            }
        }

        if (collapsed) {
            hud.style.width = 'auto';
            hud.style.gap = '0'; 
            
            header.style.padding = '12px 16px';
            header.style.backgroundColor = 'var(--neko-hud-header-bg, rgba(255, 255, 255, 0.85))';
            header.style.borderBottom = 'none';
            header.style.justifyContent = 'center';
            header.style.borderRadius = '8px'; // round all corners
            
            title.style.display = 'none';
            stats.style.display = 'flex';
            taskList.style.display = 'none'; 
            taskList.style.opacity = '0';
            minimizeBtn.style.transform = 'rotate(-90deg)';
        } else {
            hud.style.width = standaloneAgentHud ? '100%' : '320px';
            hud.style.gap = '12px'; 
            
            header.style.padding = '12px 16px';
            header.style.backgroundColor = 'var(--neko-hud-header-bg, rgba(255, 255, 255, 0.85))';
            header.style.borderBottom = '1px solid var(--neko-popup-separator, rgba(0, 0, 0, 0.08))';
            header.style.justifyContent = 'space-between';
            header.style.borderRadius = '8px 8px 0 0'; // round only top corners
            
            title.style.display = '';
            stats.style.display = 'flex';
            taskList.style.display = 'flex'; 
            taskList.style.maxHeight = 'calc(60vh - 80px)';
            taskList.style.opacity = '1';
            taskList.style.overflowY = 'auto';
            minimizeBtn.style.transform = 'rotate(0deg)';
        }
    };

    // Default: expanded
    let hudCollapsed = false;
    try { hudCollapsed = localStorage.getItem(AGENT_TASK_HUD_COLLAPSED_KEY) === 'true'; } catch (_) { }
    applyHudCollapsed(hudCollapsed);

    minimizeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        hudCollapsed = !hudCollapsed;
        applyHudCollapsed(hudCollapsed);
        try { localStorage.setItem(AGENT_TASK_HUD_COLLAPSED_KEY, String(hudCollapsed)); } catch (_) { }
    });

    hud._setAgentTaskHudCollapsed = (collapsed) => {
        hudCollapsed = collapsed === true;
        applyHudCollapsed(hudCollapsed);
        try { localStorage.setItem(AGENT_TASK_HUD_COLLAPSED_KEY, String(hudCollapsed)); } catch (_) { }
    };

    // 空状态提示
    const emptyState = document.createElement('div');
    emptyState.id = 'agent-task-empty';

    // 空状态容器
    const emptyContent = document.createElement('div');
    emptyContent.textContent = window.t ? window.t('agent.taskHud.noTasks') : '暂无活动任务';
    Object.assign(emptyContent.style, {
        textAlign: 'center',
        color: 'var(--neko-popup-text-sub, #64748b)',
        padding: '20px',
        fontSize: '12px',
        transition: 'all 0.3s ease'
    });

    // 设置空状态容器样式
    Object.assign(emptyState.style, {
        position: 'relative',
        transition: 'all 0.3s ease'
    });

    emptyState.appendChild(emptyContent);
    taskList.appendChild(emptyState);

    hud.appendChild(taskList);

    document.body.appendChild(hud);

    // 添加拖拽功能
    this._setupDragging(hud);

    return hud;
};

// 设置空状态折叠功能 (已移除, 之前的 empty-state triangle 不再使用)
window.AgentHUD._setupCollapseFunctionality = function (emptyState, collapseButton, emptyContent) {
    // Legacy function, kept for signature compatibility if referenced
};

// 显示任务 HUD
window.AgentHUD.showAgentTaskHUD = function () {
    console.log('[AgentHUD][TimeoutTrace] showAgentTaskHUD called. Current timeout ID:', this._hideTimeout);
    if (isAgentHudSuppressedByGoodbye()) {
        this.hideAgentTaskHUD();
        return;
    }
    
    // 清除任何正在进行的隐藏动画定时器，防止闪现后立刻消失
    if (this._hideTimeout) {
        console.log('[AgentHUD][TimeoutTrace] Clearing timeout ID:', this._hideTimeout);
        clearTimeout(this._hideTimeout);
        this._hideTimeout = null;
    }

    let hud = document.getElementById('agent-task-hud');
    if (!hud) {
        hud = this.createAgentTaskHUD();
    }
    hud.style.display = 'flex';
    hud.style.opacity = '1';
    const standaloneAgentHud = isStandaloneAgentHudPage();
    const savedPos = standaloneAgentHud ? null : localStorage.getItem('agent-task-hud-position');
    if (standaloneAgentHud) {
        hud.style.left = STANDALONE_HUD_POSITION.left;
        hud.style.top = STANDALONE_HUD_POSITION.top;
        hud.style.right = STANDALONE_HUD_POSITION.right;
        hud.style.transform = STANDALONE_HUD_POSITION.transform;
    } else if (savedPos) {
        try {
            const parsed = JSON.parse(savedPos);
            if (parsed.top) hud.style.top = parsed.top;
            if (parsed.left) hud.style.left = parsed.left;
            if (parsed.right) hud.style.right = parsed.right;
            if (parsed.transform) hud.style.transform = parsed.transform;
        } catch (e) {
            hud.style.transform = 'translateY(-50%) translateX(0)';
        }
        clampAgentHudElementToViewport(hud, { persist: true });
    } else {
        hud.style.transform = 'translateY(-50%) translateX(0)';
    }
};

// 隐藏任务 HUD
window.AgentHUD.expandAgentTaskHUD = function () {
    const hud = document.getElementById('agent-task-hud');
    if (!hud) return false;
    if (typeof hud._setAgentTaskHudCollapsed === 'function') {
        hud._setAgentTaskHudCollapsed(false);
        return true;
    }
    try { localStorage.setItem(AGENT_TASK_HUD_COLLAPSED_KEY, 'false'); } catch (_) {}
    return false;
};

window.AgentHUD.hideAgentTaskHUD = function () {
    const hud = document.getElementById('agent-task-hud');
    if (!hud) {
        // HUD 不存在时无需创建再隐藏，直接返回
        return;
    }

    // The tooltip lives on document.body, so hide it explicitly when the HUD fades.
    if (window.NekoTooltip && typeof window.NekoTooltip.hide === 'function') {
        window.NekoTooltip.hide();
    }

    // 已经处于隐藏状态时跳过重复操作
    if (hud.style.display === 'none') {
        return;
    }

    console.log('[AgentHUD] hideAgentTaskHUD: starting fade out');
    hud.style.opacity = '0';
    const standaloneAgentHud = isStandaloneAgentHudPage();
    const savedPos = standaloneAgentHud ? null : localStorage.getItem('agent-task-hud-position');
    if (standaloneAgentHud) {
        hud.style.left = STANDALONE_HUD_POSITION.left;
        hud.style.top = STANDALONE_HUD_POSITION.top;
        hud.style.right = STANDALONE_HUD_POSITION.right;
        hud.style.transform = STANDALONE_HUD_POSITION.transform;
    } else if (!savedPos) {
        hud.style.transform = 'translateY(-50%) translateX(20px)';
    }

    // 如果之前有正在等待的隐藏定时器，先清理掉
    if (this._hideTimeout) {
        clearTimeout(this._hideTimeout);
    }

    this._hideTimeout = setTimeout(() => {
        hud.style.display = 'none';
        this._hideTimeout = null;
    }, 300);
};

// 更新任务 HUD 内容
window.AgentHUD.updateAgentTaskHUD = function (tasksData) {
    // Cache latest snapshot so deferred re-render won't use stale closure data.
    this._latestTasksData = tasksData;
    if (isAgentHudSuppressedByGoodbye()) {
        if (this._updateRafId) {
            cancelAnimationFrame(this._updateRafId);
            this._updateRafId = null;
        }
        this.hideAgentTaskHUD();
        return;
    }

    // RAF throttle: coalesce rapid-fire WebSocket updates into a single frame
    if (this._updateRafId) return;
    this._updateRafId = requestAnimationFrame(() => {
        this._updateRafId = null;
        this._doUpdateAgentTaskHUD();
    });
};

// Local fallback: mark tasks cancelled in the shared task map and re-render.
// The HUD is purely event-driven; when the backend is wedged (the very
// situation the user is cancelling out of) its task_update(cancelled) events
// may never arrive, leaving cards stuck on "running" forever. The cancel
// click handlers below call this after reaching the server so the UI always
// converges. Backend events for the same tasks merge idempotently in
// app-websocket.js.
window.AgentHUD._markTasksCancelledLocally = function (taskIds, status) {
    const map = window._agentTaskMap;
    if (!map || typeof map.forEach !== 'function' || map.size === 0) return;
    if (!window._agentTaskRemoveTimers) window._agentTaskRemoveTimers = new Map();
    const ids = Array.isArray(taskIds) ? new Set(taskIds) : null;
    // Optional status override: when the backend reports the task already
    // reached a different terminal state, mirror it instead of "cancelled".
    const terminalStatus = status || 'cancelled';
    const now = Date.now();
    let changed = false;
    map.forEach((t, id) => {
        if (!t || (ids && !ids.has(id))) return;
        if (t.status !== 'running' && t.status !== 'queued') return;
        map.set(id, Object.assign({}, t, { status: terminalStatus, terminal_at: now }));
        changed = true;
        // Schedule the same 10s map removal as the websocket event path —
        // otherwise the entry outlives the HUD's 30s terminal cache and a
        // later full-map refresh would re-show it as a "new" terminal task.
        // If the backend's own cancelled event arrives later, app-websocket
        // clears this timer and installs its own (idempotent handoff).
        if (window._agentTaskRemoveTimers.has(id)) {
            clearTimeout(window._agentTaskRemoveTimers.get(id));
        }
        window._agentTaskRemoveTimers.set(id, setTimeout(() => {
            window._agentTaskRemoveTimers.delete(id);
            const current = window._agentTaskMap && window._agentTaskMap.get(id);
            if (!current || current.status !== terminalStatus) return;
            window._agentTaskMap.delete(id);
            const remaining = Array.from(window._agentTaskMap.values());
            window.AgentHUD.updateAgentTaskHUD({
                success: true,
                tasks: remaining,
                total_count: remaining.length,
                running_count: remaining.filter(t2 => t2.status === 'running').length,
                queued_count: remaining.filter(t2 => t2.status === 'queued').length,
                completed_count: remaining.filter(t2 => t2.status === 'completed').length,
                failed_count: remaining.filter(t2 => t2.status === 'failed').length,
                timestamp: new Date().toISOString()
            });
        }, 10000));
    });
    if (!changed) return;
    const tasks = Array.from(map.values());
    this.updateAgentTaskHUD({
        success: true,
        tasks: tasks,
        total_count: tasks.length,
        running_count: tasks.filter(t => t.status === 'running').length,
        queued_count: tasks.filter(t => t.status === 'queued').length,
        completed_count: tasks.filter(t => t.status === 'completed').length,
        failed_count: tasks.filter(t => t.status === 'failed').length,
        timestamp: new Date().toISOString()
    });
};

// Internal: actual HUD update logic (called via RAF throttle)
window.AgentHUD._doUpdateAgentTaskHUD = function () {
    if (isAgentHudSuppressedByGoodbye()) {
        this.hideAgentTaskHUD();
        return;
    }
    const tasksData = this._latestTasksData;
    if (!tasksData) return;

    const taskList = document.getElementById('agent-task-list');
    const emptyState = document.getElementById('agent-task-empty');
    const runningCount = document.getElementById('hud-running-count');
    const queuedCount = document.getElementById('hud-queued-count');
    const cancelBtn = document.getElementById('agent-task-hud-cancel');

    if (!taskList) {
        // HUD not yet created — create it now so incoming tasks can render
        if (typeof window.AgentHUD.createAgentTaskHUD === 'function') {
            window.AgentHUD.createAgentTaskHUD();
        }
        const retryList = document.getElementById('agent-task-list');
        if (!retryList) return;
        // Re-call with the now-created HUD
        return this._doUpdateAgentTaskHUD();
    }

    // 更新统计数据
    if (runningCount) runningCount.textContent = tasksData.running_count || 0;
    if (queuedCount) queuedCount.textContent = tasksData.queued_count || 0;

    // Show running/queued tasks + recently completed/failed tasks (linger 10s)
    if (!this._taskFirstSeen) this._taskFirstSeen = {};
    if (!this._taskStatusById) this._taskStatusById = {};
    if (!this._taskTerminalAt) this._taskTerminalAt = {};
    const now = Date.now();
    const MIN_DISPLAY_MS = 10000; // completed/failed tasks linger for 10 seconds

    // Track first-seen and terminal-at timestamps
    (tasksData.tasks || []).forEach(t => {
        if (!t.id) return;
        if (!this._taskFirstSeen[t.id]) this._taskFirstSeen[t.id] = now;
        this._taskStatusById[t.id] = t.status;
        // Record when a task first transitions to terminal status
        const isTerminal = t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled';
        if (isTerminal && !this._taskTerminalAt[t.id]) {
            this._taskTerminalAt[t.id] = now;
        }
    });

    // Show running/queued tasks + terminal tasks still within linger window
    const activeTasks = (tasksData.tasks || []).filter(t => {
        if (t.status === 'running' || t.status === 'queued') return true;
        const termAt = this._taskTerminalAt[t.id];
        if (termAt && (now - termAt) < MIN_DISPLAY_MS) return true;
        return false;
    });

    // Schedule a deferred re-render to clean up lingering cards after they expire
    const lingeringTasks = activeTasks.filter(t =>
        t.status !== 'running' && t.status !== 'queued'
    );
    if (lingeringTasks.length > 0) {
        // Reset timer so newly arrived terminal tasks get a full linger window
        if (this._lingerTimer) clearTimeout(this._lingerTimer);
        this._lingerTimer = setTimeout(() => {
            this._lingerTimer = null;
            if (window._agentTaskMap) {
                const snapshot = {
                    tasks: Array.from(window._agentTaskMap.values()),
                    running_count: 0,
                    queued_count: 0
                };
                snapshot.tasks.forEach(t => {
                    if (t.status === 'running') snapshot.running_count++;
                    if (t.status === 'queued') snapshot.queued_count++;
                });
                window.AgentHUD.updateAgentTaskHUD(snapshot);
            }
        }, MIN_DISPLAY_MS);
    }

    // Auto-show HUD when there are active tasks (handles race with checkAndToggleTaskHUD)
    if (activeTasks.length > 0) {
        const hud = document.getElementById('agent-task-hud');
        if (hud && (hud.style.display === 'none' || hud.style.opacity === '0')) {
            if (typeof window.AgentHUD.showAgentTaskHUD === 'function') {
                window.AgentHUD.showAgentTaskHUD();
            }
        }
    }

    // Clean up old cache entries (older than 30s since terminal or first seen)
    for (const tid in this._taskFirstSeen) {
        const terminalAt = this._taskTerminalAt[tid];
        const cleanupBase = terminalAt || this._taskFirstSeen[tid];
        if (!cleanupBase || now - cleanupBase <= 30000) continue;
        delete this._taskFirstSeen[tid];
        delete this._taskStatusById[tid];
        delete this._taskTerminalAt[tid];
    }

    if (cancelBtn) {
        const hasCancelable = activeTasks.some(t => t.status === 'running' || t.status === 'queued');
        cancelBtn.style.display = hasCancelable ? 'flex' : 'none';
    }

    // 显示/隐藏空状态（保留折叠状态）
    if (emptyState) {
        if (activeTasks.length === 0) {
            // 没有任务时显示空状态
            emptyState.style.display = 'block';
            emptyState.style.visibility = 'visible';
        } else {
            // 有任务时隐藏空状态，但保留折叠状态
            emptyState.style.display = 'none';
            emptyState.style.visibility = 'hidden';
        }
    }

    // 排序：前台任务（computer_use / mcp）优先，插件任务沉底
    const _taskSortPriority = (t) => {
        if (t.type === 'computer_use' || t.type === 'browser_use') return 0;
        if (t.type === 'mcp') return 1;
        // user_plugin / plugin_direct → 沉底
        return 2;
    };
    activeTasks.sort((a, b) => _taskSortPriority(a) - _taskSortPriority(b));

    // --- Differential DOM update: avoid full rebuild to prevent backdrop-filter recomposite flicker ---
    const activeIds = new Set(activeTasks.map(t => t.id));
    const existingCards = taskList.querySelectorAll('.task-card');
    const existingById = new Map();
    existingCards.forEach(card => {
        const tid = card.dataset.taskId;
        if (tid && activeIds.has(tid)) {
            existingById.set(tid, card);
        } else {
            const descRow = card.querySelector('.task-card-desc');
            if (descRow && window.NekoTooltip && typeof window.NekoTooltip.destroyFor === 'function') {
                window.NekoTooltip.destroyFor(descRow);
            }
            card.remove(); // remove cards no longer active
        }
    });

    // Build the desired card order, reusing/updating existing cards
    const fragment = document.createDocumentFragment();
    activeTasks.forEach(task => {
        const existing = existingById.get(task.id);
        if (existing) {
            const node = this._updateTaskCard(existing, task);
            fragment.appendChild(node || existing);
        } else {
            const card = this._createTaskCard(task);
            fragment.appendChild(card);
        }
    });

    // Re-append empty state first (it should stay at top), then task cards
    if (emptyState && emptyState.parentNode === taskList) {
        taskList.insertBefore(fragment, emptyState.nextSibling);
    } else {
        taskList.appendChild(fragment);
    }
};

// 差异更新已有任务卡片（避免全量 DOM 重建触发 backdrop-filter 重合成导致模型闪烁）
window.AgentHUD._updateTaskCard = function (card, task) {
    const isRunning = task.status === 'running';
    const isCompleted = task.status === 'completed';
    const isFailed = task.status === 'failed';
    const isCancelled = task.status === 'cancelled';
    const isTerminal = isCompleted || isFailed || isCancelled;

    // Update start_time data attribute
    if (task.start_time) card.dataset.startTime = task.start_time;

    // Compute status visuals
    let statusColor, statusText, cardBg, cardBorder;
    if (isCompleted) {
        statusColor = 'var(--neko-popup-success, #16a34a)';
        statusText = window.t ? window.t('agent.taskHud.statusCompleted') : '已完成';
        cardBg = 'var(--neko-popup-success-bg, rgba(22, 163, 74, 0.06))';
        cardBorder = 'var(--neko-popup-success-border, rgba(22, 163, 74, 0.2))';
    } else if (isFailed) {
        statusColor = 'var(--neko-popup-error, #dc2626)';
        statusText = window.t ? window.t('agent.taskHud.statusFailed') : '失败';
        cardBg = 'var(--neko-popup-error-bg, rgba(220, 38, 38, 0.06))';
        cardBorder = 'var(--neko-popup-error-border, rgba(220, 38, 38, 0.2))';
    } else if (isCancelled) {
        statusColor = 'var(--neko-popup-text-sub, #666)';
        statusText = window.t ? window.t('agent.taskHud.statusCancelled') : '已取消';
        cardBg = 'var(--neko-popup-bg, rgba(249, 249, 249, 0.6))';
        cardBorder = 'var(--neko-popup-border-color, rgba(0, 0, 0, 0.06))';
    } else if (isRunning) {
        statusColor = 'var(--neko-popup-accent, #2a7bc4)';
        statusText = window.t ? window.t('agent.taskHud.statusRunning') : '运行中';
        cardBg = 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.08))';
        cardBorder = 'var(--neko-popup-accent-border, rgba(42, 123, 196, 0.25))';
    } else {
        statusColor = 'var(--neko-popup-text-sub, #666)';
        statusText = window.t ? window.t('agent.taskHud.statusQueued') : '队列中';
        cardBg = 'var(--neko-popup-bg, rgba(249, 249, 249, 0.6))';
        cardBorder = 'var(--neko-popup-border-color, rgba(0, 0, 0, 0.06))';
    }

    // Use semantic state key to avoid comparing CSS var() strings against resolved style values
    const stateKey = isCancelled ? 'cancelled' : isCompleted ? 'completed' : isFailed ? 'failed' : isRunning ? 'running' : 'queued';
    if (card.dataset.cardState !== stateKey) {
        card.dataset.cardState = stateKey;
        card.style.background = cardBg;
        card.style.border = `1px solid ${cardBorder}`;
        card.style.opacity = isTerminal ? '0.6' : '1';
    }

    // Update status badge text & color (keyed by same state)
    const badge = card.querySelector('.task-status-badge');
    if (badge && badge.dataset.statusState !== stateKey) {
        badge.dataset.statusState = stateKey;
        badge.textContent = statusText;
        badge.style.color = statusColor;
        const badgeBg = isCompleted ? 'var(--neko-popup-success-bg, rgba(22, 163, 74, 0.1))' : isFailed ? 'var(--neko-popup-error-bg, rgba(220, 38, 38, 0.1))' : isRunning ? 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.12))' : 'var(--neko-popup-bg, rgba(0, 0, 0, 0.05))';
        badge.style.background = badgeBg;
    }

    // Update header marginBottom (running tasks have extra space for progress row)
    const headerDiv = card.firstElementChild;
    if (headerDiv) {
        const expectedMB = isRunning ? '6px' : '0';
        if (headerDiv.style.marginBottom !== expectedMB) headerDiv.style.marginBottom = expectedMB;
    }

    // Hide per-card cancel button for terminal tasks
    const cardCancelBtn = card.querySelector('.task-card-cancel');
    if (cardCancelBtn) {
        const cancelDisplay = isTerminal ? 'none' : 'flex';
        if (cardCancelBtn.style.display !== cancelDisplay) cardCancelBtn.style.display = cancelDisplay;
    }

    this._syncTaskDescriptionRow(card, task, isRunning);

    // Handle progress row: add if now running but missing, remove if no longer running
    const progressRow = card.querySelector('.task-progress-row');
    if (isRunning && !progressRow) {
        // Status just changed to running — rebuild the card cleanly
        const newCard = this._createTaskCard(task);
        const parent = card.parentNode;
        const oldDescRow = card.querySelector('.task-card-desc');
        if (oldDescRow && window.NekoTooltip && typeof window.NekoTooltip.destroyFor === 'function') {
            window.NekoTooltip.destroyFor(oldDescRow);
        }
        if (parent) parent.replaceChild(newCard, card);
        return newCard;
    } else if (!isRunning && progressRow) {
        // No longer running — remove progress row
        progressRow.remove();
    }

    // Update running timer inline so it stays current between setInterval ticks
    if (isRunning && task.start_time) {
        const timeEl = card.querySelector('[id^="task-time-"]');
        if (timeEl) {
            const startTime = new Date(task.start_time);
            const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
            const minutes = Math.floor(elapsed / 60);
            const seconds = elapsed % 60;
            timeEl.textContent = `\u23f1\ufe0f ${minutes}:${seconds.toString().padStart(2, '0')}`;
        }
    }

    // Update progress bar and step counter for running tasks
    if (isRunning && progressRow) {
        const fill = progressRow.querySelector('.task-progress-fill');
        if (fill) {
            const hasDeterminateProgress = typeof task.progress === 'number' && task.progress >= 0;
            if (hasDeterminateProgress) {
                const pct = Math.min(100, Math.max(0, Math.round(task.progress * 100)));
                const newWidth = pct + '%';
                if (fill.style.width !== newWidth) fill.style.width = newWidth;
                // Switch from indeterminate animation to determinate if needed
                if (fill.style.animation) {
                    fill.style.animation = '';
                    fill.style.transition = 'width 0.3s ease';
                }
            } else {
                // Revert to indeterminate state
                if (!fill.style.animation || fill.style.width !== '30%') {
                    fill.style.width = '30%';
                    fill.style.transition = '';
                    fill.style.animation = 'taskProgress 1.5s ease-in-out infinite';
                }
            }
        }
        const stepEl = progressRow.querySelector('.task-progress-step');
        if (typeof task.step === 'number' && typeof task.step_total === 'number' && task.step_total > 0) {
            const stepText = `${task.step}/${task.step_total}`;
            if (stepEl) {
                if (stepEl.textContent !== stepText) stepEl.textContent = stepText;
            } else {
                // Step counter appeared after card was created — append it
                const newStep = document.createElement('span');
                newStep.className = 'task-progress-step';
                newStep.textContent = stepText;
                Object.assign(newStep.style, {
                    color: 'var(--neko-popup-text-sub, #999)',
                    fontSize: '10px',
                    flexShrink: '0'
                });
                progressRow.appendChild(newStep);
            }
        } else if (stepEl) {
            // Step info no longer available — remove stale element
            stepEl.remove();
        }
    }
};

// 创建单个任务卡片
window.AgentHUD._createTaskCard = function (task) {
    const card = document.createElement('div');
    card.className = 'task-card';
    card.dataset.taskId = task.id;
    if (task.start_time) {
        card.dataset.startTime = task.start_time;
    }

    const isRunning = task.status === 'running';
    const isCompleted = task.status === 'completed';
    const isFailed = task.status === 'failed';
    const isCancelled = task.status === 'cancelled';
    const isTerminal = isCompleted || isFailed || isCancelled;

    let statusColor, statusText, cardBg, cardBorder;
    if (isCompleted) {
        statusColor = 'var(--neko-popup-success, #16a34a)';
        statusText = window.t ? window.t('agent.taskHud.statusCompleted') : '已完成';
        cardBg = 'var(--neko-popup-success-bg, rgba(22, 163, 74, 0.06))';
        cardBorder = 'var(--neko-popup-success-border, rgba(22, 163, 74, 0.2))';
    } else if (isFailed) {
        statusColor = 'var(--neko-popup-error, #dc2626)';
        statusText = window.t ? window.t('agent.taskHud.statusFailed') : '失败';
        cardBg = 'var(--neko-popup-error-bg, rgba(220, 38, 38, 0.06))';
        cardBorder = 'var(--neko-popup-error-border, rgba(220, 38, 38, 0.2))';
    } else if (isCancelled) {
        statusColor = 'var(--neko-popup-text-sub, #666)';
        statusText = window.t ? window.t('agent.taskHud.statusCancelled') : '已取消';
        cardBg = 'var(--neko-popup-bg, rgba(249, 249, 249, 0.6))';
        cardBorder = 'var(--neko-popup-border-color, rgba(0, 0, 0, 0.06))';
    } else if (isRunning) {
        statusColor = 'var(--neko-popup-accent, #2a7bc4)';
        statusText = window.t ? window.t('agent.taskHud.statusRunning') : '运行中';
        cardBg = 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.08))';
        cardBorder = 'var(--neko-popup-accent-border, rgba(42, 123, 196, 0.25))';
    } else {
        statusColor = 'var(--neko-popup-text-sub, #666)';
        statusText = window.t ? window.t('agent.taskHud.statusQueued') : '队列中';
        cardBg = 'var(--neko-popup-bg, rgba(249, 249, 249, 0.6))';
        cardBorder = 'var(--neko-popup-border-color, rgba(0, 0, 0, 0.06))';
    }

    Object.assign(card.style, {
        background: cardBg,
        borderRadius: '8px',
        padding: '10px 12px',
        border: `1px solid ${cardBorder}`,
        transition: 'all 0.2s ease',
        opacity: isTerminal ? '0.6' : '1'
    });

    // === 第一行：图标 + 名称 + 状态徽章 + 取消按钮 ===
    const header = document.createElement('div');
    Object.assign(header.style, {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: isRunning ? '6px' : '0'
    });

    // 任务类型图标和名称
    const rawTypeName = task.type || task.source || 'unknown';
    const params = task.params || {};

    // 根据类型确定图标
    let typeIcon;
    if (rawTypeName === 'user_plugin' || rawTypeName === 'plugin_direct') {
        typeIcon = '🧩';
    } else if (rawTypeName === 'computer_use') {
        typeIcon = '🖱️';
    } else if (rawTypeName === 'browser_use') {
        typeIcon = '🌐';
    } else if (rawTypeName === 'mcp') {
        typeIcon = '🔌';
    } else {
        typeIcon = '⚙️';
    }

    // 根据类型确定名称
    let typeName = rawTypeName;
    if (rawTypeName === 'user_plugin' || rawTypeName === 'plugin_direct') {
        // 优先级：plugin_name > plugin_id > 翻译文本
        typeName = params.plugin_name || params.plugin_id || (window.t ? window.t('agent.taskHud.typeUserPlugin') : '用户插件');
    } else if (rawTypeName === 'computer_use') {
        typeName = window.t ? window.t('agent.taskHud.typeComputerUse') : '电脑控制';
    } else if (rawTypeName === 'browser_use') {
        typeName = window.t ? window.t('agent.taskHud.typeBrowserUse') : '浏览器控制';
    } else if (rawTypeName === 'mcp') {
        typeName = window.t ? window.t('agent.taskHud.typeMCP') : 'MCP工具';
    } else if (rawTypeName === 'openfang') {
        typeName = window.t ? window.t('agent.taskHud.typeOpenFang') : '专属桌面';
    }

    const typeLabel = document.createElement('span');
    typeLabel.style.whiteSpace = 'nowrap';
    typeLabel.style.overflow = 'hidden';
    typeLabel.style.textOverflow = 'ellipsis';
    typeLabel.style.minWidth = '0';
    typeLabel.title = typeName;

    // 使用 textContent 防止 XSS（避免 plugin_name 中的 HTML 被解析）
    const iconSpan = document.createElement('span');
    iconSpan.textContent = typeIcon + ' ';
    const nameSpan = document.createElement('span');
    nameSpan.textContent = typeName;
    Object.assign(nameSpan.style, {
        color: 'var(--neko-popup-text-sub, #666)',
        fontSize: '12px',
        fontWeight: '500'
    });
    typeLabel.appendChild(iconSpan);
    typeLabel.appendChild(nameSpan);

    const statusBadge = document.createElement('span');
    statusBadge.className = 'task-status-badge';
    statusBadge.textContent = statusText;
    Object.assign(statusBadge.style, {
        color: statusColor,
        fontSize: '11px',
        fontWeight: '500',
        padding: '1px 8px',
        background: isCompleted ? 'var(--neko-popup-success-bg, rgba(22, 163, 74, 0.1))' : isFailed ? 'var(--neko-popup-error-bg, rgba(220, 38, 38, 0.1))' : isRunning ? 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.12))' : 'var(--neko-popup-bg, rgba(0, 0, 0, 0.05))',
        borderRadius: '10px',
        flexShrink: '0'
    });

    const headerLeft = document.createElement('div');
    Object.assign(headerLeft.style, { display: 'flex', alignItems: 'center', gap: '6px', minWidth: '0', flex: '1', overflow: 'hidden' });
    headerLeft.appendChild(typeLabel);
    headerLeft.appendChild(statusBadge);

    const taskCancelBtn = document.createElement('div');
    taskCancelBtn.className = 'task-card-cancel';
    taskCancelBtn.innerHTML = '✕';
    Object.assign(taskCancelBtn.style, {
        width: '18px',
        height: '18px',
        borderRadius: '4px',
        background: 'var(--neko-hud-subtle-bg, rgba(0, 0, 0, 0.06))',
        display: isTerminal ? 'none' : 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '10px',
        color: 'var(--neko-popup-text-sub, #999)',
        cursor: 'pointer',
        transition: 'all 0.15s ease',
        flexShrink: '0',
        marginLeft: '6px'
    });
    taskCancelBtn.title = window.t ? window.t('agent.taskHud.cancelTask') : '终止任务';
    taskCancelBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        taskCancelBtn.style.opacity = '0.4';
        taskCancelBtn.style.pointerEvents = 'none';
        try {
            const r = await fetch(`/api/agent/tasks/${encodeURIComponent(task.id)}/cancel`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            // 2xx → the backend handled the cancel (marking locally is an
            // idempotent fallback for when its event can't arrive);
            // 404 → the backend no longer tracks this task, the card is an
            // orphan (e.g. left behind by an earlier end_all) — clear it;
            // 504 → proxy timed out AFTER forwarding, the cancel did land.
            // Anything else (502 connect error, 501 remote block) means the
            // request never reached the tool server — keep the card and
            // re-enable the button for retry.
            if (r.ok || r.status === 404 || r.status === 504) {
                // "task is not active" (200 + success:false) means the task
                // hit a different terminal state right before the click —
                // mirror that status instead of faking "cancelled".
                let realStatus = null;
                if (r.ok) {
                    try {
                        const body = await r.json();
                        if (body && body.success === false
                            && ['completed', 'failed', 'cancelled'].indexOf(body.status) !== -1) {
                            realStatus = body.status;
                        }
                    } catch (_) { /* empty or non-JSON body */ }
                }
                if (window.AgentHUD._markTasksCancelledLocally) {
                    window.AgentHUD._markTasksCancelledLocally([task.id], realStatus || undefined);
                }
            } else {
                taskCancelBtn.style.opacity = '1';
                taskCancelBtn.style.pointerEvents = 'auto';
            }
        } catch (err) {
            console.error('[AgentHUD] Cancel task failed:', err);
            // Network-level failure: the request never reached the server.
            // Re-enable the button so the user can retry.
            taskCancelBtn.style.opacity = '1';
            taskCancelBtn.style.pointerEvents = 'auto';
        }
    });

    header.appendChild(headerLeft);
    header.appendChild(taskCancelBtn);
    card.appendChild(header);

    this._syncTaskDescriptionRow(card, task, isRunning);

    // === 第二行：倒计时 + 进度条（仅运行中任务） ===
    if (isRunning) {
        const secondRow = document.createElement('div');
        secondRow.className = 'task-progress-row';
        Object.assign(secondRow.style, {
            display: 'flex',
            alignItems: 'center',
            gap: '8px'
        });

        // 倒计时
        if (task.start_time) {
            const timeSpan = document.createElement('span');
            const startTime = new Date(task.start_time);
            const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
            const minutes = Math.floor(elapsed / 60);
            const seconds = elapsed % 60;

            timeSpan.id = `task-time-${task.id}`;
            timeSpan.textContent = `⏱️ ${minutes}:${seconds.toString().padStart(2, '0')}`;
            Object.assign(timeSpan.style, {
                color: 'var(--neko-popup-text-sub, #888)',
                fontSize: '11px',
                flexShrink: '0',
                whiteSpace: 'nowrap'
            });
            secondRow.appendChild(timeSpan);
        }

        // 进度条
        const hasDeterminateProgress = typeof task.progress === 'number' && task.progress >= 0;
        const progressBar = document.createElement('div');
        Object.assign(progressBar.style, {
            flex: '1',
            height: '3px',
            background: 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.15))',
            borderRadius: '2px',
            overflow: 'hidden'
        });

        const progressFill = document.createElement('div');
        progressFill.className = 'task-progress-fill';
        if (hasDeterminateProgress) {
            const pct = Math.min(100, Math.max(0, Math.round(task.progress * 100)));
            Object.assign(progressFill.style, {
                height: '100%',
                width: pct + '%',
                background: 'linear-gradient(90deg, var(--neko-popup-accent, #2a7bc4), #66b5ff)',
                borderRadius: '2px',
                transition: 'width 0.3s ease'
            });
        } else {
            Object.assign(progressFill.style, {
                height: '100%',
                width: '30%',
                background: 'linear-gradient(90deg, var(--neko-popup-accent, #2a7bc4), #66b5ff)',
                borderRadius: '2px',
                animation: 'taskProgress 1.5s ease-in-out infinite'
            });
        }
        progressBar.appendChild(progressFill);
        secondRow.appendChild(progressBar);

        // Step counter (e.g. "2/3") — 紧凑显示在进度条右侧
        if (typeof task.step === 'number' && typeof task.step_total === 'number' && task.step_total > 0) {
            const stepSpan = document.createElement('span');
            stepSpan.className = 'task-progress-step';
            stepSpan.textContent = `${task.step}/${task.step_total}`;
            Object.assign(stepSpan.style, {
                color: 'var(--neko-popup-text-sub, #999)',
                fontSize: '10px',
                flexShrink: '0'
            });
            secondRow.appendChild(stepSpan);
        }

        card.appendChild(secondRow);
    }

    return card;
};

// 设置HUD全局拖拽功能
window.AgentHUD._setupDragging = function (hud) {
    if (isStandaloneAgentHudPage()) {
        hud.addEventListener('dragstart', (e) => e.preventDefault());
        this._cleanupDragging = () => {};
        return;
    }

    let isDragging = false;
    let dragOffsetX = 0;
    let dragOffsetY = 0;

    const resetDragVisualState = () => {
        hud.style.cursor = 'move';
        hud.style.boxShadow = 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))';
        hud.style.opacity = '1';
        hud.style.transition = 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease, width 0.3s ease, padding 0.3s ease, max-height 0.3s ease';
    };

    const cancelDragState = () => {
        if (!isDragging && !touchDragging) return;
        isDragging = false;
        touchDragging = false;
        setAgentHudDraggingState(false);
        resetDragVisualState();
    };

    // 高性能拖拽函数
    const performDrag = (clientX, clientY) => {
        if (!isDragging) return;

        // 使用requestAnimationFrame确保流畅动画
        requestAnimationFrame(() => {
            // 计算新位置
            const newX = clientX - dragOffsetX;
            const newY = clientY - dragOffsetY;

            // 获取HUD尺寸和窗口尺寸
            const hudRect = hud.getBoundingClientRect();
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;

            // 边界检查 - 确保HUD不会超出窗口
            const constrainedX = Math.max(0, Math.min(newX, windowWidth - hudRect.width));
            const constrainedY = Math.max(0, Math.min(newY, windowHeight - hudRect.height));

            // 使用transform进行高性能定位
            hud.style.left = constrainedX + 'px';
            hud.style.top = constrainedY + 'px';
            hud.style.right = 'auto';
            hud.style.transform = 'none';
        });
    };

    // 鼠标按下事件 - 全局可拖动
    const handleMouseDown = (e) => {
        // 排除内部可交互元素
        const interactiveSelectors = ['button', 'input', 'textarea', 'select', 'a', '.task-card', '#agent-task-hud-minimize', '#agent-task-hud-cancel', '.task-card-cancel', '.collapse-button'];
        const isInteractive = e.target.closest(interactiveSelectors.join(','));

        if (isInteractive) return;

        // Body-level tooltips are outside the HUD tree, so close them before moving the HUD.
        if (window.NekoTooltip && typeof window.NekoTooltip.hide === 'function') {
            window.NekoTooltip.hide();
        }

        isDragging = true;
        setAgentHudDraggingState(true);

        // 视觉反馈
        hud.style.cursor = 'grabbing';
        hud.style.boxShadow = '0 12px 48px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.2)';
        hud.style.opacity = '0.95';
        hud.style.transition = 'none'; // 拖拽时禁用过渡动画

        const rect = hud.getBoundingClientRect();
        // 计算鼠标相对于HUD的偏移
        dragOffsetX = e.clientX - rect.left;
        dragOffsetY = e.clientY - rect.top;

        e.preventDefault();
        e.stopPropagation();
    };

    // 鼠标移动事件 - 高性能处理
    const handleMouseMove = (e) => {
        if (!isDragging) return;

        // 使用节流优化性能
        performDrag(e.clientX, e.clientY);

        e.preventDefault();
        e.stopPropagation();
    };

    // 鼠标释放事件
    const handleMouseUp = (e) => {
        if (!isDragging) return;

        isDragging = false;
        setAgentHudDraggingState(false);

        // 恢复视觉状态
        resetDragVisualState();

        // DOM HUD 使用 viewport 坐标；不要混用 Electron screen 坐标。
        requestAnimationFrame(() => {
            clampAgentHudElementToViewport(hud, { persist: true });
        });

        e.preventDefault();
        e.stopPropagation();
    };

    // 绑定事件监听器 - 全局拖拽
    hud.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    // 防止在拖拽时选中文本
    hud.addEventListener('dragstart', (e) => e.preventDefault());

    // 触摸事件支持（移动设备）- 全局拖拽
    let touchDragging = false;

    // 触摸开始
    const handleTouchStart = (e) => {
        // 排除内部可交互元素
        const interactiveSelectors = ['button', 'input', 'textarea', 'select', 'a', '.task-card', '#agent-task-hud-minimize', '#agent-task-hud-cancel', '.task-card-cancel', '.collapse-button'];
        const isInteractive = e.target.closest(interactiveSelectors.join(','));

        if (isInteractive) return;

        // Body-level tooltips are outside the HUD tree, so close them before moving the HUD.
        if (window.NekoTooltip && typeof window.NekoTooltip.hide === 'function') {
            window.NekoTooltip.hide();
        }

        touchDragging = true;
        isDragging = true;  // 让performDrag函数能正常工作
        setAgentHudDraggingState(true);

        // 视觉反馈
        hud.style.boxShadow = '0 12px 48px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.2)';
        hud.style.opacity = '0.95';
        hud.style.transition = 'none';

        const touch = e.touches[0];
        const rect = hud.getBoundingClientRect();
        // 使用与鼠标事件相同的偏移量变量喵
        dragOffsetX = touch.clientX - rect.left;
        dragOffsetY = touch.clientY - rect.top;

        e.preventDefault();
    };

    // 触摸移动
    const handleTouchMove = (e) => {
        if (!touchDragging) return;

        const touch = e.touches[0];
        performDrag(touch.clientX, touch.clientY);

        e.preventDefault();
    };

    // 触摸结束
    const handleTouchEnd = (e) => {
        if (!touchDragging) return;

        touchDragging = false;
        isDragging = false;  // 确保performDrag函数停止工作
        setAgentHudDraggingState(false);

        // 恢复视觉状态
        resetDragVisualState();

        // DOM HUD 使用 viewport 坐标；不要混用 Electron screen 坐标。
        requestAnimationFrame(() => {
            clampAgentHudElementToViewport(hud, { persist: true });
        });

        e.preventDefault();
    };

    // 绑定触摸事件
    hud.addEventListener('touchstart', handleTouchStart, { passive: false });
    document.addEventListener('touchmove', handleTouchMove, { passive: false });
    document.addEventListener('touchend', handleTouchEnd, { passive: false });
    document.addEventListener('touchcancel', cancelDragState, { passive: true });
    window.addEventListener('blur', cancelDragState);
    window.addEventListener('pointercancel', cancelDragState, true);

    // 窗口大小变化时重新校准位置（多屏幕支持）
    const handleResize = () => {
        if (isDragging || touchDragging) return;

        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();
            const viewport = getAgentHudViewportBounds();

            if (rect.left < viewport.left || rect.top < viewport.top ||
                rect.right > viewport.left + viewport.width || rect.bottom > viewport.top + viewport.height) {
                clampAgentHudElementToViewport(hud, { persist: true });
            }
        });
    };

    window.addEventListener('resize', handleResize);

    // 清理函数
    this._cleanupDragging = () => {
        setAgentHudDraggingState(false);
        hud.removeEventListener('mousedown', handleMouseDown);
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
        hud.removeEventListener('touchstart', handleTouchStart);
        document.removeEventListener('touchmove', handleTouchMove);
        document.removeEventListener('touchend', handleTouchEnd);
        document.removeEventListener('touchcancel', cancelDragState);
        window.removeEventListener('blur', cancelDragState);
        window.removeEventListener('pointercancel', cancelDragState, true);
        window.removeEventListener('resize', handleResize);
    };
};

// 添加任务进度动画样式
(function () {
    if (document.getElementById('agent-task-hud-styles')) return;

    const style = document.createElement('style');
    style.id = 'agent-task-hud-styles';
    style.textContent = `
        @keyframes taskProgress {
            0% { transform: translateX(-100%); }
            50% { transform: translateX(200%); }
            100% { transform: translateX(-100%); }
        }
        
        #agent-task-hud::-webkit-scrollbar {
            width: 4px;
        }
        
        #agent-task-hud::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.03);
            border-radius: 2px;
        }
        
        #agent-task-hud::-webkit-scrollbar-thumb {
            background: rgba(0, 0, 0, 0.12);
            border-radius: 2px;
        }
        
        #agent-task-list::-webkit-scrollbar {
            width: 4px;
        }
        
        #agent-task-list::-webkit-scrollbar-track {
            background: transparent;
        }
        
        #agent-task-list::-webkit-scrollbar-thumb {
            background: rgba(0, 0, 0, 0.1);
            border-radius: 2px;
        }
        
        .task-card:hover {
            background: rgba(68, 183, 254, 0.12) !important;
            transform: translateX(-2px);
        }
        
        .task-card-cancel:hover {
            background: rgba(220, 53, 69, 0.15) !important;
            color: #dc3545 !important;
            transform: scale(1.15);
        }
        
        .task-card-cancel:active {
            transform: scale(0.9);
        }
        
        #agent-task-hud-minimize:hover {
            background: rgba(68, 183, 254, 0.25);
            transform: scale(1.1);
        }
        
        #agent-task-hud-minimize:active {
            transform: scale(0.95);
        }
        
        #agent-task-hud-cancel:hover {
            background: rgba(220, 53, 69, 0.25);
            transform: scale(1.1);
        }
        
        #agent-task-hud-cancel:active {
            transform: scale(0.95);
        }
        
        /* 折叠功能样式 */
        #agent-task-empty {
            position: relative;
            transition: all 0.3s ease;
            overflow: hidden;
        }
        
        #agent-task-empty > div:first-child {
            transition: all 0.3s ease;
            opacity: 1;
            height: auto;
            padding: 20px;
            margin: 0;
        }
        
        #agent-task-empty.collapsed > div:first-child {
            opacity: 0;
            height: 0;
            padding: 0;
            margin: 0;
        }
        
        .collapse-button {
            position: absolute;
            top: 8px;
            right: 8px;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            background: rgba(68, 183, 254, 0.12);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            color: #999;
            cursor: pointer;
            transition: all 0.2s ease;
            z-index: 1;
            user-select: none;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
        }
        
        .collapse-button:hover {
            background: rgba(68, 183, 254, 0.25);
            transform: scale(1.1);
        }
        
        .collapse-button:active {
            transform: scale(0.95);
        }
        
        .collapse-button.collapsed {
            background: rgba(68, 183, 254, 0.18);
            color: #888;
        }
        
        /* 移动设备优化 */
        @media (max-width: 768px) {
            .collapse-button {
                width: 24px;
                height: 24px;
                font-size: 12px;
                top: 6px;
                right: 6px;
            }
            
            .collapse-button:hover {
                transform: scale(1.05);
            }
        }
    `;
    document.head.appendChild(style);
})();

// ===== i18n 就绪 / 语言切换后重译任务 HUD 外壳 =====
// HUD 外壳文案（标题 / 统计 / 折叠·终止按钮 title / 空态文本）由 createAgentTaskHUD
// 用 window.t 直接写入且没有 data-i18n 属性，因此 i18n 的 updatePageTexts() 不会重译它们。
// 当 HUD 在 i18n 初始化完成前就抢先构建时（典型场景：启动时后端恢复了上次 NekoClaw 会话的
// 任务，agenthud 页面拉到 active_tasks 后立即渲染 HUD），这些外壳文案会停留在初始兜底态，
// 表现为「i18n 文字加载异常」。这里监听 localechange（i18n 初始化完成及语言切换时派发），
// 补一次外壳重译，并用缓存快照刷新任务卡片状态/类型文案。
window.AgentHUD.refreshHudI18n = function () {
    if (typeof window.t !== 'function') return;
    // 仅当 i18n 真正解析出翻译（而非降级返回 key）时才重译，避免把兜底文案覆盖成 key。
    const titleText = window.t('agent.taskHud.title');
    if (!titleText || titleText === 'agent.taskHud.title') return;

    const title = document.getElementById('agent-task-hud-title');
    if (title) {
        // 用 DOM 节点重建（图标 span + 文本节点），避免 innerHTML 拼接触发静态扫描告警；
        // titleText 是受信任的 i18n 文案，此处仅为规避 innerHTML 用法。
        title.textContent = '';
        const icon = document.createElement('span');
        icon.textContent = '⚡';
        icon.style.color = 'var(--neko-popup-accent, #2a7bc4)';
        icon.style.marginRight = '8px';
        title.appendChild(icon);
        title.appendChild(document.createTextNode(titleText));
    }
    const stats = document.getElementById('agent-task-hud-stats');
    if (stats) {
        const labelled = stats.querySelectorAll('span[title]');
        if (labelled[0]) labelled[0].title = window.t('agent.taskHud.running');
        if (labelled[1]) labelled[1].title = window.t('agent.taskHud.queued');
    }
    const minimizeBtn = document.getElementById('agent-task-hud-minimize');
    if (minimizeBtn) minimizeBtn.title = window.t('agent.taskHud.minimize');
    const cancelBtn = document.getElementById('agent-task-hud-cancel');
    if (cancelBtn) cancelBtn.title = window.t('agent.taskHud.cancelAll');
    const emptyState = document.getElementById('agent-task-empty');
    if (emptyState && emptyState.firstElementChild) {
        emptyState.firstElementChild.textContent = window.t('agent.taskHud.noTasks');
    }

    // 用缓存的任务快照重渲染卡片，刷新状态 / 类型 / 终止按钮等文案。
    // 仅在 HUD 已存在且当前可见时执行：updateAgentTaskHUD 在 HUD 缺失时会创建、
    // 在有活动任务且隐藏时会自动显示，纯 i18n 重译不应触发这些副作用。
    const hud = document.getElementById('agent-task-hud');
    const hudVisible = !!(hud && hud.style.display !== 'none' && hud.style.opacity !== '0');
    if (hudVisible && this._latestTasksData && typeof this.updateAgentTaskHUD === 'function') {
        // 卡片走差分更新：_updateTaskCard 仅在状态变化时改状态徽标，且从不更新类型标签 /
        // 卡片终止按钮 title。语言切换而任务状态不变时（例如运行中的 computer_use 任务）
        // 卡片文案会停留旧语言。差分门控是每次 WS 更新的热路径，不宜改动；因此在这条低频的
        // 重译路径里显式清空已有卡片，再走 updateAgentTaskHUD（保留其 RAF 节流，与常规渲染
        // 管线一致）用当前语言重建，完整覆盖状态 / 类型 / 终止按钮等本地化字段。
        const taskList = document.getElementById('agent-task-list');
        if (taskList) {
            taskList.querySelectorAll('.task-card').forEach((card) => {
                const descRow = card.querySelector('.task-card-desc');
                if (descRow && window.NekoTooltip && typeof window.NekoTooltip.destroyFor === 'function') {
                    try { window.NekoTooltip.destroyFor(descRow); } catch (_) { /* ignore */ }
                }
                card.remove();
            });
        }
        try { this.updateAgentTaskHUD(this._latestTasksData); } catch (_) { /* ignore */ }
    }
};

window.addEventListener('localechange', function () {
    try {
        if (window.AgentHUD && typeof window.AgentHUD.refreshHudI18n === 'function') {
            window.AgentHUD.refreshHudI18n();
        }
    } catch (_) { /* ignore */ }
});
