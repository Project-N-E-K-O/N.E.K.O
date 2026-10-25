import { expect, test } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

const plugin = {
  id: 'runtime_snapshot_demo',
  name: 'Runtime snapshot demo',
  description: 'Metadata intentionally retains an older stopped status.',
  version: '1.0.0',
  type: 'plugin',
  status: 'stopped',
  runtime_enabled: true,
  runtime_auto_start: true,
  entries: [],
  list_actions: [],
}

test.beforeEach(async ({ page }) => {
  // Keep this a production-SPA test with isolated APIs; no live backend calls.
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (
      url.origin === PREVIEW_ORIGIN &&
      url.pathname.startsWith('/ui/') &&
      ['document', 'script', 'stylesheet', 'image', 'font'].includes(request.resourceType())
    ) {
      await route.continue()
    } else {
      await route.abort('blockedbyclient')
    }
  })
  await page.routeWebSocket('**/*', () => {})
  await stubCorePluginManagerApis(page)
  await page.addInitScript(() => {
    localStorage.setItem('locale', 'en-US')
    localStorage.setItem('neko-dark-mode', 'false')
  })
})

test('nested runtime snapshots update list, detail and lifecycle buttons despite stale metadata', async ({
  page,
}) => {
  await page.route('**/plugins?*', (route) =>
    route.fulfill({ json: { plugins: [plugin], message: '' } })
  )
  await page.route(`**/plugins/${plugin.id}?*`, (route) => route.fulfill({ json: plugin }))
  await page.route('**/plugins/refresh', (route) =>
    route.fulfill({
      json: {
        success: true,
        added: [],
        updated: [],
        removed: [],
        removed_running: [],
        unchanged: [plugin.id],
        failed: [],
        scanned_count: 1,
      },
    })
  )
  await page.route(`**/plugin/${plugin.id}/surfaces*`, (route) =>
    route.fulfill({ json: { surfaces: [], warnings: [] } })
  )
  await page.route('**/market/status', (route) => route.fulfill({ json: { market_url: null } }))
  let runtimeStatus = 'stopped'
  let runtimeSource = 'main_process_synthetic'
  await page.route('**/plugin/status*', (route) => {
    const status = { plugin_id: plugin.id, source: runtimeSource, status: { status: runtimeStatus } }
    const single = new URL(route.request().url()).searchParams.has('plugin_id')
    return route.fulfill({ json: single ? status : { plugins: { [plugin.id]: status } } })
  })

  const initialStatus = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/plugin/status'
  )
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  await (await initialStatus).finished()
  const card = page
    .locator('.plugin-card')
    .filter({ has: page.getByRole('button', { name: plugin.name, exact: true }) })
  await expect(card.locator('.status-stopped')).toBeVisible()

  runtimeStatus = 'running'
  await page
    .locator('.header__actions')
    .getByRole('button', { name: 'Refresh', exact: true })
    .click()
  await expect(card.locator('.status-running')).toBeVisible()
  await expect(card.locator('.status-stopped')).toHaveCount(0)
  await card.getByRole('button', { name: plugin.name, exact: true }).click()
  await expect(page).toHaveURL(`${PREVIEW_ORIGIN}/ui/plugins/${plugin.id}`)
  await expect(page.locator('.plugin-detail .status-running')).toBeVisible()
  await expect(
    page.locator('.plugin-actions').getByRole('button', { name: 'Stop', exact: true })
  ).toBeVisible()
  await expect(
    page.locator('.plugin-actions').getByRole('button', { name: 'Start', exact: true })
  ).toHaveCount(0)

  runtimeStatus = 'stopped'
  await page
    .locator('.header__actions')
    .getByRole('button', { name: 'Refresh', exact: true })
    .click()
  await expect(page.locator('.plugin-detail .status-stopped')).toBeVisible()
  await expect(
    page.locator('.plugin-actions').getByRole('button', { name: 'Start', exact: true })
  ).toBeVisible()
  await expect(
    page.locator('.plugin-actions').getByRole('button', { name: 'Stop', exact: true })
  ).toHaveCount(0)
  // A stopped process may leave a cached business report behind. It must not
  // restore Stop/require-running controls or publish arbitrary badge values.
  for (const reported of ['running', 'idle']) {
    runtimeSource = 'child_process'
    runtimeStatus = reported
    await page.locator('.header__actions').getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect(page.locator('.header__actions').getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()
    await expect(page.locator('.plugin-detail .status-stopped')).toBeVisible()
    await expect(page.locator('.plugin-actions').getByRole('button', { name: 'Start', exact: true })).toBeVisible()
  }
  await page.locator('[data-yui-guide-id="plugin-detail-back"]').click()
  await expect(card.locator('.status-stopped')).toBeVisible()
})

test('a populated market drawer revalidates installed actions after a registry change while hidden', async ({
  page,
}) => {
  let installed = true
  let projectionRequests = 0
  let catalogRequests = 0
  await page.route('**/plugins?*', (route) =>
    route.fulfill({ json: { plugins: installed ? [plugin] : [], message: '' } })
  )
  await page.route('**/plugins/refresh', (route) =>
    route.fulfill({
      json: {
        success: true,
        added: [],
        updated: [],
        removed: installed ? [] : [plugin.id],
        removed_running: [],
        unchanged: installed ? [plugin.id] : [],
        failed: [],
        scanned_count: installed ? 1 : 0,
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
    route.fulfill({ json: { bridge_token: 'state-test-token' } })
  )
  await page.route('**/market/oauth/status', (route) =>
    route.fulfill({ json: { authenticated: false, auth_state: 'disconnected' } })
  )
  await page.route('**/market/installed?*', (route) => {
    projectionRequests += 1
    return route.fulfill({
      json: {
        installed: installed
          ? [
              {
                plugin_id: plugin.id,
                effective_source: 'market',
                effective_version: '1.0.0',
                market_installed: true,
                latest_install_source: {
                  plugin_market_id: '15',
                  channel: 'stable',
                  version: '1.0.0',
                  package_url: 'https://example.test/demo.zip',
                },
              },
            ]
          : [],
      },
    })
  })
  await page.route('**/market/catalog/api/v1/plugins?*', (route) => {
    catalogRequests += 1
    return route.fulfill({
      json: {
        items: [
          {
            id: 15,
            slug: plugin.id,
            name: plugin.name,
            description: '',
            author_name: 'Test',
            latest_version: {
              version: '2.0.0',
              channel: 'stable',
              package_url: 'https://example.test/demo.zip',
              package_sha256: 'abc',
              payload_hash: null,
              created_at: '2026-10-21T00:00:00Z',
            },
            created_at: '2026-10-21T00:00:00Z',
            updated_at: '2026-10-21T00:00:00Z',
          },
        ],
        total: 1,
        page: 1,
        page_size: 8,
      },
    })
  })
  await page.route('**/market/catalog/api/v1/plugins/15/versions?*', (route) =>
    route.fulfill({ json: { items: [] } })
  )

  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  const toggle = page.locator('[data-yui-guide-id="plugin-list-market-toggle"]')
  await toggle.click()
  const card = page.locator('.market-plugin-card')
  await expect(card.getByRole('button', { name: /Upgrade.*2\.0\.0/ })).toBeVisible()
  // A read refresh with unchanged install provenance must not requery the
  // bridge merely because the already-populated market is visible.
  const refresh = page.locator('[data-yui-guide-id="plugin-list-refresh"]')
  await refresh.click()
  await expect(refresh).toBeEnabled()
  expect(projectionRequests).toBe(1)
  await toggle.click()
  await expect(card).not.toBeVisible()
  // Simulate removal outside this drawer, then reconcile through the real list
  // refresh control. Reopening must not depend on re-fetching the catalog.
  installed = false
  await page.locator('[data-yui-guide-id="plugin-list-refresh"]').click()
  await expect(page.locator('[data-yui-guide-id="plugin-list-main"] .plugin-card')).toHaveCount(0)
  expect(projectionRequests).toBe(1)
  await toggle.click()
  await expect(card.getByRole('button', { name: 'Install', exact: true })).toBeEnabled()
  await expect(card.getByRole('button', { name: /Upgrade.*2\.0\.0/ })).toHaveCount(0)
  expect(projectionRequests).toBe(2)
  expect(catalogRequests).toBe(1)
})
