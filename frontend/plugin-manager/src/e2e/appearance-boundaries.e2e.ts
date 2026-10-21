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

appearanceTest('keeps narrow rules in view and does not interrupt typing when hover dismissal runs', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 320, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  const trigger = page.locator('[data-yui-guide-id="plugin-list-filter-rules"]')
  await trigger.focus()
  await page.keyboard.press('Enter')
  const panel = page.locator('.wb-filter-rules-dropdown')
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('button').first()).toBeFocused()
  await expect(panel).toHaveCSS('transform', 'none')
  const bounds = await panel.boundingBox()
  expect(bounds!.x).toBeGreaterThanOrEqual(8)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(312)
  expect(bounds!.y).toBeGreaterThanOrEqual(8)
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(892)
  await page.keyboard.press('Escape')
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await expect(trigger).toBeFocused()

  await trigger.click()
  const input = page.getByPlaceholder('Filter plugins with text, pinyin, and is:/type:/has: rules')
  await page.keyboard.press('Tab')
  await expect(input).toBeFocused()
  await page.mouse.move(319, 899)
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await expect(input).toBeFocused()
  await page.keyboard.type('still typing')
  await expect(input).toHaveValue('still typing')
})

appearanceTest('honors reduced motion in rule choices and package import without changing their actions', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  const trigger = page.locator('[data-yui-guide-id="plugin-list-filter-rules"]')
  await trigger.focus()
  await page.keyboard.press('Enter')
  const choice = page.locator('.wb-filter-rule-chip').first()
  await choice.hover()
  await expect(choice).toHaveCSS('transform', 'none')
  await expect(choice).toHaveCSS('transition-duration', '0s')
  await choice.click()
  await expect(page.getByPlaceholder('Filter plugins with text, pinyin, and is:/type:/has: rules')).not.toHaveValue('')
  await page.locator('[data-yui-guide-id="plugin-list-import"]').click()
  const dropzone = page.locator('.plugin-import-dropzone')
  await expect(dropzone).toBeVisible()
  await dropzone.hover()
  await expect(dropzone).toHaveCSS('transform', 'none')
  await expect(dropzone).toHaveCSS('transition-duration', '0s')
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(dropzone).not.toBeVisible()
})

appearanceTest(
  'keeps language, theme, refresh and the original package toolbar usable after collapse',
  async ({ page }) => {
    await page.goto(`${PREVIEW_ORIGIN}/ui/`)
    const titlebar = page.locator('.window-titlebar')
    const header = titlebar.locator('.header__actions')
    await expect(page.locator('.app-header, .header__title')).toHaveCount(0)
    await expect(header.getByTestId('language-switcher')).toBeVisible()
    const toolsBox = await header.boundingBox()
    const controlsBox = await titlebar.locator('.titlebar-controls').boundingBox()
    expect(toolsBox!.x + toolsBox!.width).toBeLessThan(controlsBox!.x)
    await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click()
    await page.getByRole('button', { name: 'Plugins', exact: true }).click()
    await expect(page.locator('.plugin-workbench')).toBeVisible()
    await expect.poll(() => page.locator('.app-main').evaluate((element) => {
      const main = element.getBoundingClientRect()
      const workbench = element.querySelector('.plugin-workbench')!.getBoundingClientRect()
      const sidebarElement = document.querySelector('.app-sidebar')!
      const sidebar = sidebarElement.getBoundingClientRect()
      const bodyElement = document.querySelector('.app-body')!
      const body = bodyElement.getBoundingClientRect()
      return [
        main.top - sidebar.top,
        main.bottom - sidebar.bottom,
        window.innerWidth - main.right - sidebar.left,
        main.left - body.left,
        main.top - body.top,
        main.right - body.right,
        main.bottom - body.bottom,
        workbench.left - main.left,
        workbench.top - main.top,
        main.right - workbench.right,
        main.bottom - workbench.bottom,
        parseFloat(getComputedStyle(bodyElement).borderRadius) -
          parseFloat(getComputedStyle(sidebarElement).borderRadius),
      ].map(Math.round)
    })).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])

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
    await page.addInitScript(() => {
      window.nekoWindowControl = {
        getPinState: () => ({ available: true, pinned: false }),
      }
    })
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
    await page.setViewportSize({ width: 320, height: 800 })
    const titlebar = page.locator('.window-titlebar')
    await expect(titlebar.getByTestId('language-switcher')).toBeInViewport({ ratio: 1 })
    for (const name of ['Dark Mode', 'Refresh', 'Pin window', 'Minimize', 'Maximize', 'Close']) {
      await expect(titlebar.getByRole('button', { name, exact: true })).toBeInViewport({ ratio: 1 })
    }
  }
)

// Exercise the production card/grid with short-only and mixed-content rows.
async function installCardLayoutFixture(page: Page, count = 8) {
  const plugins = Array.from({ length: count }, (_, index) => ({
    id: `layout_${index}`,
    name: index < 4 ? ['猫娘小游戏', 'NEKO Live', 'RVC歌声合成', '微信集成'][index]! :
      'A deliberately long plugin name that must remain readable / 长名称插件',
    description: index % 2 ? 'A short description.' :
      '通过 OpenClaw API 连接微信个人号，支持扫码登录、消息监听和发送。A longer description for mixed content.',
    version: index === 5 ? '2026.10.21-preview-with-a-long-version-identifier' : '1.0.0',
    type: 'plugin',
    status: index % 2 ? 'stopped' : 'running',
    runtime_enabled: true,
    runtime_auto_start: index % 2 === 0,
    entry_count: index,
    entries: [],
    install_source: index === 1 ? {
      source: 'market',
      source_detail: { plugin_market_id: '42', version: '1.0.0', channel: 'stable' },
    } : index === 4 ? {
      source: 'imported',
      source_detail: { package_filename: 'a-long-imported-plugin-bundle.npkg', package_sha256: 'c'.repeat(64) },
    } : { source: 'builtin' },
    list_actions: index % 2 ? [] : [{ id: 'open_ui', kind: 'ui', requires_running: true }],
  }))
  await page.route('**/plugins?*', route => route.fulfill({ json: { plugins, message: '' } }))
  await page.route('**/market/catalog/api/v1/plugins/latest-versions?*', route =>
    route.fulfill({ json: { items: [{ plugin_id: '42', version: '2.0.0' }] } })
  )
  await page.route('**/plugin/status', route => route.fulfill({ json: {
    plugins: Object.fromEntries(plugins.map(plugin => [plugin.id, {
      plugin_id: plugin.id,
      source: 'main_process_synthetic',
      status: { status: plugin.status },
    }])),
  } }))
}

async function expectAlignedCardTracks(page: Page) {
  await expect.poll(() => page.locator('.plugin-card').evaluateAll(cards => {
    const rows = new Map<number, number[][]>()
    for (const card of cards) {
      const box = card.getBoundingClientRect()
      const children = Array.from(card.children, child => child.getBoundingClientRect().top)
      for (const selector of ['.plugin-card-footer__main', '.plugin-card-footer__actions', '.plugin-entries']) {
        children.push(card.querySelector(selector)!.getBoundingClientRect().top)
      }
      const key = Math.round(box.top)
      rows.set(key, [...(rows.get(key) || []), children])
    }
    return [...rows.values()].every(row => row.every(tracks =>
      tracks.every((top, index) => Math.abs(top - row[0]![index]!) < 1)
    ))
  })).toBe(true)
}

for (const scenario of [
  { width: 1440, zoom: 1, fontSize: 16, locale: 'en-US' },
  { width: 1440, zoom: 1.5, fontSize: 16, locale: 'en-US' },
  { width: 960, zoom: 1, fontSize: 24, locale: 'en-US' },
  { width: 640, zoom: 1, fontSize: 16, locale: 'en-US' },
  { width: 320, zoom: 1, fontSize: 24, locale: 'en-US' },
  { width: 1440, zoom: 1, fontSize: 16, locale: 'zh-CN' },
  { width: 960, zoom: 1, fontSize: 24, locale: 'zh-CN' },
]) {
  appearanceTest(`card tracks remain aligned at ${scenario.width}px, zoom ${scenario.zoom}, text ${scenario.fontSize}px, ${scenario.locale}`, async ({ page }) => {
    await installCardLayoutFixture(page)
    await page.addInitScript(locale => localStorage.setItem('locale', locale), scenario.locale)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.setViewportSize({ width: scenario.width, height: 1000 })
    await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
    await expect(page.locator('.plugin-card')).toHaveCount(8)
    await page.evaluate(({ zoom, fontSize }) => {
      document.documentElement.style.zoom = String(zoom)
      document.documentElement.style.fontSize = `${fontSize}px`
    }, scenario)
    await expectAlignedCardTracks(page)
    const first = page.locator('.plugin-card').first()
    // Short-only rows must not reserve an imaginary second title line.
    const gap = await first.evaluate(card => {
      const title = card.querySelector('.plugin-name')!.getBoundingClientRect()
      const states = card.querySelector('.plugin-statuses')!
      const contentTop = Math.min(...Array.from(states.children, child => child.getBoundingClientRect().top))
      return contentTop - title.bottom
    })
    expect(gap).toBeGreaterThanOrEqual(7 * scenario.zoom)
    expect(gap).toBeLessThanOrEqual(9 * scenario.zoom)
    await expect(first.locator('footer [data-testid="plugin-open-ui"]')).toBeVisible()
    expect(await page.locator('.plugin-card').evaluateAll(cards => cards.every(card => {
      const box = card.getBoundingClientRect()
      return card.scrollWidth <= card.clientWidth + 1 &&
        [...card.querySelectorAll('.plugin-name__button, .plugin-start-mode, .plugin-entries, .plugin-version-tag, .plugin-ui-action')]
          .every(element => {
            const bounds = element.getBoundingClientRect()
            return bounds.left >= box.left && bounds.right <= box.right + 1 &&
              element.scrollWidth <= element.clientWidth + 1
          })
    }))).toBe(true)
    await page.locator('[data-yui-guide-id="plugin-list-multi-select"]').click()
    await expect(page.locator('.grid-section__select')).toHaveCount(8)
    await page.locator('.grid-section__select').first().click()
    await expect(page.locator('.grid-section__select').first()).toHaveAttribute('aria-pressed', 'true')
  })
}

appearanceTest('card extras, themes and layout switches preserve aligned, reachable content', async ({ page }) => {
  await installCardLayoutFixture(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.route('**/plugin/metrics/**', route => route.fulfill({ json: { metrics: {
    plugin_id: new URL(route.request().url()).pathname.split('/').pop(),
    timestamp: Date.now(), cpu_percent: 3.5, memory_mb: 128, num_threads: 4,
    pending_requests: 2, total_executions: 1234,
  } } }))
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  await expect(page.locator('.plugin-card')).toHaveCount(8)
  await page.locator('[data-yui-guide-id="plugin-list-metrics-toggle"]').click()
  await page.locator('[data-yui-guide-id="plugin-list-source-toggle"]').click()
  await expect(page.locator('.plugin-card .metrics-bar')).toHaveCount(8)
  await expect(page.locator('.plugin-card').first().locator('.metrics-bar')).toContainText('3.5%')
  await expect(page.locator('.plugin-card .source-detail-bar')).toHaveCount(8)
  await page.locator('.window-titlebar').getByRole('button', { name: 'Dark Mode', exact: true }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  for (const mode of ['Single', 'Double', 'List', 'Compact']) {
    await page.locator('[data-yui-guide-id="plugin-list-layout-mode"]').getByText(mode, { exact: true }).click()
    const entries = page.locator(mode === 'List' ? '.plugin-list-row-card' : '.plugin-card')
    await expect(entries).toHaveCount(8)
    await expect(entries.first().getByTestId('plugin-open-ui')).toBeVisible()
    expect(await entries.evaluateAll(cards => cards.every(card => card.scrollWidth <= card.clientWidth + 1))).toBe(true)
    if (mode !== 'List') {
      await expectAlignedCardTracks(page)
      const first = entries.first()
      expect(await first.evaluate(card => {
        const footer = card.querySelector('footer')!.getBoundingClientRect()
        const extras = card.querySelector('.source-detail-bar')!.getBoundingClientRect()
        return footer.top >= extras.bottom
      })).toBe(true)
    }
  }
})

appearanceTest('large card grids keep real track sizes when scrolling and filtering', async ({ page }) => {
  await installCardLayoutFixture(page, 84)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  await expect(page.locator('.plugin-card')).toHaveCount(84)
  const last = page.locator('.plugin-card').last()
  await last.scrollIntoViewIfNeeded()
  await expect(last.getByTestId('plugin-details-button')).toBeInViewport()
  const height = await last.evaluate(card => card.getBoundingClientRect().height)
  expect(height).toBeGreaterThan(100)
  await page.getByPlaceholder('Filter plugins with text, pinyin, and is:/type:/has: rules').fill('NEKO Live')
  await expect(page.locator('.plugin-card')).toHaveCount(1)
  await expect(page.getByTestId('plugin-details-button')).toHaveText('NEKO Live')
  expect(await page.locator('.plugin-card').evaluate(card => card.scrollHeight <= card.clientHeight + 1)).toBe(true)
})
