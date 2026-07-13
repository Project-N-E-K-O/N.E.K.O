
// ===== 全局错误捕获：在页面状态栏显示错误信息 =====
window.addEventListener('error', (event) => {
    // 忽略浏览器扩展/Electron IPC 的已知无害错误
    const msg = event.message || '';
    if (msg.includes('message channel closed') || msg.includes('Extension context invalidated')) return;
    console.error('[model_manager] 全局错误:', event.error || msg);
    const statusSpan = document.getElementById('status-text');
    if (statusSpan) statusSpan.textContent = `初始化错误: ${msg}`;
});
window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason?.message || String(event.reason || '');
    // 忽略浏览器扩展/Electron IPC 的已知无害错误
    if (reason.includes('message channel closed') || reason.includes('Extension context invalidated')) return;
    console.error('[model_manager] 未处理的 Promise 拒绝:', event.reason);
    const statusSpan = document.getElementById('status-text');
    if (statusSpan) statusSpan.textContent = `异步错误: ${reason}`;
});

// ===== 选项条统一管理器 =====
/**
 * 选项条统一管理器
 * 封装所有选项条的通用功能，减少重复代码
 */
class DropdownManager {
    static instances = [];

    static getVisualWidth(str) {
        let width = 0;
        for (const char of str) {
            width += char.charCodeAt(0) > 127 ? 2 : 1;
        }
        return width;
    }

    static truncateText(text, maxVisualWidth) {
        if (!text || DropdownManager.getVisualWidth(text) <= maxVisualWidth) {
            return text;
        }
        let truncated = '';
        let currentWidth = 0;
        for (const char of text) {
            const charWidth = char.charCodeAt(0) > 127 ? 2 : 1;
            if (currentWidth + charWidth > maxVisualWidth - 3) break;
            truncated += char;
            currentWidth += charWidth;
        }
        return truncated + '...';
    }

    constructor(config) {
        this.config = {
            buttonId: config.buttonId,
            selectId: config.selectId,
            dropdownId: config.dropdownId,
            textSpanId: config.textSpanId,
            iconClass: config.iconClass,
            iconSrc: config.iconSrc,
            defaultText: config.defaultText || '选择',
            defaultTextKey: config.defaultTextKey || null,  // i18n key for dynamic translation
            iconAlt: config.iconAlt || config.defaultText,
            iconAltKey: config.iconAltKey || null,  // i18n key for icon alt
            onChange: config.onChange || (() => { }),
            getText: config.getText || ((option) => {
                const key = option?.dataset?.i18n;
                if (key && window.t && typeof window.t === 'function') {
                    const translated = window.t(key);
                    if (translated && translated !== key) return translated;
                }
                return option.textContent;
            }),
            shouldSkipOption: config.shouldSkipOption || ((option) => {
                const value = option.value;
                const text = option.textContent;
                return value === '' && (
                    text.includes('请先加载') ||
                    text.includes('请选择') ||
                    text.includes('没有') ||
                    text.includes('加载中')
                );
            }),
            disabled: config.disabled || false,
            ...config
        };

        this.button = document.getElementById(this.config.buttonId);
        this.select = document.getElementById(this.config.selectId);
        this.dropdown = document.getElementById(this.config.dropdownId);
        this.textSpan = null;

        if (!this.button) {
            console.warn(`[DropdownManager] Button not found: ${this.config.buttonId}`);
            return;
        }

        DropdownManager.instances.push(this);
        this.init();
    }

    init() {
        this.ensureButtonStructure();
        if (!this.config.disabled && this.select && this.dropdown) {
            this.initDropdown();
        }
        this.updateButtonText();
    }

    getDefaultLabel() {
        if (this.config.defaultTextKey && window.t && typeof window.t === 'function') {
            return window.t(this.config.defaultTextKey);
        }
        return this.config.defaultText;
    }

    getIconAltText() {
        if (this.config.iconAltKey && window.t && typeof window.t === 'function') {
            return window.t(this.config.iconAltKey);
        }
        return this.config.iconAlt;
    }

    ensureButtonStructure() {
        this.textSpan = document.getElementById(this.config.textSpanId);
        const icon = this.button.querySelector(`.${this.config.iconClass}`);

        if (!this.textSpan || !icon) {
            const defaultText = this.getDefaultLabel();
            const iconAlt = this.getIconAltText();

            const iconElement = document.createElement('img');
            iconElement.src = this.config.iconSrc;
            iconElement.alt = iconAlt;
            iconElement.className = this.config.iconClass;
            iconElement.style.cssText = 'height: 40px; width: auto; max-width: 80px; image-rendering: crisp-edges; margin-right: 10px; flex-shrink: 0; object-fit: contain; display: inline-block;';

            const textElement = document.createElement('span');
            textElement.className = 'round-stroke-text';
            textElement.id = this.config.textSpanId;
            textElement.textContent = defaultText;
            textElement.setAttribute('data-text', defaultText);

            this.button.replaceChildren(iconElement, textElement);
            this.textSpan = textElement;
        }
    }

    updateButtonText() {
        if (!this.textSpan) {
            this.ensureButtonStructure();
            if (!this.textSpan) return;
        }

        const defaultText = this.getDefaultLabel();

        let text = defaultText;
        let fullText = null;

        // 如果配置了 alwaysShowDefault，始终显示默认文字
        if (this.config.alwaysShowDefault) {
            text = defaultText;
        } else if (this.select) {
            if (this.select.value) {
                const selectedOption = this.select.options[this.select.selectedIndex];
                if (selectedOption) {
                    text = this.config.getText(selectedOption);
                    fullText = text;
                }
            } else if (this.select.options.length > 0) {
                // 没有选择，但有选项：显示第一个“可显示”的选项
                // 这里不能简单跳过空值选项，否则会导致动作/表情在未选择时显示第一个文件名
                //（看起来像自动选中），而不是“增加动作/增加表情”。
                const firstDisplayOption = Array.from(this.select.options)
                    .find(opt => !this.config.shouldSkipOption(opt));
                if (firstDisplayOption) {
                    text = this.config.getText(firstDisplayOption);
                }
            }
        }

        const maxVisualWidth = this.config.maxVisualWidth || 13;
        const displayText = DropdownManager.truncateText(text, maxVisualWidth);
        const hasFullTextLabel = !!(fullText && fullText !== defaultText);
        const accessibleLabel = hasFullTextLabel ? fullText : this.getIconAltText();

        this.textSpan.textContent = displayText;
        this.textSpan.setAttribute('data-text', displayText);

        if (this.button) {
            this.button.title = accessibleLabel;
            this.button.setAttribute('aria-label', accessibleLabel);

            const imageIcon = this.button.querySelector('img');
            if (imageIcon) {
                imageIcon.alt = accessibleLabel;
                if (hasFullTextLabel) {
                    imageIcon.removeAttribute('data-i18n-alt');
                }
            }

            const svgIcon = this.button.querySelector('svg');
            if (svgIcon) {
                svgIcon.setAttribute('aria-label', accessibleLabel);
            }

            if (hasFullTextLabel) {
                this.button.removeAttribute('data-i18n-title');
                this.button.removeAttribute('data-i18n-aria');
            }
        }
    }

    updateDropdown() {
        if (!this.dropdown || !this.select) return;
        this.dropdown.innerHTML = '';

        // 辅助函数：尝试翻译 i18n 键
        const translateText = (text) => {
            if (!text) return text;
            // 如果文本看起来像 i18n 键（包含点号，如 "live2d.addMotion"）
            if (typeof text === 'string' && text.includes('.') && !text.includes(' ')) {
                try {
                    if (window.t && typeof window.t === 'function') {
                        const translated = window.t(text);
                        // 如果翻译成功（返回的不是键本身），使用翻译结果
                        if (translated && translated !== text) {
                            return translated;
                        }
                    }
                } catch (e) {
                    // 翻译失败，继续使用原文本
                }
            }
            return text;
        };

        Array.from(this.select.options).forEach(option => {
            if (this.config.shouldSkipOption(option)) return;

            const item = document.createElement('div');
            item.className = 'dropdown-item';
            item.dataset.value = option.value;
            if (option.dataset.itemId) {
                item.dataset.itemId = option.dataset.itemId;
            }

            let text = this.config.getText(option);
            // 尝试翻译文本（如果是 i18n 键）
            text = translateText(text);

            // Steam 徽章放在最前面
            if (option.dataset.itemId) {
                const steamBadge = document.createElement('span');
                steamBadge.className = 'steam-badge';
                steamBadge.textContent = 'Steam';
                item.appendChild(steamBadge);
            }

            // 添加 VRM/MMD 子类型徽章
            const subType = option.getAttribute('data-sub-type');
            if (subType === 'vrm') {
                const badge = document.createElement('span');
                badge.className = 'vrm-badge';
                badge.textContent = 'VRM';
                item.appendChild(badge);
            } else if (subType === 'mmd') {
                const badge = document.createElement('span');
                badge.className = 'mmd-badge';
                badge.textContent = 'MMD';
                item.appendChild(badge);
            }

            const textSpan = document.createElement('span');
            textSpan.className = 'dropdown-item-text';
            textSpan.textContent = text;
            textSpan.setAttribute('data-text', text);
            item.appendChild(textSpan);

            item.addEventListener('click', (e) => {
                e.stopPropagation();
                this.selectItem(option.value);
            });
            this.dropdown.appendChild(item);
        });
    }

    selectItem(value) {
        if (!this.select) return;
        this.select.value = value;
        this.select.dispatchEvent(new Event('change', { bubbles: true }));
        this.updateButtonText();
        this.hideDropdown();
        if (this.config.onChange) {
            this.config.onChange(value, this.select.options[this.select.selectedIndex]);
        }
    }

    static hideAll() {
        DropdownManager.instances.forEach(instance => { instance.hideDropdown(); });
    }

    static updateAllButtonText() {
        DropdownManager.instances.forEach(instance => { instance.updateButtonText(); });
    }

    async showDropdown() {
        if (!this.dropdown || this.config.disabled) return;

        // 在显示当前下拉菜单前，先隐藏所有其他的下拉菜单
        DropdownManager.hideAll();

        // 如果有 onBeforeShow 回调，先执行它
        if (typeof this.config.onBeforeShow === 'function') {
            await this.config.onBeforeShow();
        }

        this.updateDropdown();
        this.dropdown.style.display = 'block';

        // 检测是否显示滚动条
        this._scrollbarRafId = requestAnimationFrame(() => {
            if (this.dropdown && this.dropdown.style.display === 'block') {
                if (this.dropdown.scrollHeight > this.dropdown.clientHeight) {
                    this.dropdown.classList.add('has-scrollbar');
                } else {
                    this.dropdown.classList.remove('has-scrollbar');
                }
            }
        });
    }

    hideDropdown() {
        if (this._scrollbarRafId) {
            cancelAnimationFrame(this._scrollbarRafId);
            this._scrollbarRafId = null;
        }
        if (this.dropdown) {
            this.dropdown.style.display = 'none';
            this.dropdown.classList.remove('has-scrollbar');
        }
    }

    async toggleDropdown() {
        if (this.config.disabled) return;
        const isVisible = this.dropdown && this.dropdown.style.display === 'block';
        if (isVisible) {
            this.hideDropdown();
        } else {
            await this.showDropdown();
        }
    }

    initDropdown() {
        if (!this.button || !this.dropdown) return;
        this.button.addEventListener('click', (e) => {
            e.stopPropagation();
            if (this.button.disabled) {
                return;
            }
            this.toggleDropdown().catch(err => console.error('[DropdownManager] toggle failed:', err));
        });
        document.addEventListener('click', (e) => {
            if (!this.button.contains(e.target) && !this.dropdown.contains(e.target)) {
                this.hideDropdown();
            }
        });
    }

    enable() {
        if (this.button) this.button.disabled = false;
        if (this.select) this.select.disabled = false;
    }

    disable() {
        if (this.button) this.button.disabled = true;
        if (this.select) this.select.disabled = true;
        this.hideDropdown();
    }
}
