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
let applyChatContainerSize = null;
let restoreChatContainerSize = null;
let getStoredChatContainerSize = null;

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
function addNewMessage(message) {
    if (!chatContentWrapper) return;

    // 【修改】如果是 Node 类型，直接进入容器，防止产生匿名的外层包裹 div 导致清理残留
    if (message instanceof Node) {
        chatContentWrapper.appendChild(message);
        scrollToBottom();
        return message;
    }

    // 字符串类型的消息维持原有的包裹逻辑
    const newMessageElement = document.createElement('div');
    if (typeof message === 'string') {
        newMessageElement.textContent = message;
    }
    
    newMessageElement.className = 'chat-message';
    chatContentWrapper.appendChild(newMessageElement);
    scrollToBottom();
    return newMessageElement;
}

// --- 整个对话区可拖拽缩放（输入区/按钮高度固定，历史区自适应） ---
function setupResizableChatContainer() {
    if (!chatContainer) return;

    const STORAGE_WIDTH_KEY = 'neko.chatContainerWidth';
    const STORAGE_HEIGHT_KEY = 'neko.chatContainerHeight';
    const BASE_WIDTH = 400;
    const BASE_HEIGHT = 500;
    const PHYSICAL_BREAKPOINT = 1920;
    const dpr = window.devicePixelRatio || 1;
    const physicalWidth = Math.round(window.innerWidth * dpr);
    const scaleFactor = physicalWidth > PHYSICAL_BREAKPOINT
        ? Math.min(1.4, physicalWidth / PHYSICAL_BREAKPOINT)
        : 1;
    const DEFAULT_WIDTH = Math.max(BASE_WIDTH, Math.min(
        Math.round(BASE_WIDTH * scaleFactor),
        Math.round(window.innerWidth * 0.28)
    ));
    const DEFAULT_HEIGHT = Math.max(BASE_HEIGHT, Math.min(
        Math.round(BASE_HEIGHT * scaleFactor),
        Math.round(window.innerHeight * 0.55)
    ));
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
    const readStoredSize = () => {
        let savedW = NaN;
        let savedH = NaN;
        try {
            savedW = Number(localStorage.getItem(STORAGE_WIDTH_KEY));
            savedH = Number(localStorage.getItem(STORAGE_HEIGHT_KEY));
        } catch (_) {
            /* localStorage 不可用时忽略 */
        }
        if (Number.isFinite(savedW) && Number.isFinite(savedH) && savedW > 0 && savedH > 0) {
            return { width: savedW, height: savedH };
        }
        return null;
    };
    // 从 localStorage 恢复容器尺寸
    const restoreContainerSize = () => {
        const stored = readStoredSize();
        if (stored) {
            applyContainerSize(stored.width, stored.height);
            return stored;
        }
        applyContainerSize(DEFAULT_WIDTH, DEFAULT_HEIGHT);
        return null;
    };

    applyChatContainerSize = applyContainerSize;
    restoreChatContainerSize = restoreContainerSize;
    getStoredChatContainerSize = readStoredSize;

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
            // 移动端：折叠时隐藏所有内容，仅保留切换按钮
            if (uiIsMobileWidth()) {
                const becomingCollapsed = !chatContainer.classList.contains('mobile-collapsed');
                const textInputArea = document.getElementById('text-input-area');
                const chatHeader = document.getElementById('chat-header');
                if (becomingCollapsed) {
                    if (chatContentWrapper) {
                        chatContentWrapper.dataset.prevDisplay = chatContentWrapper.style.display;
                        chatContentWrapper.style.display = 'none';
                    }
                    if (chatHeader) {
                        chatHeader.dataset.prevDisplay = chatHeader.style.display;
                        chatHeader.style.display = 'none';
                    }
                    if (textInputArea) {
                        textInputArea.dataset.prevDisplay = textInputArea.style.display;
                        textInputArea.style.display = 'none';
                    }
                    chatContainer.classList.add('mobile-collapsed');
                    if (toggleBtn) {
                        toggleBtn.style.display = 'block';
                        toggleBtn.style.visibility = 'visible';
                        toggleBtn.style.opacity = '1';
                    }
                } else {
                    chatContainer.classList.remove('mobile-collapsed');
                    if (chatContentWrapper) {
                        const prev = chatContentWrapper.dataset.prevDisplay;
                        if (prev) { chatContentWrapper.style.display = prev; } else { chatContentWrapper.style.removeProperty('display'); }
                        delete chatContentWrapper.dataset.prevDisplay;
                    }
                    if (chatHeader) {
                        const prev = chatHeader.dataset.prevDisplay;
                        if (prev) { chatHeader.style.display = prev; } else { chatHeader.style.removeProperty('display'); }
                        delete chatHeader.dataset.prevDisplay;
                    }
                    if (textInputArea) {
                        const prev = textInputArea.dataset.prevDisplay;
                        if (prev) { textInputArea.style.display = prev; } else { textInputArea.style.removeProperty('display'); }
                        delete textInputArea.dataset.prevDisplay;
                    }
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

            const wasMinimized = chatContainer.classList.contains('minimized');
            const willMinimize = !wasMinimized;
            if (wasMinimized && getStoredChatContainerSize && applyChatContainerSize) {
                const stored = getStoredChatContainerSize();
                if (stored) {
                    applyChatContainerSize(stored.width, stored.height);
                }
            }
            if (willMinimize) {
                const rect = chatContainer.getBoundingClientRect();
                const targetSize = 50;
                const scaleX = rect.width > 0 ? Math.min(1, targetSize / rect.width) : 1;
                const scaleY = rect.height > 0 ? Math.min(1, targetSize / rect.height) : 1;
                
                chatContainer.style.setProperty('--chat-collapse-scale-x', '1');
                chatContainer.style.setProperty('--chat-collapse-scale-y', '1');
                chatContainer.classList.add('collapsing');
                
                void chatContainer.offsetHeight;
                
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        chatContainer.style.setProperty('--chat-collapse-scale-x', String(scaleX));
                        chatContainer.style.setProperty('--chat-collapse-scale-y', String(scaleY));
                    });
                });
                
                let handled = false;
                const finishCollapse = () => {
                    if (handled) return;
                    handled = true;
                    chatContainer.removeEventListener('transitionend', onCollapseEnd);
                    chatContainer.classList.remove('collapsing');
                    chatContainer.classList.add('minimized');
                    chatContainer.style.removeProperty('--chat-collapse-scale-x');
                    chatContainer.style.removeProperty('--chat-collapse-scale-y');
                };
                const onCollapseEnd = (e) => {
                    if (e.target !== chatContainer) return;
                    if (e.propertyName !== 'transform') return;
                    finishCollapse();
                };
                chatContainer.addEventListener('transitionend', onCollapseEnd);
                
                const transitionDuration = 350;
                setTimeout(() => {
                    finishCollapse();
                }, transitionDuration);
            } else {
                chatContainer.classList.remove('minimized');
                chatContainer.classList.remove('collapsing');
                chatContainer.style.removeProperty('--chat-collapse-scale-x');
                chatContainer.style.removeProperty('--chat-collapse-scale-y');
                if (chatContainer.classList.length === 0) {
                    chatContainer.removeAttribute('class');
                }
            }
            
            const isMinimized = willMinimize;
            
            // 获取图标元素（HTML中应该已经有img标签）
            let iconImg = toggleBtn.querySelector('img');
            if (!iconImg) {
                // 如果没有图标，创建一个
                iconImg = document.createElement('img');
                iconImg.style.width = '32px';  /* 图标尺寸 */
                iconImg.style.height = '32px';  /* 图标尺寸 */
                iconImg.style.objectFit = 'contain'; // 修复：与原生初始化保持一致，防止图标被裁剪
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
// --- 【新增：APlayer UI 美化样式】 ---
    if (!document.getElementById('aplayer-custom-style')) {
        const aplayerStyle = document.createElement('style');
        aplayerStyle.id = 'aplayer-custom-style';
        aplayerStyle.textContent = `
            /* 1. 容器悬浮与毛玻璃质感 */
            .music-msg-container .aplayer {
                border-radius: 12px !important;
                background: rgba(55, 53, 53, 0.65) !important;
                backdrop-filter: blur(15px);
                -webkit-backdrop-filter: blur(15px);
                box-shadow: 0 10px 30px rgba(70, 64, 64, 0.2);
                border: 1px solid rgba(255, 255, 255, 0.1);
                font-family: inherit !important;
                overflow: hidden;
                margin-bottom: 5px;
                min-height: 158px !important;
                max-height: 158px !important;
                height: 158px !important;
            }

            /* 适配浅色主题 */
            [data-theme="light"] .music-msg-container .aplayer,
            .aplayer-theme-light .aplayer {
                background: rgba(255, 255, 255, 0.75) !important;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
                border: 1px solid rgba(0, 0, 0, 0.05);
            }

            /* 2. 封面图圆角内缩设计 */
            .music-msg-container .aplayer .aplayer-pic {
                border-radius: 8px;
                margin: 6px;
                height: calc(100% - 12px) !important;
                width: 60px !important;
                box-shadow: 0 4px 10px rgba(0,0,0,0.2);
                min-height: 134px !important;
                max-height: 134px !important;
            }

            /* 3. 封面区域固定 */
            .music-msg-container .aplayer .aplayer-pic .aplayer-pic-icon-wrap {
                width: 100% !important;
                height: 100% !important;
            }

            /* 4. 信息区域高度限制，防止文字过长影响整体高度 */
            .music-msg-container .aplayer .aplayer-info {
                height: 100% !important;
                min-height: 134px !important;
            }

            /* 3. 进度条主题色：呼应你聊天框的专属蓝色 (#44b7fe) */
            .music-msg-container .aplayer .aplayer-info .aplayer-controller .aplayer-bar-wrap .aplayer-bar .aplayer-played {
                background: #44b7fe !important;
            }
            .music-msg-container .aplayer .aplayer-info .aplayer-controller .aplayer-bar-wrap .aplayer-bar .aplayer-played .aplayer-thumb {
                background: #44b7fe !important;
                box-shadow: 0 0 6px rgba(68, 183, 254, 0.8) !important;
                transform: scale(1.2);
            }

            /* 4. 歌词与文字样式优化 */
            .music-msg-container .aplayer .aplayer-info .aplayer-music .aplayer-title {
                font-weight: 600;
                font-size: 15px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                max-width: 160px;
            }
            .music-msg-container .aplayer .aplayer-info .aplayer-music .aplayer-author {
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                max-width: 120px;
            }
            .music-msg-container .aplayer .aplayer-lrc p {
                color: #fff;
                text-shadow: 0 1px 2px rgba(0,0,0,0.5);
            }
            [data-theme="light"] .music-msg-container .aplayer .aplayer-lrc p {
                color: #333;
                text-shadow: none;
            }
        `;
        document.head.appendChild(aplayerStyle);
    }
    // --- 【美化样式结束】 ---

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

// ========== 音乐聊天气泡功能 ==========
// 记录当前正在播放的音乐信息，用于去重
let currentPlayingTrack = null;

// 统一的 APlayer 实例获取函数
const getMusicPlayerInstance = () => {
    // 优先使用 window.aplayerInjected（推荐方式）
    if (window.aplayerInjected && window.aplayerInjected.aplayer) {
        return window.aplayerInjected.aplayer;
    }
    // 回退到 window.aplayer（旧方式）
    if (window.aplayer) {
        return window.aplayer;
    }
    return null;
};

// 统一的停止音乐函数（只暂停，保留实例）
const stopMusicPlayer = () => {
    const player = getMusicPlayerInstance();
    if (player && typeof player.pause === 'function') {
        player.pause();
    }
};

// 检查是否需要清理旧音乐实例
// 条件：音乐气泡之前的消息数 > 10 且播放器未播放
const shouldCleanupOldMusicPlayer = () => {
    const player = getMusicPlayerInstance();
    if (!player) return false;
    
    // 播放器正在播放，不清理
    if (!player.paused) return false;
    
    // 查找音乐气泡
    const musicBubble = document.querySelector('.music-bubble');
    if (!musicBubble) return false;
    
    // 统计音乐气泡之前的消息数量
    const allMessages = chatContentWrapper ? chatContentWrapper.children : [];
    let messageCountBeforeMusic = 0;
    
    for (let i = 0; i < allMessages.length; i++) {
        const msg = allMessages[i];
        // 检查是否是音乐气泡或其父元素
        if (msg.contains(musicBubble) || msg === musicBubble) {
            break;
        }
        // 只计算实际的消息元素
        if (msg.querySelector && (msg.querySelector('.chat-message') || msg.classList?.contains('chat-message'))) {
            messageCountBeforeMusic++;
        }
    }
    
    return messageCountBeforeMusic > 10;
};

// 统一的销毁音乐函数（完全销毁实例，释放资源）
const destroyMusicPlayer = () => {
    if (typeof window.destroyAPlayer === 'function') {
        window.destroyAPlayer();
    } else {
        const player = getMusicPlayerInstance();
        if (player) {
            if (typeof player.pause === 'function') player.pause();
            if (typeof player.destroy === 'function') player.destroy();
        }
        if (window.aplayer) window.aplayer = null;
        if (window.aplayerInjected && window.aplayerInjected.aplayer) {
            window.aplayerInjected.aplayer = null;
        }
    }
    // 移除音乐消息根节点（整块移除，避免残留）
    document.querySelectorAll('.music-message-root').forEach(root => {
        root.remove();
    });
    currentPlayingTrack = null;
};

// APlayer 库加载单例 Promise
let aplayerLoadPromise = null;
// 当前最新的音乐请求 token，用于取消过期请求
let latestMusicRequestToken = 0;

window.sendMusicMessage = function(trackInfo) {
    // --- 【新增：前置安全校验】将原 showMusicPlayer 内部的校验移到最开始 ---
    const isSafeUrl = (url) => {
        if (!url) return false;
        try {
            const parsed = new URL(url);
            const allowedProtocols = ['http:', 'https:'];
            const allowedDomains = [
        'cdn.jsdelivr.net', 'i.scdn.co', 'p.scdn.co', 'a.scdn.co',
        'via.placeholder.com', 'i.imgur.com', 'y.qq.com',
        'music.126.net', 'p1.music.126.net', 'p2.music.126.net', 'p3.music.126.net',
        'm7.music.126.net', 'm8.music.126.net', 'm9.music.126.net',
        'mmusic.spriteapp.cn', 'gg.spriteapp.cn',
        'freemusicarchive.org', 'musopen.org', 'bandcamp.com', 'bcbits.com', 'soundcloud.com', 'sndcdn.com',
        'itunes.apple.com', 'audio-ssl.itunes.apple.com',
        'dummyimage.com', 'music.163.com'
            ];
            if (!allowedProtocols.includes(parsed.protocol)) return false;
            // 严格限制全等匹配或合法子域名匹配，防止 SSRF 域名绕过
            if (!allowedDomains.some(d => parsed.hostname === d || parsed.hostname.endsWith('.' + d))) return false;
            return true;
        } catch {
            return false;
        }
    };

    // 1. 最先检查 URL 合法性，不合法直接驳回，绝不打断当前正在播放的音乐
    if (!trackInfo.url || !isSafeUrl(trackInfo.url)) {
        console.warn('[Common UI] 音频 URL 未通过安全校验，拒绝播放:', trackInfo.url);
        return false;
    }

    // 2. 检查是否需要清理闲置的旧音乐播放器实例（防止未播放的DOM无限堆积）
    if (shouldCleanupOldMusicPlayer()) {
        console.log('[Common UI] 音乐气泡之前消息数超过10条且播放器未播放，销毁旧实例');
        destroyMusicPlayer();
    }
    
    const trackName = trackInfo.name || '未知曲目';
    const artistName = trackInfo.artist || '未知艺术家';
    
    const shouldCreateNewPlayer = () => {
        if (!currentPlayingTrack) return true;
        const player = getMusicPlayerInstance();
        if (player && !player.paused) {
            console.log('[Common UI] 已有音乐正在播放，不创建新播放器');
            return false;
        }
        return true;
    };
    
    const isSameTrack = (info) => {
        return currentPlayingTrack && 
               currentPlayingTrack.name === info.name && 
               currentPlayingTrack.artist === info.artist;
    };

    const isPlayerInDOM = () => {
        const player = getMusicPlayerInstance();
        return player && player.container && document.body.contains(player.container);
    };
    
    // 如果是同一首歌且气泡还在，复用现有实例
    const player = getMusicPlayerInstance();
    if (isSameTrack(trackInfo)) {
        if (isPlayerInDOM()) {
            console.log('[Common UI] 相同歌曲且气泡存在，复用现有实例');
            if (player && player.paused) {
                player.play();
            }
            return true;
        } else {
            console.log('[Common UI] 相同歌曲但气泡已被清理，彻底重置并重建');
            destroyMusicPlayer(); 
            currentPlayingTrack = null; 
        }
    }
    
    const loadAPlayerLibrary = () => {
        if (aplayerLoadPromise) return aplayerLoadPromise;
        
        aplayerLoadPromise = new Promise((resolve, reject) => {
            if (typeof APlayer !== 'undefined') {
                resolve();
                return;
            }
            
            if (!document.querySelector('link[href*="APlayer.min.css"]')) {
                const cssLink = document.createElement('link');
                cssLink.rel = 'stylesheet';
                cssLink.href = '/static/libs/APlayer.min.css';
                cssLink.onerror = () => console.error('[Common UI] APlayer CSS 加载失败');
                document.head.appendChild(cssLink);
            }
            
            const existingScript = document.querySelector('script[src*="APlayer.min.js"]');
            if (!existingScript) {
                const script = document.createElement('script');
                script.src = '/static/libs/APlayer.min.js';
                script.onload = () => {
                    console.log('[Common UI] APlayer 库加载成功 (local)');
                    resolve();
                };
                script.onerror = () => {
                    console.error('[Common UI] APlayer JS 加载失败');
                    aplayerLoadPromise = null;
                    reject(new Error('APlayer 库加载失败'));
                };
                document.head.appendChild(script);
            } else {
                if (typeof APlayer !== 'undefined') {
                    resolve();
                } else {
                    const onLoad = () => { cleanup(); resolve(); };
                    const onError = () => {
                        cleanup();
                        aplayerLoadPromise = null; 
                        reject(new Error('APlayer 库加载失败'));
                    };
                    const fallbackTimer = setTimeout(() => {
                        cleanup();
                        if (typeof APlayer !== 'undefined') {
                            resolve();
                        } else {
                            aplayerLoadPromise = null; 
                            reject(new Error('APlayer 加载超时'));
                        }
                    }, 5000);

                    const cleanup = () => {
                        existingScript.removeEventListener('load', onLoad);
                        existingScript.removeEventListener('error', onError);
                        clearTimeout(fallbackTimer);
                    };

                    existingScript.addEventListener('load', onLoad);
                    existingScript.addEventListener('error', onError);
                }
            }
        });
        return aplayerLoadPromise;
    };
    
    // 为本次请求生成唯一 token (移到顶层，让闭包更早捕获)
    const currentToken = ++latestMusicRequestToken;

    // --- 【核心修复：将销毁旧实例和写入状态延后至真正开始渲染前】 ---
    const executePlay = async () => {
        // 第一道防线：如果排队期间被新请求挤掉，直接放弃
        if (currentToken !== latestMusicRequestToken) {
            console.log('[Common UI] 请求已过期，取消播放执行');
            return;
        }

        // 此时 APlayer 库已就绪，且 URL 已通过校验
        // 安全地销毁旧实例并写入当前播放状态
        if (getMusicPlayerInstance() || shouldCreateNewPlayer()) {
            console.log('[Common UI] 准备切换新歌曲，正在回收旧气泡...');
            destroyMusicPlayer();
        }
        currentPlayingTrack = trackInfo;

        const playerId = 'music-msg-' + Date.now() + '-' + Math.floor(Math.random() * 10000);
        const colors = ['#667eea', '#764ba2', '#f093fb', '#f5576c', '#4facfe', '#00f2fe', '#a8edea', '#fed6e3'];
        const randomColor = colors[Math.floor(Math.random() * colors.length)];
        
        console.log('[Music] trackInfo:', trackInfo);
        
        const hasCover = trackInfo.cover && trackInfo.cover.length > 0 && isSafeUrl(trackInfo.cover);
        
        const messageDiv = document.createElement('div');
        messageDiv.className = 'chat-message bot-message music-bubble';
        messageDiv.style.cssText = 'display: inline-flex; align-items: center; gap: 12px; padding: 10px 14px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 16px; border: 1px solid rgba(255,255,255,0.1); margin-top: 8px;';
        
        const coverDiv = document.createElement('div');
        coverDiv.className = 'music-cover';
        coverDiv.style.cssText = 'width: 48px; height: 48px; border-radius: 10px; background: linear-gradient(135deg, ' + randomColor + ', #667eea); display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; box-shadow: 0 2px 8px rgba(0,0,0,0.3); overflow: hidden;';
        
        if (hasCover) {
            const coverImg = document.createElement('img');
            coverImg.src = trackInfo.cover;
            coverImg.style.cssText = 'width: 100%; height: 100%; object-fit: cover; border-radius: 10px;';
            coverImg.onerror = function() {
                this.parentElement.style.background = 'linear-gradient(135deg, ' + randomColor + ', #667eea)';
                this.style.display = 'none';
                this.nextElementSibling.style.display = 'flex';
            };
            const coverSpan = document.createElement('span');
            coverSpan.textContent = '🎵';
            coverSpan.style.display = 'none';
            coverDiv.appendChild(coverImg);
            coverDiv.appendChild(coverSpan);
        } else {
            const coverSpan = document.createElement('span');
            coverSpan.textContent = '🎵';
            coverDiv.appendChild(coverSpan);
        }
        
        const infoDiv = document.createElement('div');
        infoDiv.className = 'music-info';
        infoDiv.style.cssText = 'flex: 1; min-width: 0; overflow: hidden;';
        
        const titleDiv = document.createElement('div');
        titleDiv.className = 'music-title';
        titleDiv.style.cssText = 'color: #fff; font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px;';
        titleDiv.textContent = trackName;
        
        const artistDiv = document.createElement('div');
        artistDiv.className = 'music-artist';
        artistDiv.style.cssText = 'color: rgba(255,255,255,0.6); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;';
        artistDiv.textContent = artistName;
        
        infoDiv.appendChild(titleDiv);
        infoDiv.appendChild(artistDiv);
        
        const playBtn = document.createElement('button');
        playBtn.className = 'music-play-btn';
        playBtn.id = playerId + '-play';
        playBtn.textContent = '▶';
        playBtn.style.cssText = 'width: 36px; height: 36px; border-radius: 50%; border: none; background: linear-gradient(135deg, #667eea, #764ba2); color: white; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; box-shadow: 0 2px 6px rgba(102, 126, 234, 0.4); transition: transform 0.2s;';
        
        const playerContainer = document.createElement('div');
        playerContainer.id = playerId;
        playerContainer.className = 'music-msg-container';
        playerContainer.style.display = 'none';
        
        messageDiv.appendChild(coverDiv);
        messageDiv.appendChild(infoDiv);
        messageDiv.appendChild(playBtn);
        messageDiv.appendChild(playerContainer);
        
        let globalStyle = document.getElementById('music-player-global-style');
        if (!globalStyle) {
            globalStyle = document.createElement('style');
            globalStyle.id = 'music-player-global-style';
            globalStyle.textContent = `
                .music-bubble + .music-bubble { margin-top: 2px !important; }
                .music-bubble button.music-play-btn:hover { transform: scale(1.1); }
                .music-bubble button.music-play-btn:active { transform: scale(0.95); }
            `;
            document.head.appendChild(globalStyle);
        }
        
        const tempDiv = document.createElement('div');
        tempDiv.className = 'music-message-root';
        tempDiv.appendChild(messageDiv);
        addNewMessage(tempDiv);
        
        let aplayerInstance = null;
        
        requestAnimationFrame(() => {
            requestAnimationFrame(async () => {
                // 第二道防线：DOM 渲染的几帧内，如果来了新歌，终止挂载
                if (currentToken !== latestMusicRequestToken) {
                    console.log('[Common UI] 渲染期间请求已过期，终止挂载');
                    return;
                }

                const container = document.getElementById(playerId);
                const playBtn = document.getElementById(playerId + '-play');

                if (!container) {
                    console.error('[Common UI] 音乐气泡挂载点不存在: playerId=%s', playerId);
                    if (tempDiv && tempDiv.parentNode) {
                        tempDiv.parentNode.removeChild(tempDiv);
                    }
                    if (currentToken === latestMusicRequestToken) {
                        currentPlayingTrack = null;
                    }
                } else if (typeof window.initializeAPlayer === 'function' || typeof APlayer !== 'undefined') {
                    try {
                        const audioConfig = [{
                            name: trackName,
                            artist: artistName,
                            url: trackInfo.url,
                            cover: hasCover ? trackInfo.cover : ''
                        }];

                        if (typeof window.initializeAPlayer === 'function') {
                            aplayerInstance = await window.initializeAPlayer({ 
                                container: container,
                                theme: '#667eea',
                                loop: 'none',
                                preload: 'none',
                                mutex: true,
                                volume: 0.7,
                                listFolded: true,
                                order: 'normal',
                                audio: audioConfig
                            });
                        } else {
                            aplayerInstance = new APlayer({
                                container: container,
                                theme: '#667eea',
                                loop: 'none',
                                preload: 'none',
                                mutex: true,
                                volume: 0.7,
                                listFolded: true,
                                order: 'normal',
                                audio: audioConfig
                            });
                        }
                        
                        if (!aplayerInstance) {
                            throw new Error("APlayer instance is null after initialization");
                        }
                        
                        if (currentToken !== latestMusicRequestToken) {
                            console.log('[Common UI] APlayer 初始化后发现请求过期，销毁实例');
                            if (typeof window.destroyAPlayer === 'function' && window.aplayer === aplayerInstance) {
                                window.destroyAPlayer();
                            } else if (typeof aplayerInstance.destroy === 'function') {
                                aplayerInstance.destroy();
                            }
                            return;
                        }

                        if (!window.aplayerInjected) window.aplayerInjected = {};
                        window.aplayerInjected.aplayer = aplayerInstance;

                        if (playBtn) {
                            playBtn.addEventListener('click', () => {
                                if (aplayerInstance) {
                                    if (aplayerInstance.paused) {
                                        aplayerInstance.play();
                                        playBtn.textContent = '⏸';
                                    } else {
                                        aplayerInstance.pause();
                                        playBtn.textContent = '▶';
                                    }
                                }
                            });
                        }

                        aplayerInstance.on('play', () => {
                            if (playBtn) playBtn.textContent = '⏸';
                        });
                        aplayerInstance.on('pause', () => {
                            if (playBtn) playBtn.textContent = '▶';
                        });
                        
                        aplayerInstance.on('ended', () => {
                            if (playBtn) playBtn.textContent = '▶';
                        });

                        const apElement = container.querySelector('.aplayer');
                        if (apElement) {
                            apElement.style.display = 'none';
                        }
                        
                    } catch (err) {
                        console.error('[Common UI] 音乐气泡初始化失败，正在回滚:', err);
                        if (tempDiv && tempDiv.parentNode) {
                            tempDiv.parentNode.removeChild(tempDiv);
                        }
                        if (currentToken === latestMusicRequestToken) {
                            currentPlayingTrack = null;
                        }
                        if (window.showStatusToast) {
                            const errMsg = window.safeT ? window.safeT('music.playError', '音乐播放加载失败') : '音乐播放加载失败';
                            window.showStatusToast(errMsg, 3000);
                        }
                    }
                } else {
                    console.error('[Common UI] APlayer 库未加载，无法创建音乐气泡');
                    if (tempDiv && tempDiv.parentNode) {
                        tempDiv.parentNode.removeChild(tempDiv);
                    }
                    if (currentToken === latestMusicRequestToken) {
                        currentPlayingTrack = null;
                    }
                }
            });
        });
    };

    if (typeof APlayer === 'undefined') {
        console.log('[Common UI] APlayer 库未加载，正在动态加载...');
        loadAPlayerLibrary().then(() => {
            if (currentToken !== latestMusicRequestToken) {
                console.log('[Common UI] 脚本下载期间请求已过期，跳过渲染');
                return;
            }
            executePlay();
        }).catch(err => {
            console.error('[Common UI] APlayer 库加载失败，中止操作:', err);
        });
        return true;
    } else {
        executePlay();
        return true;
    }
};