/**
 * Common UI - 通用用户界面功能
 * 功能:
 *  - 聊天容器的展开/收起功能
 *  - 聊天内容的滚动到底部功能
 *  - 移动端检测（基于窗口宽度）
 *  - 聊天容器的可拖拽缩放功能
 */

// 获取聊天容器元素
const chatContainer = document.getElementById('chat-container');
const chatContentWrapper = document.getElementById('chat-content-wrapper');
const toggleBtn = document.getElementById('toggle-chat-btn');

let isTransitioning = false;

// 移动端检测（与 live2d.js 的 isMobileWidth 一致：基于窗口宽度）
function uiIsMobileWidth() {
    return window.innerWidth <= 768;
}

function isCollapsed() {
    return chatContainer.classList.contains('minimized') || chatContainer.classList.contains('mobile-collapsed');
}

// 定义一个滚动到底部的函数
function scrollToBottom() {
    if (chatContentWrapper && !isCollapsed()) {
        chatContentWrapper.scrollTop = chatContentWrapper.scrollHeight;
    }
}

// --- 添加新消息函数 (修正) ---
function addNewMessage(messageHTML) {
    if (!chatContentWrapper) return; // 安全检查

    const newMessageElement = document.createElement('div');
    newMessageElement.innerHTML = messageHTML;
    chatContentWrapper.appendChild(newMessageElement);

    // 确保在添加消息后立即滚动到底部
    setTimeout(scrollToBottom, 10); // 短暂延迟确保DOM更新
}

// --- 整个对话区可拖拽缩放（输入区/按钮高度固定，历史区自适应） ---
function setupResizableChatContainer() {
    if (!chatContainer) return;

    const STORAGE_WIDTH_KEY = 'neko.chatContainerWidth';
    const STORAGE_HEIGHT_KEY = 'neko.chatContainerHeight';
    const DEFAULT_WIDTH = 400;
    const DEFAULT_HEIGHT = 500;
    const MIN_WIDTH = 320;
    const MIN_HEIGHT = 340;

    // 用角标手柄控制尺寸，避免误触输入框与按钮
    let resizeHandle = chatContainer.querySelector('.chat-resize-handle');
    if (!resizeHandle) {
        resizeHandle = document.createElement('div');
        resizeHandle.className = 'chat-resize-handle';
        resizeHandle.setAttribute('aria-hidden', 'true');
        chatContainer.appendChild(resizeHandle);
    }

    if (!document.getElementById('chat-container-resize-style')) {
        const style = document.createElement('style');
        style.id = 'chat-container-resize-style';
        style.textContent = `
            #chat-container.resizable-chat-container {
                min-width: ${MIN_WIDTH}px;
                min-height: ${MIN_HEIGHT}px;
            }

            #chat-container .chat-resize-handle {
                position: absolute;
                right: 6px;
                bottom: 6px;
                width: 16px;
                height: 16px;
                z-index: 35;
                border-radius: 4px;
                cursor: nwse-resize;
                pointer-events: auto;
                touch-action: none;
                opacity: 0.75;
                background-image:
                    linear-gradient(135deg, transparent 0 35%, rgba(68, 183, 254, 0.42) 35% 43%, transparent 43% 52%, rgba(68, 183, 254, 0.58) 52% 60%, transparent 60% 70%, rgba(68, 183, 254, 0.78) 70% 78%, transparent 78% 100%);
                transition: opacity 0.2s ease, transform 0.2s ease, filter 0.2s ease;
            }

            #chat-container .chat-resize-handle:hover {
                opacity: 1;
                transform: scale(1.06);
                filter: drop-shadow(0 1px 2px rgba(68, 183, 254, 0.35));
            }

            #chat-container.is-resizing {
                transition: none !important;
                box-shadow:
                    0 2px 4px rgba(0, 0, 0, 0.04),
                    0 8px 16px rgba(0, 0, 0, 0.08),
                    0 20px 36px rgba(68, 183, 254, 0.18);
            }

            #chat-container.is-resizing .chat-resize-handle {
                opacity: 1;
                transform: scale(1.08);
            }

            #chat-container.minimized .chat-resize-handle,
            #chat-container.mobile-collapsed .chat-resize-handle {
                display: none;
            }

            @media only screen and (max-width: 768px) {
                #chat-container .chat-resize-handle {
                    display: none !important;
                }
            }

            [data-theme="dark"] #chat-container .chat-resize-handle {
                background-image:
                    linear-gradient(135deg, transparent 0 35%, rgba(74, 163, 223, 0.52) 35% 43%, transparent 43% 52%, rgba(74, 163, 223, 0.66) 52% 60%, transparent 60% 70%, rgba(74, 163, 223, 0.86) 70% 78%, transparent 78% 100%);
                filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.32));
            }
        `;
        document.head.appendChild(style);
    }
    // 初始化时添加可调整大小类
    chatContainer.classList.add('resizable-chat-container');

    const clampSize = (width, height) => {
        const maxWidth = Math.max(MIN_WIDTH, Math.floor(window.innerWidth * 0.9));
        const maxHeight = Math.max(MIN_HEIGHT, Math.floor(window.innerHeight * 0.85));
        return {
            width: Math.max(MIN_WIDTH, Math.min(maxWidth, width)),
            height: Math.max(MIN_HEIGHT, Math.min(maxHeight, height))
        };
    };
    // 应用容器尺寸（同时更新最大高度）
    const applyContainerSize = (width, height) => {
        const clamped = clampSize(width, height);
        chatContainer.style.width = `${clamped.width}px`;
        chatContainer.style.height = `${clamped.height}px`;
        chatContainer.style.maxHeight = `${clamped.height}px`;
        return clamped;
    };
    // 持久化容器尺寸到 localStorage
    const persistContainerSize = () => {
        const rect = chatContainer.getBoundingClientRect();
        try {
            localStorage.setItem(STORAGE_WIDTH_KEY, String(Math.round(rect.width)));
            localStorage.setItem(STORAGE_HEIGHT_KEY, String(Math.round(rect.height)));
        } catch (_) {
            /* localStorage 不可用时静默跳过 */
        }
    };
    // 从 localStorage 恢复容器尺寸
    const restoreContainerSize = () => {
        let savedW = NaN;
        let savedH = NaN;
        try {
            savedW = Number(localStorage.getItem(STORAGE_WIDTH_KEY));
            savedH = Number(localStorage.getItem(STORAGE_HEIGHT_KEY));
        } catch (_) {
            /* localStorage 不可用时忽略 */
        }
        if (Number.isFinite(savedW) && Number.isFinite(savedH) && savedW > 0 && savedH > 0) {
            applyContainerSize(savedW, savedH);
        } else {
            applyContainerSize(DEFAULT_WIDTH, DEFAULT_HEIGHT);
        }
    };

    restoreContainerSize();

    let isResizing = false;
    let startX = 0;
    let startY = 0;
    let startWidth = 0;
    let startHeight = 0;
    let startBottom = 0;
    // 处理调整大小移动事件
    const onResizeMove = (e) => {
        if (!isResizing) return;
        const clientX = e.type.startsWith('touch') ? e.touches[0].clientX : e.clientX;
        const clientY = e.type.startsWith('touch') ? e.touches[0].clientY : e.clientY;
        const nextWidth = startWidth + (clientX - startX);
        const rawNextHeight = startHeight + (clientY - startY);
        // 当底边触达屏幕底部后，继续向下拖拽不再增高（顶部保持锚定）
        const bottomLimitedMaxHeight = startHeight + Math.max(0, startBottom);
        const nextHeight = Math.min(rawNextHeight, bottomLimitedMaxHeight);
        const applied = applyContainerSize(nextWidth, nextHeight);
        // chat-container 采用 bottom 定位；同步调整 bottom 让垂直拉伸表现为“向下展开”
        const consumedDeltaY = applied.height - startHeight;
        chatContainer.style.bottom = `${Math.max(0, startBottom - consumedDeltaY)}px`;
        e.preventDefault();
    };
    // 处理调整大小结束事件
    const stopResize = () => {
        if (!isResizing) return;
        isResizing = false;
        chatContainer.classList.remove('is-resizing');
        persistContainerSize();
        if (window.ChatDialogSnap && typeof window.ChatDialogSnap.snapIntoScreen === 'function') {
            window.ChatDialogSnap.snapIntoScreen({ animate: true });
        }
    };
    // 处理调整大小开始事件
    const startResize = (e) => {
        if (uiIsMobileWidth() || isCollapsed()) return;
        isResizing = true;
        chatContainer.classList.add('is-resizing');
        // 记录初始位置和尺寸
        const point = e.type.startsWith('touch') ? e.touches[0] : e;
        const rect = chatContainer.getBoundingClientRect();
        startX = point.clientX;
        startY = point.clientY;
        startWidth = rect.width;
        startHeight = rect.height;
        const computedStyle = window.getComputedStyle(chatContainer);
        const parsedBottom = parseFloat(computedStyle.bottom);
        startBottom = Number.isFinite(parsedBottom) ? parsedBottom : (window.innerHeight - rect.bottom);

        e.stopPropagation();
        e.preventDefault();
    };
    // 绑定调整大小事件
    resizeHandle.addEventListener('mousedown', startResize);
    resizeHandle.addEventListener('touchstart', startResize, { passive: false });
    document.addEventListener('mousemove', onResizeMove);
    document.addEventListener('touchmove', onResizeMove, { passive: false });
    document.addEventListener('mouseup', stopResize);
    document.addEventListener('touchend', stopResize);

    window.addEventListener('resize', () => {
        const rect = chatContainer.getBoundingClientRect();
        applyContainerSize(rect.width, rect.height);
        persistContainerSize();
    });
}

// --- 切换聊天框最小化/展开状态 ---
// 用于跟踪是否刚刚发生了拖动
let justDragged = false;

// 展开后回弹（等待布局更新）
function triggerExpandSnap() {
    if (!window.ChatDialogSnap || typeof window.ChatDialogSnap.snapIntoScreen !== 'function') return;

    // 双 RAF 确保本帧布局已更新
    requestAnimationFrame(() => {
        requestAnimationFrame(() => window.ChatDialogSnap.snapIntoScreen({ animate: true }));
    });

    // 兼容存在过渡/尺寸变化的情况
    setTimeout(() => window.ChatDialogSnap.snapIntoScreen({ animate: true }), 320);
}

// 确保DOM加载后再绑定事件
if (toggleBtn) {
    toggleBtn.addEventListener('click', (event) => {
        event.stopPropagation();

        // 如果正在过渡中，阻止切换
        if (isTransitioning) {
            return;
        }

        // 如果刚刚发生了拖动，阻止切换
        if (justDragged) {
            justDragged = false;
            return;
        }

        // 设置过渡标志
        isTransitioning = true;

        try {
            // 移动端：仅折叠内容区与标题，不最小化整个容器，保持输入区常驻
            if (uiIsMobileWidth()) {
                const becomingCollapsed = !chatContainer.classList.contains('mobile-collapsed');
                if (becomingCollapsed) {
                    chatContainer.classList.add('mobile-collapsed');
                    // 隐藏内容区与标题
                    if (chatContentWrapper) chatContentWrapper.style.display = 'none';
                    const chatHeader = document.getElementById('chat-header');
                    if (chatHeader) chatHeader.style.display = 'none';
                    // 确保切换按钮始终可见
                    if (toggleBtn) {
                        toggleBtn.style.display = 'block';
                        toggleBtn.style.visibility = 'visible';
                        toggleBtn.style.opacity = '1';
                    }
                } else {
                    chatContainer.classList.remove('mobile-collapsed');
                    // 显示内容区与标题
                    if (chatContentWrapper) chatContentWrapper.style.removeProperty('display');
                    const chatHeader = document.getElementById('chat-header');
                    if (chatHeader) chatHeader.style.removeProperty('display');
                    if (toggleBtn) {
                        toggleBtn.style.removeProperty('display');
                        toggleBtn.style.removeProperty('visibility');
                        toggleBtn.style.removeProperty('opacity');
                    }
                }
                
                // 获取或创建图标
                let iconImg = toggleBtn.querySelector('img');
                if (!iconImg) {
                    iconImg = document.createElement('img');
                    iconImg.style.width = '32px';
                    iconImg.style.height = '32px';
                    iconImg.style.objectFit = 'cover';
                    iconImg.style.pointerEvents = 'none';
                    toggleBtn.innerHTML = '';
                    toggleBtn.appendChild(iconImg);
                } else {
                    iconImg.style.width = '32px';
                    iconImg.style.height = '32px';
                }
                
                if (becomingCollapsed) {
                    iconImg.src = '/static/icons/expand_icon_off.png';
                    iconImg.alt = window.t ? window.t('common.expand') : '展开';
                    toggleBtn.title = window.t ? window.t('common.expand') : '展开';
                } else {
                    iconImg.src = '/static/icons/expand_icon_off.png';
                    iconImg.alt = window.t ? window.t('common.minimize') : '最小化';
                    toggleBtn.title = window.t ? window.t('common.minimize') : '最小化';
                    setTimeout(scrollToBottom, 300);
                    // 展开后执行回弹，避免位置越界
                    triggerExpandSnap();
                }
                // 动画结束后清除过渡标志
                setTimeout(() => { isTransitioning = false; }, 350);
                return; // 移动端已处理，直接返回
            }

            const isMinimized = chatContainer.classList.toggle('minimized');
            
            // 如果容器没有其他类，完全移除class属性以避免显示为class=""
            if (!isMinimized && chatContainer.classList.length === 0) {
                chatContainer.removeAttribute('class');
            }
            
            // 获取图标元素（HTML中应该已经有img标签）
            let iconImg = toggleBtn.querySelector('img');
            if (!iconImg) {
                // 如果没有图标，创建一个
                iconImg = document.createElement('img');
                iconImg.style.width = '32px';  /* 图标尺寸 */
                iconImg.style.height = '32px';  /* 图标尺寸 */
                iconImg.style.objectFit = 'cover';
                iconImg.style.pointerEvents = 'none'; /* 确保图标不干扰点击事件 */
                toggleBtn.innerHTML = '';
                toggleBtn.appendChild(iconImg);
            } else {
                // 如果图标已存在，也更新其大小
                iconImg.style.width = '32px';  /* 图标尺寸 */
                iconImg.style.height = '32px';  /* 图标尺寸 */
            }

            if (isMinimized) {
                // 刚刚最小化，显示展开图标（加号）
                iconImg.src = '/static/icons/expand_icon_off.png';
                iconImg.alt = window.t ? window.t('common.expand') : '展开';
                toggleBtn.title = window.t ? window.t('common.expand') : '展开';
                iconImg.style.width = '100%';
                iconImg.style.height = '100%';
            } else {
                // 刚刚还原展开，显示最小化图标（减号）
                iconImg.src = '/static/icons/expand_icon_off.png';
                iconImg.alt = window.t ? window.t('common.minimize') : '最小化';
                toggleBtn.title = window.t ? window.t('common.minimize') : '最小化';
                iconImg.style.width = '32px';
                iconImg.style.height = '32px';
                // 还原后滚动到底部
                setTimeout(scrollToBottom, 300); // 给CSS过渡留出时间
                // 展开后执行回弹，避免位置越界
                triggerExpandSnap();
            }
            // 动画结束后清除过渡标志
            setTimeout(() => { isTransitioning = false; }, 350);
        } catch (e) {
            // 发生异常时立即重置过渡标志
            isTransitioning = false;
            console.error('Chat toggle error:', e);
            throw e;
        }
    });
}

// --- 鼠标悬停效果 - 仅在最小化状态下生效 ---
if (toggleBtn) {
    toggleBtn.addEventListener('mouseenter', () => {
        if (chatContainer.classList.contains('minimized')) {
            let iconImg = toggleBtn.querySelector('img');
            if (iconImg) {
                iconImg.src = '/static/icons/expand_icon_on.png';
            }
        }
    });

    toggleBtn.addEventListener('mouseleave', () => {
        if (chatContainer.classList.contains('minimized')) {
            let iconImg = toggleBtn.querySelector('img');
            if (iconImg) {
                iconImg.src = '/static/icons/expand_icon_off.png';
            }
        }
    });
}

// --- 对话区拖动功能 ---
(function() {
    let isDragging = false;
    let hasMoved = false; // 用于判断是否发生了实际的移动
    let dragStartedFromToggleBtn = false; // 记录是否从 toggleBtn 开始拖动
    let startMouseX = 0; // 开始拖动时的鼠标X位置
    let startMouseY = 0; // 开始拖动时的鼠标Y位置
    let startContainerLeft = 0; // 开始拖动时容器的left值
    let startContainerBottom = 0; // 开始拖动时容器的bottom值

    // 拖动回弹配置（多屏幕切换时使用）
    const CHAT_SNAP_CONFIG = {
        margin: 6,
        duration: 260,
        easingType: 'easeOutBack'
    };

    let snapAnimationFrameId = null;
    let isSnapping = false;
    // 聊天框拖动逻辑的缓动函数（提供多种选择）
    const EasingFunctions = {
        easeOutBack: (t) => {
            const c1 = 1.70158;
            const c3 = c1 + 1;
            return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
        },
        easeOutCubic: (t) => (--t) * t * t + 1
    };

    // 获取当前显示区域的尺寸（考虑多屏幕）
    async function getDisplayWorkAreaSize() {
        let width = window.innerWidth;
        let height = window.innerHeight;

        if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
            try {
                const currentDisplay = await window.electronScreen.getCurrentDisplay();
                if (currentDisplay && currentDisplay.workArea) {
                    width = currentDisplay.workArea.width || width;
                    height = currentDisplay.workArea.height || height;
                } else if (currentDisplay && currentDisplay.width && currentDisplay.height) {
                    width = currentDisplay.width;
                    height = currentDisplay.height;
                }
            } catch (e) {
                console.debug('[Chat Snap] 获取屏幕工作区域失败，使用窗口尺寸');
            }
        }

        return { width, height };
    }

    // 获取聊天框当前的位置（left, bottom）
    function getChatContainerPosition() {
        const computedStyle = window.getComputedStyle(chatContainer);
        const rect = chatContainer.getBoundingClientRect();

        let left = parseFloat(computedStyle.left);
        if (!Number.isFinite(left)) {
            left = rect.left;
        }

        let bottom = parseFloat(computedStyle.bottom);
        if (!Number.isFinite(bottom)) {
            bottom = window.innerHeight - rect.bottom;
        }

        return { left, bottom, rect };
    }

    // 应用新的位置（left, bottom）到聊天框
    function applyChatContainerPosition(left, bottom) {
        chatContainer.style.left = `${left}px`;
        chatContainer.style.bottom = `${bottom}px`;
    }

    // 聊天框拖动动画
    function animateChatContainerTo(startLeft, startBottom, targetLeft, targetBottom) {
        if (snapAnimationFrameId) {
            cancelAnimationFrame(snapAnimationFrameId);
        }

        const duration = CHAT_SNAP_CONFIG.duration;
        const easingFn = EasingFunctions[CHAT_SNAP_CONFIG.easingType] || EasingFunctions.easeOutCubic;
        const startTime = performance.now();

        isSnapping = true;

        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const easedProgress = easingFn(progress);

            const newLeft = startLeft + (targetLeft - startLeft) * easedProgress;
            const newBottom = startBottom + (targetBottom - startBottom) * easedProgress;

            applyChatContainerPosition(newLeft, newBottom);

            if (progress < 1) {
                snapAnimationFrameId = requestAnimationFrame(animate);
            } else {
                applyChatContainerPosition(targetLeft, targetBottom);
                isSnapping = false;
                snapAnimationFrameId = null;
            }
        };

        snapAnimationFrameId = requestAnimationFrame(animate);
    }

    // 如果正在执行回弹动画，或者没有找到聊天容器，直接返回，避免重复触发
    async function snapChatContainerIntoScreen({ animate = true } = {}) {
        if (!chatContainer || isSnapping) return;

        const { rect, left, bottom } = getChatContainerPosition();
        const { width, height } = await getDisplayWorkAreaSize();

        const maxLeft = Math.max(0, width - rect.width);
        const maxBottom = Math.max(0, height - rect.height);

        const margin = CHAT_SNAP_CONFIG.margin;
        let minLeft = 0;
        let maxLeftAllowed = maxLeft;
        let minBottom = 0;
        let maxBottomAllowed = maxBottom;

        if (maxLeft > margin * 2) {
            minLeft = margin;
            maxLeftAllowed = maxLeft - margin;
        }
        if (maxBottom > margin * 2) {
            minBottom = margin;
            maxBottomAllowed = maxBottom - margin;
        }

        const targetLeft = Math.max(minLeft, Math.min(maxLeftAllowed, left));
        const targetBottom = Math.max(minBottom, Math.min(maxBottomAllowed, bottom));

        const dx = Math.abs(targetLeft - left);
        const dy = Math.abs(targetBottom - bottom);

        if (dx < 1 && dy < 1) return;

        if (animate) {
            animateChatContainerTo(left, bottom, targetLeft, targetBottom);
        } else {
            applyChatContainerPosition(targetLeft, targetBottom);
        }
    }

    // 暴露给外部（例如展开时触发回弹）
    window.ChatDialogSnap = {
        snapIntoScreen: snapChatContainerIntoScreen
    };

    // 获取相关元素
    const chatHeader = document.getElementById('chat-header');
    const textInputArea = document.getElementById('text-input-area');

    // 开始拖动的函数
    function startDrag(e, skipPreventDefault = false) {
        isDragging = true;
        hasMoved = false;
        dragStartedFromToggleBtn = (e.target === toggleBtn || toggleBtn.contains(e.target));
        
        // 获取初始鼠标/触摸位置
        const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
        const clientY = e.type.includes('touch') ? e.touches[0].clientY : e.clientY;
        
        // 记录开始时的鼠标位置
        startMouseX = clientX;
        startMouseY = clientY;
        
        // 获取当前容器的实际位置（从计算样式中读取，确保准确）
        const computedStyle = window.getComputedStyle(chatContainer);
        startContainerLeft = parseFloat(computedStyle.left) || 0;
        startContainerBottom = parseFloat(computedStyle.bottom) || 0;
        
        console.log('[Drag Start] Mouse:', clientX, clientY, 'Container:', startContainerLeft, startContainerBottom);
        
        // 添加拖动样式
        chatContainer.style.cursor = 'grabbing';
        if (chatHeader) chatHeader.style.cursor = 'grabbing';
        
        // 开始拖动时，临时禁用按钮的 pointer-events（使用 live2d-ui-drag.js 中的共享工具函数）
        if (window.DragHelpers) {
            window.DragHelpers.disableButtonPointerEvents();
        }
        
        // 阻止默认行为（除非明确跳过）
        if (!skipPreventDefault) {
            e.preventDefault();
        }
    }

    // 移动中
    function onDragMove(e) {
        if (!isDragging) return;
        
        const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
        const clientY = e.type.includes('touch') ? e.touches[0].clientY : e.clientY;
        
        // 计算鼠标的位移
        const deltaX = clientX - startMouseX;
        const deltaY = clientY - startMouseY;
        
        // 检查是否真的移动了（移动距离超过5px）
        const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);
        
        if (distance > 5) {
            hasMoved = true;
        }
        
        // 立即更新位置：初始位置 + 鼠标位移
        const newLeft = startContainerLeft + deltaX;
        // 注意：Y轴向下为正，但bottom值向上为正，所以要减去deltaY
        const newBottom = startContainerBottom - deltaY;
        
        // 限制在视口内
        const maxLeft = window.innerWidth - chatContainer.offsetWidth;
        const maxBottomRaw = window.innerHeight - chatContainer.offsetHeight;
        const topBoundary = CHAT_SNAP_CONFIG.margin;
        const maxBottom = Math.max(0, maxBottomRaw - topBoundary);
        
        chatContainer.style.left = Math.max(0, Math.min(maxLeft, newLeft)) + 'px';
        chatContainer.style.bottom = Math.max(0, Math.min(maxBottom, newBottom)) + 'px';
    }

    // 结束拖动
    function endDrag() {
        if (isDragging) {
            const wasDragging = isDragging;
            const didMove = hasMoved;
            const fromToggleBtn = dragStartedFromToggleBtn;
            
            isDragging = false;
            hasMoved = false;
            dragStartedFromToggleBtn = false;
            chatContainer.style.cursor = '';
            if (chatHeader) chatHeader.style.cursor = '';
            
            // 拖拽结束后恢复按钮的 pointer-events（使用 live2d-ui-drag.js 中的共享工具函数）
            if (window.DragHelpers) {
                window.DragHelpers.restoreButtonPointerEvents();
            }
            
            console.log('[Drag End] Moved:', didMove, 'FromToggleBtn:', fromToggleBtn);
            
            // 如果发生了移动，标记 justDragged 以阻止后续的 click 事件
            if (didMove && fromToggleBtn) {
                justDragged = true;
                // 100ms 后清除标志（防止影响后续正常点击）
                setTimeout(() => {
                    justDragged = false;
                }, 100);
            }
            
            // 如果在折叠状态下，没有发生移动，则触发展开
            // 但如果是从 toggleBtn 开始的，让自然的 click 事件处理
            if (wasDragging && !didMove && isCollapsed() && !fromToggleBtn) {
                // 使用 setTimeout 确保 click 事件之前执行
                setTimeout(() => {
                    toggleBtn.click();
                }, 0);
            }

            // 拖拽结束后：若被拖到另一屏导致越界，回弹到屏幕内侧
            snapChatContainerIntoScreen({ animate: true });
        }
    }

    // 展开状态：通过header或输入区域空白处拖动
    if (chatHeader) {
        // 鼠标事件
        chatHeader.addEventListener('mousedown', (e) => {
            if (!isCollapsed()) {
                startDrag(e);
            }
        });
        
        // 触摸事件
        chatHeader.addEventListener('touchstart', (e) => {
            if (!isCollapsed()) {
                startDrag(e);
            }
        }, { passive: false });
    }
    
    // 让切换按钮也可以触发拖拽（任何状态下都可以）
    if (toggleBtn) {
        // 鼠标事件
        toggleBtn.addEventListener('mousedown', (e) => {
            // 使用 skipPreventDefault=true 来保留 click 事件
            startDrag(e, true);
            e.stopPropagation(); // 阻止事件冒泡到 chatContainer
        });
        
        // 触摸事件
        toggleBtn.addEventListener('touchstart', (e) => {
            startDrag(e, true);
            e.stopPropagation(); // 阻止事件冒泡到 chatContainer
        }, { passive: false });
    }
    
    // 输入区域整体可拖动，但排除 textarea/button 等交互子元素
    if (textInputArea) {
        const isInteractiveTarget = (el) =>
            !!el.closest('textarea, input, button, select, a, [contenteditable]');

        textInputArea.addEventListener('mousedown', (e) => {
            if (!isCollapsed() && !isInteractiveTarget(e.target)) {
                startDrag(e);
            }
        });

        textInputArea.addEventListener('touchstart', (e) => {
            if (!isCollapsed() && !isInteractiveTarget(e.target)) {
                startDrag(e);
            }
        }, { passive: false });
    }

    // 折叠状态：点击容器（除了按钮）可以拖动或展开
    chatContainer.addEventListener('mousedown', (e) => {
        if (isCollapsed()) {
            // 如果点击的是切换按钮，不启动拖动
            if (e.target === toggleBtn || toggleBtn.contains(e.target)) {
                return;
            }
            
            // 启动拖动（移动时拖动，不移动时会在 endDrag 中展开）
            startDrag(e, true); // 跳过 preventDefault，允许后续的 click 事件
        }
    });

    chatContainer.addEventListener('touchstart', (e) => {
        if (isCollapsed()) {
            // 如果点击的是切换按钮，不启动拖动
            if (e.target === toggleBtn || toggleBtn.contains(e.target)) {
                return;
            }
            
            // 启动拖动
            startDrag(e);
        }
    }, { passive: false });

    // 全局移动和释放事件
    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('touchmove', onDragMove, { passive: false });
    document.addEventListener('mouseup', endDrag);
    document.addEventListener('touchend', endDrag);

    // 屏幕切换后，确保对话框回弹到新屏幕内侧
    window.addEventListener('electron-display-changed', () => {
        snapChatContainerIntoScreen({ animate: true });
    });
})();

// --- Sidebar相关代码已移除 ---
// 注意：sidebar元素本身需要保留（虽然隐藏），因为app.js中的功能逻辑仍需要使用sidebar内的按钮元素
const sidebar = document.getElementById('sidebar');

// --- 初始化 ---
document.addEventListener('DOMContentLoaded', () => {
    setupResizableChatContainer();

    // 设置初始按钮状态 - 聊天框
    if (chatContainer && toggleBtn) {
        // 获取图标元素（HTML中应该已经有img标签）
        let iconImg = toggleBtn.querySelector('img');
        if (!iconImg) {
            // 如果没有图标，创建一个
            iconImg = document.createElement('img');
            iconImg.style.width = '32px';  /* 图标尺寸 */
            iconImg.style.height = '32px';  /* 图标尺寸 */
            iconImg.style.objectFit = 'contain';
            iconImg.style.pointerEvents = 'none'; /* 确保图标不干扰点击事件 */
            toggleBtn.innerHTML = '';
            toggleBtn.appendChild(iconImg);
        }
        
        if (isCollapsed()) {
            // 最小化状态，显示展开图标（加号）
            iconImg.src = '/static/icons/expand_icon_off.png';
            iconImg.alt = window.t ? window.t('common.expand') : '展开';
            toggleBtn.title = window.t ? window.t('common.expand') : '展开';
        } else {
            // 展开状态，显示最小化图标（减号）
            iconImg.src = '/static/icons/expand_icon_off.png';
            iconImg.alt = window.t ? window.t('common.minimize') : '最小化';
            toggleBtn.title = window.t ? window.t('common.minimize') : '最小化';
            scrollToBottom(); // 初始加载时滚动一次
        }
    }

    // 确保自动滚动在页面加载后生效
    scrollToBottom();
});

// 监听 DOM 变化，确保新内容添加后自动滚动
const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
        if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
            scrollToBottom();
        }
    });
});

// 开始观察聊天内容区域的变化
if (chatContentWrapper) {
    observer.observe(chatContentWrapper, {childList: true, subtree: true});
}

// ========== Electron 全局快捷键接口 ==========
// 以下接口供 Electron 主进程通过 IPC 调用，用于全局快捷键功能

/**
 * 切换语音会话状态（开始/结束）
 * Electron 调用此接口来触发语音按钮的切换
 */
window.toggleVoiceSession = function() {
    // 获取浮动按钮的当前状态
    const micButton = window.live2dManager?._floatingButtons?.mic?.button;
    const isActive = micButton?.dataset.active === 'true';
    
    // 派发切换事件
    const event = new CustomEvent('live2d-mic-toggle', {
        detail: { active: !isActive }
    });
    window.dispatchEvent(event);
    
    console.log('[Electron Shortcut] toggleVoiceSession:', !isActive ? 'start' : 'stop');
};

/**
 * 切换屏幕分享状态（开始/结束）
 * Electron 调用此接口来触发屏幕分享按钮的切换
 */
window.toggleScreenShare = function() {
    // 获取浮动按钮的当前状态
    const screenBtn = window.live2dManager?._floatingButtons?.screen?.button;
    const isActive = screenBtn?.dataset.active === 'true';
    const isRecording = window.isRecording || false;
    
    // 屏幕分享仅在语音会话中有效
    // 如果尝试开启屏幕分享但语音会话未开启，显示提示并阻止操作
    if (!isActive && !isRecording) {
        console.log('[Electron Shortcut] toggleScreenShare: blocked - voice session not active');
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(
                window.t ? window.t('app.screenShareRequiresVoice') : '屏幕分享仅用于音视频通话',
                3000
            );
        }
        return;
    }
    
    // 派发切换事件
    const event = new CustomEvent('live2d-screen-toggle', {
        detail: { active: !isActive }
    });
    window.dispatchEvent(event);
    
    console.log('[Electron Shortcut] toggleScreenShare:', !isActive ? 'start' : 'stop');
};

/**
 * 触发截图功能
 * Electron 调用此接口来触发截图按钮点击
 */
window.triggerScreenshot = function() {
    // 语音会话中禁止截图（文本框处于禁用态时意味着用户处于语音会话中）
    if (window.isRecording) {
        console.log('[Electron Shortcut] triggerScreenshot: blocked - in voice session');
        return;
    }
    
    const screenshotButton = document.getElementById('screenshotButton');
    if (screenshotButton && !screenshotButton.disabled) {
        screenshotButton.click();
        console.log('[Electron Shortcut] triggerScreenshot: triggered');
    } else {
        console.log('[Electron Shortcut] triggerScreenshot: button disabled or not found');
    }
};
