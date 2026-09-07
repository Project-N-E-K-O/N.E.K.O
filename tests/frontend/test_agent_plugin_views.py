"""Browser regressions for plugin content inside the existing task HUD."""

import re

import pytest
from playwright.sync_api import Page, expect


def _open_hud(page: Page, running_server: str, surface_path: str = "chat") -> None:
    page.add_init_script(
        "localStorage.setItem('neko_tutorial_settings', 'seen');"
        "localStorage.setItem('neko-agent-taskhud-visible', 'true');"
        "localStorage.setItem('agent-task-hud-collapsed-v2', 'false');"
    )
    page.goto(f"{running_server}/{surface_path}", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => window.NekoPluginViews && window.NekoChatWindow"
        " && typeof NekoChatWindow.mountPluginContent === 'function'"
        " && window.AgentHUD && window.checkAndToggleTaskHUD"
        " && window.reactChatWindowHost && window.appState"
    )
    page.wait_for_function("() => appState.socket && appState.socket.readyState === 1")
    page.evaluate("""() => {
        NekoPluginViews.clear();
        window._agentTaskMap = new Map();
        window._agentStatusSnapshot = {flags: {
            agent_enabled: false, computer_use_enabled: false,
            browser_use_enabled: false, user_plugin_enabled: false,
            openclaw_enabled: false
        }};
        if (window.agent_ui_v2_state) window.agent_ui_v2_state.optimistic = {};
        if (window.agentStateMachine) {
            window.agentStateMachine._cachedFlags = window._agentStatusSnapshot.flags;
        }
        document.querySelectorAll('input[id*="-agent-"]').forEach(input => {
            input.checked = input.id.endsWith('-taskhud');
        });
        reactChatWindowHost.clearMessages();
        reactChatWindowHost.appendMessage({
            id: 'ordinary-chat-message', role: 'assistant', author: 'Alice',
            time: '12:00:00', status: 'sent',
            blocks: [{type: 'text', text: 'Ordinary chat stays here'}]
        });
        AgentHUD.updateAgentTaskHUD({success: true, tasks: [], running_count: 0, queued_count: 0});
    }""")


def _view(card_id="view-one", plugin_id="demo", **fields):
    return {
        "type": "html_card",
        "presentation": "agent",
        "operation": "create",
        "cardId": card_id,
        "pluginId": plugin_id,
        "targetLanlan": "Alice",
        "title": "Download task",
        "html": '<p>Ready to download</p><button data-neko-action="go">Download</button>',
        "css": "button { padding: 8px; }",
        "summary": "Download task",
        "actions": {"go": {"entry": "download", "args": {"file_id": "123"}}},
        **fields,
    }


def _receive(page: Page, view: dict) -> bool:
    return page.evaluate("view => NekoPluginViews.receive(view)", view)


def _change(page: Page, original: dict, operation="update", **fields) -> bool:
    return _receive(page, {
        "type": "html_card", "presentation": "agent", "operation": operation,
        "cardId": original["cardId"], "pluginId": original["pluginId"],
        "targetLanlan": original["targetLanlan"], **fields,
    })


def _content(page: Page, card_id: str):
    return page.locator(f'.agent-plugin-view[data-view-id="{card_id}"]')


def _task_update(page: Page, status="running") -> None:
    page.evaluate("""status => {
        const task = {
            id: 'existing-agent-task', status, type: 'user_plugin',
            lanlan_name: 'Alice', description: 'Existing agent task',
            params: {description: 'Existing agent task'},
            start_time: new Date().toISOString()
        };
        window._agentTaskMap = new Map([[task.id, task]]);
        AgentHUD.updateAgentTaskHUD({
            success: true, tasks: [task],
            running_count: status === 'running' ? 1 : 0,
            queued_count: status === 'queued' ? 1 : 0
        });
    }""", status)
    expect(page.locator('#agent-task-list .task-card')).to_have_count(1)


@pytest.mark.frontend
@pytest.mark.parametrize("surface_path", ["", "chat", "chat_full"], ids=["index", "compact", "full"])
def test_plugin_content_keeps_hud_visible_without_agent_tasks(mock_page, running_server, surface_path):
    page = mock_page
    calls = []

    def download(route):
        calls.append(route.request.post_data_json)
        route.fulfill(json={"result": {"message": "Download started"}})

    page.route("**/api/plugin-cards/demo/action/download", download)
    _open_hud(page, running_server, surface_path)
    messages = page.evaluate("reactChatWindowHost.getState().messages")
    page.evaluate("view => appState.socket.onmessage({data: JSON.stringify({type: 'plugin_view', view})})", _view())
    content = _content(page, "view-one")
    button = content.frame_locator('iframe').get_by_role('button', name='Download', exact=True)
    expect(button).to_be_visible()
    page.evaluate("checkAndToggleTaskHUD()")
    # A task-only hide schedules a 300 ms fade; check after it could have fired.
    page.wait_for_timeout(400)
    expect(page.locator('#agent-task-hud')).to_be_visible()
    expect(page.locator('.agent-plugin-tab')).to_have_attribute('aria-selected', 'true')
    expect(button).to_be_visible()
    button.click()
    expect(content.get_by_role('status')).to_have_text('Download started')
    assert calls == [{
        "card_id": "view-one", "target_lanlan": "Alice", "presentation": "agent",
        "args": {"file_id": "123"},
        "locale": page.locator('html').get_attribute('lang') or 'en',
    }]
    assert page.evaluate("reactChatWindowHost.getState().messages") == messages


@pytest.mark.frontend
def test_task_updates_preserve_plugin_tab_iframe_and_pending_action(mock_page, running_server):
    page = mock_page
    pending = []
    page.route("**/api/plugin-cards/demo/action/download", lambda route: pending.append(route))
    _open_hud(page, running_server)
    definition = _view()
    assert _receive(page, definition) is True
    content = _content(page, "view-one")
    iframe = content.locator('iframe')
    button = content.frame_locator('iframe').get_by_role('button', name='Download', exact=True)
    expect(button).to_be_visible()
    original_frame = iframe.element_handle()
    button.click()
    expect(button).to_be_disabled()
    assert len(pending) == 1
    assert pending[0].request.post_data_json['presentation'] == 'agent'

    _task_update(page)
    expect(page.locator('.agent-plugin-tab')).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#agent-task-list')).to_be_hidden()
    assert iframe.evaluate("(current, previous) => current === previous", original_frame)
    expect(button).to_be_disabled()
    assert _change(page, definition, summary="Downloading") is True
    assert _change(page, definition, html='<p>Downloading</p><button data-neko-action="go">Download</button>') is True
    expect(content.frame_locator('iframe').get_by_text('Downloading', exact=True)).to_be_visible()
    expect(button).to_be_disabled()
    button.dispatch_event('click')
    assert len(pending) == 1

    page.locator('#agent-content-tasks-tab').click()
    expect(page.locator('#agent-task-list')).to_be_visible()
    assert _change(page, definition, css='button { padding: 12px; }') is True
    expect(page.locator('#agent-content-tasks-tab')).to_have_attribute('aria-selected', 'true')
    page.locator('.agent-plugin-tab').click()
    expect(button).to_be_disabled()
    pending[0].fulfill(json={"result": {"message": "Downloaded once"}})
    expect(content.get_by_role('status')).to_have_text('Downloaded once')
    expect(button).to_be_enabled()
    assert len(pending) == 1


@pytest.mark.frontend
def test_closing_one_plugin_preserves_other_content_and_agent_tasks(mock_page, running_server):
    page = mock_page
    cancel_requests = []
    page.on('request', lambda request: cancel_requests.append(request)
            if '/api/agent/admin/control' in request.url else None)
    _open_hud(page, running_server, "chat_full")
    messages = page.evaluate("reactChatWindowHost.getState().messages")
    first = _view(title="First download")
    second = _view("view-two", "second-plugin", title="Second download", html='<p>Second content</p>', actions={})
    assert _receive(page, first) is True
    assert _receive(page, second) is True
    _task_update(page)
    page.locator('.agent-plugin-tab').filter(has_text='First download').click()
    expect(_content(page, 'view-one')).to_be_visible()
    expect(page.locator('#agent-task-hud-cancel')).to_be_hidden()
    page.locator('#agent-plugin-close').click()
    expect(_content(page, 'view-one')).to_have_count(0)
    expect(_content(page, 'view-two').frame_locator('iframe').get_by_text('Second content', exact=True)).to_be_visible()
    assert _change(page, first, html='<p>Stale completion</p>') is False
    expect(_content(page, 'view-one')).to_have_count(0)
    page.locator('#agent-content-tasks-tab').click()
    expect(page.locator('#agent-task-list .task-card')).to_have_count(1)
    assert page.evaluate("window._agentTaskMap.get('existing-agent-task').status") == 'running'
    assert page.evaluate("reactChatWindowHost.getState().messages") == messages
    assert cancel_requests == []


@pytest.mark.frontend
def test_replaced_plugin_instance_ignores_old_update_and_close(mock_page, running_server):
    page = mock_page
    _open_hud(page, running_server)
    old = _view()
    replacement = _view('replacement', html='<p>Replacement content</p>', actions={})
    assert _receive(page, old) is True
    expect(_content(page, 'view-one').frame_locator('iframe').get_by_text('Ready to download', exact=True)).to_be_visible()
    assert _receive(page, replacement) is True
    assert _change(page, old, html='<p>Old completion</p>') is False
    assert _change(page, old, operation='close') is False
    expect(_content(page, 'view-one')).to_have_count(0)
    expect(_content(page, 'replacement').frame_locator('iframe').get_by_text('Replacement content', exact=True)).to_be_visible()
    page.locator('#agent-plugin-close').click()
    expect(_content(page, 'replacement')).to_have_count(0)
    assert page.evaluate('NekoPluginViews.hasContent()') is False
    assert _change(page, replacement, html='<p>Late completion</p>') is False
    expect(page.locator('.agent-plugin-view')).to_have_count(0)


@pytest.mark.frontend
def test_late_standalone_hud_gets_latest_snapshot_and_closes_owner_content(mock_page, running_server):
    owner = mock_page
    _open_hud(owner, running_server)
    definition = _view()
    assert _receive(owner, definition) is True
    assert _change(owner, definition, html='<p>Latest download result</p>', summary='Completed download', actions={}) is True
    expect(_content(owner, 'view-one').frame_locator('iframe').get_by_text('Latest download result', exact=True)).to_be_visible()

    standalone = owner.context.new_page()
    sockets = []
    standalone.on('websocket', lambda socket: sockets.append(socket.url))
    try:
        standalone.goto(f'{running_server}/agenthud', wait_until='domcontentloaded')
        standalone.wait_for_function(
            "() => window.NekoPluginViews && window.NekoChatWindow"
            " && typeof NekoChatWindow.mountPluginContent === 'function'"
        )
        content = _content(standalone, 'view-one')
        expect(content.frame_locator('iframe').get_by_text('Latest download result', exact=True)).to_be_visible()
        expect(content.locator('iframe')).to_have_attribute('title', 'Completed download')
        expect(standalone.locator('.agent-plugin-tab')).to_have_attribute('aria-selected', 'true')
        standalone.locator('#agent-plugin-close').click()
        expect(_content(standalone, 'view-one')).to_have_count(0)
        expect(_content(owner, 'view-one')).to_have_count(0)
        assert owner.evaluate('NekoPluginViews.hasContent()') is False
        assert _change(owner, definition, html='<p>Late completion</p>') is False
        assert sockets == []
    finally:
        standalone.close()


@pytest.mark.frontend
@pytest.mark.parametrize("surface_path", ["chat", "chat_full"], ids=["compact", "full"])
def test_native_chat_socket_mirrors_keep_task_state_without_rendering_hud(mock_page, running_server, surface_path):
    page = mock_page
    _open_hud(page, running_server, surface_path)
    messages = page.evaluate('reactChatWindowHost.getState().messages')
    page.evaluate("""async () => {
        // Let the normal browser bootstrap finish before marking this same page
        // as a native Chat mirror; no replacement Electron preload is installed.
        await new Promise(requestAnimationFrame);
        window.__nativeHudRootsAdded = 0;
        window.__nativeHudObserver = new MutationObserver(changes => {
            for (const change of changes) for (const node of change.addedNodes) {
                if (node.nodeType === Node.ELEMENT_NODE &&
                    (node.id === 'agent-task-hud' || node.querySelector('#agent-task-hud'))) {
                    window.__nativeHudRootsAdded += 1;
                }
            }
        });
        window.__nativeHudObserver.observe(document.body, {childList: true, subtree: true});
        window.nekoChatWindow = {};
    }""")
    expect(page.locator('body')).to_have_class(re.compile(r'\belectron-chat-window\b'))
    page.evaluate("""view => {
        appState.socket.onmessage({data: JSON.stringify({type: 'plugin_view', view})});
        appState.socket.onmessage({data: JSON.stringify({
            type: 'agent_task_update', task: {
                id: 'native-mirrored-task', status: 'running', type: 'user_plugin',
                params: {description: 'Mirrored task remains in state'},
                start_time: new Date().toISOString()
            }
        })});
    }""", _view())
    page.wait_for_function(
        "() => window._agentTaskMap.get('native-mirrored-task')?.status === 'running'"
    )
    assert page.evaluate('NekoPluginViews.hasContent()') is False
    expect(page.locator('.agent-plugin-view')).to_have_count(0)
    result = page.evaluate("""async () => {
        const tasks = Array.from(window._agentTaskMap.values());
        const directCreate = AgentHUD.createAgentTaskHUD();
        AgentHUD.showAgentTaskHUD({ignoreVisibilityPreference: true});
        AgentHUD.updateAgentTaskHUD({success: true, tasks, running_count: 1, queued_count: 0});
        AgentHUD._doUpdateAgentTaskHUD();
        await new Promise(requestAnimationFrame);
        await new Promise(requestAnimationFrame);
        return {created: !!directCreate, rootsAdded: window.__nativeHudRootsAdded};
    }""")
    assert result == {'created': False, 'rootsAdded': 0}
    expect(page.locator('#agent-task-hud:visible')).to_have_count(0)
    assert page.evaluate("window._agentTaskMap.get('native-mirrored-task').params.description") == 'Mirrored task remains in state'
    assert page.evaluate('reactChatWindowHost.getState().messages') == messages
    page.evaluate('window.__nativeHudObserver.disconnect()')


@pytest.mark.frontend
@pytest.mark.parametrize("surface_path", ["chat", "chat_full"], ids=["compact", "full"])
def test_queued_hud_update_cannot_reveal_a_late_native_chat_mirror(mock_page, running_server, surface_path):
    page = mock_page
    _open_hud(page, running_server, surface_path)
    _task_update(page)
    page.evaluate('AgentHUD.showAgentTaskHUD({ignoreVisibilityPreference: true})')
    expect(page.locator('#agent-task-hud')).to_be_visible()
    result = page.evaluate("""async () => {
        const existingHud = document.getElementById('agent-task-hud');
        let rootsAdded = 0;
        const observer = new MutationObserver(changes => {
            for (const change of changes) for (const node of change.addedNodes) {
                if (node.nodeType === Node.ELEMENT_NODE &&
                    (node.id === 'agent-task-hud' || node.querySelector('#agent-task-hud'))) {
                    rootsAdded += 1;
                }
            }
        });
        observer.observe(document.body, {childList: true, subtree: true});
        // This update was admitted while the page was still a browser Chat.
        // Its already-queued callback must recheck the newly available marker.
        AgentHUD.updateAgentTaskHUD({
            success: true, tasks: Array.from(window._agentTaskMap.values()),
            running_count: 1, queued_count: 0
        });
        const queuedBeforeNativeMarker = !!AgentHUD._updateRafId;
        window.nekoChatWindow = {};
        await new Promise(requestAnimationFrame);
        await new Promise(requestAnimationFrame);
        AgentHUD._doUpdateAgentTaskHUD();
        AgentHUD.showAgentTaskHUD({ignoreVisibilityPreference: true});
        await new Promise(requestAnimationFrame);
        observer.disconnect();
        return {
            queuedBeforeNativeMarker,
            rootsAdded,
            survivingRootIsOriginal: !document.getElementById('agent-task-hud') ||
                document.getElementById('agent-task-hud') === existingHud
        };
    }""")
    assert result == {
        'queuedBeforeNativeMarker': True,
        'rootsAdded': 0,
        'survivingRootIsOriginal': True,
    }
    expect(page.locator('#agent-task-hud:visible')).to_have_count(0)
    assert page.evaluate("window._agentTaskMap.get('existing-agent-task').status") == 'running'


@pytest.mark.frontend
def test_compact_plugin_tabs_keep_chrome_visible_and_do_not_steal_selection(mock_page, running_server, tmp_path):
    page = mock_page
    _open_hud(page, running_server)
    first = _view(title="插件推送测试", html=(
        '<strong>插件推送测试</strong><p>创建成功：请在测试面板推送内容。</p>'
        '<button data-neko-action="go">测试插件回调</button>'
    ))
    _receive(page, first)
    hud = page.locator('#agent-task-hud')
    page.evaluate("""() => {
        Object.assign(document.getElementById('agent-task-hud').style,
            {top: '100px', left: '100px', right: 'auto', transform: 'none'});
    }""")
    expect(_content(page, 'view-one').frame_locator('iframe').get_by_role('button')).to_be_visible()
    expect(page.locator('#agent-plugin-select')).to_have_count(0)
    navigation = page.locator('#agent-content-navigation')
    assert navigation.bounding_box()['height'] < 45
    assert hud.bounding_box()['width'] <= 322
    screenshot = tmp_path / 'agent-hud.png'
    hud.screenshot(path=str(screenshot), animations='disabled')
    print(f'\nHUD screenshot: {screenshot}')

    original_frame = _content(page, 'view-one').locator('iframe').element_handle()
    for i in range(5):
        _receive(page, _view(f'extra-{i}', f'plugin-{i}', title=f'Long background content title {i}',
                             html='<p>Background</p>', actions={}))
    first_tab = page.locator('.agent-plugin-tab').first
    expect(first_tab).to_have_attribute('aria-selected', 'true')
    expect(page.locator('.agent-plugin-tab.has-unread')).to_have_count(5)
    assert navigation.bounding_box()['height'] < 55, 'tabs scroll horizontally, not into multiple rows'
    assert _content(page, 'view-one').locator('iframe').evaluate('(node, old) => node === old', original_frame)
    first_tab.focus()
    first_tab.press('End')
    last_tab = page.locator('.agent-plugin-tab').last
    expect(last_tab).to_be_focused()
    expect(last_tab).to_have_attribute('aria-selected', 'true')
    expect(page.locator('.agent-plugin-tab.has-unread')).to_have_count(4)
    page.locator('#agent-plugin-close').click()
    expect(page.locator('.agent-plugin-tab')).to_have_count(5)
    expect(page.locator('.agent-plugin-tab').last).to_be_focused()

    _change(page, _view('extra-3', 'plugin-3'), html='<p>Large content</p>' * 100)
    content_panel = page.locator('#agent-plugin-content')
    page.wait_for_function("""() => {
        const panel = document.getElementById('agent-plugin-content');
        return panel.scrollHeight > panel.clientHeight;
    }""")
    before = navigation.bounding_box()
    content_panel.evaluate('(panel) => { panel.scrollTop = panel.scrollHeight; }')
    assert navigation.bounding_box() == before
    expect(page.locator('#agent-plugin-close')).to_be_visible()
    page.locator('#agent-task-hud-minimize').click()
    expect(navigation).to_be_hidden()
    expect(page.locator('#agent-task-hud-title')).to_be_visible()
    _receive(page, _view('while-collapsed', 'another-plugin'))
    expect(hud).to_have_attribute('data-agent-hud-collapsed', 'true')
    expect(navigation).to_be_hidden()


@pytest.mark.frontend
def test_theme_changes_update_chat_and_agent_colors_without_rebuilding_content(mock_page, running_server):
    page = mock_page
    pending = []
    page.route("**/api/plugin-cards/demo/action/download", lambda route: pending.append(route))
    _open_hud(page, running_server, "chat_full")
    page.wait_for_function('() => window.nekoTheme')
    page.evaluate("""() => {
        if (document.documentElement.getAttribute('data-theme') === 'dark') nekoTheme.toggle();
    }""")
    page.wait_for_function("() => !document.documentElement.classList.contains('theme-transitioning')")
    view = _view(html='<p>Theme test</p><input value="initial"><button data-neko-action="go">Download</button>')
    _receive(page, view)
    chat = {**view, "cardId": "theme-chat", "presentation": "chat"}
    page.evaluate('block => appendReactChatBlocks({blocks: [block]})', chat)
    chat_content = page.locator('[data-message-id="plugin-card-demo:theme-chat"]')
    agent_content = _content(page, 'view-one')
    for content in (chat_content, agent_content):
        expect(content.frame_locator('iframe').get_by_role('button', name='Download', exact=True)).to_be_visible()
    chat_content.frame_locator('iframe').get_by_role('textbox').fill('unsent edit')
    chat_content.frame_locator('iframe').get_by_role('button', name='Download', exact=True).click()
    assert len(pending) == 1
    page.evaluate("""() => {
        window.themeTestNodes = [
            document.querySelector('[data-message-id="plugin-card-demo:theme-chat"] iframe'),
            document.querySelector('.agent-plugin-view iframe')
        ].map(frame => ({frame, doc: frame.contentDocument,
            input: frame.contentDocument.querySelector('input'),
            button: frame.contentDocument.querySelector('button'),
            style: frame.contentDocument.querySelector('[data-card-style]')}));
    }""")
    for dark in (True, False):
        assert page.evaluate('nekoTheme.toggle()') is dark
        page.wait_for_function("""() => !document.documentElement.classList.contains('theme-transitioning') &&
            themeTestNodes.every(({frame, doc}) => doc.body.style.color === getComputedStyle(frame.parentElement).color)
        """)
        assert page.evaluate("""() => themeTestNodes.every(({frame, doc, input, button, style}) =>
            frame.contentDocument === doc && doc.querySelector('input') === input &&
            doc.querySelector('button') === button && doc.querySelector('[data-card-style]') === style)
        """)
        expect(chat_content.frame_locator('iframe').get_by_role('textbox')).to_have_value('unsent edit')
        expect(chat_content.frame_locator('iframe').get_by_role('button', name='Download', exact=True)).to_be_disabled()
    pending[0].fulfill(json={"result": {"message": "Completed after theme changes"}})
    expect(chat_content.get_by_role('status')).to_have_text('Completed after theme changes')
    assert len(pending) == 1
