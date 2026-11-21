/**
 * 表情包页的下拉：闭合控件是一颗药丸按钮，展开菜单跟 API 设置同一套
 * （按钮 + 选项列表），不另造一套会丢打开状态的组件。
 *
 * select 仍是唯一的值。菜单每次打开时现读 option；选中写回 value 并
 * 派发 change。菜单挂在 document.body 上，侧栏的 overflow / backdrop-filter
 * 裁不到它。行被页面重建时，旧菜单跟着旧 select 一起拆掉。
 */
(function () {
    'use strict';

    const valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    const indexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
    const tracked = new Set();
    let suppressScrollClose = false;

    function visibleOptions(select) {
        return Array.from(select.options).filter((opt) => !opt.hidden);
    }

    function selectedLabel(select) {
        const opt = select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
        return opt ? opt.textContent.trim() : '';
    }

    function closeMenu({ menu, trigger }) {
        menu.classList.remove('is-open');
        menu.style.top = '';
        menu.style.left = '';
        menu.style.width = '';
        trigger.setAttribute('aria-expanded', 'false');
    }

    function closeAll(exceptEntry) {
        tracked.forEach((entry) => {
            if (entry !== exceptEntry && entry.menu.classList.contains('is-open')) closeMenu(entry);
        });
    }

    function paint(entry) {
        const { select, trigger, value } = entry;
        value.textContent = selectedLabel(select);
        const disabled = !!select.disabled;
        trigger.disabled = disabled;
        trigger.closest('.emote-select').classList.toggle('is-disabled', disabled);
        const aria = select.getAttribute('aria-label');
        if (aria) trigger.setAttribute('aria-label', aria);
        if (disabled) closeMenu(entry);
    }

    function iconSvg(kind) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 16 16');
        svg.setAttribute('width', '14');
        svg.setAttribute('height', '14');
        svg.setAttribute('aria-hidden', 'true');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', 'currentColor');
        path.setAttribute('stroke-width', '1.6');
        path.setAttribute('stroke-linecap', 'round');
        path.setAttribute('stroke-linejoin', 'round');
        path.setAttribute('d', kind === 'rename'
            ? 'M9.2 2.6l4.2 4.2M2.8 13.2l2.5-.5L13.2 4.8 11.2 2.8 3.3 10.7z'
            : 'M3.2 4.4h9.6M6.2 4.4V3h3.6v1.4M4.4 4.4l.6 8.4h6l.6-8.4');
        svg.appendChild(path);
        return svg;
    }

    function dispatchSelectAction(select, detail) {
        select.dispatchEvent(new CustomEvent('emote-select-action', { bubbles: true, detail }));
    }

    /** 自建组这一行：铅笔改名，垃圾桶删除。点字本身仍然是选中。 */
    function appendGroupActions(entry, item, opt, label) {
        const actions = document.createElement('span');
        actions.className = 'emote-select-actions';
        ['rename', 'delete'].forEach((action) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'emote-select-action' + (action === 'delete' ? ' is-delete' : '');
            button.dataset.action = action;
            button.setAttribute('aria-label', action === 'rename'
                ? (opt.dataset.renameLabel || '改名')
                : (opt.dataset.deleteLabel || '删除'));
            button.appendChild(iconSvg(action));
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (action === 'delete') {
                    closeMenu(entry);
                    triggerFocus(entry.trigger);
                    dispatchSelectAction(entry.select, { action: 'delete', value: opt.value });
                    return;
                }
                beginInlineRename(entry, opt, label);
            });
            actions.appendChild(button);
        });
        item.appendChild(actions);
    }

    function beginInlineRename(entry, opt, label) {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'emote-select-rename';
        input.maxLength = 40;
        input.value = opt.textContent.trim();
        input.setAttribute('aria-label', opt.dataset.renameLabel || '改名');
        label.replaceWith(input);
        let closed = false;
        const finish = (commit) => {
            if (closed) return;
            closed = true;
            const name = input.value.trim();
            if (commit && name && name !== opt.textContent.trim()) {
                dispatchSelectAction(entry.select, { action: 'rename', value: opt.value, name });
            } else if (entry.menu.classList.contains('is-open')) {
                buildMenu(entry);
                placeMenu(entry);
            }
        };
        input.addEventListener('click', (event) => event.stopPropagation());
        input.addEventListener('keydown', (event) => {
            event.stopPropagation();
            if (event.key === 'Enter') {
                event.preventDefault();
                finish(true);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                finish(false);
            }
        });
        input.addEventListener('blur', () => finish(true));
        input.focus();
        input.select();
    }

    function buildMenu(entry) {
        const { select, menu } = entry;
        const focused = menu.contains(document.activeElement)
            ? document.activeElement.closest('.emote-select-option')?.dataset.value : null;
        menu.replaceChildren();
        const options = visibleOptions(select);
        if (!options.length) {
            const empty = document.createElement('div');
            empty.className = 'emote-select-empty';
            empty.textContent = '—';
            menu.appendChild(empty);
            return;
        }
        options.forEach((opt) => {
            const writable = opt.dataset.writable === '1';
            const item = document.createElement(writable ? 'div' : 'button');
            if (!writable) item.type = 'button';
            item.className = 'emote-select-option' + (writable ? ' has-actions' : '');
            item.dataset.value = opt.value;
            item.setAttribute('role', 'option');
            if (writable) item.setAttribute('aria-keyshortcuts', 'F2 Delete');
            item.tabIndex = -1;
            const label = document.createElement('span');
            label.className = 'emote-select-option-label';
            label.textContent = opt.textContent.trim();
            item.appendChild(label);
            const selected = opt.index === select.selectedIndex;
            item.classList.toggle('is-selected', selected);
            item.setAttribute('aria-selected', selected ? 'true' : 'false');
            if (opt.disabled) item.disabled = true;
            if (writable) appendGroupActions(entry, item, opt, label);
            item.addEventListener('click', (event) => {
                if (event.target.closest('.emote-select-action, .emote-select-rename')) return;
                event.preventDefault();
                event.stopPropagation();
                if (opt.disabled || select.disabled) return;
                if (select.selectedIndex !== opt.index) {
                    select.selectedIndex = opt.index;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }
                paint(entry);
                closeMenu(entry);
                triggerFocus(entry.trigger);
            });
            menu.appendChild(item);
        });
        if (focused != null) {
            const restored = Array.from(menu.querySelectorAll('.emote-select-option'))
                .find((item) => item.dataset.value === focused);
            if (menu.classList.contains('is-open')) triggerFocus(restored || entry.trigger);
            else triggerFocus(entry.trigger);
        }
    }

    function triggerFocus(trigger) {
        if (trigger && typeof trigger.focus === 'function') trigger.focus();
    }

    function placeMenu(entry) {
        const rect = entry.trigger.getBoundingClientRect();
        const width = Math.max(rect.width, 168);
        const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
        entry.menu.style.width = width + 'px';
        entry.menu.style.left = left + 'px';
        const menuHeight = Math.min(entry.menu.scrollHeight, 240);
        const spaceBelow = window.innerHeight - rect.bottom;
        const openUp = spaceBelow < menuHeight + 12 && rect.top > spaceBelow;
        const top = openUp
            ? Math.max(8, rect.top - menuHeight - 6)
            : rect.bottom + 6;
        entry.menu.style.top = top + 'px';
    }

    function openMenu(entry) {
        if (entry.select.disabled) return;
        suppressScrollClose = true;
        closeAll(entry);
        buildMenu(entry);
        entry.menu.classList.add('is-open');
        entry.trigger.setAttribute('aria-expanded', 'true');
        placeMenu(entry);
        const selected = entry.menu.querySelector('.emote-select-option.is-selected')
            || entry.menu.querySelector('.emote-select-option:not(:disabled)');
        if (selected) {
            // focus 默认会把页面滚到选项上，滚动监听会立刻把刚打开的菜单关掉。
            selected.focus({ preventScroll: true });
            entry.menu.scrollTop = Math.max(0, selected.offsetTop - 8);
        }
        requestAnimationFrame(() => { suppressScrollClose = false; });
    }

    function watchValue(select, entry) {
        Object.defineProperty(select, 'value', {
            configurable: true,
            enumerable: true,
            get() { return valueDesc.get.call(this); },
            set(next) {
                valueDesc.set.call(this, next);
                paint(entry);
            },
        });
        Object.defineProperty(select, 'selectedIndex', {
            configurable: true,
            enumerable: true,
            get() { return indexDesc.get.call(this); },
            set(next) {
                indexDesc.set.call(this, next);
                paint(entry);
            },
        });
    }

    function enhance(select) {
        if (!(select instanceof HTMLSelectElement)) return;
        if (select.dataset.emoteSelect === '1' || select.hasAttribute('data-native-select')) return;
        select.dataset.emoteSelect = '1';

        const wrap = document.createElement('div');
        wrap.className = 'emote-select';
        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'emote-select-trigger';
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');
        const value = document.createElement('span');
        value.className = 'emote-select-value';
        const caret = document.createElement('span');
        caret.className = 'emote-select-caret';
        caret.setAttribute('aria-hidden', 'true');
        trigger.append(value, caret);

        const menu = document.createElement('div');
        menu.className = 'emote-select-menu';
        menu.setAttribute('role', 'listbox');
        if (select.id) {
            trigger.id = select.id + '-trigger';
            menu.id = select.id + '-menu';
            trigger.setAttribute('aria-controls', menu.id);
            document.querySelectorAll('label[for="' + CSS.escape(select.id) + '"]').forEach((label) => {
                label.htmlFor = trigger.id;
            });
        }

        select.parentNode.insertBefore(wrap, select);
        wrap.append(select, trigger);
        document.body.appendChild(menu);
        select.classList.add('emote-select-native');
        select.tabIndex = -1;
        select.setAttribute('aria-hidden', 'true');

        const entry = { select, wrap, trigger, value, menu };
        tracked.add(entry);
        watchValue(select, entry);

        // 包在 <label> 里时，点文字会把点击转发给 select，从而弹出系统列表。
        select.addEventListener('mousedown', (event) => event.preventDefault());
        select.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (!menu.classList.contains('is-open')) openMenu(entry);
        });

        trigger.addEventListener('pointerdown', (event) => event.stopPropagation());
        trigger.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (menu.classList.contains('is-open')) closeMenu(entry);
            else openMenu(entry);
        });
        trigger.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openMenu(entry);
            }
        });
        menu.addEventListener('keydown', (event) => {
            const items = Array.from(menu.querySelectorAll('.emote-select-option:not(:disabled)'));
            const at = items.indexOf(document.activeElement);
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                (items[Math.min(items.length - 1, Math.max(0, at) + (at < 0 ? 0 : 1))] || items[0])?.focus();
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                (items[Math.max(0, at < 0 ? 0 : at - 1)] || items[0])?.focus();
            } else if (event.key === 'Home') {
                event.preventDefault();
                items[0]?.focus();
            } else if (event.key === 'End') {
                event.preventDefault();
                items[items.length - 1]?.focus();
            } else if (event.key === 'Enter') {
                const current = document.activeElement;
                if (current && current.classList.contains('emote-select-option') && current.tagName !== 'BUTTON') {
                    event.preventDefault();
                    current.click();
                }
            } else if ((event.key === 'F2' || event.key === 'Delete')
                && document.activeElement?.classList.contains('has-actions')) {
                event.preventDefault();
                const action = event.key === 'F2' ? 'rename' : 'delete';
                document.activeElement.querySelector(`[data-action="${action}"]`)?.click();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                closeMenu(entry);
                triggerFocus(trigger);
            } else if (event.key === 'Tab') {
                closeMenu(entry);
                triggerFocus(trigger);
            }
        });

        select.addEventListener('change', () => paint(entry));
        const sync = new MutationObserver(() => {
            paint(entry);
            if (menu.classList.contains('is-open')) {
                buildMenu(entry);
                placeMenu(entry);
            }
        });
        sync.observe(select, {
            childList: true,
            subtree: true,
            characterData: true,
            attributes: true,
            attributeFilter: ['disabled', 'hidden', 'aria-label'],
        });
        paint(entry);
    }

    document.addEventListener('pointerdown', (event) => {
        const target = event.target;
        if (!target || !target.closest) return;
        if (target.closest('.emote-select') || target.closest('.emote-select-menu')) return;
        closeAll();
    }, true);

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeAll();
    });

    window.addEventListener('resize', () => closeAll());

    document.addEventListener('scroll', (event) => {
        if (suppressScrollClose) return;
        const target = event.target;
        if (target && target.classList && target.classList.contains('emote-select-menu')) return;
        closeAll();
    }, true);

    new MutationObserver((mutations) => {
        tracked.forEach((entry) => {
            if (entry.select.isConnected) return;
            entry.menu.remove();
            tracked.delete(entry);
        });
        mutations.forEach((mutation) => {
            mutation.addedNodes.forEach((node) => {
                if (node.nodeType !== 1) return;
                if (node.tagName === 'SELECT') enhance(node);
                else if (node.querySelectorAll) node.querySelectorAll('select').forEach(enhance);
            });
        });
    }).observe(document.documentElement, { childList: true, subtree: true });

    function boot() {
        document.querySelectorAll('select').forEach(enhance);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();

})();
