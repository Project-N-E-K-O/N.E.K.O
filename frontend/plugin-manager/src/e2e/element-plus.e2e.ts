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
