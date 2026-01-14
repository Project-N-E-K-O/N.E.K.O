/**
 * Live2D UI Drag - 拖拽和弹出框管理
 * 包含弹出框管理、容器拖拽、显示弹出框、折叠功能、按钮事件传播管理
 */

// ===== 拖拽辅助工具 - 按钮事件传播管理 =====
(function() {
    'use strict';

    /**
     * 禁用按钮的 pointer-events
     * 在拖动开始时调用，防止按钮拦截拖动事件
     */
    function disableButtonPointerEvents() {
        // 收集所有按钮元素（包括浮动按钮和三角触发按钮）
        const buttons = document.querySelectorAll('.live2d-floating-btn, .live2d-trigger-btn, [id^="live2d-btn-"]');
        buttons.forEach(btn => {
            if (btn) {
                // 如果已经保存过，说明正在拖拽中，跳过
                if (btn.hasAttribute('data-prev-pointer-events')) {
                    return;
                }
                // 保存当前的pointerEvents值
                const currentValue = btn.style.pointerEvents || '';
                btn.setAttribute('data-prev-pointer-events', currentValue);
                btn.style.pointerEvents = 'none';
            }
        });
        
        // 收集并处理所有按钮包装器元素（包括三角按钮的包装器）
        const wrappers = new Set();
        buttons.forEach(btn => {
            if (btn && btn.parentElement) {
                // 排除返回按钮和其容器，避免破坏其拖拽行为
                if (btn.id === 'live2d-btn-return' || 
                    (btn.parentElement && btn.parentElement.id === 'live2d-return-button-container')) {
                    return;
                }
                wrappers.add(btn.parentElement);
            }
        });
        
        wrappers.forEach(wrapper => {
            const currentValue = wrapper.style.pointerEvents || '';
            wrapper.setAttribute('data-prev-pointer-events', currentValue);
            wrapper.style.pointerEvents = 'none';
        });
        
        // 禁用所有弹窗元素的 pointer-events，避免拖拽时与弹窗冲突
        const popups = document.querySelectorAll('.live2d-popup, [id^="live2d-popup-"]');
        popups.forEach(popup => {
            if (popup) {
                // 如果已经保存过，说明正在拖拽中，跳过
                if (popup.hasAttribute('data-prev-pointer-events')) {
                    return;
                }
                const currentValue = popup.style.pointerEvents || '';
                popup.setAttribute('data-prev-pointer-events', currentValue);
                popup.style.pointerEvents = 'none';
            }
        });
    }

    /**
     * 恢复按钮的 pointer-events
     * 在拖动结束时调用，恢复按钮的正常点击功能
     */
    function restoreButtonPointerEvents() {
        const elementsToRestore = document.querySelectorAll('[data-prev-pointer-events]');
        elementsToRestore.forEach(element => {
            if (element) {
                const prevValue = element.getAttribute('data-prev-pointer-events');
                if (prevValue === '') {
                    element.style.pointerEvents = '';
                } else {
                    element.style.pointerEvents = prevValue;
                }
                element.removeAttribute('data-prev-pointer-events');
            }
        });
    }

    // 挂载到全局 window 对象，供其他脚本使用
    window.DragHelpers = {
        disableButtonPointerEvents: disableButtonPointerEvents,
        restoreButtonPointerEvents: restoreButtonPointerEvents
    };
})();

// ===== 弹出框管理 =====

// 关闭指定按钮对应的弹出框，并恢复按钮状态
Live2DManager.prototype.closePopupById = function (buttonId) {
    if (!buttonId) return false;
    this._floatingButtons = this._floatingButtons || {};
    this._popupTimers = this._popupTimers || {};
    const popup = document.getElementById(`live2d-popup-${buttonId}`);
    if (!popup || popup.style.display !== 'flex') {
        return false;
    }

    // 如果是 agent 弹窗关闭，派发关闭事件
    if (buttonId === 'agent') {
        window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));
    }

    popup.style.opacity = '0';
    popup.style.transform = 'translateX(-10px)';
    setTimeout(() => {
        popup.style.display = 'none';
    }, 200);

    const buttonEntry = this._floatingButtons[buttonId];
    if (buttonEntry && buttonEntry.button) {
        buttonEntry.button.dataset.active = 'false';
        buttonEntry.button.style.background = 'rgba(255, 255, 255, 0.65)';  // Fluent Acrylic

        if (buttonEntry.imgOff && buttonEntry.imgOn) {
            buttonEntry.imgOff.style.opacity = '1';
            buttonEntry.imgOn.style.opacity = '0';
        }
    }

    if (this._popupTimers[buttonId]) {
        clearTimeout(this._popupTimers[buttonId]);
        this._popupTimers[buttonId] = null;
    }

    return true;
};

// 关闭除当前按钮之外的所有弹出框
Live2DManager.prototype.closeAllPopupsExcept = function (currentButtonId) {
    const popups = document.querySelectorAll('[id^="live2d-popup-"]');
    popups.forEach(popup => {
        const popupId = popup.id.replace('live2d-popup-', '');
        if (popupId !== currentButtonId && popup.style.display === 'flex') {
            this.closePopupById(popupId);
        }
    });
};

// 关闭所有通过 window.open 打开的设置窗口，可选保留特定 URL
Live2DManager.prototype.closeAllSettingsWindows = function (exceptUrl = null) {
    if (!this._openSettingsWindows) return;
    Object.keys(this._openSettingsWindows).forEach(url => {
        if (exceptUrl && url === exceptUrl) return;
        const winRef = this._openSettingsWindows[url];
        try {
            if (winRef && !winRef.closed) {
                winRef.close();
            }
        } catch (_) {
            // 忽略跨域导致的 close 异常
        }
        delete this._openSettingsWindows[url];
    });
};

// 为"请她回来"按钮容器设置拖动功能
Live2DManager.prototype.setupReturnButtonContainerDrag = function (returnButtonContainer) {
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let containerStartX = 0;
    let containerStartY = 0;
    let isClick = false; // 标记是否为点击操作

    // 鼠标按下事件
    returnButtonContainer.addEventListener('mousedown', (e) => {
        // 允许在按钮容器本身和按钮元素上都能开始拖动
        // 这样就能在按钮正中心位置进行拖拽操作
        if (e.target === returnButtonContainer || e.target.classList.contains('live2d-return-btn')) {
            isDragging = true;
            isClick = true;
            dragStartX = e.clientX;
            dragStartY = e.clientY;

            const currentLeft = parseInt(returnButtonContainer.style.left) || 0;
            const currentTop = parseInt(returnButtonContainer.style.top) || 0;
            containerStartX = currentLeft;
            containerStartY = currentTop;

            returnButtonContainer.setAttribute('data-dragging', 'false');
            returnButtonContainer.style.cursor = 'grabbing';
            e.preventDefault();
        }
    });

    // 鼠标移动事件
    document.addEventListener('mousemove', (e) => {
        if (isDragging) {
            const deltaX = e.clientX - dragStartX;
            const deltaY = e.clientY - dragStartY;

            const dragThreshold = 5;
            if (Math.abs(deltaX) > dragThreshold || Math.abs(deltaY) > dragThreshold) {
                isClick = false;
                returnButtonContainer.setAttribute('data-dragging', 'true');
            }

            const newX = containerStartX + deltaX;
            const newY = containerStartY + deltaY;

            // 边界检查 - 使用窗口尺寸（窗口只覆盖当前屏幕）
            const containerWidth = returnButtonContainer.offsetWidth || 64;
            const containerHeight = returnButtonContainer.offsetHeight || 64;

            const boundedX = Math.max(0, Math.min(newX, window.innerWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, window.innerHeight - containerHeight));

            returnButtonContainer.style.left = `${boundedX}px`;
            returnButtonContainer.style.top = `${boundedY}px`;
        }
    });

    // 鼠标释放事件
    document.addEventListener('mouseup', (e) => {
        if (isDragging) {
            setTimeout(() => {
                returnButtonContainer.setAttribute('data-dragging', 'false');
            }, 10);

            isDragging = false;
            isClick = false;
            returnButtonContainer.style.cursor = 'grab';
        }
    });

    // 设置初始鼠标样式
    returnButtonContainer.style.cursor = 'grab';

    // 触摸事件支持
    returnButtonContainer.addEventListener('touchstart', (e) => {
        // 允许在按钮容器本身和按钮元素上都能开始拖动
        if (e.target === returnButtonContainer || e.target.classList.contains('live2d-return-btn')) {
            isDragging = true;
            isClick = true;
            const touch = e.touches[0];
            dragStartX = touch.clientX;
            dragStartY = touch.clientY;

            const currentLeft = parseInt(returnButtonContainer.style.left) || 0;
            const currentTop = parseInt(returnButtonContainer.style.top) || 0;
            containerStartX = currentLeft;
            containerStartY = currentTop;

            returnButtonContainer.setAttribute('data-dragging', 'false');
            e.preventDefault();
        }
    });

    document.addEventListener('touchmove', (e) => {
        if (isDragging) {
            const touch = e.touches[0];
            const deltaX = touch.clientX - dragStartX;
            const deltaY = touch.clientY - dragStartY;

            const dragThreshold = 5;
            if (Math.abs(deltaX) > dragThreshold || Math.abs(deltaY) > dragThreshold) {
                isClick = false;
                returnButtonContainer.setAttribute('data-dragging', 'true');
            }

            const newX = containerStartX + deltaX;
            const newY = containerStartY + deltaY;

            // 边界检查 - 使用窗口尺寸
            const containerWidth = returnButtonContainer.offsetWidth || 64;
            const containerHeight = returnButtonContainer.offsetHeight || 64;

            const boundedX = Math.max(0, Math.min(newX, window.innerWidth - containerWidth));
            const boundedY = Math.max(0, Math.min(newY, window.innerHeight - containerHeight));

            returnButtonContainer.style.left = `${boundedX}px`;
            returnButtonContainer.style.top = `${boundedY}px`;
            e.preventDefault();
        }
    });

    document.addEventListener('touchend', (e) => {
        if (isDragging) {
            setTimeout(() => {
                returnButtonContainer.setAttribute('data-dragging', 'false');
            }, 10);

            isDragging = false;
            isClick = false;
        }
    });
};

// 显示弹出框（1秒后自动隐藏），支持点击切换
Live2DManager.prototype.showPopup = function (buttonId, popup) {
    // 检查当前状态
    const isVisible = popup.style.display === 'flex' && popup.style.opacity === '1';

    // 清除之前的定时器
    if (this._popupTimers[buttonId]) {
        clearTimeout(this._popupTimers[buttonId]);
        this._popupTimers[buttonId] = null;
    }

    // 如果是设置弹出框，每次显示时更新开关状态（确保与 app.js 同步）
    if (buttonId === 'settings') {
        const focusCheckbox = popup.querySelector('#live2d-focus-mode');
        const proactiveChatCheckbox = popup.querySelector('#live2d-proactive-chat');

        // 辅助函数：更新 checkbox 的视觉样式
        const updateCheckboxStyle = (checkbox) => {
            if (!checkbox) return;
            const toggleItem = checkbox.parentElement;
            if (!toggleItem) return;

            const indicator = toggleItem.querySelector('.vrm-toggle-indicator');
            const checkmark = indicator?.querySelector('.vrm-toggle-checkmark');
            if (!indicator || !checkmark) return;

            if (checkbox.checked) {
                indicator.style.backgroundColor = '#44b7fe';
                indicator.style.borderColor = '#44b7fe';
                checkmark.style.opacity = '1';
                toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
            } else {
                indicator.style.backgroundColor = 'transparent';
                indicator.style.borderColor = '#ccc';
                checkmark.style.opacity = '0';
                toggleItem.style.background = 'transparent';
            }
        };

        // 更新 focus mode checkbox 状态和视觉样式
        if (focusCheckbox && typeof window.focusModeEnabled !== 'undefined') {
            // "允许打断"按钮值与 focusModeEnabled 相反
            const newChecked = !window.focusModeEnabled;
            // 只在状态改变时更新，避免不必要的 DOM 操作
            if (focusCheckbox.checked !== newChecked) {
                focusCheckbox.checked = newChecked;
                // 使用 requestAnimationFrame 确保 DOM 已更新后再更新样式
                requestAnimationFrame(() => {
                    updateCheckboxStyle(focusCheckbox);
                });
            } else {
                // 即使状态相同，也确保视觉样式正确（处理概率性问题）
                requestAnimationFrame(() => {
                    updateCheckboxStyle(focusCheckbox);
                });
            }
        }

        // 更新 proactive chat checkbox 状态和视觉样式
        if (proactiveChatCheckbox && typeof window.proactiveChatEnabled !== 'undefined') {
            const newChecked = window.proactiveChatEnabled;
            // 只在状态改变时更新，避免不必要的 DOM 操作
            if (proactiveChatCheckbox.checked !== newChecked) {
                proactiveChatCheckbox.checked = newChecked;
                requestAnimationFrame(() => {
                    updateCheckboxStyle(proactiveChatCheckbox);
                });
            } else {
                // 即使状态相同，也确保视觉样式正确（处理概率性问题）
                requestAnimationFrame(() => {
                    updateCheckboxStyle(proactiveChatCheckbox);
                });
            }
        }
    }

    // 如果是 agent 弹窗，触发服务器状态检查事件
    if (buttonId === 'agent' && !isVisible) {
        // 弹窗即将显示，派发事件让 app.js 检查服务器状态
        window.dispatchEvent(new CustomEvent('live2d-agent-popup-opening'));
    }

    if (isVisible) {
        // 如果已经显示，则隐藏
        popup.style.opacity = '0';
        popup.style.transform = 'translateX(-10px)';

        // 如果是 agent 弹窗关闭，派发关闭事件
        if (buttonId === 'agent') {
            window.dispatchEvent(new CustomEvent('live2d-agent-popup-closed'));
        }

        setTimeout(() => {
            popup.style.display = 'none';
            // 重置位置和样式
            popup.style.left = '100%';
            popup.style.right = 'auto';
            popup.style.top = '0';
            popup.style.marginLeft = '8px';
            popup.style.marginRight = '0';
            // 重置高度限制，确保下次打开时状态一致
            if (buttonId === 'settings' || buttonId === 'agent') {
                popup.style.maxHeight = '200px';
                popup.style.overflowY = 'auto';
            }
        }, 200);
    } else {
        // 全局互斥：打开前关闭其他弹出框
        this.closeAllPopupsExcept(buttonId);

        // 如果隐藏，则显示
        popup.style.display = 'flex';
        // 先让弹出框可见但透明，以便计算尺寸
        popup.style.opacity = '0';
        popup.style.visibility = 'visible';

        // 关键：在计算位置之前，先移除高度限制，确保获取真实尺寸
        if (buttonId === 'settings' || buttonId === 'agent') {
            popup.style.maxHeight = 'none';
            popup.style.overflowY = 'visible';
        }

        // 等待popup内的所有图片加载完成，确保尺寸准确
        const images = popup.querySelectorAll('img');
        const imageLoadPromises = Array.from(images).map(img => {
            if (img.complete) {
                return Promise.resolve();
            }
            return new Promise(resolve => {
                img.onload = resolve;
                img.onerror = resolve; // 即使加载失败也继续
                // 超时保护：最多等待100ms
                setTimeout(resolve, 100);
            });
        });

        Promise.all(imageLoadPromises).then(() => {
            // 强制触发reflow，确保布局完全更新
            void popup.offsetHeight;

            // 再次使用RAF确保布局稳定
            requestAnimationFrame(() => {
                const popupRect = popup.getBoundingClientRect();
                const screenWidth = window.innerWidth;
                const screenHeight = window.innerHeight;
                const rightMargin = 20; // 距离屏幕右侧的安全边距
                const bottomMargin = 60; // 距离屏幕底部的安全边距（考虑系统任务栏，Windows任务栏约40-48px）

                // 检查是否超出屏幕右侧
                const popupRight = popupRect.right;
                if (popupRight > screenWidth - rightMargin) {
                    // 超出右边界，改为向左弹出
                    // 获取按钮的实际宽度来计算正确的偏移
                    const button = document.getElementById(`live2d-btn-${buttonId}`);
                    const buttonWidth = button ? button.offsetWidth : 48;
                    const gap = 8;

                    // 让弹出框完全移到按钮左侧，不遮挡按钮
                    popup.style.left = 'auto';
                    popup.style.right = '0';
                    popup.style.marginLeft = '0';
                    popup.style.marginRight = `${buttonWidth + gap}px`;
                    popup.style.transform = 'translateX(10px)'; // 反向动画
                }

                // 检查是否超出屏幕底部（设置弹出框或其他较高的弹出框）
                if (buttonId === 'settings' || buttonId === 'agent') {
                    const popupBottom = popupRect.bottom;
                    if (popupBottom > screenHeight - bottomMargin) {
                        // 计算需要向上移动的距离
                        const overflow = popupBottom - (screenHeight - bottomMargin);
                        const currentTop = parseInt(popup.style.top) || 0;
                        const newTop = currentTop - overflow;
                        popup.style.top = `${newTop}px`;
                    }
                }

                // 显示弹出框
                popup.style.visibility = 'visible';
                popup.style.opacity = '1';
                popup.style.transform = 'translateX(0)';
            });
        });

        // 设置、agent、麦克风、屏幕源弹出框不自动隐藏，其他的1秒后隐藏
        if (buttonId !== 'settings' && buttonId !== 'agent' && buttonId !== 'mic' && buttonId !== 'screen') {
            this._popupTimers[buttonId] = setTimeout(() => {
                popup.style.opacity = '0';
                popup.style.transform = popup.style.right === '100%' ? 'translateX(10px)' : 'translateX(-10px)';
                setTimeout(() => {
                    popup.style.display = 'none';
                    // 重置位置
                    popup.style.left = '100%';
                    popup.style.right = 'auto';
                    popup.style.top = '0';
                }, 200);
                this._popupTimers[buttonId] = null;
            }, 1000);
        }
    }
};

// 设置折叠功能
Live2DManager.prototype._setupCollapseFunctionality = function (emptyState, collapseButton, emptyContent) {
    // 获取折叠状态
    const getCollapsedState = () => {
        try {
            const saved = localStorage.getItem('agent-task-empty-collapsed');
            return saved === 'true';
        } catch (error) {
            console.warn('Failed to read collapse state from localStorage:', error);
            return false;
        }
    };

    // 保存折叠状态
    const saveCollapsedState = (collapsed) => {
        try {
            localStorage.setItem('agent-task-empty-collapsed', collapsed.toString());
        } catch (error) {
            console.warn('Failed to save collapse state to localStorage:', error);
        }
    };

    // 初始化状态
    let isCollapsed = getCollapsedState();
    let touchProcessed = false; // 防止触摸设备双重切换的标志

    // 更新折叠状态
    const updateCollapseState = (collapsed) => {
        isCollapsed = collapsed;

        if (collapsed) {
            // 折叠状态
            emptyState.classList.add('collapsed');
            collapseButton.classList.add('collapsed');
            collapseButton.innerHTML = '▶';
        } else {
            // 展开状态
            emptyState.classList.remove('collapsed');
            collapseButton.classList.remove('collapsed');
            collapseButton.innerHTML = '▼';
        }

        // 保存状态
        saveCollapsedState(collapsed);
    };

    // 应用初始状态
    updateCollapseState(isCollapsed);

    // 点击事件处理
    collapseButton.addEventListener('click', (e) => {
        e.stopPropagation();
        // 如果是触摸设备刚刚处理过，则忽略click事件
        if (touchProcessed) {
            touchProcessed = false; // 重置标志
            return;
        }
        updateCollapseState(!isCollapsed);
    });

    // 悬停效果
    collapseButton.addEventListener('mouseenter', () => {
        collapseButton.style.background = 'rgba(100, 116, 139, 0.6)';
        collapseButton.style.transform = 'scale(1.1)';
    });

    collapseButton.addEventListener('mouseleave', () => {
        collapseButton.style.background = isCollapsed ?
            'rgba(100, 116, 139, 0.5)' : 'rgba(100, 116, 139, 0.3)';
        collapseButton.style.transform = 'scale(1)';
    });

    // 触摸设备优化
    collapseButton.addEventListener('touchstart', (e) => {
        e.stopPropagation();
        // 阻止默认行为，防止后续click事件
        e.preventDefault();
        collapseButton.style.background = 'rgba(100, 116, 139, 0.7)';
        collapseButton.style.transform = 'scale(1.1)';
    }, { passive: false });

    collapseButton.addEventListener('touchend', (e) => {
        e.stopPropagation();
        // 阻止click事件的触发
        e.preventDefault();

        // 设置标志，阻止后续的click事件
        touchProcessed = true;

        updateCollapseState(!isCollapsed);
        collapseButton.style.background = isCollapsed ?
            'rgba(100, 116, 139, 0.5)' : 'rgba(100, 116, 139, 0.3)';
        collapseButton.style.transform = 'scale(1)';

        // 短时间后重置标志，允许后续的点击操作
        setTimeout(() => {
            touchProcessed = false;
        }, 100);
    }, { passive: false });
};
