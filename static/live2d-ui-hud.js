/**
 * Live2D UI HUD - Agent任务HUD组件
 * 包含任务面板、任务卡片、HUD拖拽功能
 */

// 创建Agent弹出框内容
Live2DManager.prototype._createAgentPopupContent = function (popup) {
    // 添加状态显示栏 - Fluent Design
    const statusDiv = document.createElement('div');
    statusDiv.id = 'live2d-agent-status';
    Object.assign(statusDiv.style, {
        fontSize: '12px',
        color: '#44b7fe',  // 主题浅蓝色
        padding: '6px 8px',
        borderRadius: '4px',
        background: 'rgba(68, 183, 254, 0.05)',  // 浅蓝背景
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
            id: 'agent-mcp', 
            label: window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具', 
            labelKey: 'settings.toggles.mcpTools', 
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        },{ 
            id: 'agent-user-plugin', 
            label: window.t ? window.t('settings.toggles.userPlugin') : '用户插件', 
            labelKey: 'settings.toggles.userPlugin', 
            initialDisabled: true,
            initialTitle: window.t ? window.t('settings.toggles.checking') : '查询中...'
        }
    ];

    agentToggles.forEach(toggle => {
        const toggleItem = this._createToggleItem(toggle, popup);
        popup.appendChild(toggleItem);
    });
};

// 创建 Agent 任务 HUD（屏幕正中右侧）
Live2DManager.prototype.createAgentTaskHUD = function () {
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
        background: 'rgba(15, 23, 42, 0.92)',
        backdropFilter: 'blur(12px)',
        borderRadius: '16px',
        padding: '16px',
        boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.1)',
        color: '#e2e8f0',
        fontFamily: "'Segoe UI', 'SF Pro Display', -apple-system, sans-serif",
        fontSize: '13px',
        zIndex: '9999',
        display: 'none', // 默认隐藏
        flexDirection: 'column',
        gap: '12px',
        pointerEvents: 'auto',
        overflowY: 'auto',
        transition: 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease',
        cursor: 'move',
        userSelect: 'none',
        willChange: 'transform', // 优化性能
        touchAction: 'none' // 防止浏览器默认触摸行为
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
        paddingBottom: '12px',
        borderBottom: '1px solid rgba(255, 255, 255, 0.1)'
    });

    const title = document.createElement('div');
    title.id = 'agent-task-hud-title';
    title.innerHTML = `<span style="color: #60a5fa; margin-right: 8px;">⚡</span>${window.t ? window.t('agent.taskHud.title') : 'Agent 任务'}`;
    Object.assign(title.style, {
        fontWeight: '600',
        fontSize: '15px',
        color: '#f1f5f9'
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
        <span style="color: #fbbf24;" title="${window.t ? window.t('agent.taskHud.running') : '运行中'}">● <span id="hud-running-count">0</span></span>
        <span style="color: #60a5fa;" title="${window.t ? window.t('agent.taskHud.queued') : '队列中'}">◐ <span id="hud-queued-count">0</span></span>
    `;

    header.appendChild(title);
    header.appendChild(stats);
    hud.appendChild(header);

    // 任务列表容器
    const taskList = document.createElement('div');
    taskList.id = 'agent-task-list';
    Object.assign(taskList.style, {
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        maxHeight: 'calc(60vh - 80px)',
        overflowY: 'auto'
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

    // 折叠控制按钮
    const collapseButton = document.createElement('div');
    collapseButton.className = 'collapse-button';
    collapseButton.innerHTML = '▼';
    Object.assign(collapseButton.style, {
        position: 'absolute',
        top: '8px',
        right: '8px',
        width: '20px',
        height: '20px',
        borderRadius: '50%',
        background: 'rgba(100, 116, 139, 0.3)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '10px',
        color: '#64748b',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        zIndex: '1'
    });

    // 设置空状态容器样式
    Object.assign(emptyState.style, {
        position: 'relative',
        transition: 'all 0.3s ease'
    });

    emptyState.appendChild(emptyContent);
    emptyState.appendChild(collapseButton);
    taskList.appendChild(emptyState);

    // 初始化折叠状态
    this._setupCollapseFunctionality(emptyState, collapseButton, emptyContent);

    hud.appendChild(taskList);

    document.body.appendChild(hud);

    // 添加拖拽功能
    this._setupDragging(hud);

    return hud;
};

// 显示任务 HUD
Live2DManager.prototype.showAgentTaskHUD = function () {
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
Live2DManager.prototype.hideAgentTaskHUD = function () {
    const hud = document.getElementById('agent-task-hud');
    if (hud) {
        hud.style.opacity = '0';
        const savedPos = localStorage.getItem('agent-task-hud-position');
        if (!savedPos) {
            hud.style.transform = 'translateY(-50%) translateX(20px)';
        }
        setTimeout(() => {
            hud.style.display = 'none';
        }, 300);
    }
};

// 更新任务 HUD 内容
Live2DManager.prototype.updateAgentTaskHUD = function (tasksData) {
    const taskList = document.getElementById('agent-task-list');
    const emptyState = document.getElementById('agent-task-empty');
    const runningCount = document.getElementById('hud-running-count');
    const queuedCount = document.getElementById('hud-queued-count');

    if (!taskList) return;

    // 更新统计数据
    if (runningCount) runningCount.textContent = tasksData.running_count || 0;
    if (queuedCount) queuedCount.textContent = tasksData.queued_count || 0;

    // 获取活动任务（running 和 queued）
    const activeTasks = (tasksData.tasks || []).filter(t =>
        t.status === 'running' || t.status === 'queued'
    );

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
Live2DManager.prototype._createTaskCard = function (task) {
    const card = document.createElement('div');
    card.className = 'task-card';
    card.dataset.taskId = task.id;
    if (task.start_time) {
        card.dataset.startTime = task.start_time;
    }

    const isRunning = task.status === 'running';
    const statusColor = isRunning ? '#fbbf24' : '#60a5fa';
    const statusText = isRunning
        ? (window.t ? window.t('agent.taskHud.statusRunning') : '运行中')
        : (window.t ? window.t('agent.taskHud.statusQueued') : '队列中');

    Object.assign(card.style, {
        background: 'rgba(30, 41, 59, 0.8)',
        borderRadius: '10px',
        padding: '12px',
        border: `1px solid ${isRunning ? 'rgba(251, 191, 36, 0.3)' : 'rgba(96, 165, 250, 0.2)'}`,
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
    const typeIcon = task.source === 'mcp' ? '🔌' : (task.source === 'computer_use' ? '🖱️' : '⚙️');
    const typeName = task.type || task.source || 'unknown';

    const typeLabel = document.createElement('span');
    typeLabel.innerHTML = `${typeIcon} <span style="color: #94a3b8; font-size: 11px;">${typeName}</span>`;

    const statusBadge = document.createElement('span');
    statusBadge.textContent = statusText;
    Object.assign(statusBadge.style, {
        color: statusColor,
        fontSize: '11px',
        fontWeight: '500',
        padding: '2px 8px',
        background: isRunning ? 'rgba(251, 191, 36, 0.15)' : 'rgba(96, 165, 250, 0.15)',
        borderRadius: '10px'
    });

    header.appendChild(typeLabel);
    header.appendChild(statusBadge);
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
        color: '#cbd5e1',
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
        timeDiv.innerHTML = `<span style="color: #64748b;">⏱️</span> ${minutes}:${seconds.toString().padStart(2, '0')}`;
        Object.assign(timeDiv.style, {
            color: '#94a3b8',
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
            background: 'rgba(251, 191, 36, 0.2)',
            borderRadius: '1px',
            marginTop: '8px',
            overflow: 'hidden'
        });

        const progressFill = document.createElement('div');
        Object.assign(progressFill.style, {
            height: '100%',
            width: '30%',
            background: 'linear-gradient(90deg, #fbbf24, #f59e0b)',
            borderRadius: '1px',
            animation: 'taskProgress 1.5s ease-in-out infinite'
        });
        progressBar.appendChild(progressFill);
        card.appendChild(progressBar);
    }

    return card;
};

// 设置HUD全局拖拽功能
Live2DManager.prototype._setupDragging = function (hud) {
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

            // 获取窗口尺寸和HUD尺寸
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;
            const hudRect = hud.getBoundingClientRect();

            // 边界检查 - 确保HUD不会超出视口
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
        const interactiveSelectors = ['button', 'input', 'textarea', 'select', 'a', '.task-card'];
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
        hud.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.1)';
        hud.style.opacity = '1';
        hud.style.transition = 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease';

        // 最终位置校准
        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;

            // 确保位置在视口内
            let finalLeft = parseFloat(hud.style.left) || 0;
            let finalTop = parseFloat(hud.style.top) || 0;

            finalLeft = Math.max(0, Math.min(finalLeft, windowWidth - rect.width));
            finalTop = Math.max(0, Math.min(finalTop, windowHeight - rect.height));

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
        const interactiveSelectors = ['button', 'input', 'textarea', 'select', 'a', '.task-card'];
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
        hud.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.1)';
        hud.style.opacity = '1';
        hud.style.transition = 'opacity 0.3s ease, transform 0.3s ease, box-shadow 0.2s ease';

        // 最终位置校准
        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;

            // 确保位置在视口内
            let finalLeft = parseFloat(hud.style.left) || 0;
            let finalTop = parseFloat(hud.style.top) || 0;

            finalLeft = Math.max(0, Math.min(finalLeft, windowWidth - rect.width));
            finalTop = Math.max(0, Math.min(finalTop, windowHeight - rect.height));

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

    // 窗口大小变化时重新校准位置
    const handleResize = () => {
        if (isDragging || touchDragging) return;

        requestAnimationFrame(() => {
            const rect = hud.getBoundingClientRect();
            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;

            // 如果HUD超出视口，调整到可见位置
            if (rect.left < 0 || rect.top < 0 ||
                rect.right > windowWidth || rect.bottom > windowHeight) {

                let newLeft = parseFloat(hud.style.left) || 0;
                let newTop = parseFloat(hud.style.top) || 0;

                newLeft = Math.max(0, Math.min(newLeft, windowWidth - rect.width));
                newTop = Math.max(0, Math.min(newTop, windowHeight - rect.height));

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
            background: rgba(255, 255, 255, 0.05);
            border-radius: 2px;
        }
        
        #agent-task-hud::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.2);
            border-radius: 2px;
        }
        
        #agent-task-list::-webkit-scrollbar {
            width: 4px;
        }
        
        #agent-task-list::-webkit-scrollbar-track {
            background: transparent;
        }
        
        #agent-task-list::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.15);
            border-radius: 2px;
        }
        
        .task-card:hover {
            background: rgba(51, 65, 85, 0.8) !important;
            transform: translateX(-2px);
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
            background: rgba(100, 116, 139, 0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            color: #64748b;
            cursor: pointer;
            transition: all 0.2s ease;
            z-index: 1;
            user-select: none;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
        }
        
        .collapse-button:hover {
            background: rgba(100, 116, 139, 0.6);
            transform: scale(1.1);
        }
        
        .collapse-button:active {
            transform: scale(0.95);
        }
        
        .collapse-button.collapsed {
            background: rgba(100, 116, 139, 0.5);
            color: #94a3b8;
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
