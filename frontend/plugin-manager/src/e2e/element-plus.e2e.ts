import { expect, test, type Page } from '@playwright/test'
import {
  PREVIEW_ORIGIN,
  collectVueResolutionWarnings,
  stubCorePluginManagerApis,
} from './plugin-manager-test-helpers'

async function stubPluginManagerApis(page: Page) {
  await stubCorePluginManagerApis(page)
  await page.route('**/plugins/refresh', (route) =>
    route.fulfill({
      json: {
        success: true,
        added: [],
        updated: [],
        removed: [],
        removed_running: [],
        unchanged: [],
        failed: [],
        scanned_count: 0,
      },
    })
  )
  await page.route('**/market/status', (route) =>
    route.fulfill({
      json: {
        market_url: 'https://market.example.test',
        market_web_url: 'https://market.example.test',
      },
    })
  )
  await page.route('**/market/bridge-token', (route) =>
    route.fulfill({
      json: { bridge_token: 'test-bridge-token' },
    })
  )
  await page.route('**/market/oauth/status', (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        auth_state: 'connected',
        profile: { username: 'neko', display_name: 'Neko' },
      },
    })
  )
  await page.route('**/plugin/*/logs*', (route) =>
    route.fulfill({
      json: { plugin_id: 'demo', logs: [], total_lines: 0, returned_lines: 0 },
    })
  )
  await page.route('**/plugin/metrics/*', (route) =>
    route.fulfill({ json: { plugin_id: 'demo', metrics: null } })
  )
  await page.route('**/plugin/*/config/base', (route) =>
    route.fulfill({ json: { schema: {}, config: {} } })
  )
  await page.route('**/plugin/*/config/profiles', (route) =>
    route.fulfill({ json: { profiles: [], active_profile: null } })
  )
  await page.route('**/plugin/*/config', (route) =>
    route.fulfill({ json: { schema: {}, config: {} } })
  )
  await page.route('**/market/installed?*', (route) => route.fulfill({ json: { items: [] } }))
  await page.routeWebSocket('**/ws/**', () => {})
}

test.beforeEach(async ({ page }) => {
  await stubPluginManagerApis(page)
})

test('preserves the global message offset and overlay z-index', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('neko-dark-mode', 'true')
  })
  await page.goto(`${PREVIEW_ORIGIN}/ui/`)
  await page.locator('.header__actions .header-btn').last().click()

  const message = page.locator('.el-message')
  await expect(message).toBeVisible()
  await expect(message).toHaveCSS('top', '54px')
  expect(
    Number(await message.evaluate((element) => getComputedStyle(element).zIndex))
  ).toBeGreaterThan(12000)
  await expect(page.locator('html')).toHaveClass(/dark/)
})

test('resolves routed components and the loading directive on the plugin list', async ({
  page,
}) => {
  let finishAccountSummary: (() => void) | undefined
  const accountSummaryGate = new Promise<void>((resolve) => {
    finishAccountSummary = resolve
  })
  const resolutionWarnings = collectVueResolutionWarnings(page)
  await page.addInitScript(() => {
    window.localStorage.setItem('neko_bridge_token', 'test-bridge-token')
  })
  await page.route('**/market/oauth/account-summary', async (route) => {
    await accountSummaryGate
    await route.fulfill({
      json: {
        authenticated: true,
        profile: { username: 'neko', display_name: 'Neko' },
        market: { plugins_count: 0, downloads_count: 0 },
      },
    })
  })

  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  await expect(page.locator('.plugin-workbench')).toBeVisible()
  await page.locator('.market-auth-trigger--connected').click()

  const accountCard = page.locator('.market-account-card')
  await expect(accountCard).toBeVisible()
  await expect(accountCard.locator('.el-loading-mask')).toBeVisible()

  finishAccountSummary?.()
  await expect(accountCard.locator('.el-loading-mask')).toHaveCount(0)
  expect(resolutionWarnings).toEqual([])
})

test('preserves the configured Element Plus locale on plugin detail dialogs', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('locale', 'ja')
    window.localStorage.setItem('neko-dark-mode', 'true')
  })
  await page.route('**/plugins?*', (route) =>
    route.fulfill({
      json: {
        plugins: [
          {
            id: 'demo',
            name: 'Demo',
            description: '',
            version: '1.0.0',
            type: 'plugin',
            status: 'running',
            runtime_enabled: true,
            runtime_auto_start: false,
            entries: [],
            list_actions: [],
          },
        ],
        message: '',
      },
    })
  )
  await page.route('**/plugin/status?*', (route) =>
    route.fulfill({ json: { status: 'running', plugin_id: 'demo' } })
  )
  await page.route('**/plugin/demo/surfaces?*', (route) =>
    route.fulfill({ json: { surfaces: [], warnings: [] } })
  )

  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/demo`)
  await expect(page.locator('.plugin-detail')).toBeVisible()
  await page.getByRole('button', { name: '停止' }).click()

  const messageBox = page.locator('.el-message-box')
  const overlay = page.locator('.el-overlay.is-message-box')
  await expect(messageBox).toBeVisible()
  await expect(overlay).toBeVisible()
  await expect(messageBox.getByRole('button', { name: 'キャンセル' })).toBeVisible()
  expect(
    Number(await overlay.evaluate((element) => getComputedStyle(element).zIndex))
  ).toBeGreaterThan(12000)
  expect(
    await messageBox.evaluate((element) => getComputedStyle(element).backgroundColor)
  ).not.toBe('rgb(255, 255, 255)')
  await messageBox.getByRole('button', { name: 'キャンセル' }).click()
  await expect(messageBox).toHaveCount(0)
})

test('renders every top-level route without unresolved Element Plus components', async ({
  page,
}) => {
  const resolutionWarnings = collectVueResolutionWarnings(page)

  const routes = [
    { path: '/', selector: '.dashboard' },
    { path: '/plugins', selector: '.plugin-workbench' },
    { path: '/runs', selector: '.runs-page' },
    { path: '/packages', selector: '.package-manager' },
    { path: '/logs/_server', selector: '.logs-page' },
    { path: '/market', selector: '.market-workbench' },
    { path: '/adapter/demo/ui', selector: '.adapter-ui' },
  ]

  for (const route of routes) {
    await page.goto(`${PREVIEW_ORIGIN}/ui${route.path}`)
    await expect(page.locator(route.selector)).toBeVisible()
  }

  expect(resolutionWarnings).toEqual([])
})

for (const initialAutoStart of [undefined, false]) {
  const initialPreference = initialAutoStart === undefined ? 'omitted' : 'false'

  test(`auto-start stays independent of manual start when the preference is ${initialPreference}`, async ({
    page,
  }, testInfo) => {
    // Exercise the built UI and its HTTP contract. This stateful API stub does
    // not claim to verify backend persistence or launch a real plugin process.
    let autoStart: boolean | undefined = initialAutoStart
    let status = 'stopped'
    const mutations: Array<{ method: string; action: string; body: unknown }> = []
    const plugin = () => ({
      id: 'demo',
      name: 'Demo',
      description: '',
      version: '1.0.0',
      type: 'plugin',
      status,
      runtime_enabled: true,
      ...(autoStart === undefined ? {} : { runtime_auto_start: autoStart }),
      entries: [],
      list_actions: [],
    })

    await page.addInitScript(() => {
      window.localStorage.setItem('locale', 'en-US')
    })
    await page.route('**/plugins?*', (route) =>
      route.fulfill({ json: { plugins: [plugin()], message: '' } })
    )
    await page.route((url) => url.pathname === '/plugins/demo', (route) =>
      route.fulfill({ json: { plugin: plugin() } })
    )
    await page.route('**/plugin/status', (route) =>
      route.fulfill({ json: { plugins: { demo: { status, plugin_id: 'demo' } } } })
    )
    await page.route('**/plugin/status?*', (route) =>
      route.fulfill({ json: { status, plugin_id: 'demo' } })
    )
    await page.route('**/plugin/demo/surfaces?*', (route) =>
      route.fulfill({ json: { surfaces: [], warnings: [] } })
    )
    await page.route('**/plugin/demo/config/application-state', (route) =>
      route.fulfill({ json: { plugin_id: 'demo', config_state: 'matched' } })
    )
    await page.route('**/plugin/demo/config/base/effective', (route) =>
      route.fulfill({ json: { schema: {}, config: {} } })
    )
    await page.route('**/api/model-config/plugins/demo/bindings', (route) =>
      route.fulfill({ json: { plugin_id: 'demo', requirements: {}, bindings: {}, ready: true } })
    )
    await page.route(/\/plugin\/demo\/(start|stop|reload|auto-start)$/, (route) => {
      const request = route.request()
      const action = new URL(request.url()).pathname.split('/').pop()!
      const body = request.postDataJSON()
      mutations.push({ method: request.method(), action, body })
      if (action === 'auto-start') {
        autoStart = body.auto_start
      } else {
        status = action === 'stop' ? 'stopped' : 'running'
      }
      return route.fulfill({
        json: { success: true, plugin_id: 'demo', auto_start: autoStart, message: '' },
      })
    })

    await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/demo?tab=info`)
    const autoStartControl = page.getByTestId('plugin-auto-start-switch')
    const autoStartSwitch = page.getByRole('switch', { name: 'Auto Start', exact: true })
    await expect(autoStartControl).toBeVisible()
    await expect(autoStartSwitch).not.toBeChecked()
    await expect(autoStartSwitch).toBeEnabled()

    await page.getByRole('button', { name: 'Start', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeEnabled()
    await expect(page.locator('.el-message--success')).toHaveText('Plugin started successfully')
    await expect(autoStartSwitch).not.toBeChecked()
    expect(mutations).toEqual([{ method: 'POST', action: 'start', body: null }])

    await autoStartControl.click()
    await expect(autoStartSwitch).toBeChecked()
    await expect(autoStartSwitch).toBeEnabled()
    expect(mutations).toEqual([
      { method: 'POST', action: 'start', body: null },
      { method: 'PUT', action: 'auto-start', body: { auto_start: true } },
    ])

    await page.reload()
    await expect(autoStartControl).toBeVisible()
    await expect(autoStartSwitch).toBeChecked()
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeEnabled()
    expect(mutations).toHaveLength(2)
    await testInfo.attach(`auto-start-${initialPreference}-after-refresh`, {
      body: await page.screenshot(),
      contentType: 'image/png',
    })
  })
}
