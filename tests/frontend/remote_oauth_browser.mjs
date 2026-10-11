// Real Chromium navigation against two isolated TLS origins. The IdP is a
// fixture; the instance gate, PKCE callback persistence and relay are production.
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.NEKO_TEST_PLAYWRIGHT_MODULE);
const browser = await chromium.launch({
  executablePath: process.env.NEKO_TEST_CHROME,
  headless: true,
  args: ['--no-proxy-server', '--host-resolver-rules=MAP backend.neko.test 127.0.0.1, MAP auth.neko.test 127.0.0.1'],
});
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  page.on('console', (message) => { if (message.type() === 'error') console.error('Browser:', message.text()); });
  page.on('response', (response) => console.log('Fixture HTTP:', new URL(response.url()).pathname, response.status()));
  const origin = process.env.NEKO_TEST_BACKEND_ORIGIN;
  await page.goto(origin + '/');
  await page.locator('input[name="key"]').fill(process.env.NEKO_TEST_INSTANCE_KEY);
  await page.locator('button').click();
  await page.waitForSelector('#login-community');
  const cookies = await context.cookies(origin);
  const credential = cookies.find((cookie) => cookie.name === 'neko_instance_access');
  assert.ok(credential?.httpOnly && credential.secure);
  assert.equal((await context.cookies(process.env.NEKO_TEST_AUTH_ORIGIN)).length, 0);
  const popupPromise = page.waitForEvent('popup');
  await page.click('#login-community');
  const popup = await popupPromise;
  if (process.env.NEKO_TEST_KEEP_POPUP === '1') await popup.clock.install();
  await page.waitForFunction(() => window.oauthPendingRelays?.size === 1);
  assert.equal(await popup.evaluate(() => window.opener === null), true);
  const keepPopup = process.env.NEKO_TEST_KEEP_POPUP === '1';
  if (!keepPopup) await popup.close();
  await page.waitForFunction(() => window.completion === true);
  const state = await page.evaluate(() => window.browserOAuthState);
  const completion = await page.evaluate(async (state) =>
    (await fetch('/api/card-drop/oauth/completion?state=' + encodeURIComponent(state))).json(), state);
  assert.deepEqual(completion, { logged_in: true });
  assert.equal(page.url(), origin + '/');
  if (keepPopup) {
    // A slow post-login handoff exceeds the former 60-second channel lifetime.
    await popup.clock.fastForward(121000);
    const destination = process.env.NEKO_TEST_AUTH_ORIGIN + '/community?neko_source_origin=' + encodeURIComponent(origin);
    assert.equal(await page.evaluate((url) => window.navigateRemoteCommunity(url), destination), true);
    await popup.waitForURL(destination);
    assert.equal(await popup.evaluate(() => window.opener === null), true);
    assert.equal(context.pages().length, 2, 'COOP return uses the original popup, never a second blocked popup');
  }
  const account = await page.evaluate(async () => (await fetch('/api/card-drop/oauth/status')).json());
  assert.equal(account.logged_in, true);
  assert.equal(account.user.email, 'fixture@example.test');
  for (const field of ['session_path', 'session_paths', 'access_token', 'refresh_token']) {
    assert.equal(field in account, false);
  }
  const replay = await page.evaluate(async (state) =>
    (await fetch('/api/card-drop/oauth/remote-callback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state, code: 'fixture-one-time-code' }),
    })).status, state);
  assert.equal(replay, 400);
  // Exercise actual browser CORS preflight/redemption without an instance
  // cookie. The community's one-use ticket is the credential for this hop.
  const ticket = await page.evaluate(async () =>
    (await (await fetch('/api/card-drop/sync-ticket')).json()).sync_ticket);
  const communityPage = await context.newPage();
  await communityPage.goto(process.env.NEKO_TEST_AUTH_ORIGIN + '/');
  const corsResult = await communityPage.evaluate(async ({ origin, ticket }) => {
    const capabilities = await fetch(origin + '/api/card-drop/capabilities', { credentials: 'omit' });
    const options = { method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sync_ticket: ticket }) };
    const handoff = await fetch(origin + '/api/card-drop/social-session-init', options);
    const data = await handoff.json();
    const replay = await fetch(origin + '/api/card-drop/social-session-init', options).catch(() => null);
    return { capabilities: capabilities.status, handoff: handoff.status, data, replay: replay?.status ?? 0 };
  }, { origin, ticket });
  assert.equal(corsResult.capabilities, 200);
  assert.equal(corsResult.handoff, 200);
  assert.ok(corsResult.data.access_token);
  assert.equal('refresh_token' in corsResult.data, false);
  assert.equal(corsResult.replay, 401);
  await communityPage.close();
  const stranger = await browser.newContext({ ignoreHTTPSErrors: true });
  const strangerPage = await stranger.newPage();
  const probe = await strangerPage.goto(origin + '/api/card-drop/auth-status');
  assert.equal(probe.status(), 401);
  assert.equal((await probe.text()).includes('fixture@example.test'), false);
  await stranger.close();
  await context.close();
  console.log('PASS: two-origin TLS browser pairing, production popup relay, PKCE persistence, completion, credential-free CORS ticket handoff, account isolation and replay rejection');
} finally {
  await browser.close();
}
