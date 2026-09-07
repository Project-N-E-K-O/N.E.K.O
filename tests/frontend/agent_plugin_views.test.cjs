const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const frontendRequire = createRequire(path.resolve(__dirname, '../../frontend/react-neko-chat/package.json'));
const { JSDOM } = frontendRequire('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/app/app-plugin-views.js'), 'utf8');
const hudSource = fs.readFileSync(path.resolve(__dirname, '../../static/common-ui-hud.js'), 'utf8');
const mirrorPredicate = hudSource.slice(hudSource.indexOf('window.AgentHUD.isNativeChatMirror ='),
    hudSource.indexOf('\nfunction suppressAgentHudInChatMirror'));

function environment() {
    const clients = new Set();
    const windows = [];
    function page(standalone = false, mirror = false) {
        const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:48911/', runScripts: 'outside-only' });
        const w = dom.window;
        windows.push(w);
        w.document.body.className = standalone ? 'agent-hud-standalone-page' : mirror ? 'electron-chat-window' : '';
        if (mirror) w.nekoChatWindow = {};
        w.BroadcastChannel = class {
            constructor() { clients.add(this); }
            postMessage(message) {
                for (const peer of clients) if (peer !== this && peer.onmessage) {
                    peer.onmessage({ data: JSON.parse(JSON.stringify(message)) });
                }
            }
            close() { clients.delete(this); }
        };
        w.AgentHUD = {
            _latestTasksData: { tasks: [{ id: 'task', status: 'running' }] },
            createAgentTaskHUD() {
                let hud = w.document.getElementById('agent-task-hud');
                if (!hud) {
                    hud = w.document.createElement('div');
                    hud.id = 'agent-task-hud';
                    hud.innerHTML = '<div id="agent-task-hud-stats"></div><button id="agent-task-hud-cancel">Cancel tasks</button><div id="agent-task-list">Real task</div>';
                    w.document.body.appendChild(hud);
                }
                return hud;
            },
            showAgentTaskHUD() { this.createAgentTaskHUD().style.display = 'flex'; },
            expandAgentTaskHUD() {},
        };
        w.NekoChatWindow = {
            mountPluginContent(node, block) { node.currentBlock = block; node.renders = (node.renders || 0) + 1; },
            unmountPluginContent(node) { node.unmounted = true; },
        };
        w.eval(mirrorPredicate);
        w.eval(source);
        return w;
    }
    return { page, dispose() { clients.clear(); windows.forEach(w => w.close()); } };
}
const view = { type: 'html_card', presentation: 'agent', operation: 'create', cardId: 'one',
    pluginId: 'demo', targetLanlan: 'Alice', title: 'Downloads', html: '<button>Run</button>',
    css: 'button{color:red}', summary: 'Download', actions: { go: { entry: 'run' } } };

test('shared mirror predicate preserves browser Chat, Pet and standalone ownership', () => {
    const env = environment();
    try {
        const w = env.page();
        w.document.body.className = 'electron-chat-window';
        assert.equal(w.AgentHUD.isNativeChatMirror(), false, 'browser Chat keeps its HUD');
        w.nekoChatWindow = {};
        assert.equal(w.AgentHUD.isNativeChatMirror(), true, 'native Chat bridge marks a mirror');
        w.document.body.classList.add('lanlan-pet-mode');
        assert.equal(w.AgentHUD.isNativeChatMirror(), false, 'Pet ownership takes precedence');
        w.document.body.className = 'agent-hud-standalone-page electron-chat-window neko-electron-runtime';
        assert.equal(w.AgentHUD.isNativeChatMirror(), false, 'standalone HUD owns its rendering');
        delete w.nekoChatWindow;
        w.document.body.className = 'electron-chat-window neko-electron-runtime';
        assert.equal(w.AgentHUD.isNativeChatMirror(), true, 'runtime class is sufficient');
    } finally { env.dispose(); }
});

test('updates preserve the mount and selected task tab; closing only removes content', () => {
    const env = environment();
    try {
        const w = env.page();
        w.NekoPluginViews.receive(view);
        const node = w.document.querySelector('.agent-plugin-view');
        w.document.getElementById('agent-content-tasks-tab').click();
        const renders = node.renders;
        w.NekoPluginViews.syncHud();
        assert.equal(node.renders, renders, 'task rendering does not rerender plugin content');
        w.NekoPluginViews.receive({ ...view, operation: 'update', html: '<p>Progress</p>' });
        assert.equal(w.document.querySelector('.agent-plugin-view'), node);
        assert.equal(node.currentBlock.css, view.css);
        assert.equal(w.document.getElementById('agent-task-list').style.display, 'flex');
        w.NekoPluginViews.receive({ ...view, operation: 'close' });
        assert.equal(node.unmounted, true);
        assert.equal(w.NekoPluginViews.hasContent(), false);
        assert.equal(w.AgentHUD._latestTasksData.tasks[0].status, 'running');
        assert.equal(w.NekoPluginViews.receive({ ...view, operation: 'update' }), false);
    } finally { env.dispose(); }
});

test('new instance replaces only its plugin slot and ignores stale close', () => {
    const env = environment();
    try {
        const w = env.page();
        w.NekoPluginViews.receive(view);
        w.NekoPluginViews.receive({ ...view, pluginId: 'other' });
        w.NekoPluginViews.receive({ ...view, cardId: 'new' });
        w.NekoPluginViews.receive({ ...view, operation: 'close' });
        assert.equal(w.document.querySelectorAll('.agent-plugin-view').length, 2);
        assert.ok(w.document.querySelector('[data-view-id="new"]'));
        w.document.getElementById('agent-plugin-close').click();
        assert.equal(w.document.querySelectorAll('.agent-plugin-view').length, 1);
        assert.equal(w.NekoPluginViews.hasContent(), true);
    } finally { env.dispose(); }
});

test('late standalone page gets current snapshot and closes the owner view', () => {
    const env = environment();
    try {
        const owner = env.page();
        owner.NekoPluginViews.receive(view);
        owner.NekoPluginViews.receive({ ...view, operation: 'update', html: 'Latest' });
        const hud = env.page(true);
        assert.equal(hud.document.querySelector('.agent-plugin-view').currentBlock.html, 'Latest');
        hud.document.getElementById('agent-plugin-close').click();
        assert.equal(owner.NekoPluginViews.hasContent(), false);
        assert.equal(hud.NekoPluginViews.hasContent(), false);
        owner.NekoPluginViews.receive({ ...view, operation: 'update', html: 'Late response' });
        assert.equal(hud.NekoPluginViews.hasContent(), false);
    } finally { env.dispose(); }
});

test('native chat mirrors do not duplicate owner views; role reset clears replicas', () => {
    const env = environment();
    try {
        const owner = env.page();
        const mirror = env.page(false, true);
        const hud = env.page(true);
        owner.NekoPluginViews.receive(view);
        assert.equal(mirror.NekoPluginViews.receive(view), false);
        assert.equal(mirror.NekoPluginViews.hasContent(), false);
        assert.equal(hud.NekoPluginViews.hasContent(), true);
        owner.NekoPluginViews.clear();
        assert.equal(hud.NekoPluginViews.hasContent(), false);
    } finally { env.dispose(); }
});

test('later creates preserve the chosen tab and collapsed state, with an unread indicator', () => {
    const env = environment();
    try {
        const w = env.page();
        let shows = 0;
        w.AgentHUD.expandAgentTaskHUD = () => { shows++; };
        w.NekoPluginViews.receive(view);
        const first = w.document.querySelector('.agent-plugin-tab');
        assert.equal(first.textContent, 'Downloads', 'technical source IDs stay out of the tab label');
        assert.match(first.title, /demo.*Alice/);
        assert.equal(w.document.querySelector('#agent-plugin-select'), null);
        w.document.getElementById('agent-content-tasks-tab').click();
        w.document.getElementById('agent-task-hud').dataset.agentHudCollapsed = 'true';
        w.NekoPluginViews.receive({ ...view, pluginId: 'second', cardId: 'second', title: 'Report' });
        assert.equal(shows, 1, 'a later create must not reopen the window');
        const second = w.document.querySelectorAll('.agent-plugin-tab')[1];
        assert.equal(second.classList.contains('has-unread'), true);
        assert.equal(w.document.getElementById('agent-content-tasks-tab').getAttribute('aria-selected'), 'true');
        w.NekoPluginViews.receive({ ...view, operation: 'update', title: 'Updated download' });
        assert.equal(first.textContent, 'Updated download');
        assert.equal(w.document.getElementById('agent-content-navigation').style.display, 'none');
        w.document.getElementById('agent-task-hud').dataset.agentHudCollapsed = 'false';
        second.click();
        assert.equal(second.classList.contains('has-unread'), false);
        assert.equal(second.getAttribute('aria-selected'), 'true');
        assert.equal(first.getAttribute('aria-selected'), 'false');
        assert.equal(w.document.getElementById(second.getAttribute('aria-controls')).dataset.viewId, 'second');
    } finally { env.dispose(); }
});

test('tabs support keyboard selection and close selects the neighboring page', () => {
    const env = environment();
    try {
        const w = env.page();
        w.NekoPluginViews.receive(view);
        w.NekoPluginViews.receive({ ...view, pluginId: 'second', cardId: 'two', title: 'Report' });
        const tasks = w.document.getElementById('agent-content-tasks-tab');
        const first = w.document.querySelectorAll('.agent-plugin-tab')[0];
        const second = w.document.querySelectorAll('.agent-plugin-tab')[1];
        first.focus();
        first.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        assert.equal(w.document.activeElement, second);
        assert.equal(second.tabIndex, 0);
        assert.equal(first.tabIndex, -1);
        w.document.getElementById('agent-plugin-close').click();
        assert.equal(w.document.activeElement, first);
        assert.equal(first.getAttribute('aria-selected'), 'true');
        first.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
        assert.equal(w.document.activeElement, tasks);
        assert.equal(w.document.getElementById('agent-task-list').style.display, 'flex');
        assert.equal(w.document.getElementById('agent-plugin-close').style.display, 'none');
    } finally { env.dispose(); }
});
