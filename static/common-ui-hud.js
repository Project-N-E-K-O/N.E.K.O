/**
 * Live2D UI HUD - Agent任务HUD组件
 * 包含任务面板、任务卡片、HUD拖拽功能
 */

window.AgentHUD = window.AgentHUD || {};

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

// 缓存当前显示器边界信息（多屏幕支持）
let cachedDisplayHUD = {
    x: 0,
    y: 0,
    width: window.innerWidth,
    height: window.innerHeight
};

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
    // 添加状态显示栏 - Fluent Design
    const statusDiv = document.createElement('div');
    statusDiv.id = 'live2d-agent-status';
    Object.assign(statusDiv.style, {
        fontSize: '12px',
        color: 'var(--neko-popup-accent, #2a7bc4)',
        padding: '6px 8px',
        borderRadius: '4px',
        background: 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.05))',
        marginBottom: '8px',
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
            id: 'agent-user-plugin',
            label: window.t ? window.t('settings.toggles.userPlugin') : '用户插件',
            labelKey: 'settings.toggles.userPlugin',
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        },
        {
            id: 'agent-openfang',
            label: window.t ? window.t('settings.toggles.openfang') : '虚拟机',
            labelKey: 'settings.toggles.openfang',
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

        // 用户插件侧边面板
        if (toggle.id === 'agent-user-plugin' && typeof this._createSidePanelContainer === 'function') {
            const sidePanel = this._createSidePanelContainer();
            sidePanel.style.flexDirection = 'column';
            sidePanel.style.alignItems = 'stretch';
            sidePanel.style.gap = '4px';
            sidePanel.style.padding = '6px 10px';
            sidePanel._anchorElement = toggleItem;
            sidePanel._popupElement = popup;

            const configBtn = document.createElement('div');
            const LABEL_KEY = 'settings.toggles.pluginManagementPanel';
            const LABEL_FALLBACK = '管理面板';
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
            configIcon.textContent = '⚙';
            configIcon.style.fontSize = '13px';
            const configLabel = document.createElement('span');
            configLabel.textContent = window.t ? window.t(LABEL_KEY) : LABEL_FALLBACK;
            configLabel.setAttribute('data-i18n', LABEL_KEY);
            configLabel.style.userSelect = 'none';
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
                const dashboardUrl = '/api/agent/user_plugin/dashboard';
                const width = Math.min(1280, Math.round(screen.width * 0.8));
                const height = Math.min(900, Math.round(screen.height * 0.8));
                const left = Math.max(0, Math.floor((screen.width - width) / 2));
                const top = Math.max(0, Math.floor((screen.height - height) / 2));
                const features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;
                if (typeof window.openOrFocusWindow === 'function') {
                    window.openOrFocusWindow(dashboardUrl, 'neko_plugin_dashboard', features);
                } else {
                    window.open(dashboardUrl, 'neko_plugin_dashboard', features);
                }
                setTimeout(() => { isOpening = false; }, 500);
            });

            sidePanel.appendChild(configBtn);
            document.body.appendChild(sidePanel);
            this._attachSidePanelHover(toggleItem, sidePanel);
        }

    });
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

    // 初始化显示器边界缓存
    updateDisplayBounds();

    const hud = document.createElement('div');
    hud.id = 'agent-task-hud';

    // 获取保存的位置或使用默认位置
    const savedPos = localStorage.getItem('agent-task-hud-position');
    let position = { top: '50%', right: '20px', transform: 'translateY(-50%)' };

    if (savedPos) {
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
        width: '320px',
        maxHeight: '60vh',
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
        cursor: 'move',
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
            await fetch('/api/agent/admin/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'end_all' })
            });
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
    const hudCollapsedKey = 'agent-task-hud-collapsed-v2';
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
            hud.style.width = '320px';
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
    try { hudCollapsed = localStorage.getItem(hudCollapsedKey) === 'true'; } catch (_) { }
    applyHudCollapsed(hudCollapsed);

    minimizeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        hudCollapsed = !hudCollapsed;
        applyHudCollapsed(hudCollapsed);
        try { localStorage.setItem(hudCollapsedKey, String(hudCollapsed)); } catch (_) { }
    });

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
    const savedPos = localStorage.getItem('agent-task-hud-position');
    if (savedPos) {
        try {
            const parsed = JSON.parse(savedPos);
            if (parsed.top) hud.style.top = parsed.top;
            if (parsed.left) hud.style.left = parsed.left;
            if (parsed.right) hud.style.right = parsed.right;
            if (parsed.transform) hud.style.transform = parsed.transform;
        } catch (e) {
            hud.style.transform = 'translateY(-50%) translateX(0)';
        }
    } else {
        hud.style.transform = 'translateY(-50%) translateX(0)';
    }
};

// 隐藏任务 HUD
window.AgentHUD.hideAgentTaskHUD = function () {
    console.log('[AgentHUD] hideAgentTaskHUD called');
    let hud = document.getElementById('agent-task-hud');
    if (!hud) {
        console.log('[AgentHUD] HUD element not found, creating it first to hide it properly');
        hud = this.createAgentTaskHUD();
    }
    
    console.log('[AgentHUD] HUD element found, starting fade out');
    hud.style.opacity = '0';
    const savedPos = localStorage.getItem('agent-task-hud-position');
    if (!savedPos) {
        hud.style.transform = 'translateY(-50%) translateX(20px)';
    }

    // 如果之前有正在等待的隐藏定时器，先清理掉
    if (this._hideTimeout) {
        console.log('[AgentHUD][TimeoutTrace] hideAgentTaskHUD clearing previous timeout ID:', this._hideTimeout);
        clearTimeout(this._hideTimeout);
    }

    this._hideTimeout = setTimeout(() => {
        console.log('[AgentHUD][TimeoutTrace] HUD element display set to none. Timeout ID was:', this._hideTimeout);
        hud.style.display = 'none';
        this._hideTimeout = null;
    }, 300);
    console.log('[AgentHUD][TimeoutTrace] hideAgentTaskHUD set new timeout ID:', this._hideTimeout);
};

// 更新任务 HUD 内容
window.AgentHUD.updateAgentTaskHUD = function (tasksData) {
    // Cache latest snapshot so deferred re-render won't use stale closure data.
    this._latestTasksData = tasksData;

    // RAF throttle: coalesce rapid-fire WebSocket updates into a single frame
    if (this._updateRafId) return;
    this._updateRafId = requestAnimationFrame(() => {
        this._updateRafId = null;
        this._doUpdateAgentTaskHUD();
    });
};

// Internal: actual HUD update logic (called via RAF throttle)
window.AgentHUD._doUpdateAgentTaskHUD = function () {
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

    // Handle progress row: add if now running but missing, remove if no longer running
    const progressRow = card.querySelector('.task-progress-row');
    if (isRunning && !progressRow) {
        // Status just changed to running — rebuild the card cleanly
        const newCard = this._createTaskCard(task);
        const parent = card.parentNode;
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
    }

    const typeLabel = document.createElement('span');
    typeLabel.style.whiteSpace = 'nowrap';
    typeLabel.style.overflow = 'hidden';
    typeLabel.style.textOverflow = 'ellipsis';
    typeLabel.style.minWidth = '0';

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
    taskCancelBtn.title = window.t ? window.t('agent.taskHud.cancelAll') : '终止任务';
    taskCancelBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        taskCancelBtn.style.opacity = '0.4';
        taskCancelBtn.style.pointerEvents = 'none';
        try {
            await fetch(`/api/agent/tasks/${encodeURIComponent(task.id)}/cancel`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
        } catch (err) {
            console.error('[AgentHUD] Cancel task failed:', err);
        }
    });

    header.appendChild(headerLeft);
    header.appendChild(taskCancelBtn);
    card.appendChild(header);

    // === 描述行：显示任务具体内容（如"15分钟后 起来活动"） ===
    const rawDesc = params.description || params.instruction || '';
    const descText = rawDesc ? window.AgentHUD._shortenDesc(rawDesc) : '';
    if (descText) {
        const descRow = document.createElement('div');
        descRow.textContent = descText;
        if (rawDesc !== descText) descRow.title = rawDesc; // hover 显示完整内容
        Object.assign(descRow.style, {
            color: 'var(--neko-popup-text-sub, #888)',
            fontSize: '11px',
            lineHeight: '1.3',
            marginTop: '3px',
            marginBottom: isRunning ? '3px' : '0',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
        });
        card.appendChild(descRow);
    }

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
    let isDragging = false;
    let dragOffsetX = 0;
    let dragOffsetY = 0;

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

        isDragging = true;

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

        // 恢复视觉状态
        hud.style.cursor = 'move';
        hud.style.boxShadow = 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))';
        hud.style.opacity = '1';
        hud.style.transition = 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease, width 0.3s ease, padding 0.3s ease, max-height 0.3s ease';

        // 最终位置校准（多屏幕支持）
        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();

            // 使用缓存的屏幕边界进行限制
            if (!cachedDisplayHUD) {
                console.warn('cachedDisplayHUD not initialized, skipping bounds check');
                return;
            }
            const displayLeft = cachedDisplayHUD.x;
            const displayTop = cachedDisplayHUD.y;
            const displayRight = cachedDisplayHUD.x + cachedDisplayHUD.width;
            const displayBottom = cachedDisplayHUD.y + cachedDisplayHUD.height;

            // 确保位置在当前屏幕内
            let finalLeft = parseFloat(hud.style.left) || 0;
            let finalTop = parseFloat(hud.style.top) || 0;

            finalLeft = Math.max(displayLeft, Math.min(finalLeft, displayRight - rect.width));
            finalTop = Math.max(displayTop, Math.min(finalTop, displayBottom - rect.height));

            hud.style.left = finalLeft + 'px';
            hud.style.top = finalTop + 'px';

            // 保存位置到localStorage
            const position = {
                left: hud.style.left,
                top: hud.style.top,
                right: hud.style.right,
                transform: hud.style.transform
            };

            try {
                localStorage.setItem('agent-task-hud-position', JSON.stringify(position));
            } catch (error) {
                console.warn('Failed to save position to localStorage:', error);
            }
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

        touchDragging = true;
        isDragging = true;  // 让performDrag函数能正常工作

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

        // 恢复视觉状态
        hud.style.boxShadow = 'var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04))';
        hud.style.opacity = '1';
        hud.style.transition = 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease, width 0.3s ease, padding 0.3s ease, max-height 0.3s ease';

        // 最终位置校准（多屏幕支持）
        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();

            // 使用缓存的屏幕边界进行限制
            if (!cachedDisplayHUD) {
                console.warn('cachedDisplayHUD not initialized, skipping bounds check');
                return;
            }
            const displayLeft = cachedDisplayHUD.x;
            const displayTop = cachedDisplayHUD.y;
            const displayRight = cachedDisplayHUD.x + cachedDisplayHUD.width;
            const displayBottom = cachedDisplayHUD.y + cachedDisplayHUD.height;

            // 确保位置在当前屏幕内
            let finalLeft = parseFloat(hud.style.left) || 0;
            let finalTop = parseFloat(hud.style.top) || 0;

            finalLeft = Math.max(displayLeft, Math.min(finalLeft, displayRight - rect.width));
            finalTop = Math.max(displayTop, Math.min(finalTop, displayBottom - rect.height));

            hud.style.left = finalLeft + 'px';
            hud.style.top = finalTop + 'px';

            // 保存位置到localStorage
            const position = {
                left: hud.style.left,
                top: hud.style.top,
                right: hud.style.right,
                transform: hud.style.transform
            };

            try {
                localStorage.setItem('agent-task-hud-position', JSON.stringify(position));
            } catch (error) {
                console.warn('Failed to save position to localStorage:', error);
            }
        });

        e.preventDefault();
    };

    // 绑定触摸事件
    hud.addEventListener('touchstart', handleTouchStart, { passive: false });
    document.addEventListener('touchmove', handleTouchMove, { passive: false });
    document.addEventListener('touchend', handleTouchEnd, { passive: false });

    // 窗口大小变化时重新校准位置（多屏幕支持）
    const handleResize = async () => {
        if (isDragging || touchDragging) return;

        // 更新屏幕信息
        const rect = hud.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;
        await updateDisplayBounds(centerX, centerY);

        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();

            // 使用缓存的屏幕边界进行限制
            if (!cachedDisplayHUD) {
                console.warn('cachedDisplayHUD not initialized, skipping bounds check');
                return;
            }
            const displayLeft = cachedDisplayHUD.x;
            const displayTop = cachedDisplayHUD.y;
            const displayRight = cachedDisplayHUD.x + cachedDisplayHUD.width;
            const displayBottom = cachedDisplayHUD.y + cachedDisplayHUD.height;

            // 如果HUD超出当前屏幕，调整到可见位置
            if (rect.left < displayLeft || rect.top < displayTop ||
                rect.right > displayRight || rect.bottom > displayBottom) {

                let newLeft = parseFloat(hud.style.left) || 0;
                let newTop = parseFloat(hud.style.top) || 0;

                newLeft = Math.max(displayLeft, Math.min(newLeft, displayRight - rect.width));
                newTop = Math.max(displayTop, Math.min(newTop, displayBottom - rect.height));

                hud.style.left = newLeft + 'px';
                hud.style.top = newTop + 'px';

                // 更新保存的位置
                const position = {
                    left: hud.style.left,
                    top: hud.style.top,
                    right: hud.style.right,
                    transform: hud.style.transform
                };

                try {
                    localStorage.setItem('agent-task-hud-position', JSON.stringify(position));
                } catch (error) {
                    console.warn('Failed to save position to localStorage:', error);
                }
            }
        });
    };

    window.addEventListener('resize', handleResize);

    // 清理函数
    this._cleanupDragging = () => {
        hud.removeEventListener('mousedown', handleMouseDown);
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
        hud.removeEventListener('touchstart', handleTouchStart);
        document.removeEventListener('touchmove', handleTouchMove);
        document.removeEventListener('touchend', handleTouchEnd);
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
        
        /* 请她回来按钮呼吸特效 */
        @keyframes returnButtonBreathing {
            0%, 100% {
                box-shadow: 0 0 8px rgba(68, 183, 254, 0.6), 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08);
            }
            50% {
                box-shadow: 0 0 18px rgba(68, 183, 254, 1), 0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08);
            }
        }
        
        #live2d-btn-return {
            animation: returnButtonBreathing 2s ease-in-out infinite;
            will-change: box-shadow;
        }
        
        #live2d-btn-return:hover {
            animation: none;
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
