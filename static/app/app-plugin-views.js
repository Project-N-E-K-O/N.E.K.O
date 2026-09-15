/** Plugin HTML views share the existing AgentHUD. No desktop window/IPC changes. */
(function () {
    'use strict';
    var records = new Map();
    var mounts = new Map();
    var tabNodes = new Map();
    var unread = new Set();
    var nextTabId = 0;
    var selectedKey = '';
    var activeTab = 'tasks';
    var owner = 'plugin-views-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    var standalone = document.body.classList.contains('agent-hud-standalone-page');
    var channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('neko-plugin-views-v1') : null;

    function t(key, fallback) {
        if (typeof window.t !== 'function') return fallback;
        var result = window.t('agent.taskHud.' + key);
        return result && result !== 'agent.taskHud.' + key ? result : fallback;
    }
    function keyOf(view) { return JSON.stringify([view.pluginId, view.targetLanlan]); }
    function post(message) {
        if (channel) channel.postMessage(Object.assign({ sender: owner }, message));
    }
    function isNativeChatMirror() {
        return !!(window.AgentHUD && window.AgentHUD.isNativeChatMirror && window.AgentHUD.isNativeChatMirror());
    }
    function remove(key) {
        var node = mounts.get(key);
        if (node) {
            if (window.NekoChatWindow && window.NekoChatWindow.unmountPluginContent) {
                window.NekoChatWindow.unmountPluginContent(node);
            }
            node.remove();
            mounts.delete(key);
        }
        var keys = Array.from(records.keys());
        var index = keys.indexOf(key);
        records.delete(key);
        unread.delete(key);
        var tab = tabNodes.get(key);
        if (tab) tab.remove();
        tabNodes.delete(key);
        if (selectedKey === key) {
            var remaining = Array.from(records.keys());
            selectedKey = remaining[Math.min(index, remaining.length - 1)] || '';
        }
    }
    function publishSnapshot(to) {
        post({ kind: 'snapshot', to: to, views: Array.from(records.values()).filter(function (view) { return view.owner === owner; }) });
    }
    function show() {
        if (!window.AgentHUD) return;
        window.AgentHUD.createAgentTaskHUD();
        if (window.nekoAgentHud && typeof window.nekoAgentHud.show === 'function') window.nekoAgentHud.show();
        else window.AgentHUD.showAgentTaskHUD({ ignoreVisibilityPreference: true });
        window.AgentHUD.expandAgentTaskHUD();
        syncHud();
    }
    function apply(view, source) {
        if (!view || typeof view.cardId !== 'string' || !view.cardId ||
            typeof view.pluginId !== 'string' || !view.pluginId ||
            typeof view.targetLanlan !== 'string' || !view.targetLanlan) return false;
        var key = keyOf(view);
        var existing = records.get(key);
        if (view.operation === 'create') {
            var firstContent = records.size === 0;
            var wasSelected = selectedKey === key;
            if (typeof view.html !== 'string') return false;
            if (existing && existing.cardId !== view.cardId) remove(key);
            records.set(key, Object.assign({ css: '', summary: '', actions: {} }, view, { owner: source }));
            if (firstContent) {
                selectedKey = key;
                activeTab = 'plugins';
            } else if (wasSelected) {
                selectedKey = key;
            }
            if (activeTab !== 'plugins' || selectedKey !== key) unread.add(key);
        } else {
            if (!existing || existing.cardId !== view.cardId || existing.owner !== source) return false;
            if (view.operation === 'close') remove(key);
            else if (view.operation === 'update') records.set(key, Object.assign({}, existing, view));
            else return false;
        }
        syncHud();
        return true;
    }
    function receive(view) {
        // Native Chat receives a mirror of Pet's socket; only Pet owns these views.
        if (standalone || isNativeChatMirror()) return false;
        var reveal = !records.size && view && view.operation === 'create';
        if (!apply(view, owner)) return false;
        post({ kind: 'view', view: view });
        if (reveal) show();
        if (view.operation === 'close' && !records.size && window.checkAndToggleTaskHUD) window.checkAndToggleTaskHUD();
        return true;
    }
    function closeSelected() {
        var view = records.get(selectedKey);
        if (!view) return;
        var close = { pluginId: view.pluginId, targetLanlan: view.targetLanlan, cardId: view.cardId, operation: 'close' };
        if (view.owner === owner) receive(close);
        else {
            post({ kind: 'close-request', to: view.owner, view: close });
            apply(close, view.owner);
        }
    }
    function selectTab(key) {
        if (key) selectedKey = key;
        activeTab = key ? 'plugins' : 'tasks';
        syncHud();
    }
    function button(id, onClick) {
        var element = document.createElement('button');
        element.id = id;
        element.type = 'button';
        element.addEventListener('click', onClick);
        return element;
    }
    function syncHud() {
        var hud = document.getElementById('agent-task-hud');
        if (!hud) return;
        var taskList = document.getElementById('agent-task-list');
        if (!taskList) return;
        var tabs = document.getElementById('agent-content-tabs');
        if (!tabs) {
            var navigation = document.createElement('div');
            navigation.id = 'agent-content-navigation';
            tabs = document.createElement('div');
            tabs.id = 'agent-content-tabs';
            tabs.setAttribute('role', 'tablist');
            var tasksButton = button('agent-content-tasks-tab', function () { selectTab(''); });
            tasksButton.setAttribute('role', 'tab');
            tasksButton.setAttribute('aria-controls', 'agent-task-list');
            tabs.appendChild(tasksButton);
            tabs.addEventListener('keydown', function (event) {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                var buttons = Array.from(tabs.querySelectorAll('[role="tab"]'));
                var index = buttons.indexOf(document.activeElement);
                if (index < 0) return;
                event.preventDefault();
                if (event.key === 'Home') index = 0;
                else if (event.key === 'End') index = buttons.length - 1;
                else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
                buttons[index].click();
                buttons[index].focus();
            });
            var close = button('agent-plugin-close', function () {
                closeSelected();
                var selected = tabs.querySelector('[aria-selected="true"]');
                if (selected) selected.focus();
            });
            close.textContent = '×';
            navigation.append(tabs, close);
            hud.insertBefore(navigation, taskList);
            taskList.setAttribute('role', 'tabpanel');
            taskList.setAttribute('aria-labelledby', tasksButton.id);
            var panel = document.createElement('div');
            panel.id = 'agent-plugin-content';
            var content = document.createElement('div');
            content.id = 'agent-plugin-views';
            panel.appendChild(content);
            hud.appendChild(panel);
        }
        if (!records.has(selectedKey)) selectedKey = records.size ? records.keys().next().value : '';
        if (!records.size) activeTab = 'tasks';
        var pluginSelected = activeTab === 'plugins' && records.size > 0;
        var collapsed = hud.dataset.agentHudCollapsed === 'true';
        hud.classList.toggle('has-plugin-content', records.size > 0);
        document.getElementById('agent-content-navigation').style.display = records.size && !collapsed ? 'flex' : 'none';
        tabs.setAttribute('aria-label', t('contentTabs', 'Tasks and plugin content'));
        var tasksTab = document.getElementById('agent-content-tasks-tab');
        var taskData = window.AgentHUD._latestTasksData;
        var activeTasks = taskData && (taskData.tasks || []).filter(function (task) {
            return task.status === 'running' || task.status === 'queued';
        }).length || 0;
        tasksTab.textContent = t('tasksTab', 'Tasks') + (activeTasks ? ' · ' + activeTasks : '');
        tasksTab.setAttribute('aria-selected', String(!pluginSelected));
        tasksTab.tabIndex = pluginSelected ? -1 : 0;
        taskList.style.display = !collapsed && !pluginSelected ? 'flex' : 'none';
        document.getElementById('agent-plugin-content').style.display = !collapsed && pluginSelected ? 'flex' : 'none';
        var stats = document.getElementById('agent-task-hud-stats');
        if (stats) stats.style.display = pluginSelected ? 'none' : 'flex';
        // A collapsed plugin window still needs an identifiable title, not just an arrow.
        var title = document.getElementById('agent-task-hud-title');
        if (title) title.style.display = collapsed && !pluginSelected ? 'none' : '';
        var cancel = document.getElementById('agent-task-hud-cancel');
        if (cancel) cancel.style.display = !pluginSelected && activeTasks ? 'flex' : 'none';
        var close = document.getElementById('agent-plugin-close');
        close.style.display = pluginSelected ? 'flex' : 'none';
        close.title = t('closePluginContent', 'Close content');
        close.setAttribute('aria-label', close.title);
        if (pluginSelected && !collapsed) unread.delete(selectedKey);
        records.forEach(function (view, key) {
            var tab = tabNodes.get(key);
            if (!tab) {
                tab = button('agent-plugin-tab-' + (++nextTabId), function () { selectTab(key); });
                tab.className = 'agent-plugin-tab';
                tab.setAttribute('role', 'tab');
                tab.dataset.viewKey = key;
                var label = document.createElement('span');
                label.className = 'agent-plugin-tab-label';
                var indicator = document.createElement('span');
                indicator.className = 'agent-plugin-tab-unread';
                indicator.setAttribute('aria-hidden', 'true');
                tab.append(label, indicator);
                tabs.appendChild(tab);
                tabNodes.set(key, tab);
            }
            var label = view.title || view.summary || view.pluginId;
            var selected = pluginSelected && selectedKey === key;
            var isUnread = unread.has(key);
            tab.firstChild.textContent = label;
            tab.title = label + ' · ' + view.pluginId + ' · ' + view.targetLanlan;
            tab.setAttribute('aria-label', tab.title + (isUnread ? ' · ' + t('newPluginContent', 'New content') : ''));
            tab.setAttribute('aria-selected', String(selected));
            tab.classList.toggle('has-unread', isUnread);
            tab.tabIndex = selected ? 0 : -1;
        });
        var selectedTab = pluginSelected ? tabNodes.get(selectedKey) : tasksTab;
        if (selectedTab && tabs.dataset.selectedTab !== selectedTab.id) {
            tabs.dataset.selectedTab = selectedTab.id;
            if (!collapsed && selectedTab.scrollIntoView) selectedTab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
        records.forEach(function (view, key) {
            var node = mounts.get(key);
            if (!node) {
                node = document.createElement('div');
                node.className = 'agent-plugin-view';
                node.dataset.viewId = view.cardId;
                node.id = tabNodes.get(key).id + '-panel';
                node.setAttribute('role', 'tabpanel');
                node.setAttribute('aria-labelledby', tabNodes.get(key).id);
                tabNodes.get(key).setAttribute('aria-controls', node.id);
                document.getElementById('agent-plugin-views').appendChild(node);
                mounts.set(key, node);
            }
            node.style.display = key === selectedKey ? 'block' : 'none';
            var api = window.NekoChatWindow;
            if (api && api.mountPluginContent) {
                // Cache the rendering input: task timers don't need to reparse plugin HTML.
                var serialized = JSON.stringify(view);
                if (node._pluginViewDefinition !== serialized) {
                    try {
                        api.mountPluginContent(node, Object.assign({}, view, { type: 'html_card', presentation: 'agent' }));
                        node._pluginViewDefinition = serialized;
                    } catch (error) {
                        console.warn('[PluginViews] Invalid view content');
                        node.textContent = view.summary || view.title || view.pluginId;
                    }
                }
            }
        });
    }
    function clear() {
        Array.from(records).forEach(function (entry) { if (entry[1].owner === owner) remove(entry[0]); });
        syncHud();
        publishSnapshot();
    }
    if (channel) channel.onmessage = function (event) {
        var message = event.data;
        if (!message || message.sender === owner || (message.to && message.to !== owner)) return;
        if (message.kind === 'request-snapshot' && !standalone && !isNativeChatMirror()) {
            publishSnapshot(message.sender);
        } else if (message.kind === 'close-request' && message.to === owner) {
            receive(message.view);
        } else if (standalone && message.kind === 'view') {
            var reveal = !records.size && message.view && message.view.operation === 'create';
            if (apply(message.view, message.sender) && reveal) show();
        } else if (standalone && message.kind === 'snapshot' && Array.isArray(message.views)) {
            var hadContent = records.size > 0;
            // Remove only that owner's missing views, not another page's content.
            var ids = new Set(message.views.map(function (view) { return view.cardId; }));
            Array.from(records).forEach(function (entry) {
                if (entry[1].owner === message.sender && !ids.has(entry[1].cardId)) remove(entry[0]);
            });
            message.views.forEach(function (view) {
                var existing = records.get(keyOf(view));
                apply(Object.assign({}, view, { operation: existing && existing.cardId === view.cardId && existing.owner === message.sender ? 'update' : 'create' }), message.sender);
            });
            syncHud();
            if (!hadContent && records.size) show();
        }
    };
    window.NekoPluginViews = { receive: receive, hasContent: function () { return records.size > 0; }, syncHud: syncHud, clear: clear };
    window.addEventListener('neko-plugin-content-ready', syncHud);
    window.addEventListener('localechange', syncHud);
    window.addEventListener('pagehide', function () { if (!standalone) clear(); });
    if (standalone) post({ kind: 'request-snapshot' });
})();
