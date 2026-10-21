import { expect, test as base, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import type { PluginMeta } from '../types/api'

const ORIGIN = 'http://127.0.0.1:4174'
const FIXED_TIME = '2026-10-21T04:00:00.000Z'

const source = (channel: 'builtin' | 'manual' | 'imported' | 'market', version = '1.2.0', marketId = '101'): PluginMeta['install_source'] => ({
  source: channel,
  reason: null,
  installed_at: FIXED_TIME,
  source_detail: channel === 'market' ? {
    plugin_market_id: marketId,
    version,
    channel: 'stable',
    package_url: `https://market.example.test/download/${marketId}`,
    previous_version: null,
  } : null,
})

// Deliberately varied title lengths, statuses and action combinations cover
// card alignment without depending on a developer's installed plugins.
const pluginSamples: PluginMeta[] = [
  {
    id: 'companion_games', name: 'Companion Games', version: '1.2.0',
    description: 'Play a quick board game with your companion, from matching pairs to classic strategy.',
    status: 'running', runtime_auto_start: true, entry_count: 18,
    install_source: source('imported'),
    list_actions: [{ id: 'open_ui', kind: 'ui', label: 'Open Interface', target: 'main' }],
  },
  {
    id: 'neko_live', name: 'NEKO Live', version: '0.2.9',
    description: 'Bring live chat, captions and audience interactions into one place.',
    status: 'stopped', runtime_auto_start: true, entry_count: 34,
    install_source: source('market', '0.2.9', '102'),
  },
  {
    id: 'voice_studio', name: 'Voice Studio', version: '1.2.0',
    description: 'Create songs and voice performances using your configured local voice models.',
    status: 'running', runtime_auto_start: true, entry_count: 8,
    install_source: source('market'),
    list_actions: [{ id: 'open_ui', kind: 'ui', label: 'Open Interface', target: 'main' }],
  },
  {
    id: 'application_launcher', name: 'Application Launcher', version: '1.3.0',
    description: 'Keep shortcuts to applications and useful files ready for a quick launch.',
    status: 'stopped', runtime_auto_start: false, entry_count: 4,
    install_source: source('builtin'),
    list_actions: [{ id: 'open_ui', kind: 'ui', label: 'Open Interface', target: 'main' }],
  },
  {
    id: 'daily_assistant', name: 'Daily Assistant', version: '0.3.0',
    description: 'Check the weather, plan a small reminder and keep track of everyday tasks.',
    status: 'running', runtime_auto_start: true, entry_count: 19,
    install_source: source('builtin'),
  },
  {
    id: 'reminders', name: 'Reminders', version: '1.0.0',
    description: 'Schedule a reminder and let your companion help you remember the next step.',
    status: 'stopped', runtime_auto_start: false, entry_count: 5,
    install_source: source('manual'),
  },
  {
    id: 'web_search', name: 'Web Search', version: '0.1.6',
    description: 'Look up useful information using the search providers you have configured.',
    status: 'crashed', runtime_auto_start: true, entry_count: 2,
    install_source: source('builtin'),
  },
  {
    id: 'mcp_adapter', name: 'MCP Adapter', type: 'adapter', version: '1.0.0',
    description: 'Connect external tools and services to your companion.',
    status: 'running', runtime_auto_start: true, entry_count: 6,
    install_source: source('builtin'),
  },
]
export const visualPlugins: PluginMeta[] = pluginSamples.map((plugin) => ({ type: 'plugin', runtime_enabled: true, entries: [], list_actions: [], ...plugin }))

const fontCss = `
  @font-face {
    font-family: 'Neko Visual Sans'; font-style: normal; font-weight: 400;
    src: url('/__visual__/LiberationSans-Regular.ttf') format('truetype'); font-display: block;
  }
  @font-face {
    font-family: 'Neko Visual Sans'; font-style: normal; font-weight: 700;
    src: url('/__visual__/LiberationSans-Bold.ttf') format('truetype'); font-display: block;
  }
  /* Keep the capture font independent from the CI host. This CSS exists only
     in Playwright; product styles and bundled production assets are unchanged. */
  *, *::before, *::after { font-family: 'Neko Visual Sans', sans-serif !important; }
  *, *::before, *::after { animation: none !important; transition: none !important; }
`

async function isolateNetwork(page: Page) {
  const unexpectedRequests: string[] = []
  await page.routeWebSocket('**/*', () => {})
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (url.origin !== ORIGIN) {
      unexpectedRequests.push(`${request.method()} ${url.href}`)
      await route.abort('blockedbyclient')
      return
    }
    if (/^\/__visual__\/LiberationSans-(Regular|Bold)\.ttf$/.test(path)) {
      await route.fulfill({
        contentType: 'font/ttf',
        path: fileURLToPath(new URL(`./fonts/${path.split('/').pop()}`, import.meta.url)),
      })
      return
    }
    if (path.startsWith('/ui/') && ['document', 'script', 'stylesheet', 'image', 'font'].includes(request.resourceType())) {
      await route.continue()
      return
    }
    let payload: unknown
    if (path === '/health') payload = { status: 'ok' }
    else if (path === '/security/csrf-token') payload = { csrf_token: 'visual-test-token' }
    else if (path === '/plugins') payload = { plugins: visualPlugins, message: '' }
    else if (path === '/plugins/refresh') payload = {
      success: true, added: [], updated: [], removed: [], removed_running: [],
      unchanged: visualPlugins.map((plugin) => plugin.id), failed: [], scanned_count: visualPlugins.length,
    }
    else if (path === '/plugin/status') payload = { plugins: Object.fromEntries(visualPlugins.map((plugin) => [plugin.id, plugin.status])) }
    else if (path === '/server/info') payload = { sdk_version: '0.1.0', plugins_count: visualPlugins.length, time: FIXED_TIME }
    else if (path === '/plugin/metrics') payload = {
      global: { total_cpu_percent: 12.4, total_memory_percent: 18, total_memory_mb: 1228.8, total_threads: 31, active_plugins: 4 },
    }
    else if (path === '/market/status') payload = { market_url: 'https://market.example.test', market_web_url: 'https://market.example.test' }
    else if (path === '/market/bridge-token') payload = { bridge_token: 'visual-test-token' }
    else if (path === '/market/oauth/status') payload = { authenticated: false, auth_state: 'disconnected' }
    else if (path === '/market/installed') payload = { items: [] }
    else if (path === '/market/catalog/api/v1/plugins/latest-versions') payload = { items: [
      { plugin_id: 101, version: '1.2.0', channel: 'stable' },
      { plugin_id: 102, version: '0.2.9', channel: 'stable' },
    ] }
    else if (/^\/market\/catalog\/api\/v1\/plugins\/101\/versions$/.test(path)) payload = { items: [] }
    else {
      unexpectedRequests.push(`${request.method()} ${path}`)
      await route.abort('blockedbyclient')
      return
    }
    await route.fulfill({ json: payload })
  })
  return unexpectedRequests
}

export const test = base.extend<{ fixtureNetwork: void }>({
  fixtureNetwork: [async ({ page }, use) => {
    const unexpected = await isolateNetwork(page)
    await page.clock.setFixedTime(new Date(FIXED_TIME))
    await use()
    expect(unexpected, 'visual captures must use explicit fixtures for every API request').toEqual([])
  }, { auto: true }],
})

export async function openVisualPage(page: Page, path: '/' | '/plugins', dark: boolean) {
  await page.addInitScript((theme) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('locale', 'en-US')
    localStorage.setItem('neko-dark-mode', String(theme))
  }, dark)
  await page.goto(`/ui${path}`, { waitUntil: 'load' })
  await expect(page.locator('.app-root')).toBeVisible()
  await page.addStyleTag({ content: fontCss })
  await page.evaluate(async () => {
    await document.fonts.load('400 14px "Neko Visual Sans"')
    await document.fonts.load('700 14px "Neko Visual Sans"')
    await document.fonts.ready
  })
  await expect(page.locator('html')).toHaveClass(dark ? /dark/ : /^(?!.*\bdark\b).*$/)
  if (path === '/') {
    await expect(page.locator('.stat-card--total .stat-card__value')).toHaveText('8')
    await expect(page.locator('.gauge__value').first()).toHaveText('12.4%')
    await expect(page.locator('.server-info__badge')).toHaveText('0.1.0')
  } else {
    await expect(page.locator('.plugin-card')).toHaveCount(visualPlugins.length)
    await expect(page.locator('.plugin-name').first()).toBeVisible()
    await expect(page.locator('[data-yui-guide-id="plugin-list-refresh"]')).not.toBeDisabled()
  }
  await page.mouse.move(0, 0)
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
}

export { expect }
