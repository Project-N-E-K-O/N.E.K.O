/**
 * 拖拽辅助工具 - 共享的按钮事件传播管理函数
 * 用于在拖动过程中临时禁用/恢复按钮的事件拦截
 */

(function() {
    'use strict';

    /**
     * 启用按钮事件传播（禁用按钮的pointer-events）
     * 在拖动开始时调用，防止按钮拦截拖动事件
     */
    function enableButtonEventPropagation() {
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
                // 排除返回按钮和 returnButtonContainer，避免破坏其拖拽行为
                if (btn.id === 'live2d-btn-return' || btn.parentElement.id === 'returnButtonContainer') {
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
    }

    /**
     * 禁用按钮事件传播（恢复按钮的pointer-events）
     * 在拖动结束时调用，恢复按钮的正常点击功能
     */
    function disableButtonEventPropagation() {
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
        enableButtonEventPropagation: enableButtonEventPropagation,
        disableButtonEventPropagation: disableButtonEventPropagation
    };
})();
