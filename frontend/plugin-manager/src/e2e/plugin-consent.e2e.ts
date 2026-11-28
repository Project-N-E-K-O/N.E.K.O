import { expect, test, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

type Options = {
  analysisStatus?: string
  running?: boolean
  permissions?: Record<string, { declared: string | null; findings: Array<{ file: string; line: number }> }>
  blindSpots?: Array<{ type: string; file: string | null; line: number | null }>
  /** The pending list's entry for the plugin: never decided, or more after an update. */
  waiting?: 'to_confirm' | 'upgraded' | null
  /** The pending list cannot be read. */
  pendingFails?: boolean
  /** An earlier plugin-wide denial. */
  denied?: boolean
}

async function fixture(page: Page, options: Options = {}) {
  const analysisStatus = options.analysisStatus ?? 'completed'
  let running = options.running ?? false
  const permissions = options.permissions ?? {}
  const blindSpots = options.blindSpots ?? []
  let denied = options.denied ?? false
  const states: Record<string, 'granted' | 'denied' | 'undecided'> = {}
  const calls = { decided: [] as Array<[string, string, string]>, started: 0, revoked: 0 }
  const record = () => ({
    plugin_id: 'consent_demo',
    version: '1.0.0',
    decision: denied ? 'denied' : null,
    decided_at: null,
    state: 'clear',
    analysis: {
      status: analysisStatus,
      version: '1.0.0',
      analyzed_at: '2026-11-10T09:00:00Z',
      capabilities: [],
      evidence: [],
      warnings: analysisStatus === 'failed' ? [{ code: 'analysis_failed' }] : [],
      permissions,
      blind_spots: blindSpots,
    },
  })
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (
      url.pathname.startsWith('/ui/') &&
      ['document', 'script', 'stylesheet', 'image', 'font'].includes(request.resourceType())
    ) {
      await route.continue()
    } else await route.fulfill({ json: {} })
  })
  await page.routeWebSocket('**/*', () => {})
  await page.addInitScript(() => {
    localStorage.setItem('locale', 'en-US')
    localStorage.setItem('neko-dark-mode', 'false')
  })
  await stubCorePluginManagerApis(page)
  const plugin = () => ({
    id: 'consent_demo',
    name: 'Conversation reminder',
    version: '1.0.0',
    description: 'A consent example.',
    type: 'plugin',
    status: running ? 'running' : 'stopped',
    entries: [],
    consent: {
      state: 'confirm',
      decision: null,
      upgraded: false,
      blind_spots: blindSpots.length,
    },
  })
  await page.route('**/plugins?*', (route) =>
    route.fulfill({ json: { plugins: [plugin()], message: '' } })
  )
  await page.route('**/plugins/consent_demo?*', async (route) => {
    if (route.request().resourceType() === 'document') await route.fallback()
    else await route.fulfill({ json: { plugin: plugin() } })
  })
  await page.route('**/plugin/consent_demo/surfaces*', (route) =>
    route.fulfill({ json: { surfaces: [], warnings: [] } })
  )
  await page.route('**/plugin/status*', (route) =>
    route.fulfill({ json: { plugins: { consent_demo: running ? 'running' : 'stopped' } } })
  )
  await page.route('**/plugins/refresh', (route) =>
    route.fulfill({
      json: {
        success: true,
        added: [],
        updated: [],
        removed: [],
        removed_running: [],
        unchanged: ['consent_demo'],
        failed: [],
        scanned_count: 1,
      },
    })
  )
  await page.route('**/plugin-consent/plugins/consent_demo', async (route) => {
    if (route.request().method() === 'DELETE') {
      denied = false
      calls.revoked++
    }
    await route.fulfill({ json: record() })
  })
  const rows = () =>
    Object.entries(permissions).map(([permission, item]) => ({
      permission,
      tier: 'high',
      reconcile: item.declared ? (item.findings.length ? 'matched' : 'unscanned') : 'undeclared',
      declared: item.declared
        ? { level: item.declared, reason: 'to fetch the weather', background: false, background_reason: null, hosts: [] }
        : null,
      scan: item.findings.length
        ? {
            files: new Set(item.findings.map((f) => f.file)).size,
            sites: item.findings.length,
            basis: 'direct',
            possible_only: false,
          }
        : null,
      state: states[permission] ?? 'undecided',
      condition: states[permission] === 'granted' ? 'in_use' : null,
      condition_basis: 'plugin_reported',
      anytime_allowed: true,
      legacy_any: false,
      enforced: true,
      usage_tracked: true,
      last_used: null,
      unused_30d: null,
    }))
  const panel = () => {
    const current = rows()
    const counted = blindSpots.filter((spot) => spot.type !== 'not_analyzed' && spot.file)
    const files = [...new Set(counted.map((spot) => spot.file as string))].map((file) => {
      const types: Record<string, number> = {}
      for (const spot of counted.filter((item) => item.file === file)) types[spot.type] = (types[spot.type] ?? 0) + 1
      return { file, count: Object.values(types).reduce((a, b) => a + b, 0), types, high_risk: false }
    })
    return {
      plugin_id: 'consent_demo',
      version: '1.0.0',
      source: { channel: 'local', fingerprint8: null, confirmed: true },
      grants_reset: null,
      checks_disabled: false,
      manifest: 'read',
      analysis: {
        status: analysisStatus,
        analyzer_version: '1',
        analyzed_at: '2026-11-10T09:00:00Z',
        declarations: 'declared',
      },
      counts: {
        total: current.length,
        granted: current.filter((row) => row.state === 'granted').length,
        denied: current.filter((row) => row.state === 'denied').length,
        undecided: current.filter((row) => row.state === 'undecided').length,
        undeclared_found: current.filter((row) => row.reconcile === 'undeclared').length,
      },
      rows: current,
      tiers: ['high', 'medium', 'low'].map((tier) => ({
        tier,
        count: current.filter((row) => row.tier === tier).length,
        collapsed: false,
      })),
      blind_spots: {
        files,
        counted: files.reduce((sum, file) => sum + file.count, 0),
        not_analyzed_files: blindSpots.filter((spot) => spot.type === 'not_analyzed').length,
      },
      developer: { findings: {}, blind_spots: [], not_analyzed: [], manifest_permissions: null },
    }
  }
  await page.route('**/plugin-permissions/plugins/consent_demo/panel', (route) =>
    route.fulfill({ json: panel() })
  )
  await page.route('**/plugin-permissions/plugins/consent_demo/permissions/*', async (route) => {
    const request = route.request()
    const permission = decodeURIComponent(new URL(request.url()).pathname.split('/').pop()!)
    const body = request.postDataJSON() as { state: 'granted' | 'denied'; condition: string }
    states[permission] = body.state
    calls.decided.push([permission, body.state, body.condition])
    await route.fulfill({ json: panel() })
  })
  await page.route('**/plugin-permissions/pending', async (route) => {
    if (options.pendingFails) return route.fulfill({ status: 500, json: { detail: 'down' } })
    const items = rows().filter((row) => row.state === 'undecided').map((row) => row.permission)
    const entry = options.waiting && items.length ? [{ plugin_id: 'consent_demo', items }] : []
    await route.fulfill({
      json: {
        to_confirm: options.waiting === 'to_confirm' ? entry : [],
        upgraded: options.waiting === 'upgraded' ? entry : [],
        inferred: [],
        first_use: [],
        incomplete: [],
      },
    })
  })
  await page.route('**/plugin/consent_demo/start', async (route) => {
    running = true
    calls.started++
    await route.fulfill({ json: { success: true, plugin_id: 'consent_demo' } })
  })
  await page.route('**/plugin/consent_demo/stop', async (route) => {
    running = false
    await route.fulfill({ json: { success: true } })
  })
  return calls
}

const startButton = (page: Page) =>
  page
    .locator('[data-yui-guide-id="plugin-detail-actions"]')
    .getByRole('button', { name: 'Start', exact: true })

const NET = 'system.network:connect'
const ENV = 'system.env:read'
const WAITING = {
  [NET]: { declared: 'require', findings: [{ file: 'main.py', line: 12 }] },
  [ENV]: { declared: null, findings: [{ file: 'main.py', line: 20 }] },
}

test('a plugin with nothing waiting starts without a dialog', async ({ page }) => {
  const calls = await fixture(page)
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo?tab=permissions`)
  const permissions = page.locator('.perm-tab')
  await expect(permissions).toContainText('No files the scan cannot see into')
  await expect(permissions.getByRole('button', { name: 'Decide item by item' })).toHaveCount(0)
  await startButton(page).click()
  await expect.poll(() => calls.started).toBe(1)
  await expect(page.locator('[data-test="permission-dialog"]')).toHaveCount(0)
})

test('used permissions and blind spots are disclosed instead of nothing found', async ({
  page,
}, testInfo) => {
  await fixture(page, {
    permissions: {
      [NET]: { declared: null, findings: [{ file: 'main.py', line: 12 }] },
    },
    blindSpots: [
      { type: 'dynamic_import', file: 'main.py', line: 30 },
      { type: 'not_analyzed', file: 'vendor/big.py', line: null },
    ],
  })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo?tab=permissions`)
  const permissions = page.locator('.perm-tab')
  await expect(permissions.locator(`[data-test="row-${NET}"]`)).toContainText('Connect to the internet')
  await expect(permissions).toContainText('The plugin does not say why it needs this')
  await expect(permissions).toContainText('1 file(s) the scan cannot see into')
  await expect(permissions).toContainText('1 more test and interface file(s) are outside the scan')
  await page.screenshot({ path: testInfo.outputPath('blind-spots-detail.png'), fullPage: true })

  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  await expect(page.locator('[data-testid="plugin-blind-spots"]').first()).toHaveText(
    'Partly unconfirmable',
  )
  await expect(page.locator('[data-testid="plugin-permission-review"]')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('blind-spots-list.png'), fullPage: true })
})

test('before the first start: answers are saved one by one and Start is always there', async ({
  page,
}, testInfo) => {
  const calls = await fixture(page, { permissions: WAITING, waiting: 'to_confirm' })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo`)
  await startButton(page).click()
  const dialog = page.getByRole('dialog', { name: 'Before starting “Conversation reminder”' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('Connect to the internet')
  await expect(dialog).toContainText('Not declared, found by the scan')
  await expect(page.locator('.el-overlay').last()).toHaveCSS('opacity', '1')
  await page.screenshot({ path: testInfo.outputPath('first-start-dialog.png'), fullPage: true })
  await dialog.locator(`[data-test="dialog-allow-${NET}"]`).click()
  await expect.poll(() => calls.decided).toEqual([[NET, 'granted', 'anytime']])
  const start = dialog.getByRole('button', { name: 'Start (1 not decided yet)' })
  await expect(start).toBeEnabled()
  expect(calls.started).toBe(0)
  await start.click()
  await expect(dialog).toBeHidden()
  await expect.poll(() => calls.started).toBe(1)
})

test('"Don\'t start" sends no start', async ({ page }) => {
  const calls = await fixture(page, { permissions: WAITING, waiting: 'to_confirm' })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo`)
  await startButton(page).click()
  await page.getByRole('button', { name: 'Don\'t start', exact: true }).click()
  await expect(page.locator('[data-test="permission-dialog"]')).toHaveCount(0)
  expect(calls.started).toBe(0)
  expect(calls.decided).toEqual([])
})

test('after an update: only the added items, then start', async ({ page }) => {
  const calls = await fixture(page, { permissions: WAITING, waiting: 'upgraded' })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo`)
  await startButton(page).click()
  const dialog = page.getByRole('dialog', { name: '“Conversation reminder” updated to 1.0.0' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Not now', exact: true }).click()
  expect(calls.started).toBe(0)
  await startButton(page).click()
  await dialog.getByRole('button', { name: /^Start/ }).click()
  await expect.poll(() => calls.started).toBe(1)
})

test('a pending list that cannot be read starts the plugin', async ({ page }) => {
  const calls = await fixture(page, { permissions: WAITING, waiting: 'to_confirm', pendingFails: true })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo`)
  await startButton(page).click()
  await expect.poll(() => calls.started).toBe(1)
  await expect(page.locator('[data-test="permission-dialog"]')).toHaveCount(0)
})

test('"Decide item by item" on the Permissions tab opens the dialog without starting', async ({
  page,
}, testInfo) => {
  const calls = await fixture(page, { permissions: WAITING, waiting: 'to_confirm' })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo?tab=permissions`)
  await page.locator('.perm-tab').getByRole('button', { name: 'Decide item by item' }).click()
  const dialog = page.getByRole('dialog', { name: 'Permissions “Conversation reminder” needs' })
  await expect(dialog).toBeVisible()
  await dialog.locator(`[data-test="dialog-refuse-${ENV}"]`).click()
  await expect.poll(() => calls.decided).toEqual([[ENV, 'denied', 'in_use']])
  await page.screenshot({ path: testInfo.outputPath('decide-items-dialog.png'), fullPage: true })
  await dialog.getByRole('button', { name: /^Done/ }).click()
  await expect(dialog).toBeHidden()
  expect(calls.started).toBe(0)
})

test('an earlier plugin-wide denial can be withdrawn on the Permissions tab', async ({ page }) => {
  const calls = await fixture(page, { denied: true })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo?tab=permissions`)
  const line = page.locator('[data-test="legacy-consent"]')
  await expect(line).toContainText('Denied')
  await line.getByRole('button', { name: 'Withdraw decision' }).click()
  await expect.poll(() => calls.revoked).toBe(1)
  await expect(line).toHaveCount(0)
})
