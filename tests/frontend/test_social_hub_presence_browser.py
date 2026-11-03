"""Real-browser presence menu regression checks; run this file directly, without a server."""

import mimetypes
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
PANEL = '.neko-social-hub-panel'
MENU = '.neko-social-hub-presence-menu'
TOGGLE = '.neko-social-hub-presence-toggle'
BODY = {
    'logged_in': True,
    'account': {'id': 'owner', 'display_name': 'Owner', 'avatar_url': 'hue:205', 'presence_status': 'online'},
    'friends': [{'id': f'friend-{i}', 'display_name': f'Friend {i}', 'avatar_url': 'hue:90',
                 'online': i < 2, 'presence_status': 'online' if i < 2 else 'offline'} for i in range(5)],
    'recommendations': [{'id': f'rec-{i}', 'display_name': f'Recommended {i}', 'avatar_url': 'hue:120'}
                        for i in range(3)],
}


class SocialHubAccountSwitchBrowserTests(unittest.TestCase):
    """Exercise the real first-reveal deadline with browser fetch scheduling."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_global_presence_recovery_uses_one_compact_read_on_all_desktop_routes(self):
        for entry in ['/', '/chat', '/chat_full']:
            with self.subTest(entry=entry):
                context = self.browser.new_context()
                page = context.new_page()
                page.clock.install()
                reads, errors = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))

                def route(request_route):
                    path = urlparse(request_route.request.url).path
                    if path == entry:
                        request_route.fulfill(content_type='text/html', body='''<!doctype html><body><script>
                        Math.random = () => 0;
                        window.nekoSocial = {
                          onNotifyIncoming(callback) { window.__notify = callback; },
                          onConnState(callback) { window.__connection = callback; },
                          onInvitationsChanged() {},
                          getInvitations: async () => ({generation: 1, accountKey: 'owner', accountId: 'owner',
                            rolloutEnabled: true, realtimeEnabled: true, synced: true, replicaValid: true, items: []})
                        };
                        </script><script src="/static/social-hub.js"></script></body>''')
                    elif path == '/api/community/visit-hub':
                        reads.append('hub')
                        request_route.fulfill(json=BODY)
                    elif path == '/api/community/friend-presence':
                        reads.append('compact')
                        request_route.fulfill(json={'friends': [{'id': 'friend-0',
                            'presence_status': 'away' if reads.count('compact') == 1 else 'online'}]})
                    else:
                        asset = ROOT / path.lstrip('/')
                        request_route.fulfill(status=200 if asset.is_file() else 404,
                            body=asset.read_bytes() if asset.is_file() else b'',
                            content_type=(mimetypes.guess_type(asset.name)[0] or 'application/octet-stream') + '; charset=utf-8')

                try:
                    page.route('**/*', route)
                    page.goto('http://neko.test' + entry)
                    self.assertEqual(errors, [])
                    page.wait_for_function('!!(window.nekoSocialHub && window.__notify)', timeout=5000)
                    page.evaluate('nekoSocialHub.open()')
                    page.wait_for_function('nekoSocialHub._state.loggedIn && !nekoSocialHub._state.loading')
                    baseline = reads.count('hub')
                    page.evaluate('''() => { for(let i=0; i<20; i++) __notify({kind: 'presence_resync', payload: {resync: true}}); }''')
                    page.wait_for_function("nekoSocialHub._state.friends[0].presenceStatus === 'away'")
                    self.assertEqual(page.evaluate('nekoSocialHub._state.friends.length'), len(BODY['friends']))
                    self.assertEqual(reads.count('compact'), 1)
                    self.assertEqual(reads.count('hub'), baseline)
                    page.clock.fast_forward(16000)
                    page.evaluate('__connection({connected: true})')
                    page.clock.run_for(600)
                    page.wait_for_function("nekoSocialHub._state.friends[0].presenceStatus === 'online'")
                    self.assertEqual(reads.count('compact'), 2)
                    self.assertEqual(page.evaluate('nekoSocialHub._state.friends.length'), len(BODY['friends']))
                    self.assertEqual(reads.count('hub'), baseline)
                    page.evaluate('nekoSocialHub.close(); __connection({connected: true})')
                    page.wait_for_timeout(600)
                    self.assertEqual(reads.count('compact'), 2)
                    self.assertEqual(errors, [])
                finally:
                    context.close()

    def test_account_switch_before_first_reveal_does_not_leave_hidden_or_reopen_closed_panel(self):
        for outcome in ['fast', 'slow', 'closed']:
            with self.subTest(outcome=outcome):
                context = self.browser.new_context(viewport={'width': 1280, 'height': 800})
                page = context.new_page()
                pending, errors = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))

                def route(request_route):
                    path = urlparse(request_route.request.url).path
                    if path == '/':
                        request_route.fulfill(content_type='text/html', body='''<!doctype html>
                            <html><head><link rel="stylesheet" href="/static/css/social-hub.css"></head>
                            <body><script>
                            window.__owner = { generation: 1, accountKey: 'account-a', accountId: 'account-a',
                              rolloutEnabled: true, realtimeEnabled: true, synced: true, replicaValid: true, items: [] };
                            window.nekoSocial = {
                              onInvitationsChanged(callback) { window.__changeOwner = callback; },
                              getInvitations() { return Promise.resolve(window.__owner); }
                            };
                            </script><script src="/static/social-hub.js"></script></body></html>''')
                    elif path == '/api/community/visit-hub':
                        pending.append(request_route)
                    else:
                        asset = ROOT / path.lstrip('/')
                        request_route.fulfill(status=200 if asset.is_file() else 404,
                            body=asset.read_bytes() if asset.is_file() else b'',
                            content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')

                try:
                    page.route('**/*', route)
                    page.goto('http://neko.test/')
                    page.wait_for_function('window.__changeOwner && window.nekoSocialHub')
                    page.evaluate('''() => {
                        nekoSocialHub.open();
                        __changeOwner({ ...__owner, generation: 2, accountKey: 'account-b', accountId: 'account-b' });
                    }''')
                    page.wait_for_function('window.nekoSocialHub._state.loading')
                    deadline = time.monotonic() + 3
                    while len(pending) < 2 and time.monotonic() < deadline:
                        page.wait_for_timeout(10)
                    self.assertEqual(len(pending), 2)
                    old, replacement = pending
                    old.fulfill(json={'logged_in': True, 'account': {'id': 'account-a'},
                        'friends': [], 'recommendations': []})
                    if outcome == 'closed':
                        page.evaluate('nekoSocialHub.close()')
                    elif outcome == 'slow':
                        expect(page.locator(PANEL)).to_be_visible(timeout=2000)
                        self.assertTrue(page.evaluate('nekoSocialHub._state.loading'))
                    replacement.fulfill(json={'logged_in': True,
                        'account': {'id': 'account-b', 'display_name': 'New Account'},
                        'friends': [], 'recommendations': []})
                    pending.clear()
                    if outcome == 'closed':
                        page.wait_for_timeout(350)
                        expect(page.locator(PANEL)).to_be_hidden()
                    else:
                        expect(page.locator(PANEL)).to_be_visible()
                        page.wait_for_function("nekoSocialHub._state.accountId === 'account-b' && !nekoSocialHub._state.loading")
                    self.assertEqual(errors, [])
                finally:
                    context.close()


class SocialHubPresenceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1280, 'height': 800},
                                                screen={'width': 1280, 'height': 800}, reduced_motion='reduce')
        self.page = self.context.new_page()
        self.pending = []
        self.reads = 0
        self.presence_updates = []
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/*', self.route)
        self.page.goto('http://neko.test/static/social-hub-preview.html')
        self.page.wait_for_function('window.nekoSocialHub && !nekoSocialHub._state.loading')
        self.page.locator('#preview-btn-social').click()
        expect(self.page.locator(PANEL)).to_be_visible()

    def tearDown(self):
        try:
            for route in self.pending:
                route.abort()
            self.page.unroute_all(behavior='wait')
            self.assertEqual(self.errors, [])
        finally:
            self.context.close()

    def route(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/community/visit-hub':
            self.reads += 1
            if self.reads == 1:
                route.fulfill(json=BODY)
            else:
                # Leave refresh pending so DOM geometry is tested before any new data arrives.
                self.pending.append(route)
        elif path == '/api/community/presence':
            status = route.request.post_data_json['status']
            self.presence_updates.append(status)
            route.fulfill(json={'status': status})
        elif path == '/api/community/friend-presence':
            route.fulfill(json={'friends': BODY['friends']})
        else:
            asset = ROOT / path.lstrip('/')
            route.fulfill(status=200 if asset.is_file() else 404,
                          body=asset.read_bytes() if asset.is_file() else b'',
                          content_type=(mimetypes.guess_type(asset.name)[0] or 'application/octet-stream') + '; charset=utf-8')

    def complete_refresh(self, body=BODY):
        self.page.wait_for_function('nekoSocialHub._state.loading')
        self.assertTrue(self.pending)
        self.pending.pop(0).fulfill(json=body)
        self.page.wait_for_function('!nekoSocialHub._state.loading')

    def open_menu(self):
        self.page.locator(TOGGLE).click()
        expect(self.page.locator(MENU)).to_be_visible()

    def assert_options_hit_test(self):
        for option in self.page.locator(MENU + ' [role="menuitemradio"]').all():
            self.assertTrue(option.evaluate('''e => {
                const r=e.getBoundingClientRect(); const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                return hit === e || e.contains(hit);
            }'''))

    def test_loading_menu_is_visible_and_clickable_beyond_panel(self):
        height = self.page.locator(PANEL).evaluate('e=>e.offsetHeight')
        self.open_menu()
        self.assertTrue(self.page.evaluate('nekoSocialHub._state.loading'))
        self.assertEqual(self.page.locator(PANEL).evaluate('e=>e.offsetHeight'), height)
        self.assertEqual(self.page.locator(PANEL).evaluate('e=>getComputedStyle(e).overflow'), 'hidden')
        self.assertFalse(self.page.locator(MENU).evaluate('e=>e.closest(".neko-social-hub-panel") !== null'))
        self.assert_options_hit_test()
        self.assertTrue(self.page.locator('[data-neko-focus-key="presence-invisible"]').evaluate('''e=>{
            const r=e.getBoundingClientRect();
            return AvatarPopupUI.isRectOverlappedByVisibleOverlay(r);
        }'''))
        self.page.locator('[data-neko-focus-key="presence-invisible"]').click()
        self.page.wait_for_function('!nekoSocialHub._state.presenceSaving')
        self.assertEqual(self.presence_updates, ['invisible'])
        expect(self.page.locator(PANEL)).to_be_visible()
        expect(self.page.locator(TOGGLE)).to_be_focused()

    def test_small_loaded_panel_does_not_clip_menu(self):
        self.complete_refresh({**BODY, 'friends': BODY['friends'][:1], 'recommendations': []})
        self.open_menu()
        self.assert_options_hit_test()
        self.assertLess(self.page.locator(PANEL).evaluate('e=>e.offsetHeight'), 300)

    def test_refresh_preserves_menu_focus_and_repositions_after_panel_growth(self):
        self.page.locator('#preview-btn-social').evaluate("e=>e.style.top='46vh'")
        self.page.evaluate("window.dispatchEvent(new Event('resize'))")
        self.open_menu()
        self.page.keyboard.press('End')
        self.complete_refresh()
        expect(self.page.locator('[data-neko-focus-key="presence-invisible"]')).to_be_focused()
        self.assert_options_hit_test()
        self.page.wait_for_function('''() => {
            const m=document.querySelector('.neko-social-hub-presence-menu').getBoundingClientRect();
            const a=document.querySelector('.neko-social-hub-presence-picker').getBoundingClientRect();
            return Math.abs(m.top-a.bottom-6)<2 || Math.abs(a.top-m.bottom-6)<2;
        }''')

    def test_escape_and_tab_return_focus_without_closing_panel(self):
        self.complete_refresh()
        self.open_menu()
        self.page.keyboard.press('End')
        expect(self.page.locator('[data-neko-focus-key="presence-invisible"]')).to_be_focused()
        self.page.keyboard.press('ArrowDown')
        expect(self.page.locator('[data-neko-focus-key="presence-online"]')).to_be_focused()
        self.page.keyboard.press('Escape')
        expect(self.page.locator(MENU)).to_have_count(0)
        expect(self.page.locator(TOGGLE)).to_be_focused()
        expect(self.page.locator(PANEL)).to_be_visible()
        self.page.keyboard.press('ArrowUp')
        expect(self.page.locator('[data-neko-focus-key="presence-invisible"]')).to_be_focused()
        self.page.keyboard.press('Tab')
        expect(self.page.locator(MENU)).to_have_count(0)
        expect(self.page.locator(PANEL)).to_be_visible()
        expect(self.page.locator('[data-neko-focus-key="recommendation-rec-0"]')).to_be_focused()
        self.page.locator(TOGGLE).focus()
        self.page.keyboard.press('ArrowDown')
        self.page.keyboard.press('Shift+Tab')
        expect(self.page.locator(MENU)).to_have_count(0)
        expect(self.page.locator('[data-neko-focus-key="open-community"]')).to_be_focused()
        self.page.keyboard.press('Escape')
        expect(self.page.locator(PANEL)).to_be_hidden()

    def test_dismissing_menu_keeps_original_panel_click_target(self):
        self.open_menu()
        self.page.locator(PANEL).evaluate('''p=>{
            const title=p.querySelector('h2');title.id='click-target';
            title.onclick=()=>window.panelClicked=true;
        }''')
        self.page.locator('#click-target').click()
        self.assertTrue(self.page.evaluate('window.panelClicked'))
        expect(self.page.locator(MENU)).to_have_count(0)
        expect(self.page.locator(PANEL)).to_be_visible()

    def test_close_and_reopen_drops_menu_state(self):
        self.open_menu()
        self.page.locator('#preview-btn-social').click()
        expect(self.page.locator(MENU)).to_have_count(0)
        self.page.locator('#preview-btn-social').click()
        expect(self.page.locator(PANEL)).to_be_visible()
        expect(self.page.locator(MENU)).to_have_count(0)
        expect(self.page.locator(TOGGLE)).to_have_attribute('aria-expanded', 'false')

    def test_viewport_edges_flip_and_constrain_menu(self):
        for width, height in [(1280, 540), (360, 300), (280, 180)]:
            with self.subTest(viewport=(width, height)):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.locator('#preview-btn-social').evaluate("e=>{e.style.top='90vh';e.style.left='80vw'}")
                self.page.evaluate("window.dispatchEvent(new Event('resize'))")
                self.open_menu()
                self.page.wait_for_function('''() => {
                    const r=document.querySelector('.neko-social-hub-presence-menu').getBoundingClientRect();
                    return r.left>=11 && r.top>=11 && r.right<=innerWidth-11 && r.bottom<=innerHeight-11;
                }''')
                self.page.keyboard.press('End')
                expect(self.page.locator('[data-neko-focus-key="presence-invisible"]')).to_be_focused()
                self.assertTrue(self.page.locator('[data-neko-focus-key="presence-invisible"]').evaluate('''e=>{
                    const r=e.getBoundingClientRect(),h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                    return h===e || e.contains(h);
                }'''))
                self.page.keyboard.press('Escape')

    def test_dark_menu_keeps_theme_after_leaving_panel(self):
        self.page.evaluate("document.documentElement.dataset.theme='dark'")
        self.open_menu()
        self.assertEqual(self.page.locator(MENU).evaluate('e=>getComputedStyle(e).getPropertyValue("--hub-text").trim()'), '#edf5ff')

    def test_standalone_window_menu_uses_root_and_remains_clickable(self):
        for route in self.pending:
            route.abort()
        self.pending.clear()
        self.reads = 0
        html = (ROOT / 'templates/social_hub.html').read_text(encoding='utf-8').replace('{{ static_asset_version }}', 'test')
        self.page.route('**/social_hub', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        self.page.goto('http://neko.test/social_hub')
        self.page.wait_for_function('window.nekoSocialHub && !nekoSocialHub._state.loading')
        self.open_menu()
        self.assertEqual(self.page.locator(MENU).evaluate('e=>e.parentNode.id'), 'neko-social-hub-root')
        self.assert_options_hit_test()
        self.page.locator('[data-neko-focus-key="presence-away"]').click()
        self.page.wait_for_function('!nekoSocialHub._state.presenceSaving')
        self.assertEqual(self.presence_updates, ['away'])

    def test_animation_completion_keeps_menu_attached_to_current_anchor(self):
        self.page.emulate_media(reduced_motion='no-preference')
        self.open_menu()
        self.page.wait_for_timeout(220)
        self.assert_options_hit_test()
        self.assertTrue(self.page.evaluate('''() => {
            const a=document.querySelector('.neko-social-hub-presence-picker').getBoundingClientRect();
            const m=document.querySelector('.neko-social-hub-presence-menu').getBoundingClientRect();
            return Math.abs(m.top-a.bottom-6)<2;
        }'''))


if __name__ == '__main__':
    unittest.main()
