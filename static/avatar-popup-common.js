/**
 * Shared popup positioning utilities for Live2D/VRM.
 */
(function () {
    if (window.AvatarPopupUI) return;

    // 全局侧面板注册表：确保展开新面板时能顺滑收起其他所有面板
    const _sidePanels = new Set();

    function registerSidePanel(panel) {
        _sidePanels.add(panel);
    }

    function unregisterSidePanel(panel) {
        _sidePanels.delete(panel);
    }

    /**
     * 立即隐藏除 current 以外的所有侧面板（跳过动画）。
     * 必须在计算新面板位置之前调用，确保旧面板不影响空间判断。
     * 双重查找：注册表 + DOM 查询 data-neko-sidepanel 属性。
     * 同时完全清除位置状态，防止残留 CSS 污染后续定位。
     */
    function collapseOtherSidePanels(current) {
        // 收集所有需要隐藏的面板（注册表 + DOM 双重保障）
        const toHide = new Set();
        for (const panel of _sidePanels) {
            if (panel !== current) toHide.add(panel);
        }
        document.querySelectorAll('[data-neko-sidepanel]').forEach(panel => {
            if (panel !== current) toHide.add(panel);
        });

        for (const panel of toHide) {
            if (panel.style.display === 'none') continue;
            // 清除所有定时器
            if (panel._collapseTimeout) {
                clearTimeout(panel._collapseTimeout);
                panel._collapseTimeout = null;
            }
            if (panel._hoverCollapseTimer) {
                clearTimeout(panel._hoverCollapseTimer);
                panel._hoverCollapseTimer = null;
            }
            // 立即隐藏 + 彻底清除位置状态，不留任何残影
            panel.style.transition = 'none';
            panel.style.opacity = '0';
            panel.style.display = 'none';
            panel.style.pointerEvents = 'none';
            panel.style.left = '';
            panel.style.right = '';
            panel.style.top = '';
            panel.style.transform = '';
            // 清除 inline transition，让 CSS 定义的 transition 在下次 _expand() 时生效
            panel.style.transition = '';
            // 恢复原始 maxWidth
            if (panel._originalMaxWidth !== undefined) {
                panel.style.maxWidth = panel._originalMaxWidth;
            }
        }
    }

    function toNumber(value, fallback = 0) {
        const n = Number.parseFloat(value);
        return Number.isFinite(n) ? n : fallback;
    }

    function resetPopupPosition(popup, options = {}) {
        const left = options.left || '100%';
        const top = options.top || '0';
        popup.style.left = left;
        popup.style.right = 'auto';
        popup.style.top = top;
        popup.style.marginLeft = '8px';
        popup.style.marginRight = '0';
    }

    function positionPopup(popup, options = {}) {
        const buttonId = options.buttonId;
        const buttonPrefix = options.buttonPrefix || 'live2d-btn-';
        const triggerPrefix = options.triggerPrefix || 'live2d-trigger-icon-';
        const rightMargin = Number.isFinite(options.rightMargin) ? options.rightMargin : 20;
        const bottomMargin = Number.isFinite(options.bottomMargin) ? options.bottomMargin : 60;
        const topMargin = Number.isFinite(options.topMargin) ? options.topMargin : 8;
        const gap = Number.isFinite(options.gap) ? options.gap : 8;
        const sidePanelWidth = Number.isFinite(options.sidePanelWidth) ? options.sidePanelWidth : 0;

        const triggerIcon = document.querySelector(`.${triggerPrefix}${buttonId}`);
        const screenWidth = window.innerWidth;
        const screenHeight = window.innerHeight;
        let opensLeft = false;

        // ── 关键修复：先重置到默认右弹位置再测量 ──
        // 防止上一次 opensLeft 残留的 inline styles 干扰溢出检测
        resetPopupPosition(popup);
        void popup.offsetHeight; // 强制 reflow，确保测量基于默认位置

        // Horizontal overflow handling.
        let popupRect = popup.getBoundingClientRect();
        // 考虑侧面板宽度：如果 popup + gap + 侧面板一起会溢出右边缘，提前选择向左弹出
        // sidePanelWidth 是纯面板宽度（不含 gap），gap 在此处统一添加
        const effectiveRight = sidePanelWidth > 0
            ? popupRect.right + gap + sidePanelWidth
            : popupRect.right;
        if (effectiveRight > screenWidth - rightMargin) {
            const button = document.getElementById(`${buttonPrefix}${buttonId}`);
            const buttonWidth = button ? button.offsetWidth : 48;
            popup.style.left = 'auto';
            popup.style.right = '0';
            popup.style.marginLeft = '0';
            // 从 popup 定位容器的右边缘到按钮左边缘的实际距离，确保面板不遮挡按钮
            // 注意：popup 在 transform:scale(X) 容器内，getBoundingClientRect 返回视觉坐标（已缩放），
            // 但 CSS margin 作用于本地坐标系（未缩放），需要除以 scale 转换
            let rightClearance = buttonWidth + gap;
            if (button && popup.parentElement) {
                const parentRect = popup.parentElement.getBoundingClientRect();
                const buttonRect = button.getBoundingClientRect();
                let scale = 1;
                const scaledContainer = popup.closest('[id$="-floating-buttons"]');
                if (scaledContainer) {
                    const transform = getComputedStyle(scaledContainer).transform;
                    if (transform && transform !== 'none') {
                        const match = transform.match(/matrix\(([^,]+)/);
                        if (match) scale = parseFloat(match[1]) || 1;
                    }
                }
                rightClearance = (parentRect.right - buttonRect.left) / scale + gap;
            }
            popup.style.marginRight = `${Math.max(rightClearance, 0)}px`;
            opensLeft = true;
            if (triggerIcon) triggerIcon.style.transform = 'rotate(180deg)';
        } else {
            popup.style.left = popup.style.left || '100%';
            popup.style.right = 'auto';
            popup.style.marginLeft = `${gap}px`;
            popup.style.marginRight = '0';
            if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';
        }

        popup.dataset.opensLeft = String(opensLeft);

        // Vertical overflow handling.
        popupRect = popup.getBoundingClientRect();
        const currentTop = toNumber(popup.style.top, 0);
        let nextTop = currentTop;
        if (popupRect.bottom > screenHeight - bottomMargin) {
            nextTop -= (popupRect.bottom - (screenHeight - bottomMargin));
        }
        popup.style.top = `${nextTop}px`;

        popupRect = popup.getBoundingClientRect();
        if (popupRect.top < topMargin) {
            popup.style.top = `${toNumber(popup.style.top, 0) + (topMargin - popupRect.top)}px`;
        }

        return { opensLeft };
    }

    /**
     * 获取所有浮动按钮的包围盒（禁区）。
     * 返回 { left, right, top, bottom, hasButtons }。
     * 优先扫描单个按钮元素；若按钮元素不可见，回退到按钮容器元素。
     * ownerPrefix: 可选，'vrm' 或 'live2d'，只扫描当前系统的按钮，避免多系统按钮混入导致包围盒偏移。
     */
    function getButtonZone(ownerPrefix) {
        let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
        let hasButtons = false;

        // 按系统前缀过滤按钮选择器
        const selector = ownerPrefix
            ? `[id^="${ownerPrefix}-btn-"]`
            : '[id^="vrm-btn-"], [id^="live2d-btn-"]';

        // 第一优先级：扫描所有单个按钮
        const allBtns = document.querySelectorAll(selector);
        for (const btn of allBtns) {
            const r = btn.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            hasButtons = true;
            if (r.left < left) left = r.left;
            if (r.right > right) right = r.right;
            if (r.top < top) top = r.top;
            if (r.bottom > bottom) bottom = r.bottom;
        }

        // 第二优先级：单个按钮找不到时，回退到按钮容器
        if (!hasButtons) {
            const containerSelector = ownerPrefix
                ? `#${ownerPrefix}-floating-buttons`
                : '#live2d-floating-buttons, #vrm-floating-buttons, [id$="-floating-buttons"]';
            const containers = document.querySelectorAll(containerSelector);
            for (const c of containers) {
                if (c.style.display === 'none' || !c.offsetWidth) continue;
                const r = c.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                hasButtons = true;
                if (r.left < left) left = r.left;
                if (r.right > right) right = r.right;
                if (r.top < top) top = r.top;
                if (r.bottom > bottom) bottom = r.bottom;
            }
        }

        return { left, right, top, bottom, hasButtons };
    }

    /**
     * 定位侧面板：基于 popup 的方向和位置级联定位。
     * 核心原则：
     *   1. 面板绝不能覆盖浮动按钮
     *   2. 方向由 positionPopup 的溢出检测决定（popup.dataset.opensLeft），不再独立猜测
     *   3. 水平锚点基于 popup 的实际位置（popupRect），不再基于按钮区域
     *   4. getButtonZone 仅作碰撞检测兜底
     *
     * container: 侧面板元素（position: fixed, 挂在 document.body）
     * anchor: 触发菜单项元素（用于垂直参考）
     */
    function positionSidePanel(container, anchor, options = {}) {
        const gap = Number.isFinite(options.gap) ? options.gap : 12;
        const edgeMargin = Number.isFinite(options.edgeMargin) ? options.edgeMargin : 8;
        const bottomSafe = Number.isFinite(options.bottomSafe) ? options.bottomSafe : 60;

        // ── Step 0：彻底清除上一次定位残留 ──
        container.style.left = '';
        container.style.right = '';
        container.style.top = '';
        container.style.transform = 'none';
        // 恢复原始 maxWidth（可能被上一次边缘钳制覆盖过）
        if (container._originalMaxWidth !== undefined) {
            container.style.maxWidth = container._originalMaxWidth;
        }
        void container.offsetHeight; // 强制 reflow，基于干净状态测量尺寸
        // 记录原始 maxWidth 供后续恢复
        if (container._originalMaxWidth === undefined) {
            container._originalMaxWidth = container.style.maxWidth;
        }

        // ── Step 1：从 popup 获取方向（取代 getButtonZone 启发式） ──
        const popup = container._popupElement;
        // 如果 opensLeft 未设置，默认为 true（保守策略：面板放在按钮左侧）
        const goLeft = popup ? (popup.dataset.opensLeft === 'true' || !popup.dataset.opensLeft) : true;
        container.dataset.goLeft = String(goLeft);

        // ── Step 2：基于 popup 实际位置定位（取代基于 button zone 定位） ──
        const popupRect = popup ? popup.getBoundingClientRect() : anchor.getBoundingClientRect();
        const anchorRect = anchor.getBoundingClientRect();
        const screenW = window.innerWidth;
        const screenH = window.innerHeight;
        const panelW = container.offsetWidth;
        const panelH = container.offsetHeight;

        // 从 popup ID 推断系统前缀，用于过滤 getButtonZone
        const popupId = popup ? popup.id : '';
        const ownerPrefix = popupId.startsWith('vrm-') ? 'vrm'
                          : popupId.startsWith('live2d-') ? 'live2d' : '';

        if (goLeft) {
            // popup 向左弹出 → 侧面板放在 popup 的左侧（更远离按钮）
            let panelRight = popupRect.left - gap;
            let panelLeft = panelRight - panelW;

            // 超出屏幕左边缘时限制
            if (panelLeft < edgeMargin) {
                panelLeft = edgeMargin;
                container.style.maxWidth = `${panelRight - edgeMargin}px`;
            }
            container.style.left = `${panelLeft}px`;
            container.style.right = 'auto';
            container.style.transform = 'translateX(6px)';
        } else {
            // popup 向右弹出 → 侧面板放在 popup 的右侧（更远离按钮）
            let panelLeft = popupRect.right + gap;

            // 超出屏幕右边缘时限制
            if (panelLeft + panelW > screenW - edgeMargin) {
                container.style.maxWidth = `${screenW - edgeMargin - panelLeft}px`;
            }
            container.style.left = `${panelLeft}px`;
            container.style.right = 'auto';
            container.style.transform = 'translateX(-6px)';
        }

        // ── Step 3：垂直定位（对齐 anchor） ──
        let topVal = anchorRect.top;

        // 边界钳制
        if (topVal + panelH > screenH - bottomSafe) topVal = screenH - bottomSafe - panelH;
        if (topVal < edgeMargin) topVal = edgeMargin;
        container.style.top = `${topVal}px`;

        // ── Step 4：按钮禁区安全验证（降级为 fallback，不再是主逻辑） ──
        const zone = getButtonZone(ownerPrefix);
        if (zone.hasButtons) {
            const savedTransform = container.style.transform;
            container.style.transform = 'none';
            void container.offsetHeight;
            const pr = container.getBoundingClientRect();
            container.style.transform = savedTransform;

            const overlapsH = pr.right > zone.left && pr.left < zone.right;
            const overlapsV = pr.bottom > zone.top && pr.top < zone.bottom;

            if (overlapsH && overlapsV) {
                // 紧急修正：强制推到按钮对侧
                if (goLeft) {
                    container.style.left = `${edgeMargin}px`;
                    container.style.maxWidth = `${zone.left - gap - edgeMargin}px`;
                } else {
                    container.style.left = `${zone.right + gap}px`;
                    container.style.maxWidth = `${screenW - edgeMargin - zone.right - gap}px`;
                }
            }
        }

        // ── Step 5：动画结束后二次验证（自愈机制） ──
        // 在动画完成后再次检查是否覆盖按钮，修正任何因动画/时序导致的偏差
        const _containerRef = container;
        const _ownerPrefix = ownerPrefix;
        const _goLeft = goLeft;
        const _gap = gap;
        const _edgeMargin = edgeMargin;
        const _screenW = screenW;
        setTimeout(() => {
            if (_containerRef.style.display === 'none' || _containerRef.style.opacity === '0') return;
            const z = getButtonZone(_ownerPrefix);
            if (!z.hasButtons) return;
            const r = _containerRef.getBoundingClientRect();
            const oH = r.right > z.left && r.left < z.right;
            const oV = r.bottom > z.top && r.top < z.bottom;
            if (oH && oV) {
                if (_goLeft) {
                    _containerRef.style.left = `${_edgeMargin}px`;
                    _containerRef.style.maxWidth = `${z.left - _gap - _edgeMargin}px`;
                } else {
                    _containerRef.style.left = `${z.right + _gap}px`;
                    _containerRef.style.maxWidth = `${_screenW - _edgeMargin - z.right - _gap}px`;
                }
                _containerRef.style.transform = 'translateX(0)';
            }
        }, 300);
    }

    window.AvatarPopupUI = {
        positionPopup,
        resetPopupPosition,
        registerSidePanel,
        unregisterSidePanel,
        collapseOtherSidePanels,
        positionSidePanel
    };
})();
