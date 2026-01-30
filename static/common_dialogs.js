/**
 * 通用异步模态对话框系统
 * 用于替代 alert(), confirm(), prompt() 等同步弹窗
 * 适用于 Electron 环境
 */

(function() {
    'use strict';

    // 创建对话框样式
    const style = document.createElement('style');
    style.textContent = `
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
            animation: fadeIn 0.2s ease-out;
            pointer-events: auto !important;
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @keyframes slideIn {
            from { transform: translateY(-20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }

        .modal-dialog {
            background: white;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            min-width: 320px;
            max-width: 500px;
            max-height: 80vh;
            overflow: hidden;
            animation: slideIn 0.3s ease-out;
            pointer-events: auto !important;
        }

        .modal-header {
            padding: 20px 24px 16px;
            border-bottom: 1px solid #e0e0e0;
            pointer-events: auto !important;
        }

        .modal-title {
            margin: 0;
            font-size: 1.2rem;
            font-weight: 600;
            color: #222;
            pointer-events: auto !important;
        }

        .modal-body {
            padding: 20px 24px;
            color: #444;
            font-size: 1rem;
            line-height: 1.6;
            max-height: 60vh;
            overflow-y: auto;
            white-space: pre-wrap;
            pointer-events: auto !important;
        }

        .modal-input {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #ccc;
            border-radius: 6px;
            font-size: 1rem;
            margin-top: 12px;
            box-sizing: border-box;
            font-family: inherit;
            pointer-events: auto !important;
        }

        .modal-input:focus {
            outline: none;
            border-color: #4f8cff;
            box-shadow: 0 0 0 3px rgba(79, 140, 255, 0.1);
        }

        .modal-footer {
            padding: 16px 24px;
            border-top: 1px solid #e0e0e0;
            display: flex;
            justify-content: flex-end;
            gap: 10px;
            pointer-events: auto !important;
        }

        .modal-btn {
            padding: 8px 20px;
            border: none;
            border-radius: 6px;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.2s;
            font-weight: 500;
            pointer-events: auto !important;
        }

        .modal-btn:focus {
            outline: none;
            box-shadow: 0 0 0 3px rgba(79, 140, 255, 0.2);
        }

        .modal-btn-primary {
            background: #4f8cff;
            color: white;
        }

        .modal-btn-primary:hover {
            background: #3a7ae8;
        }

        .modal-btn-primary:active {
            background: #2662c8;
        }

        .modal-btn-secondary {
            background: #e0e0e0;
            color: #444;
        }

        .modal-btn-secondary:hover {
            background: #d0d0d0;
        }

        .modal-btn-secondary:active {
            background: #c0c0c0;
        }

        .modal-btn-danger {
            background: #e74c3c;
            color: white;
        }

        .modal-btn-danger:hover {
            background: #d43f2f;
        }

        .modal-btn-danger:active {
            background: #c0392b;
        }
    `;
    document.head.appendChild(style);

    /**
     * 创建模态对话框
     */
    function createModal(config) {
        return new Promise((resolve) => {
            // 创建遮罩层
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay';

            // 创建对话框
            const dialog = document.createElement('div');
            dialog.className = 'modal-dialog';

            // 创建标题
            if (config.title) {
                const header = document.createElement('div');
                header.className = 'modal-header';
                const title = document.createElement('h3');
                title.className = 'modal-title';
                title.textContent = config.title;
                header.appendChild(title);
                dialog.appendChild(header);
            }

            // 创建内容
            const body = document.createElement('div');
            body.className = 'modal-body';
            body.textContent = config.message;

            // 如果是 prompt 类型，添加输入框
            let input = null;
            if (config.type === 'prompt') {
                input = document.createElement('input');
                input.type = 'text';
                input.className = 'modal-input';
                input.value = config.defaultValue || '';
                input.placeholder = config.placeholder || '';
                body.appendChild(input);
            }

            dialog.appendChild(body);

            // 创建按钮区域
            const footer = document.createElement('div');
            footer.className = 'modal-footer';

            // 根据类型创建按钮
            if (config.type === 'alert') {
                const okBtn = document.createElement('button');
                okBtn.className = 'modal-btn modal-btn-primary';
                let okText = config.okText;
                if (!okText) {
                    try {
                        okText = (window.t && typeof window.t === 'function') ? window.t('common.ok') : '确定';
                    } catch (e) {
                        okText = '确定';
                    }
                }
                okBtn.textContent = okText;
                okBtn.onclick = () => {
                    closeModal();
                    resolve(true);
                };
                footer.appendChild(okBtn);
            } else if (config.type === 'confirm') {
                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'modal-btn modal-btn-secondary';
                let cancelText = config.cancelText;
                if (!cancelText) {
                    try {
                        cancelText = (window.t && typeof window.t === 'function') ? window.t('common.cancel') : '取消';
                    } catch (e) {
                        cancelText = '取消';
                    }
                }
                cancelBtn.textContent = cancelText;
                cancelBtn.onclick = () => {
                    closeModal();
                    resolve(false);
                };
                footer.appendChild(cancelBtn);

                const okBtn = document.createElement('button');
                okBtn.className = config.danger ? 'modal-btn modal-btn-danger' : 'modal-btn modal-btn-primary';
                let okText = config.okText;
                if (!okText) {
                    try {
                        okText = (window.t && typeof window.t === 'function') ? window.t('common.ok') : '确定';
                    } catch (e) {
                        okText = '确定';
                    }
                }
                okBtn.textContent = okText;
                okBtn.onclick = () => {
                    closeModal();
                    resolve(true);
                };
                footer.appendChild(okBtn);
            } else if (config.type === 'prompt') {
                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'modal-btn modal-btn-secondary';
                cancelBtn.textContent = config.cancelText || (window.t ? window.t('common.cancel') : '取消');
                cancelBtn.onclick = () => {
                    closeModal();
                    resolve(null);
                };
                footer.appendChild(cancelBtn);

                const okBtn = document.createElement('button');
                okBtn.className = 'modal-btn modal-btn-primary';
                okBtn.textContent = config.okText || (window.t ? window.t('common.ok') : '确定');
                okBtn.onclick = () => {
                    closeModal();
                    resolve(input.value);
                };
                footer.appendChild(okBtn);

                // Enter 键确认
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        closeModal();
                        resolve(input.value);
                    } else if (e.key === 'Escape') {
                        closeModal();
                        resolve(null);
                    }
                });
            }

            dialog.appendChild(footer);
            overlay.appendChild(dialog);

            // 点击遮罩层关闭（可选）
            if (config.closeOnClickOutside !== false) {
                overlay.addEventListener('click', (e) => {
                    if (e.target === overlay) {
                        closeModal();
                        resolve(config.type === 'prompt' ? null : false);
                    }
                });
            }

            // ESC 键关闭
            const escHandler = (e) => {
                if (e.key === 'Escape') {
                    closeModal();
                    resolve(config.type === 'prompt' ? null : false);
                }
            };
            document.addEventListener('keydown', escHandler);

            function closeModal() {
                document.removeEventListener('keydown', escHandler);
                overlay.style.animation = 'fadeOut 0.2s ease-out';
                setTimeout(() => {
                    document.body.removeChild(overlay);
                }, 200);
            }

            // 添加到页面
            document.body.appendChild(overlay);

            // 自动聚焦
            setTimeout(() => {
                if (input) {
                    input.focus();
                    input.select();
                } else {
                    const firstBtn = footer.querySelector('.modal-btn');
                    if (firstBtn) firstBtn.focus();
                }
            }, 100);
        });
    }

    /**
     * 显示警告对话框（替代 alert）
     * @param {string} message - 消息内容
     * @param {string} title - 标题（可选）
     * @returns {Promise<boolean>}
     */
    window.showAlert = function(message, title = null) {
        if (title === null) {
            title = window.t ? window.t('common.alert') : '提示';
        }
        return createModal({
            type: 'alert',
            title: title,
            message: message,
        });
    };

    /**
     * 显示确认对话框（替代 confirm）
     * @param {string} message - 消息内容
     * @param {string} title - 标题（可选）
     * @param {Object} options - 额外选项
     * @returns {Promise<boolean>}
     */
    window.showConfirm = function(message, title = null, options = {}) {
        console.log('[showConfirm] 被调用，参数:', { message, title, options });
        if (title === null) {
            try {
                title = (window.t && typeof window.t === 'function') ? window.t('common.confirm') : '确认';
            } catch (e) {
                console.error('翻译函数调用失败:', e);
                title = '确认';
            }
        }
        console.log('[showConfirm] 创建对话框，title:', title, 'message:', message);
        const promise = createModal({
            type: 'confirm',
            title: title,
            message: message,
            okText: options.okText,
            cancelText: options.cancelText,
            danger: options.danger || false,
        });
        console.log('[showConfirm] 返回 Promise:', promise);
        return promise;
    };

    /**
     * 显示输入对话框（替代 prompt）
     * @param {string} message - 消息内容
     * @param {string} defaultValue - 默认值
     * @param {string} title - 标题（可选）
     * @returns {Promise<string|null>}
     */
    window.showPrompt = function(message, defaultValue = '', title = null) {
        if (title === null) {
            title = window.t ? window.t('common.input') : '输入';
        }
        return createModal({
            type: 'prompt',
            title: title,
            message: message,
            defaultValue: defaultValue,
        });
    };

    // 添加 fadeOut 动画
    const fadeOutStyle = document.createElement('style');
    fadeOutStyle.textContent = `
        @keyframes fadeOut {
            from { opacity: 1; }
            to { opacity: 0; }
        }
    `;
    document.head.appendChild(fadeOutStyle);

})();

/**
 * 禁用浏览器缩放快捷键
 * 阻止 Ctrl+/Ctrl- 和 Ctrl+滚轮 缩放页面
 */
(function() {
    'use strict';
    
    // 禁用 Ctrl+/- 和 Ctrl+0 键盘快捷键
    document.addEventListener('keydown', function(event) {
        // 检测 Ctrl 或 Cmd 键（Mac）
        if (event.ctrlKey || event.metaKey) {
            // 禁用加号、减号、等号（=键位常用作+）和数字0
            if (event.key === '+' || 
                event.key === '=' || 
                event.key === '-' || 
                event.key === '_' || 
                event.key === '0') {
                event.preventDefault();
                return false;
            }
        }
    }, { passive: false });
    
    // 禁用 Ctrl + 滚轮缩放
    document.addEventListener('wheel', function(event) {
        if (event.ctrlKey || event.metaKey) {
            event.preventDefault();
            return false;
        }
    }, { passive: false });
    
    // 禁用触控板的双指缩放手势（适用于部分浏览器）
    document.addEventListener('gesturestart', function(event) {
        event.preventDefault();
        return false;
    }, { passive: false });
    
    document.addEventListener('gesturechange', function(event) {
        event.preventDefault();
        return false;
    }, { passive: false });
    
    document.addEventListener('gestureend', function(event) {
        event.preventDefault();
        return false;
    }, { passive: false });
    
    console.log('页面缩放快捷键已禁用');
})();

/**
 * 共享窗口管理工具
 * 用于防止重复打开同一个窗口
 */
(function() {
    'use strict';
    
    // 初始化已打开窗口的存储
    if (!window._openedWindows) {
        window._openedWindows = {};
    }
    
    /**
     * 打开或聚焦窗口
     * 如果同名窗口已存在且未关闭，则聚焦到该窗口
     * 否则打开新窗口
     * 
     * @param {string} url - 要打开的 URL
     * @param {string} windowName - 窗口名称（用于标识和重用）
     * @param {string} [features] - 窗口特性（可选，默认为标准设置窗口）
     * @returns {Window|null} - 返回窗口对象
     */
    window.openOrFocusWindow = function(url, windowName, features) {
        // 默认窗口特性（移除 noopener 以便获取窗口引用）
        const defaultFeatures = 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no';
        features = features || defaultFeatures;

        // 检查窗口是否已打开且未关闭
        const existingWindow = window._openedWindows[windowName];
        if (existingWindow && !existingWindow.closed) {
            existingWindow.focus();
            return existingWindow;
        }

        // 打开新窗口并存储引用
        const newWindow = window.open(url, windowName, features);
        if (newWindow) {
            window._openedWindows[windowName] = newWindow;

            // 监听窗口关闭事件，清理引用
            const checkClosed = setInterval(() => {
                if (newWindow.closed) {
                    clearInterval(checkClosed);
                    // 只有当缓存的引用仍然是这个窗口时才删除
                    // 防止在1秒内重新打开同名窗口时误删新窗口的引用
                    if (window._openedWindows[windowName] === newWindow) {
                        delete window._openedWindows[windowName];
                    }
                }
            }, 1000);
        }
        return newWindow;
    };
    
    /**
     * 关闭指定名称的窗口
     * 
     * @param {string} windowName - 窗口名称
     */
    window.closeNamedWindow = function(windowName) {
        const win = window._openedWindows[windowName];
        if (win && !win.closed) {
            win.close();
        }
        delete window._openedWindows[windowName];
    };
    
    /**
     * 统一的页面关闭函数
     * 适用于通过 window.open() 或 iframe 打开的页面
     * 
     * @param {string} [closeMessageType] - 发送给父窗口的消息类型（用于 iframe 模式）
     */
    window.closeCurrentPage = function(closeMessageType) {
        if (window.opener) {
            // 如果是通过 window.open() 打开的，直接关闭
            window.close();
        } else if (window.parent && window.parent !== window) {
            // 如果在 iframe 中，通知父窗口关闭
            if (closeMessageType) {
                window.parent.postMessage({ type: closeMessageType }, '*');
            } else {
                window.parent.postMessage({ type: 'close_page' }, '*');
            }
        } else {
            // 普通页面，返回上一页或主页
            if (window.history.length > 1) {
                window.history.back();
            } else {
                window.location.href = '/';
            }
        }
    };
    
    console.log('窗口管理工具已加载');
})();

