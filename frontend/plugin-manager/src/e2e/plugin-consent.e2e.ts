import { expect, test, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

type Options = {
  analysisStatus?: string
  capabilities?: string[]
  running?: boolean
}

const ANALYZING = new Set(['queued', 'running', 'unavailable'])

async function fixture(page: Page, options: Options = {}) {
  const analysisStatus = options.analysisStatus ?? 'completed'
  const capabilities = options.capabilities ?? ['conversations.read', 'memory.read', 'messages.push']
  let running = options.running ?? false
  let decision: 'approved' | 'denied' | null = null
  const approvedCapabilities: string[] = []
  const calls = { decided: [] as boolean[], started: 0 }
  const unconfirmed = () => capabilities.filter((cap) => !approvedCapabilities.includes(cap))
  const state = () =>
    ANALYZING.has(analysisStatus)
      ? 'analyzing'
      : decision === 'denied' ||
          unconfirmed().length ||
          (decision !== 'approved' && ['partial', 'failed'].includes(analysisStatus))
        ? 'confirm'
        : 'clear'
  const consent = () => ({
    plugin_id: 'consent_demo',
    version: '1.0.0',
    decision,
    decided_at: decision ? '2026-11-10T10:00:00Z' : null,
    approved_capabilities: approvedCapabilities,
    unconfirmed: unconfirmed(),
    state: state(),
    upgraded: decision === 'approved' && unconfirmed().length > 0,
    analysis: {
      status: analysisStatus,
      version: '1.0.0',
      analyzed_at: '2026-11-10T09:00:00Z',
      capabilities,
      evidence: capabilities.length
        ? [
            {
              capability: capabilities[0],
              method: 'self.bus.conversations_v2.watch',
              file: 'main.py',
              line: 18,
            },
          ]
        : [],
      warnings: analysisStatus === 'failed' ? [{ code: 'analysis_failed' }] : [],
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
    consent: { state: state(), decision, upgraded: decision === 'approved' && unconfirmed().length > 0 },
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
  await page.route('**/plugin-consent/notices*', (route) =>
    route.fulfill({
      json:
        running && state() === 'confirm' && decision !== 'denied'
          ? [
              {
                plugin_id: 'consent_demo',
                name: 'Conversation reminder',
                version: '1.0.0',
                unconfirmed: unconfirmed(),
                upgraded: false,
                analysis_status: analysisStatus,
              },
            ]
          : [],
    })
  )
  await page.route('**/plugin-consent/plugins/consent_demo', async (route) => {
    const request = route.request()
    if (request.method() === 'POST') {
      decision = request.postDataJSON().approved ? 'approved' : 'denied'
      if (decision === 'approved') approvedCapabilities.push(...capabilities)
      calls.decided.push(decision === 'approved')
    }
    if (request.method() === 'DELETE') {
      decision = null
      approvedCapabilities.length = 0
    }
    await route.fulfill({ json: consent() })
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

test('clean analysis is disclosed in Permissions and starts without a decision', async ({ page }) => {
  const calls = await fixture(page, { capabilities: [] })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo?tab=permissions`)
  const permissions = page.locator('.plugin-permissions')
  await expect(permissions).toContainText('No calls that read conversations')
  await expect(permissions.getByRole('button', { name: 'Approve and start' })).toHaveCount(0)
  await startButton(page).click()
  await expect.poll(() => calls.started).toBe(1)
  expect(calls.decided).toEqual([])
  await expect(page.getByRole('dialog', { name: 'Confirm plugin capabilities' })).toHaveCount(0)
})

test('discloses capabilities, saves the decision once, and starts without asking again', async ({
  page,
}, testInfo) => {
  const calls = await fixture(page)
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo?tab=permissions`)
  await expect(page.getByRole('tab', { name: 'Permissions', exact: true })).toBeVisible()
  await expect(page.locator('.plugin-permissions')).toContainText('Read conversations')
  await page.screenshot({ path: testInfo.outputPath('permissions.png'), fullPage: true })
  await startButton(page).click()
  const dialog = page.getByRole('dialog', { name: 'Confirm plugin capabilities' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('Read memory')
  await expect(page.locator('.el-overlay').last()).toHaveCSS('opacity', '1')
  await expect(dialog).toHaveCSS('opacity', '1')
  await page.screenshot({ path: testInfo.outputPath('first-run-consent.png'), fullPage: true })
  await dialog.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(dialog).toBeHidden()
  await expect.poll(() => calls.started).toBe(1)
  await expect(page.locator('.plugin-permissions')).toContainText('Approved')
  const actions = page.locator('[data-yui-guide-id="plugin-detail-actions"]')
  await actions.getByRole('button', { name: 'Stop', exact: true }).click()
  await page
    .locator('.el-message-box')
    .getByRole('button', { name: /OK|Confirm/ })
    .click()
  await actions.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => calls.started).toBe(2)
  expect(calls.decided).toEqual([true])
  await expect(page.getByRole('dialog', { name: 'Confirm plugin capabilities' })).toHaveCount(0)
})

test('unfinished analysis warns once, then a second click starts directly', async ({ page }) => {
  const calls = await fixture(page, { analysisStatus: 'queued' })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo`)
  await startButton(page).click()
  await expect(page.locator('.el-message--warning').last()).toContainText(
    'Capability analysis is not finished',
  )
  expect(calls.started).toBe(0)
  expect(calls.decided).toEqual([])
  await startButton(page).click()
  await expect.poll(() => calls.started).toBe(1)
})

test('failed analysis asks for confirmation with an incomplete warning', async ({ page }) => {
  const calls = await fixture(page, { analysisStatus: 'failed' })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo`)
  await startButton(page).click()
  const dialog = page.getByRole('dialog', { name: 'Confirm plugin capabilities' })
  await expect(dialog).toContainText('analysis is incomplete')
  await expect(dialog.getByRole('button', { name: 'Approve', exact: true })).toBeEnabled()
  await dialog.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect.poll(() => calls.started).toBe(1)
})

test('cancelling the consent dialog does not start or record a decision', async ({ page }) => {
  const calls = await fixture(page)
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/consent_demo`)
  await startButton(page).click()
  await page
    .getByRole('dialog', { name: 'Confirm plugin capabilities' })
    .getByRole('button', { name: 'Cancel', exact: true })
    .click()
  await expect(page.getByRole('dialog', { name: 'Confirm plugin capabilities' })).toHaveCount(0)
  expect(calls).toEqual({ decided: [], started: 0 })
})

test('a running plugin with unconfirmed capabilities is listed in the notice', async ({
  page,
}) => {
  const calls = await fixture(page, { running: true })
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  const notification = page.locator('.el-notification')
  await expect(notification).toContainText('Plugin capabilities need confirmation')
  await expect(notification).toContainText('Conversation reminder')
  await expect(notification).toContainText('Read memory')
  await notification.getByRole('button', { name: 'Deny', exact: true }).click()
  await expect.poll(() => calls.decided).toEqual([false])
  await expect(notification).toHaveCount(0)
})
