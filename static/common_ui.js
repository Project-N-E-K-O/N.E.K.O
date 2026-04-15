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
    let resizeRAFId = null;
    let startX = 0;
    let startY = 0;
    let startWidth = 0;
    let startHeight = 0;
    let startBottom = 0;
    let pendingResizeClientX = 0;
    let pendingResizeClientY = 0;

    // rAF 回调：批量应用 resize 尺寸
    const applyResizeFrame = () => {
        resizeRAFId = null;
        const nextWidth = startWidth + (pendingResizeClientX - startX);
        const rawNextHeight = startHeight + (pendingResizeClientY - startY);
        // 当底边触达屏幕底部后，继续向下拖拽不再增高（顶部保持锚定）
        const bottomLimitedMaxHeight = startHeight + Math.max(0, startBottom);
        const nextHeight = Math.min(rawNextHeight, bottomLimitedMaxHeight);
        const applied = applyContainerSize(nextWidth, nextHeight);
        // chat-container 采用 bottom 定位；同步调整 bottom 让垂直拉伸表现为"向下展开"
        const consumedDeltaY = applied.height - startHeight;
        chatContainer.style.bottom = `${Math.max(0, startBottom - consumedDeltaY)}px`;
    };

    // 处理调整大小移动事件（仅记录位置，通过 rAF 合并更新）
    const onResizeMove = (e) => {
        if (!isResizing) return;
        const clientX = e.type.startsWith('touch') ? e.touches[0].clientX : e.clientX;
        const clientY = e.type.startsWith('touch') ? e.touches[0].clientY : e.clientY;
        pendingResizeClientX = clientX;
        pendingResizeClientY = clientY;
        if (!resizeRAFId) {
            resizeRAFId = requestAnimationFrame(applyResizeFrame);
        }
        e.preventDefault();
    };

    // 动态绑定/解绑全局 resize 事件监听器
    const bindResizeListeners = () => {
        document.addEventListener('mousemove', onResizeMove);
        document.addEventListener('touchmove', onResizeMove, { passive: false });
        document.addEventListener('mouseup', stopResize);
        document.addEventListener('touchend', stopResize);
    };
    const unbindResizeListeners = () => {
        document.removeEventListener('mousemove', onResizeMove);
        document.removeEventListener('touchmove', onResizeMove);
        document.removeEventListener('mouseup', stopResize);
        document.removeEventListener('touchend', stopResize);
    };

    // 处理调整大小结束事件
    const stopResize = () => {
        if (!isResizing) return;
        // 取消待执行的 rAF，立即应用最终尺寸
        if (resizeRAFId) {
            cancelAnimationFrame(resizeRAFId);
            resizeRAFId = null;
            applyResizeFrame();
        }
        isResizing = false;
        chatContainer.classList.remove('is-resizing');
        unbindResizeListeners();
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
        pendingResizeClientX = point.clientX;
        pendingResizeClientY = point.clientY;
        const computedStyle = window.getComputedStyle(chatContainer);
        const parsedBottom = parseFloat(computedStyle.bottom);
        startBottom = Number.isFinite(parsedBottom) ? parsedBottom : (window.innerHeight - rect.bottom);

        // 动态绑定全局事件
        bindResizeListeners();

        e.stopPropagation();
        e.preventDefault();
    };
    // 绑定调整大小启动事件（仅 handle 上绑定 mousedown/touchstart）
    resizeHandle.addEventListener('mousedown', startResize);
    resizeHandle.addEventListener('touchstart', startResize, { passive: false });

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
                    // 折叠后执行回弹，避免位置越界
                    triggerExpandSnap();
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
                // 展开动画：从最小化尺寸过渡到完整尺寸
                const targetSize = 50;
                // 计算初始 scale（从最小尺寸到完整尺寸的逆向）
                const storedSize = getStoredChatContainerSize ? getStoredChatContainerSize() : null;
                const targetW = storedSize ? storedSize.width : 400;
                const targetH = storedSize ? storedSize.height : 500;
                const startScaleX = targetW > 0 ? Math.min(1, targetSize / targetW) : 1;
                const startScaleY = targetH > 0 ? Math.min(1, targetSize / targetH) : 1;

                chatContainer.classList.remove('minimized');
                chatContainer.classList.remove('collapsing');

                // 设置初始 scale 并添加展开类
                chatContainer.style.setProperty('--chat-expand-scale-x', String(startScaleX));
                chatContainer.style.setProperty('--chat-expand-scale-y', String(startScaleY));
                chatContainer.classList.add('expanding');

                // 强制 reflow，确保初始状态被渲染
                void chatContainer.offsetHeight;

                // 在下一帧设置目标 scale 为 1，触发动画
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        chatContainer.style.setProperty('--chat-expand-scale-x', '1');
                        chatContainer.style.setProperty('--chat-expand-scale-y', '1');
                    });
                });

                // 动画结束后清理
                let expandHandled = false;
                const finishExpand = () => {
                    if (expandHandled) return;
                    expandHandled = true;
                    chatContainer.removeEventListener('transitionend', onExpandEnd);
                    chatContainer.classList.remove('expanding');
                    chatContainer.style.removeProperty('--chat-expand-scale-x');
                    chatContainer.style.removeProperty('--chat-expand-scale-y');
                    isTransitioning = false;
                };
                const onExpandEnd = (e) => {
                    if (e.target !== chatContainer || e.propertyName !== 'transform') return;
                    finishExpand();
                };
                chatContainer.addEventListener('transitionend', onExpandEnd);
                setTimeout(finishExpand, 400);
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
                // 折叠后执行回弹，避免位置越界
                triggerExpandSnap();
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
            // 折叠动画结束后清除过渡标志（展开动画由 finishExpand 清除）
            if (willMinimize) {
                setTimeout(() => { isTransitioning = false; }, 350);
            }
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
(function () {
    let isDragging = false;
    let hasMoved = false; // 用于判断是否发生了实际的移动
    let dragStartedFromToggleBtn = false; // 记录是否从 toggleBtn 开始拖动
    let startMouseX = 0; // 开始拖动时的鼠标X位置
    let startMouseY = 0; // 开始拖动时的鼠标Y位置
    let startContainerLeft = 0; // 开始拖动时容器的left值
    let startContainerBottom = 0; // 开始拖动时容器的bottom值
    let cachedEffectiveWidth = 0; // 拖动开始时缓存的宽度（避免拖动中反复触发 reflow）
    let cachedEffectiveHeight = 0; // 拖动开始时缓存的高度
    let dragRAFId = null; // 拖动时的 requestAnimationFrame ID
    let pendingDragClientX = 0; // 待处理的鼠标位置
    let pendingDragClientY = 0;

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
    // 多屏下 workArea/display 可能大于实际窗口像素（窗口还未跟上屏幕切换），
    // 直接用会导致聊天框被吸附到窗口外、被窗口边界裁切。
    // 因此 clamp 边界始终以 window.innerWidth/innerHeight 为上限，workArea 仅用来取更保守值。
    async function getDisplayWorkAreaSize() {
        let width = window.innerWidth;
        let height = window.innerHeight;

        if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
            try {
                const currentDisplay = await window.electronScreen.getCurrentDisplay();
                if (currentDisplay && currentDisplay.workArea) {
                    const waW = currentDisplay.workArea.width;
                    const waH = currentDisplay.workArea.height;
                    if (Number.isFinite(waW) && waW > 0) width = Math.min(width, waW);
                    if (Number.isFinite(waH) && waH > 0) height = Math.min(height, waH);
                } else if (currentDisplay && currentDisplay.width && currentDisplay.height) {
                    width = Math.min(width, currentDisplay.width);
                    height = Math.min(height, currentDisplay.height);
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
        let rect = chatContainer.getBoundingClientRect();
        if (isCollapsed() && toggleBtn) {
            const toggleRect = toggleBtn.getBoundingClientRect();
            if (toggleRect.width > 0 && toggleRect.height > 0) {
                rect = toggleRect;
            }
        }

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
        // 设置全局拖拽标志，供 preload 等跳过昂贵操作
        if (window.DragHelpers) window.DragHelpers.isDragging = true;
        dragStartedFromToggleBtn = (e.target === toggleBtn || toggleBtn.contains(e.target));

        // 获取初始鼠标/触摸位置
        const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
        const clientY = e.type.includes('touch') ? e.touches[0].clientY : e.clientY;

        // 记录开始时的鼠标位置
        startMouseX = clientX;
        startMouseY = clientY;
        // 同步初始化 pending 坐标，防止 click-without-move 时
        // commitDragPosition() 使用过期值产生错误位移
        pendingDragClientX = clientX;
        pendingDragClientY = clientY;

        // 获取当前容器的实际位置（从计算样式中读取，确保准确）
        const computedStyle = window.getComputedStyle(chatContainer);
        startContainerLeft = parseFloat(computedStyle.left) || 0;
        startContainerBottom = parseFloat(computedStyle.bottom) || 0;

        // 缓存拖动期间不变的尺寸，避免每次 mousemove 触发 reflow
        cachedEffectiveWidth = chatContainer.offsetWidth;
        cachedEffectiveHeight = chatContainer.offsetHeight;
        if (isCollapsed() && toggleBtn) {
            const toggleRect = toggleBtn.getBoundingClientRect();
            if (toggleRect.width > 0 && toggleRect.height > 0) {
                cachedEffectiveWidth = toggleRect.width;
                cachedEffectiveHeight = toggleRect.height;
            }
        }

        // 添加拖动样式（禁用 backdrop-filter 等昂贵效果）
        chatContainer.classList.add('dragging');
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

    // 计算边界限制后的位移量
    function clampedDragDelta() {
        const deltaX = pendingDragClientX - startMouseX;
        const deltaY = pendingDragClientY - startMouseY;

        const newLeft = startContainerLeft + deltaX;
        const newBottom = startContainerBottom - deltaY;

        const maxLeft = window.innerWidth - cachedEffectiveWidth;
        const maxBottomRaw = window.innerHeight - cachedEffectiveHeight;
        const topBoundary = CHAT_SNAP_CONFIG.margin;
        const maxBottom = Math.max(0, maxBottomRaw - topBoundary);

        const clampedLeft = Math.max(0, Math.min(maxLeft, newLeft));
        const clampedBottom = Math.max(0, Math.min(maxBottom, newBottom));

        return {
            tx: clampedLeft - startContainerLeft,
            ty: -(clampedBottom - startContainerBottom), // transform Y 轴向下为正
            finalLeft: clampedLeft,
            finalBottom: clampedBottom
        };
    }

    // 实际执行位置更新（在 rAF 回调中调用，避免跳帧）
    // 使用 transform: translate() 移动，完全跳过 layout，仅走 GPU 合成
    function applyDragPosition() {
        dragRAFId = null;
        const { tx, ty } = clampedDragDelta();
        chatContainer.style.transform = `translate(${tx}px, ${ty}px)`;
    }

    // 拖动结束时，将 transform 位移落实到 left/bottom，并清除 transform
    function commitDragPosition() {
        const { finalLeft, finalBottom } = clampedDragDelta();
        chatContainer.style.transform = '';
        chatContainer.style.left = finalLeft + 'px';
        chatContainer.style.bottom = finalBottom + 'px';
    }

    // 移动中
    function onDragMove(e) {
        if (!isDragging) return;

        const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
        const clientY = e.type.includes('touch') ? e.touches[0].clientY : e.clientY;

        // 检查是否真的移动了（移动距离超过5px）
        const deltaX = clientX - startMouseX;
        const deltaY = clientY - startMouseY;
        if (!hasMoved && (deltaX * deltaX + deltaY * deltaY) > 25) {
            hasMoved = true;
        }

        // 记录最新鼠标位置，通过 rAF 合并更新，避免每个 mousemove 都触发重绘
        pendingDragClientX = clientX;
        pendingDragClientY = clientY;
        if (!dragRAFId) {
            dragRAFId = requestAnimationFrame(applyDragPosition);
        }
    }

    // 结束拖动
    function endDrag() {
        if (isDragging) {
            // 取消待执行的 rAF，将 transform 位移落实到 left/bottom
            if (dragRAFId) {
                cancelAnimationFrame(dragRAFId);
                dragRAFId = null;
            }
            commitDragPosition();

            const wasDragging = isDragging;
            const didMove = hasMoved;
            const fromToggleBtn = dragStartedFromToggleBtn;

            isDragging = false;
            hasMoved = false;
            dragStartedFromToggleBtn = false;
            chatContainer.classList.remove('dragging');
            chatContainer.style.cursor = '';
            if (chatHeader) chatHeader.style.cursor = '';

            // 清除全局拖拽标志
            if (window.DragHelpers) window.DragHelpers.isDragging = false;

            // 拖拽结束后恢复按钮的 pointer-events
            if (window.DragHelpers) {
                window.DragHelpers.restoreButtonPointerEvents();
            }

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
    // 延迟一帧：主进程 setBounds 到 renderer 的 innerWidth/innerHeight 刷新有几毫秒~一帧延迟，
    // 立即 snap 会让 getDisplayWorkAreaSize 读到旧尺寸，把聊天框永久 clamp 到旧屏内侧
    // （对照 live2d-core.js 中 _displayChangeHandler 的 rAF 包裹方式）
    window.addEventListener('electron-display-changed', () => {
        requestAnimationFrame(() => {
            snapChatContainerIntoScreen({ animate: true });
        });
    });

    // 窗口大小改变后，确保对话框回弹到屏幕内侧（包括折叠状态）
    let resizeSnapTimer = null;
    window.addEventListener('resize', () => {
        if (resizeSnapTimer) clearTimeout(resizeSnapTimer);
        resizeSnapTimer = setTimeout(() => {
            resizeSnapTimer = null;
            snapChatContainerIntoScreen({ animate: true });
        }, 200);
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
    observer.observe(chatContentWrapper, { childList: true, subtree: true });
}

// ========== Electron 全局快捷键接口 ==========
// 以下接口供 Electron 主进程通过 IPC 调用，用于全局快捷键功能

/**
 * 切换语音会话状态（开始/结束）
 * Electron 调用此接口来触发语音按钮的切换
 */
window.toggleVoiceSession = function () {
    // 获取浮动按钮的当前状态（Live2D / VRM / MMD）
    const micButton = window.live2dManager?._floatingButtons?.mic?.button
        || window.vrmManager?._floatingButtons?.mic?.button
        || window.mmdManager?._floatingButtons?.mic?.button;
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
window.toggleScreenShare = function () {
    // 获取浮动按钮的当前状态（Live2D / VRM / MMD）
    const screenBtn = window.live2dManager?._floatingButtons?.screen?.button
        || window.vrmManager?._floatingButtons?.screen?.button
        || window.mmdManager?._floatingButtons?.screen?.button;
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
window.triggerScreenshot = function () {
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
