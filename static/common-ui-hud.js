/**
 * Live2D UI HUD - Agent任务HUD组件
 * 包含任务面板、任务卡片、HUD拖拽功能
 */

window.AgentHUD = window.AgentHUD || {};

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
        }
    ];

    agentToggles.forEach(toggle => {
        const toggleItem = this._createToggleItem(toggle, popup);
        popup.appendChild(toggleItem);
    });

    // 添加适配中的按钮（不可选）
    const adaptingItems = [
        { labelKey: 'settings.toggles.userPluginAdapting', fallback: '用户插件（开发中）' },
        { labelKey: 'settings.toggles.moltbotAdapting', fallback: 'moltbot（开发中）' }
    ];

    adaptingItems.forEach(item => {
        const adaptingItem = document.createElement('div');
        Object.assign(adaptingItem.style, {
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '6px 8px',
            borderRadius: '6px',
            fontSize: '13px',
            whiteSpace: 'nowrap',
            opacity: '0.5',
            cursor: 'not-allowed',
            color: '#666'
        });

        const indicator = document.createElement('div');
        Object.assign(indicator.style, {
            width: '20px',
            height: '20px',
            borderRadius: '50%',
            border: '2px solid #ccc',
            backgroundColor: 'transparent',
            flexShrink: '0'
        });

        const label = document.createElement('span');
        label.textContent = window.t ? window.t(item.labelKey) : item.fallback;
        label.setAttribute('data-i18n', item.labelKey);
        label.style.userSelect = 'none';
        label.style.fontSize = '13px';
        label.style.color = '#999';

        adaptingItem.appendChild(indicator);
        adaptingItem.appendChild(label);
        popup.appendChild(adaptingItem);
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
        background: 'rgba(255, 255, 255, 0.65)',
        backdropFilter: 'saturate(180%) blur(20px)',
        WebkitBackdropFilter: 'saturate(180%) blur(20px)',
        borderRadius: '8px',
        padding: '0',
        border: '1px solid rgba(255, 255, 255, 0.18)',
        boxShadow: '0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04)',
        color: '#333',
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
        touchAction: 'none'
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
        backgroundColor: 'rgba(255, 255, 255, 0.85)',
        borderTopLeftRadius: '8px',
        borderTopRightRadius: '8px',
        borderBottom: '1px solid rgba(0, 0, 0, 0.08)',
        transition: 'padding 0.4s ease, margin 0.4s ease, border-color 0.4s ease, border-radius 0.4s ease, background-color 0.4s ease'
    });

    const title = document.createElement('div');
    title.id = 'agent-task-hud-title';
    title.innerHTML = `<span style="color: var(--neko-popup-accent, #2a7bc4); margin-right: 8px;">⚡</span>${window.t ? window.t('agent.taskHud.title') : 'Agent 任务'}`;
    Object.assign(title.style, {
        fontWeight: '600',
        fontSize: '15px',
        color: '#333',
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
        background: 'rgba(220, 53, 69, 0.12)',
        display: 'none',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '11px',
        fontWeight: 'bold',
        color: '#dc3545',
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
        transition: 'max-height 0.3s ease, opacity 0.3s ease, padding 0.3s ease'
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
            header.style.backgroundColor = 'rgba(255, 255, 255, 0.85)';
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
            header.style.backgroundColor = 'rgba(255, 255, 255, 0.85)';
            header.style.borderBottom = '1px solid rgba(0, 0, 0, 0.08)';
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
        color: '#64748b',
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
    const taskList = document.getElementById('agent-task-list');
    const emptyState = document.getElementById('agent-task-empty');
    const runningCount = document.getElementById('hud-running-count');
    const queuedCount = document.getElementById('hud-queued-count');
    const cancelBtn = document.getElementById('agent-task-hud-cancel');

    if (!taskList) return;

    // 更新统计数据
    if (runningCount) runningCount.textContent = tasksData.running_count || 0;
    if (queuedCount) queuedCount.textContent = tasksData.queued_count || 0;

    // 获取活动任务（running 和 queued）
    const activeTasks = (tasksData.tasks || []).filter(t =>
        t.status === 'running' || t.status === 'queued'
    );

    if (cancelBtn) {
        cancelBtn.style.display = activeTasks.length > 0 ? 'flex' : 'none';
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

    // 清除旧的任务卡片（保留空状态）
    const existingCards = taskList.querySelectorAll('.task-card');
    existingCards.forEach(card => card.remove());

    // 添加任务卡片
    activeTasks.forEach(task => {
        const card = this._createTaskCard(task);
        taskList.appendChild(card);
    });
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
    const statusColor = isRunning ? 'var(--neko-popup-accent, #2a7bc4)' : 'var(--neko-popup-text-sub, #666)';
    const statusText = isRunning
        ? (window.t ? window.t('agent.taskHud.statusRunning') : '运行中')
        : (window.t ? window.t('agent.taskHud.statusQueued') : '队列中');

    Object.assign(card.style, {
        background: isRunning ? 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.08))' : 'var(--neko-popup-bg, rgba(249, 249, 249, 0.6))',
        borderRadius: '8px',
        padding: '12px',
        border: `1px solid ${isRunning ? 'var(--neko-popup-accent-border, rgba(42, 123, 196, 0.25))' : 'var(--neko-popup-border, rgba(0, 0, 0, 0.06))'}`,
        transition: 'all 0.2s ease'
    });

    // 任务类型和状态
    const header = document.createElement('div');
    Object.assign(header.style, {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '8px'
    });

    // 任务类型图标
    const typeIcon = task.source === 'computer_use' ? '🖱️' : '⚙️';
    const typeName = task.type || task.source || 'unknown';

    const typeLabel = document.createElement('span');
    typeLabel.innerHTML = `${typeIcon} <span style="color: #666; font-size: 11px;">${typeName}</span>`;

    const statusBadge = document.createElement('span');
    statusBadge.textContent = statusText;
    Object.assign(statusBadge.style, {
        color: statusColor,
        fontSize: '11px',
        fontWeight: '500',
        padding: '2px 8px',
        background: isRunning ? 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.12))' : 'var(--neko-popup-bg, rgba(0, 0, 0, 0.05))',
        borderRadius: '10px'
    });

    const headerLeft = document.createElement('div');
    Object.assign(headerLeft.style, { display: 'flex', alignItems: 'center', gap: '4px', minWidth: '0' });
    headerLeft.appendChild(typeLabel);
    headerLeft.appendChild(statusBadge);

    const taskCancelBtn = document.createElement('div');
    taskCancelBtn.className = 'task-card-cancel';
    taskCancelBtn.innerHTML = '✕';
    Object.assign(taskCancelBtn.style, {
        width: '18px',
        height: '18px',
        borderRadius: '4px',
        background: 'rgba(0, 0, 0, 0.06)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '10px',
        color: '#999',
        cursor: 'pointer',
        transition: 'all 0.15s ease',
        flexShrink: '0'
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

    // 任务参数/描述
    const params = task.params || {};
    let description = '';
    if (params.query) {
        description = params.query;
    } else if (params.instruction) {
        // computer_use 任务使用 instruction 字段
        description = params.instruction;
    } else if (task.original_query) {
        // planner 任务使用 original_query 字段
        description = task.original_query;
    } else if (params.tool_name) {
        description = params.tool_name;
    } else if (params.action) {
        description = params.action;
    } else {
        description = task.id?.substring(0, 8) || 'Task';
    }

    const descDiv = document.createElement('div');
    descDiv.textContent = description.length > 60 ? description.substring(0, 60) + '...' : description;
    Object.assign(descDiv.style, {
        color: '#444',
        fontSize: '12px',
        lineHeight: '1.4',
        marginBottom: '8px',
        wordBreak: 'break-word'
    });
    card.appendChild(descDiv);

    // 运行时间
    if (task.start_time && isRunning) {
        const timeDiv = document.createElement('div');
        const startTime = new Date(task.start_time);
        const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
        const minutes = Math.floor(elapsed / 60);
        const seconds = elapsed % 60;

        timeDiv.id = `task-time-${task.id}`;
        timeDiv.innerHTML = `<span style="color: #999;">⏱️</span> ${minutes}:${seconds.toString().padStart(2, '0')}`;
        Object.assign(timeDiv.style, {
            color: '#888',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            gap: '4px'
        });
        card.appendChild(timeDiv);
    }

    // 如果是运行中的任务，添加动画指示器
    if (isRunning) {
        const progressBar = document.createElement('div');
        Object.assign(progressBar.style, {
            height: '2px',
            background: 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.15))',
            borderRadius: '1px',
            marginTop: '8px',
            overflow: 'hidden'
        });

        const progressFill = document.createElement('div');
        Object.assign(progressFill.style, {
            height: '100%',
            width: '30%',
            background: 'linear-gradient(90deg, var(--neko-popup-accent, #2a7bc4), #66b5ff)',
            borderRadius: '1px',
            animation: 'taskProgress 1.5s ease-in-out infinite'
        });
        progressBar.appendChild(progressFill);
        card.appendChild(progressBar);
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
        hud.style.boxShadow = '0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04)';
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
    let touchOffsetX = 0;
    let touchOffsetY = 0;

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
        hud.style.boxShadow = '0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04)';
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
