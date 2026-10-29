import { expect, test, type Page } from '@playwright/test'

export const PREVIEW_ORIGIN = 'http://127.0.0.1:4173'

export const ADAPTER_ID = 'adapter#appearance'
export const ADAPTER_NAME = 'Boundary adapter'
export const adapter = {
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

export async function isolateAppearanceApis(page: Page) {
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

// The only writer of the stored locale: init scripts run in no guaranteed
// order, so a test needing another locale overrides this option instead.
export const appearanceTest = test.extend<{ appLocale: string; isolateNetwork: void }>({
  appLocale: ['en-US', { option: true }],
  isolateNetwork: [
    async ({ page, appLocale }, use) => {
      const requests = await isolateAppearanceApis(page)
      await page.addInitScript(locale => {
        window.localStorage.setItem('locale', locale)
        window.localStorage.setItem('neko-dark-mode', 'false')
      }, appLocale)
      await use()
      expect(requests, 'all HTTP requests must use explicit fixtures').toEqual([])
    },
    { auto: true },
  ],
})

// Exercise the production card/grid with short-only and mixed-content rows.
export async function installCardLayoutFixture(page: Page, count = 8) {
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

export function collectVueResolutionWarnings(page: Page) {
  const warnings: string[] = []
  page.on('console', (message) => {
    if (/Failed to resolve (component|directive)/.test(message.text())) {
      warnings.push(message.text())
    }
  })
  return warnings
}

export async function stubCorePluginManagerApis(page: Page) {
  await page.route('**/security/csrf-token', (route) =>
    route.fulfill({ json: { csrf_token: 'e2e-csrf-token' } })
  )
  await page.route('**/health', (route) => route.fulfill({ json: { status: 'ok' } }))
  await page.route('**/plugins?*', (route) => route.fulfill({ json: { plugins: [], message: '' } }))
  await page.route('**/plugin/status', (route) => route.fulfill({ json: { plugins: {} } }))
  await page.route('**/server/info', (route) =>
    route.fulfill({
      json: { sdk_version: 'test', plugins_count: 0, time: '2026-08-21T00:00:00Z' },
    })
  )
  await page.route('**/plugin/metrics', (route) =>
    route.fulfill({
      json: {
        global: {
          total_cpu_percent: 0,
          total_memory_percent: 0,
          total_memory_mb: 0,
          total_threads: 0,
          active_plugins: 0,
        },
      },
    })
  )
}

// Resolves the rendered text and background colours (including color-mix and
// translucent ancestors) in the browser, so default Element Plus styles cannot
// silently override intended contrast. Returns each selector whose first match
// falls below `minimum`; a selector with no match is reported as a failure.
export function contrastFailures(page: Page, selectors: string[], minimum = 4.5) {
  return page.evaluate(
    ({ selectors, minimum }) => {
      const context = document.createElement('canvas').getContext('2d', {
        willReadFrequently: true,
      })!
      const rgba = (value: string) => {
        context.clearRect(0, 0, 1, 1)
        context.fillStyle = value
        context.fillRect(0, 0, 1, 1)
        return Array.from(context.getImageData(0, 0, 1, 1).data)
      }
      const luminance = (rgb: number[]) =>
        rgb
          .slice(0, 3)
          .map((value) => {
            const v = value / 255
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
          })
          .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index]!, 0)
      const background = (element: Element | null): number[] => {
        if (!element) return [0, 0, 0]
        const color = rgba(getComputedStyle(element).backgroundColor)
        const alpha = color[3]! / 255
        if (alpha === 1) return color.slice(0, 3)
        const parent = background(element.parentElement)
        return color
          .slice(0, 3)
          .map((value, index) => value * alpha + parent[index]! * (1 - alpha))
      }
      return selectors.flatMap((selector) => {
        const element = document.querySelector(selector)
        if (!element) return [{ selector, foreground: '', background: [], ratio: 0 }]
        const foreground = getComputedStyle(element).color
        const bg = background(element)
        const fg = luminance(rgba(foreground))
        const bl = luminance(bg)
        const ratio = (Math.max(fg, bl) + 0.05) / (Math.min(fg, bl) + 0.05)
        return ratio < minimum ? [{ selector, foreground, background: bg, ratio }] : []
      })
    },
    { selectors, minimum }
  )
}
