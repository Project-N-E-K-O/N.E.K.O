import { expect, test, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

const ADAPTER_ID = 'adapter#appearance'
const ADAPTER_NAME = 'Boundary adapter'
const adapter = {
  id: ADAPTER_ID,
  name: ADAPTER_NAME,
  description: '',
  version: '1.0.0',
  type: 'adapter',
  status: 'stopped',
  runtime_enabled: true,
  runtime_auto_start: false,
  entries: [],
  list_actions: [],
}

async function isolateAppearanceApis(page: Page) {
  const unexpectedRequests: string[] = []
  // Only the built application may reach the preview server. An unanticipated
  // API or external request must never fall through to Vite's backend proxy.
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const staticResource = ['document', 'script', 'stylesheet', 'image', 'font'].includes(
      request.resourceType()
    )
    if (url.origin === PREVIEW_ORIGIN && url.pathname.startsWith('/ui/') && staticResource) {
      await route.continue()
      return
    }
    unexpectedRequests.push(`${request.method()} ${url.pathname}`)
    await route.abort('blockedbyclient')
  })
  await page.routeWebSocket('**/*', () => {})
  await stubCorePluginManagerApis(page)
  await page.route('**/plugins?*', (route) =>
    route.fulfill({ json: { plugins: [adapter], message: '' } })
  )
  await page.route('**/plugin/status', (route) =>
    route.fulfill({ json: { plugins: { [ADAPTER_ID]: 'stopped' } } })
  )
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
    route.fulfill({ json: { bridge_token: 'appearance-test-token' } })
  )
  await page.route('**/market/oauth/status', (route) =>
    route.fulfill({ json: { authenticated: false, auth_state: 'disconnected' } })
  )
  await page.route('**/market/installed?*', (route) => route.fulfill({ json: { items: [] } }))
  await page.route('**/plugin-cli/plugins', (route) =>
    route.fulfill({ json: { plugins: [], plugin_refs: [], count: 0 } })
  )
  await page.route('**/plugin-cli/packages', (route) =>
    route.fulfill({ json: { packages: [], count: 0, target_dir: '' } })
  )
  await page.route('**/plugins/adapter%23appearance?*', (route) => route.fulfill({ json: adapter }))
  await page.route('**/plugin/adapter%23appearance/surfaces*', (route) =>
    route.fulfill({ json: { surfaces: [], warnings: [] } })
  )
  await page.route('**/plugin/adapter%23appearance/ui-info', (route) =>
    route.fulfill({ json: { has_ui: false, ui_path: null, static_dir: null, static_files: [] } })
  )
  await page.route('**/plugin/adapter%23appearance/ui/?*', (route) =>
    route.fulfill({ status: 404, contentType: 'text/html', body: 'No custom UI in this fixture' })
  )
  return unexpectedRequests
}

const appearanceTest = test.extend<{ isolateNetwork: void }>({
  isolateNetwork: [
    async ({ page }, use) => {
      const requests = await isolateAppearanceApis(page)
      await page.addInitScript(() => {
        window.localStorage.setItem('locale', 'en-US')
        window.localStorage.setItem('neko-dark-mode', 'false')
      })
      await use()
      expect(requests, 'all HTTP requests must use explicit fixtures').toEqual([])
    },
    { auto: true },
  ],
})

appearanceTest(
  'keeps language, theme, refresh and the original package toolbar usable after collapse',
  async ({ page }) => {
    await page.goto(`${PREVIEW_ORIGIN}/ui/`)
    const header = page.locator('.header__actions')
    await expect(header.getByTestId('language-switcher')).toBeVisible()
    await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click()
    await page.getByRole('button', { name: 'Plugins', exact: true }).click()
    await expect(page.locator('.plugin-workbench')).toBeVisible()

    await header.getByTestId('language-switcher').click()
    await page.getByRole('menuitem', { name: /简体中文/ }).click()
    await expect(page.locator('[data-yui-guide-id="sidebar-plugins"]')).toHaveAccessibleName(
      '插件管理'
    )
    await header.getByRole('button', { name: '深色模式', exact: true }).click()
    await expect(page.locator('html')).toHaveClass(/dark/)
    await header.getByTestId('language-switcher').click()
    await page.getByRole('menuitem', { name: /English/ }).click()
    await expect(header.getByRole('button', { name: 'Light Mode', exact: true })).toBeVisible()

    const statusResponse = page.waitForResponse(
      (response) => new URL(response.url()).pathname === '/plugin/status'
    )
    await header.getByRole('button', { name: 'Refresh', exact: true }).click()
    await statusResponse
    await expect(header.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()

    const packageToggle = page.locator(
      '.workbench-header [data-yui-guide-id="plugin-list-package-panel-toggle"]'
    )
    const filter = page.getByPlaceholder(
      'Filter plugins with text, pinyin, and is:/type:/has: rules'
    )
    await expect(packageToggle).toBeInViewport()
    await filter.fill('retain this filter')
    const toggleBox = await packageToggle.boundingBox()
    const filterBox = await filter.boundingBox()
    expect(toggleBox!.y + toggleBox!.height).toBeLessThanOrEqual(filterBox!.y)
    await packageToggle.click()
    await expect(
      page.locator('.plugin-workbench__rail--package .package-manager--embedded')
    ).toBeVisible()
    await expect(page).toHaveURL(/\/ui\/plugins\?tab=packages$/)
    await expect(filter).toHaveValue('retain this filter')
    await packageToggle.click()
    await expect(page.locator('.plugin-workbench__rail--package')).toHaveAttribute(
      'aria-hidden',
      'true'
    )
    await page.getByRole('button', { name: 'Dashboard', exact: true }).click()
    await expect(page.locator('.dashboard')).toBeVisible()
    await expect(header.getByTestId('language-switcher')).toBeVisible()
    await expect(header.getByRole('button', { name: 'Light Mode', exact: true })).toBeVisible()
    await expect(header.getByRole('button', { name: 'Refresh', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toHaveAttribute(
      'aria-expanded',
      'false'
    )
  }
)

appearanceTest(
  'keeps navigation names and encoded adapter routes available with keyboard group disclosure',
  async ({ page }) => {
    await page.goto(`${PREVIEW_ORIGIN}/ui/`)
    const sidebar = page.locator('.sidebar')
    for (const name of [
      'Dashboard',
      'Plugins',
      'Knowledge',
      'Plugin API',
      'Runs',
      'Server Logs',
      'Development plugins',
    ]) {
      await expect(sidebar.getByRole('button', { name, exact: true })).toBeVisible()
    }
    const group = sidebar.getByRole('button', { name: 'Adapters', exact: true })
    await group.focus()
    await page.keyboard.press('Enter')
    await expect(group).toHaveAttribute('aria-expanded', 'false')
    await expect(sidebar.getByRole('button', { name: ADAPTER_NAME, exact: true })).toHaveCount(0)
    await expect(group).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(group).toHaveAttribute('aria-expanded', 'true')
    await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click()
    const adapterLink = sidebar.getByRole('button', { name: ADAPTER_NAME, exact: true })
    await expect(adapterLink).toHaveAttribute('title', ADAPTER_NAME)
    await adapterLink.click()
    await expect(page).toHaveURL(
      `${PREVIEW_ORIGIN}/ui/adapter/${encodeURIComponent(ADAPTER_ID)}/ui`
    )
    await expect(page.locator('.adapter-ui')).toContainText(ADAPTER_NAME)
  }
)

appearanceTest(
  'uses compact navigation on narrow screens without overwriting desktop preference and honors reduced motion',
  async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.setViewportSize({ width: 600, height: 800 })
    await page.goto(`${PREVIEW_ORIGIN}/ui/`)
    await expect(page.locator('.sidebar')).toHaveClass(/sidebar--collapsed/)
    await expect(page.getByRole('button', { name: /^(Collapse|Expand) sidebar$/ })).toHaveCount(0)
    await expect(page.getByTestId('language-switcher')).toBeInViewport()
    await expect(page.getByRole('button', { name: 'Dark Mode', exact: true })).toBeInViewport()
    await expect(
      page.locator('.header__actions').getByRole('button', { name: 'Refresh', exact: true })
    ).toBeInViewport()
    await page.getByRole('button', { name: 'Plugins', exact: true }).click()
    await expect(page.locator('.plugin-workbench')).toBeVisible()
    await page.setViewportSize({ width: 1280, height: 800 })
    await expect(
      page.getByRole('button', { name: 'Collapse sidebar', exact: true })
    ).toHaveAttribute('aria-expanded', 'true')
    await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click()
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('neko-sidebar-collapsed')))
      .toBe('true')
    expect(
      await page.locator('.app-sidebar').evaluate((element) => element.getAnimations().length)
    ).toBe(0)
    await page.reload()
    await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toHaveAttribute(
      'aria-expanded',
      'false'
    )
    await page.setViewportSize({ width: 600, height: 800 })
    await page.setViewportSize({ width: 1280, height: 800 })
    await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toHaveAttribute(
      'aria-expanded',
      'false'
    )
  }
)
