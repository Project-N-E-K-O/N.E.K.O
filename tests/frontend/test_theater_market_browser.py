"""Market entry, host handoff and window completion stay outside the engine."""

import pytest
import json
from pathlib import Path
from playwright.sync_api import expect


@pytest.mark.frontend
def test_lan_market_entry_explains_local_only_import_without_pairing(page):
    repo = Path(__file__).resolve().parents[2]
    message = json.loads((repo / 'static/locales/zh-CN.json').read_text(encoding='utf-8'))['theater']['marketLocalOnly']
    def serve(route):
        if route.request.url.endswith('/theater_market.js'):
            return route.fulfill(path=repo / 'static/js/theater_market.js', content_type='application/javascript')
        route.fulfill(content_type='text/html', body=f'''
            <meta charset="utf-8">
            <div id="theater-selector-status"></div>
            <script>window.__requests = 0;
            window.nekoTheaterTransport = {{requestJson: () => {{window.__requests++; throw Error('unexpected pairing');}}}};
            window.t = () => {json.dumps(message, ensure_ascii=False)};</script>
            <script src="/theater_market.js"></script>
        ''')
    page.route('http://192.168.1.100:48911/**', serve)
    page.goto('http://192.168.1.100:48911/theater?market=browse')
    page.evaluate('window.nekoTheaterMarket.redirectSelector()')
    expect(page.locator('#theater-selector-status')).to_have_text(message)
    assert page.evaluate('window.__requests') == 0


@pytest.mark.frontend
def test_market_entry_is_independent_of_toggle_for_pointer_and_keyboard(mock_page, running_server):
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.route('**/api/seven-day-tutorial/state', lambda route: route.fulfill(json={
        'success': True, 'initialized': True, 'revision': 1, 'state': {'completedRounds': list(range(1, 8))}}))
    mock_page.route('**/api/characters/persona-onboarding-state', lambda route: route.fulfill(json={
        'success': True, 'state': {'status': 'completed'}}))
    mock_page.goto(running_server, wait_until="domcontentloaded")
    mock_page.wait_for_function("() => typeof window.createChatModeToggle === 'function' && window.nekoTheaterMarket")
    mock_page.wait_for_function("() => window.i18n && window.i18n.isInitialized")
    mock_page.evaluate("""() => {
        window.proactiveTheaterInviteEnabled = false;
        window.__marketSaved = 0;
        window.saveNEKOSettings = () => window.__marketSaved++;
        window.__marketOpened = [];
        window.open = url => { window.__marketOpened.push(url); return {}; };
        window.openOrFocusWindow = (url) => window.open(url);
        const row = window.createChatModeToggle({checkboxId:'test-theater-entry',
            labelKey:'settings.toggles.proactiveTheaterInviteChat',
            tooltipKey:'settings.toggles.proactiveTheaterInviteChatTooltip',
            globalVarName:'proactiveTheaterInviteEnabled'});
        row.id = 'test-theater-entry-row';
        Object.assign(row.style, {position:'fixed',top:'20px',left:'20px',width:'320px',zIndex:'2147483647',background:'white',pointerEvents:'auto'});
        document.body.appendChild(row);
    }""")
    row = mock_page.locator("#test-theater-entry-row")
    button = row.locator('button[data-i18n="theater.marketEnter"]')
    expect(button).to_be_visible()
    button.click()
    button.focus()
    button.press("Enter")
    button.press("Space")
    assert mock_page.evaluate("window.__marketOpened") == ["/theater"] * 3
    assert mock_page.evaluate("window.proactiveTheaterInviteEnabled") is False
    assert mock_page.evaluate("window.__marketSaved") == 0
    row.locator(".chat-mode-indicator").click()
    assert mock_page.evaluate("window.proactiveTheaterInviteEnabled") is True
    assert mock_page.evaluate("window.__marketSaved") == 1


def _market_host(page, running_server):
    """Real transport/adapter, with an isolated host and no model or user data."""
    page.route('**/__market_test_host', lambda route: route.fulfill(content_type='text/html', body='''
        <button onclick="nekoTheaterMarket.browse()">Market</button>
        <button onclick="nekoTheaterMarket.open()">Theater</button>
        <script src="/static/js/theater_transport.js"></script>
        <script>window.__starts = [];
        window.addEventListener('message', event => {
            const data = event.data;
            if (data.action === 'theater:start-request') {
                window.__starts.push(data);
            }
        });</script>
        <script src="/static/js/theater_market.js"></script>'''))
    page.goto(running_server + '/__market_test_host')


@pytest.mark.frontend
def test_market_bootstrap_never_initializes_or_displays_local_selector(page, running_server):
    _market_host(page, running_server)
    requests = []
    page.context.route('**/api/theater-numeric/**', lambda route: (
        requests.append(route.request.url), route.fulfill(json={'ok': False})))
    page.context.route('**/api/theater-market/launch', lambda route: route.fulfill(json={
        'ok': False, 'reason': 'isolated_connection_failure'}))
    with page.expect_popup() as opened:
        page.get_by_role('button', name='Market', exact=True).click()
    popup = opened.value
    expect(popup.locator('#theater-selector-status')).to_have_text('无法连接剧本市场，请保持 NEKO 开启并重试。')
    expect(popup.locator('#theater-story-list')).to_have_count(0)
    expect(popup.locator('#theater-start-btn')).to_have_count(0)
    assert not requests
    assert len(page.context.pages) == 2  # host plus exactly one entry window


@pytest.mark.frontend
def test_market_entry_replaces_idle_library_without_starting(page, running_server):
    _market_host(page, running_server)
    calls = []
    page.context.route('**/api/theater-market/launch/test-launch**', lambda route: (
        calls.append(route.request.url), route.fulfill(status=404, json={'ok': False})))
    page.context.route('**/api/theater-market/launch', lambda route: route.fulfill(json={
        'ok': True, 'request_id': 'test-launch', 'url': 'http://market.test/selected'}))
    page.context.route('http://market.test/**', lambda route: route.fulfill(body='<h1>Market selection</h1>'))
    page.context.route('**/api/theater-numeric/stories', lambda route: route.fulfill(json={
        'ok': True, 'stories': [], 'character_id': 'test-cat'}))
    page.context.route('**/api/theater-numeric/memory/stories', lambda route: route.fulfill(json={
        'ok': True, 'stories': []}))
    with page.expect_popup() as old_opened:
        page.evaluate("window.open('/theater', 'old_local_library')")
    old_library = old_opened.value
    expect(old_library.locator('#theater-selector-status')).to_have_attribute('data-i18n', 'theater.ready')
    with old_library.expect_event('close'):
        with page.expect_popup() as opened:
            page.get_by_role('button', name='Market', exact=True).click()
    popup = opened.value
    expect(popup.get_by_role('heading', name='Market selection')).to_be_visible()
    assert old_library.is_closed()
    assert not calls
    assert page.evaluate('window.__starts') == []
    assert len(page.context.pages) == 2


@pytest.mark.frontend
def test_manual_entry_shows_three_bundled_scripts_before_more_scripts_opens_market(page, running_server):
    from services.theater.numeric_v2_registry import NumericV2PackageRegistry
    repo = Path(__file__).resolve().parents[2]
    registry = NumericV2PackageRegistry(repo / 'assets/theater')
    stories = registry.list_packages()
    assert len(stories) == 3
    _market_host(page, running_server)
    page.context.route('**/api/theater-numeric/stories', lambda route: route.fulfill(json={
        'ok': True, 'stories': stories, 'character_id': 'test-cat'}))
    page.context.route('**/api/theater-numeric/memory/stories', lambda route: route.fulfill(json={
        'ok': True, 'stories': [], 'character_id': 'test-cat'}))
    page.context.route('**/api/theater-numeric/session/active**', lambda route: route.fulfill(
        status=404, json={'ok': False, 'reason': 'numeric_session_not_found'}))
    launches = []
    page.context.route('**/api/theater-market/launch', lambda route: (
        launches.append(route.request.post_data_json), route.fulfill(json={
            'ok': True, 'request_id': 'more-scripts', 'url': 'http://market.test/theater'})))
    page.context.route('http://market.test/**', lambda route: route.fulfill(body=(
        '<h1>Script Market</h1><a href="' + running_server + '/theater?story_id='
        + stories[0]['story_id'] + '">Imported</a>')))
    page.context.route('**/api/theater-numeric/memory/archives**', lambda route: route.fulfill(
        json={'ok': True, 'archives': []}))
    page.evaluate("""() => window.addEventListener('message', event => {
        if (event.data.action === 'theater:start-request')
            event.source.postMessage({...event.data, action:'theater:start-ready'}, window.location.origin);
    })""")
    with page.expect_popup() as opened:
        page.get_by_role('button', name='Theater', exact=True).click()
    selector = opened.value
    expect(selector.locator('.theater-story-card')).to_have_count(3)
    for story in stories:
        expect(selector.get_by_role('option', name=story['title'], exact=False)).to_be_visible()
    expect(selector.locator('#theater-start-btn')).to_be_enabled()
    assert launches == []
    selector.locator('#theater-market-btn').click()
    expect(selector.get_by_role('heading', name='Script Market')).to_be_visible()
    assert not selector.is_closed()
    assert len(page.context.pages) == 2  # Reuse the selector, avoiding an opener-owned market child.
    assert launches == [{'random': False}]
    selector.get_by_role('link', name='Imported', exact=True).click()
    expect(selector.locator('#theater-start-btn')).to_be_enabled()
    assert selector.evaluate('window.opener && !window.opener.closed')
    with selector.expect_event('close'):
        selector.locator('#theater-start-btn').click()
    assert len(page.context.pages) == 1
    assert page.evaluate('window.__starts.map(value => value.story_id)') == [stories[0]['story_id']]


@pytest.mark.frontend
def test_more_scripts_does_not_interrupt_an_import(page, running_server):
    _market_host(page, running_server)
    pending = {}
    launches = []
    page.context.route('**/api/theater-numeric/stories', lambda route: route.fulfill(
        json={'ok': True, 'stories': [], 'character_id': 'test-cat'}))
    page.context.route('**/api/theater-numeric/memory/stories', lambda route: route.fulfill(
        json={'ok': True, 'stories': [], 'character_id': 'test-cat'}))
    page.context.route('**/api/theater-numeric/packages/import', lambda route: pending.update(import_route=route))
    page.context.route('**/api/theater-market/launch', lambda route: (
        launches.append(route.request.url), route.fulfill(json={'ok': False})))
    with page.expect_popup() as opened:
        page.get_by_role('button', name='Theater', exact=True).click()
    selector = opened.value
    expect(selector.locator('#theater-market-btn')).to_be_enabled()
    selector.locator('#theater-import-input').set_input_files({
        'name': 'test.json', 'mimeType': 'application/json', 'buffer': b'{}'})
    expect(selector.locator('#theater-market-btn')).to_be_disabled()
    # A dispatched event must obey the same busy guard as an ordinary click.
    selector.evaluate("document.getElementById('theater-market-btn').dispatchEvent(new Event('click'))")
    assert selector.url == running_server + '/theater'
    assert not launches
    assert len(page.context.pages) == 2
    for _ in range(50):
        if 'import_route' in pending:
            break
        page.wait_for_timeout(20)
    assert 'import_route' in pending
    pending['import_route'].fulfill(json={'ok': False, 'reason': 'invalid_test_package'})
    expect(selector.locator('#theater-market-btn')).to_be_enabled()


@pytest.mark.frontend
def test_theater_invitation_names_the_reserved_window_for_native_controls(mock_page, running_server):
    pending = []
    mock_page.route('**/api/mini_game/invite/respond', lambda route: pending.append(route))
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(running_server + '/chat', wait_until='domcontentloaded')
    mock_page.wait_for_function('() => window.reactChatWindowHost && window.reactChatWindowHost.setMiniGameInvitePrompt')
    mock_page.evaluate("""() => {
        window.reactChatWindowHost.openWindow();
        window.reactChatWindowHost.setMiniGameInvitePrompt({sessionId:'native-theater-test',gameType:'theater',
            options:[{choice:'accept',label:'接下邀约'},{choice:'decline',label:'暂不参加'}]});
    }""")
    with mock_page.expect_popup() as opened:
        mock_page.get_by_role('button', name='接下邀约', exact=True).click()
    popup = opened.value
    assert popup.evaluate('window.name') == 'neko_theater_market'
    for _ in range(50):
        if pending:
            break
        mock_page.wait_for_timeout(20)
    assert pending
    with popup.expect_event('close'):
        pending[0].fulfill(json={'action': 'cooldown'})
    assert not mock_page.is_closed()


@pytest.mark.frontend
def test_theater_invitation_keeps_an_existing_market_window_on_non_accept_outcome(mock_page, running_server):
    pending = []
    mock_page.route('**/api/mini_game/invite/respond', lambda route: pending.append(route))
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(running_server + '/chat', wait_until='domcontentloaded')
    mock_page.wait_for_function('() => window.reactChatWindowHost && window.reactChatWindowHost.setMiniGameInvitePrompt')
    # 用户上一次邀约或「更多剧本」留下的同名窗口：已经载入页面，不是 about:blank。
    # 用 /chat 而不是选剧页，因为选剧页自己会跳到市场站，title 会随之变化。
    with mock_page.expect_popup() as existing:
        mock_page.evaluate("url => window.open(url, 'neko_theater_market')", running_server + '/chat')
    existing_window = existing.value
    existing_window.wait_for_load_state('domcontentloaded')
    mock_page.evaluate("""() => {
        window.reactChatWindowHost.openWindow();
        window.reactChatWindowHost.setMiniGameInvitePrompt({sessionId:'existing-market-window',gameType:'theater',
            options:[{choice:'accept',label:'接下邀约'},{choice:'decline',label:'暂不参加'}]});
    }""")
    mock_page.get_by_role('button', name='接下邀约', exact=True).click()
    for _ in range(50):
        if pending:
            break
        mock_page.wait_for_timeout(20)
    assert pending
    pending[0].fulfill(json={'action': 'cooldown'})
    mock_page.wait_for_timeout(400)
    # 旧窗口归用户：既不该被占位文本覆盖，也不该替他关掉。
    # 页面自己会改 title（角色名），所以只锁「不是占位页」而不锁具体标题。
    assert not existing_window.is_closed()
    assert 'Loading' not in existing_window.title()
    assert existing_window.url.startswith(running_server)
