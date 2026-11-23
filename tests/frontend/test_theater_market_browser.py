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


def _click_and_expect_close(target, selector):
    """Click from a page task so Playwright never awaits an action on a closing page."""
    with target.expect_event('close'):
        target.evaluate("selector => setTimeout(() => document.querySelector(selector).click(), 0)", selector)


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
@pytest.mark.parametrize('failure', ['http', 'network', 'timeout'])
def test_market_bootstrap_never_initializes_or_displays_local_selector(page, running_server, failure):
    _market_host(page, running_server)
    requests = []
    page.context.route('**/api/theater-numeric/**', lambda route: (
        requests.append(route.request.url), route.fulfill(json={'ok': False})))
    def fail_connection(route):
        if failure == 'http':
            route.fulfill(status=502, json={'ok': False, 'reason': 'isolated_connection_failure'})
        else:
            route.abort('timedout' if failure == 'timeout' else 'connectionrefused')
    page.context.route('**/api/theater-market/launch', fail_connection)
    with page.expect_popup() as opened:
        page.get_by_role('button', name='Market', exact=True).click()
    popup = opened.value
    expect(popup.locator('#theater-selector-status')).to_have_text('暂时无法连接剧本市场，请重试。')
    expect(popup.locator('.theater-title-bar')).to_be_visible()
    expect(popup.locator('[data-neko-window-control]')).to_have_count(4)
    expect(popup.locator('[data-neko-window-control="close"]')).to_be_visible()
    expect(popup.locator('#theater-market-retry-btn')).to_be_enabled()
    expect(popup.get_by_role('link', name='返回选择剧本')).to_have_attribute('href', '/theater')
    expect(popup.locator('#theater-market-connection')).to_have_attribute('aria-busy', 'false')
    expect(popup.locator('#theater-story-list')).to_have_count(0)
    expect(popup.locator('#theater-start-btn')).to_have_count(0)
    assert not requests
    assert len(page.context.pages) == 2  # host plus exactly one entry window
    _click_and_expect_close(popup, '[data-neko-window-control="close"]')
    assert not page.is_closed()


@pytest.mark.frontend
def test_market_retry_preserves_parameters_and_blocks_overlapping_requests(page, running_server):
    _market_host(page, running_server)
    calls = []
    pending = []
    def connect(route):
        calls.append(route.request.post_data_json)
        if len(calls) == 1:
            route.fulfill(status=502, json={'ok': False})
        else:
            pending.append(route)
    page.context.route('**/api/theater-market/launch', connect)
    page.context.route('**/__market_success', lambda route: route.fulfill(body='<h1>Market connected</h1>'))
    with page.expect_popup() as opened:
        page.evaluate("void window.open('/theater?market=random&share_id=12&market_revision=3&file_sha256=test-hash', 'neko_theater_market')")
    popup = opened.value
    retry = popup.locator('#theater-market-retry-btn')
    expect(retry).to_be_visible()
    with popup.expect_request('**/api/theater-market/launch'):
        retry.click()
    expect(retry).to_be_disabled()
    expect(popup.locator('#theater-market-connection')).to_have_attribute('aria-busy', 'true')
    popup.evaluate('window.nekoTheaterMarket.redirectSelector()')
    assert calls == [{'random': True, 'share_id': 12, 'revision': 3, 'file_sha256': 'test-hash'}] * 2
    assert len(pending) == 1
    expect(popup.get_by_role('link', name='返回选择剧本')).to_be_visible()
    expect(popup.locator('[data-neko-window-control="close"]')).to_be_enabled()
    pending[0].fulfill(json={'ok': True, 'request_id': 'retry-success', 'url': running_server + '/__market_success'})
    expect(popup.get_by_role('heading', name='Market connected')).to_be_visible()
    assert len(page.context.pages) == 2


@pytest.mark.frontend
def test_failed_market_connection_returns_to_selector_in_the_same_window(page, running_server):
    page.route('**/api/theater-market/launch', lambda route: route.fulfill(status=502, json={'ok': False}))
    page.route('**/api/theater-numeric/stories', lambda route: route.fulfill(json={'ok': True, 'stories': []}))
    page.route('**/api/theater-numeric/memory/stories', lambda route: route.fulfill(json={'ok': True, 'stories': []}))
    page.route('**/api/theater-numeric/session/active**', lambda route: route.fulfill(
        status=404, json={'ok': False, 'reason': 'numeric_session_not_found'}))
    page.goto(running_server + '/theater?market=browse')
    expect(page.locator('#theater-market-retry-btn')).to_be_visible()
    page.get_by_role('link', name='返回选择剧本').click()
    expect(page).to_have_url(running_server + '/theater')
    expect(page.locator('[data-theater-selector-app]')).to_be_visible()
    expect(page.locator('#theater-market-btn')).to_be_enabled()
    assert len(page.context.pages) == 1


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


def _selector_api_stubs(context):
    """Keep selector pages offline: empty library, no active session; count focus requests."""
    context.add_init_script("""
        window.__focusCalls = 0;
        const nativeFocus = window.focus.bind(window);
        window.focus = () => { window.__focusCalls += 1; return nativeFocus(); };
        window.__restoreRequests = 0;
        window.addEventListener('message', event => {
            if (event.data && event.data.type === 'neko:restore-window') window.__restoreRequests += 1;
        });
    """)
    context.route('**/api/theater-numeric/stories', lambda route: route.fulfill(json={'ok': True, 'stories': []}))
    context.route('**/api/theater-numeric/memory/stories', lambda route: route.fulfill(json={'ok': True, 'stories': []}))
    context.route('**/api/theater-numeric/session/active**', lambda route: route.fulfill(
        status=404, json={'ok': False, 'reason': 'numeric_session_not_found'}))
    context.route('**/api/theater-market/launch', lambda route: route.fulfill(status=502, json={'ok': False}))


def _host_with_window_registry(page, running_server):
    """Host page that opens windows through the real openOrFocusWindow registry."""
    page.route('**/__market_registry_host', lambda route: route.fulfill(content_type='text/html', body='''
        <button onclick="nekoTheaterMarket.browse()">Market</button>
        <button onclick="nekoTheaterMarket.open()">Theater</button>
        <script src="/static/common_dialogs.js"></script>
        <script src="/static/js/theater_transport.js"></script>
        <script src="/static/js/theater_market.js"></script>'''))
    page.goto(running_server + '/__market_registry_host')
    page.wait_for_function("() => typeof window.openOrFocusWindow === 'function' && window.nekoTheaterMarket")


@pytest.mark.frontend
def test_market_window_back_focuses_existing_selector_and_closes_itself(page, running_server):
    _selector_api_stubs(page.context)
    _host_with_window_registry(page, running_server)
    with page.expect_popup() as opened_selector:
        page.get_by_role('button', name='Theater', exact=True).click()
    selector = opened_selector.value
    expect(selector.locator('[data-theater-selector-app]')).to_be_visible()
    with page.expect_popup() as opened_market:
        page.get_by_role('button', name='Market', exact=True).click()
    market = opened_market.value
    assert market.evaluate('window.name') == 'neko_theater_market'
    expect(market.locator('#theater-market-retry-btn')).to_be_visible()
    expect(market.get_by_role('link', name='返回选择剧本')).to_be_visible()
    _click_and_expect_close(market, '#theater-market-back-link')
    assert not selector.is_closed()
    assert selector.url == running_server + '/theater'
    assert selector.evaluate('window.__focusCalls') >= 1
    selector.wait_for_function('() => window.__restoreRequests >= 1')
    assert len(page.context.pages) == 2  # host plus the one selector


@pytest.mark.frontend
def test_market_window_back_prefers_a_selector_opener(page, running_server):
    _selector_api_stubs(page.context)
    page.goto(running_server + '/theater')
    expect(page.locator('[data-theater-selector-app]')).to_be_visible()
    with page.expect_popup() as opened_market:
        page.evaluate("void window.open('/theater?market=browse', 'neko_theater_market')")
    market = opened_market.value
    expect(market.locator('#theater-market-retry-btn')).to_be_visible()
    expect(market.get_by_role('link', name='返回选择剧本')).to_be_visible()
    _click_and_expect_close(market, '#theater-market-back-link')
    assert page.url == running_server + '/theater'
    assert page.evaluate('window.__focusCalls') >= 1
    page.wait_for_function('() => window.__restoreRequests >= 1')
    assert len(page.context.pages) == 1


@pytest.mark.frontend
def test_market_window_back_focuses_selector_registered_by_another_window(page, running_server):
    """Invite accepted in a window that never opened the selector (e.g. /chat vs main page)."""
    _selector_api_stubs(page.context)
    page.goto(running_server + '/theater')
    page.evaluate("window.name = 'neko_theater'")
    page.reload()
    expect(page.locator('[data-theater-selector-app]')).to_be_visible()
    page.wait_for_function("() => !!localStorage.getItem('neko:named-window:neko_theater')")
    other = page.context.new_page()
    _host_with_window_registry(other, running_server)
    with other.expect_popup() as opened_market:
        other.get_by_role('button', name='Market', exact=True).click()
    market = opened_market.value
    expect(market.locator('#theater-market-retry-btn')).to_be_visible()
    _click_and_expect_close(market, '#theater-market-back-link')
    page.wait_for_function('() => window.__focusCalls >= 1')
    assert page.url == running_server + '/theater'
    assert len(page.context.pages) == 2  # the selector and the other host window


@pytest.mark.frontend
def test_market_window_back_without_selector_falls_back_to_in_place_selector(page, running_server):
    _selector_api_stubs(page.context)
    _host_with_window_registry(page, running_server)
    with page.expect_popup() as opened_market:
        page.get_by_role('button', name='Market', exact=True).click()
    market = opened_market.value
    expect(market.locator('#theater-market-retry-btn')).to_be_visible()
    market.get_by_role('link', name='返回选择剧本').click()
    expect(market).to_have_url(running_server + '/theater')
    expect(market.locator('[data-theater-selector-app]')).to_be_visible()
    assert len(page.context.pages) == 2  # host plus the market window, now a selector


@pytest.mark.frontend
def test_market_opened_inside_selector_window_returns_in_place(page, running_server):
    _selector_api_stubs(page.context)
    _host_with_window_registry(page, running_server)
    with page.expect_popup() as opened_selector:
        page.get_by_role('button', name='Theater', exact=True).click()
    selector = opened_selector.value
    expect(selector.locator('#theater-market-btn')).to_be_enabled()
    selector.locator('#theater-market-btn').click()
    expect(selector).to_have_url(running_server + '/theater?market=browse')
    expect(selector.locator('#theater-market-retry-btn')).to_be_visible()
    assert selector.evaluate('window.name') == 'neko_theater'
    selector.get_by_role('link', name='返回选择剧本').click()
    expect(selector).to_have_url(running_server + '/theater')
    expect(selector.locator('[data-theater-selector-app]')).to_be_visible()
    assert len(page.context.pages) == 2


@pytest.mark.frontend
def test_market_window_back_ignores_stale_registry_without_selector(page, running_server):
    """A heartbeat left by a crashed selector must not close the market with nowhere to go."""
    _selector_api_stubs(page.context)
    _host_with_window_registry(page, running_server)
    page.evaluate("""() => localStorage.setItem('neko:named-window:neko_theater',
        JSON.stringify({url: location.origin + '/theater', timestamp: Date.now() + 60000}))""")
    with page.expect_popup() as opened_market:
        page.get_by_role('button', name='Market', exact=True).click()
    market = opened_market.value
    expect(market.locator('#theater-market-retry-btn')).to_be_visible()
    market.get_by_role('link', name='返回选择剧本').click()
    expect(market).to_have_url(running_server + '/theater')
    expect(market.locator('[data-theater-selector-app]')).to_be_visible()
    assert len(page.context.pages) == 2


@pytest.mark.frontend
@pytest.mark.parametrize('stale_registry', [True, False])
def test_market_back_ignores_a_connection_that_succeeds_afterwards(page, running_server, stale_registry):
    """Back wins over a pending launch, whether waiting for a selector ack or navigating in place."""
    _selector_api_stubs(page.context)
    pending = []
    page.context.route('**/api/theater-market/launch', lambda route: pending.append(route))
    page.context.route('**/__market_success', lambda route: route.fulfill(body='<h1>Market connected</h1>'))
    _host_with_window_registry(page, running_server)
    if stale_registry:
        page.evaluate("""() => localStorage.setItem('neko:named-window:neko_theater',
            JSON.stringify({url: location.origin + '/theater', timestamp: Date.now() + 60000}))""")
    with page.expect_popup() as opened_market:
        page.get_by_role('button', name='Market', exact=True).click()
    market = opened_market.value
    market.wait_for_function('() => document.readyState === "complete"')
    expect(market.locator('#theater-market-connection')).to_have_attribute('aria-busy', 'true')
    assert len(pending) == 1
    market.get_by_role('link', name='返回选择剧本').click()
    pending[0].fulfill(json={'ok': True, 'request_id': 'late-success', 'url': running_server + '/__market_success'})
    expect(market).to_have_url(running_server + '/theater')
    expect(market.locator('[data-theater-selector-app]')).to_be_visible()


@pytest.mark.frontend
def test_registered_selector_acknowledges_only_after_restore_settles(page, running_server):
    _selector_api_stubs(page.context)
    page.add_init_script("""
        window.nekoWindowControl = {restore: () => new Promise(resolve => setTimeout(() => {
            window.__restoredAt = performance.now();
            resolve({ok: true});
        }, 300))};
        const recordFocus = window.focus;
        window.focus = () => { window.__focusAt = performance.now(); return recordFocus(); };
    """)
    page.goto(running_server + '/theater')
    page.evaluate("window.name = 'neko_theater'")
    page.reload()
    expect(page.locator('[data-theater-selector-app]')).to_be_visible()
    page.wait_for_function("() => !!localStorage.getItem('neko:named-window:neko_theater')")
    other = page.context.new_page()
    _host_with_window_registry(other, running_server)
    with other.expect_popup() as opened_market:
        other.get_by_role('button', name='Market', exact=True).click()
    market = opened_market.value
    expect(market.locator('#theater-market-retry-btn')).to_be_visible()
    _click_and_expect_close(market, '#theater-market-back-link')
    page.wait_for_function('() => window.__focusAt !== undefined')
    assert page.evaluate('window.__focusAt >= window.__restoredAt')


@pytest.mark.frontend
def test_market_retry_stays_locked_after_a_successful_launch(page, running_server):
    calls = []
    def connect(route):
        calls.append(route.request.post_data_json)
        if len(calls) == 1:
            route.fulfill(status=502, json={'ok': False})
        else:
            route.fulfill(json={'ok': True, 'request_id': 'ok', 'url': running_server + '/__market_success_204'})
    landed = []
    # A 204 response never commits, so the page stays put after location.replace like a slow market load.
    page.route('**/__market_success_204', lambda route: (landed.append(1), route.fulfill(status=204)))
    page.route('**/api/theater-market/launch', connect)
    page.goto(running_server + '/theater?market=browse')
    retry = page.locator('#theater-market-retry-btn')
    expect(retry).to_be_visible()
    retry.click()
    for _ in range(100):
        if landed:
            break
        page.wait_for_timeout(20)
    assert landed, 'successful launch should navigate to the market url'
    assert page.evaluate("document.getElementById('theater-market-retry-btn').disabled") is True
    page.evaluate('window.nekoTheaterMarket.redirectSelector()')
    page.wait_for_timeout(100)
    assert len(calls) == 2


@pytest.mark.frontend
def test_market_page_restored_from_bfcache_unlocks_back_and_retry(page, running_server):
    calls = []
    page.route('**/api/theater-market/launch', lambda route: (calls.append(1), route.fulfill(status=502, json={'ok': False})))
    page.goto(running_server + '/theater?market=browse')
    retry = page.locator('#theater-market-retry-btn')
    expect(retry).to_be_visible()
    assert len(calls) == 1
    # Cancel the in-place navigation so the page stays with returning already set.
    page.evaluate("document.addEventListener('click', event => event.preventDefault())")
    page.get_by_role('link', name='返回选择剧本').click()
    page.wait_for_timeout(100)
    page.evaluate('window.nekoTheaterMarket.redirectSelector()')
    page.wait_for_timeout(100)
    assert len(calls) == 1
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    with page.expect_request('**/api/theater-market/launch'):
        retry.click()
    assert len(calls) == 2


@pytest.mark.frontend
def test_registered_selector_handles_a_focus_request_once_across_both_channels(page, running_server):
    _selector_api_stubs(page.context)
    page.add_init_script("""
        window.__restoreCalls = 0;
        window.nekoWindowControl = {restore: () => { window.__restoreCalls += 1; return Promise.resolve({ok: true}); }};
    """)
    page.goto(running_server + '/theater')
    page.evaluate("window.name = 'neko_theater'")
    page.reload()
    expect(page.locator('[data-theater-selector-app]')).to_be_visible()
    page.wait_for_function("() => !!localStorage.getItem('neko:named-window:neko_theater')")
    other = page.context.new_page()
    other.goto(running_server + '/theater?market=browse')
    acks = other.evaluate("""() => new Promise(resolve => {
        const channel = new BroadcastChannel('neko:named-window');
        let count = 0;
        channel.onmessage = event => {
            if (event.data && event.data.type === 'neko:named-window-focus-ack' && event.data.requestId === 'dup-1') count += 1;
        };
        const message = {type: 'neko:named-window-focus', windowName: 'neko_theater', requestId: 'dup-1'};
        channel.postMessage(message);
        localStorage.setItem('neko:named-window-focus:neko_theater', JSON.stringify(message));
        setTimeout(() => { channel.close(); resolve(count); }, 500);
    })""")
    assert acks == 1
    assert page.evaluate('window.__restoreCalls') == 1


@pytest.mark.frontend
def test_market_back_handoff_pending_across_bfcache_restore_does_not_navigate_later(page, running_server):
    """A handoff still waiting for the selector ack is dropped once the page comes back from bfcache."""
    _selector_api_stubs(page.context)
    _host_with_window_registry(page, running_server)
    page.evaluate("""() => localStorage.setItem('neko:named-window:neko_theater',
        JSON.stringify({url: location.origin + '/theater', timestamp: Date.now() + 60000}))""")
    with page.expect_popup() as opened_market:
        page.get_by_role('button', name='Market', exact=True).click()
    market = opened_market.value
    expect(market.locator('#theater-market-retry-btn')).to_be_visible()
    market_url = market.url
    market.get_by_role('link', name='返回选择剧本').click()
    # The handoff now waits for an ack that never comes; simulate a bfcache restore meanwhile.
    market.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    market.wait_for_timeout(2600)
    assert market.url == market_url
    expect(market.locator('#theater-market-back-link')).to_be_visible()
