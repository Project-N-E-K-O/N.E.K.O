import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

const client = readFileSync(fileURLToPath(new URL('../../../../static/plugin-ui-sdk/display.js', import.meta.url)), 'utf8')
const plugin = { id: 'display_demo', name: 'Display example', type: 'adapter', version: '1.0.0', status: 'running', entries: [] }
const source = `import { Page, Button, Input, Text, useState, useEffect } from '@neko/plugin-ui'
const documentToken = Math.random()
export default function Panel(props) {
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState('ready')
  const [mode, setMode] = useState('normal')
  useEffect(() => props.api.display.onStateChange(state => setMode(state.mode)), [])
  async function request(mode) { setStatus('pending'); setStatus((await props.api.display.requestMode(mode)).status) }
  return <Page><Input value={draft} onChange={setDraft} />
    <Button onClick={() => request('focus')}>Focus</Button>
    <Button onClick={() => request('fullscreen')}>Fullscreen</Button>
    <Button onClick={() => props.api.display.exit()}>Exit</Button>
    <p id="status">{status}</p><p id="mode">{mode}</p><p id="document-token">{String(documentToken)}</p></Page>
}`
const html = `<html><body><input id="draft"><button id="focus">Focus</button><button id="full">Fullscreen</button><button id="exit">Exit</button>
<p id="status">ready</p><p id="mode">normal</p><p id="document-token"></p><script src="/plugin-ui-sdk/display.js"></script><script>
document.getElementById('document-token').textContent = String(Math.random());
async function request(mode) { document.getElementById('status').textContent = 'pending'; document.getElementById('status').textContent = (await NekoPluginDisplay.requestMode(mode)).status; }
document.getElementById('focus').onclick = () => request('focus');
document.getElementById('full').onclick = () => request('fullscreen');
document.getElementById('exit').onclick = () => NekoPluginDisplay.exit();
NekoPluginDisplay.onStateChange(state => document.getElementById('mode').textContent = state.mode);
</script></body></html>`

async function fixture(page: Page, kind: 'static' | 'hosted-tsx' | 'legacy', detail = false, panelSource = source) {
  await page.addInitScript(() => localStorage.setItem('locale', 'en-US'))
  await page.routeWebSocket('**/*', () => {})
  await stubCorePluginManagerApis(page)
  await page.route(`${PREVIEW_ORIGIN}/plugins?*`, route => route.fulfill({ json: { plugins: [plugin] } }))
  await page.route(`${PREVIEW_ORIGIN}/plugins/display_demo*`, route => route.fulfill({ json: plugin }))
  await page.route('**/plugin/status', route => route.fulfill({ json: { plugins: { display_demo: 'running' } } }))
  await page.route('**/plugin/display_demo/surfaces*', route => route.fulfill({ json: {
    surfaces: kind === 'legacy' ? [] : [{ id: 'main', kind: 'panel', mode: kind, entry: kind === 'static' ? 'static/index.html' : 'ui/panel.tsx', available: true, url: kind === 'static' ? '/plugin/display_demo/ui/' : undefined }], warnings: [],
  } }))
  await page.route('**/plugin/display_demo/ui-info', route => route.fulfill({ json: { has_ui: true } }))
  await page.route('**/plugin/display_demo/ui/**', route => route.fulfill({ contentType: 'text/html', body: html }))
  await page.route('**/plugin-ui-sdk/display.js', route => route.fulfill({ contentType: 'application/javascript', body: client }))
  await page.route('**/plugin/display_demo/hosted-ui/source*', route => route.fulfill({ json: { source: panelSource } }))
  await page.route('**/plugin/display_demo/hosted-ui/context*', route => route.fulfill({ json: {
    plugin, state: {}, actions: [], entries: [], config: { schema: {}, value: {}, readonly: true }, warnings: [],
  } }))
  await page.goto(`${PREVIEW_ORIGIN}/ui/${detail ? 'plugins/display_demo?tab=panel' : 'adapter/display_demo/ui'}`)
  const frame = page.frameLocator('iframe').first()
  await expect(frame.getByRole('button', { name: 'Focus', exact: true })).toBeVisible()
  return frame
}

for (const kind of ['static', 'hosted-tsx', 'legacy'] as const) {
  test(`${kind}: host consent, focus and real fullscreen preserve the iframe draft`, async ({ page }) => {
    const frame = await fixture(page, kind)
    const iframe = await page.locator('iframe').elementHandle()
    await frame.getByRole('textbox').fill('keep this draft')
    await frame.getByRole('button', { name: 'Focus', exact: true }).click()
    await expect(page.getByRole('dialog')).toContainText('Display example')
    await expect(page.locator('.app-sidebar')).toBeVisible()
    await page.getByTestId('display-cancel').click()
    await expect(frame.locator('#status')).toHaveText('declined')
    await frame.getByRole('button', { name: 'Focus', exact: true }).click()
    await page.getByTestId('display-confirm').click()
    await expect(page.locator('.app-sidebar')).toBeHidden()
    await expect(frame.locator('#mode')).toHaveText('focus')
    await frame.getByRole('button', { name: 'Fullscreen', exact: true }).click()
    await page.getByTestId('display-confirm').click()
    await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true)
    await expect(frame.locator('#mode')).toHaveText('fullscreen')
    await expect(page.getByTestId('display-exit')).toBeVisible()
    await expect(frame.getByRole('textbox')).toHaveValue('keep this draft')
    expect(await iframe!.evaluate(element => element === document.querySelector('iframe'))).toBe(true)
    await page.getByTestId('display-exit').click()
    await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(false)
    await expect(page.locator('.app-sidebar')).toBeVisible()
    await expect(frame.getByRole('textbox')).toHaveValue('keep this draft')
  })
}

test('detail panel requests use the shared host and navigation cancels an open prompt', async ({ page }) => {
  const frame = await fixture(page, 'static', true)
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect(page.locator('.plugin-detail')).toHaveClass(/plugin-detail--focus/)
  await frame.getByRole('button', { name: 'Exit', exact: true }).click()
  await expect(page.locator('.app-sidebar')).toBeVisible()
  await frame.getByRole('button', { name: 'Fullscreen', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  // A plugin can navigate to logs while another async operation has a display
  // prompt open. Keep the SPA mounted so cancellation must reach its document.
  await frame.locator('body').evaluate(() => parent.postMessage({ type: 'neko-hosted-surface-open-logs' }, location.origin))
  await expect(page.getByRole('dialog')).toBeHidden()
  await expect(frame.locator('#status')).toHaveText('cancelled')
  await expect(page.locator('.app-sidebar')).toBeVisible()
})

// Host-only regressions run once; the client matrix above covers both adapters.
test('Escape declines a fullscreen upgrade without leaving the detail panel focus session', async ({ page }) => {
  const frame = await fixture(page, 'static', true)
  await frame.getByRole('textbox').fill('keep this draft')
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect(frame.locator('#mode')).toHaveText('focus')
  await frame.getByRole('button', { name: 'Fullscreen', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()

  await page.keyboard.press('Escape')

  await expect(page.getByRole('dialog')).toBeHidden()
  await expect(frame.locator('#status')).toHaveText('declined')
  await expect(frame.locator('#mode')).toHaveText('focus')
  await expect(page.locator('.app-sidebar')).toBeHidden()
  await expect(frame.getByRole('textbox')).toHaveValue('keep this draft')
})

test('the host exits manual focus in one click after fullscreen is downgraded', async ({ page }) => {
  const frame = await fixture(page, 'static', true)
  await page.getByTestId('focus-enter').click()
  await frame.getByRole('button', { name: 'Fullscreen', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true)
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(false)
  await expect(frame.locator('#mode')).toHaveText('focus')

  await page.getByTestId('focus-exit').click()

  await expect(page.locator('.app-sidebar')).toBeVisible()
  await expect(frame.locator('#mode')).toHaveText('normal')
  await expect(page.getByTestId('focus-exit')).toBeHidden()
})

for (const kind of ['static', 'hosted-tsx', 'legacy'] as const) {
  test(`${kind}: same-frame reload ends the old session and invalidates its pending confirmation`, async ({ page }) => {
    const frame = await fixture(page, kind)
    await frame.getByRole('button', { name: 'Focus', exact: true }).click()
    await page.getByTestId('display-confirm').click()
    await expect(page.locator('.app-sidebar')).toBeHidden()
    let token = await frame.locator('#document-token').textContent()
    await frame.locator('body').evaluate(() => location.reload())
    await expect(frame.locator('#document-token')).not.toHaveText(token!)
    await expect(page.locator('.app-sidebar')).toBeVisible()
    await frame.getByRole('button', { name: 'Fullscreen', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    token = await frame.locator('#document-token').textContent()
    await frame.locator('body').evaluate(() => location.reload())
    await expect(frame.locator('#document-token')).not.toHaveText(token!)
    await expect(page.getByRole('dialog')).toBeHidden()
    await frame.getByRole('button', { name: 'Focus', exact: true }).click()
    await page.getByTestId('display-confirm').click()
    await expect(frame.locator('#status')).toHaveText('applied')
    await page.getByTestId('display-exit').click()
  })
}

test('static locale replacement restores layout and gives the new document a fresh display session', async ({ page }) => {
  const frame = await fixture(page, 'static')
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  const token = await frame.locator('#document-token').textContent()
  await page.getByTestId('language-switcher').click()
  await page.getByRole('menuitem', { name: /简体中文/ }).click()
  await expect(frame.locator('#document-token')).not.toHaveText(token!)
  await expect(page.locator('.app-sidebar')).toBeVisible()
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect(frame.locator('#status')).toHaveText('applied')
  await page.getByTestId('display-exit').click()
})

test('host confirmation leaves the old fullscreen target when the browser exits while it is open', async ({ page }) => {
  const frame = await fixture(page, 'static')
  await frame.getByRole('button', { name: 'Fullscreen', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true)
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await expect(page.getByTestId('display-confirm')).toBeVisible()
  await page.evaluate(() => document.exitFullscreen())
  await expect.poll(() => page.getByRole('dialog').evaluate(element => !!element.closest('.hosted-surface-frame'))).toBe(false)
  await page.getByTestId('display-cancel').click()
  await expect(frame.locator('#status')).toHaveText('declined')
  await expect(page.locator('.app-sidebar')).toBeVisible()
})

test('fullscreen confirmation stays reachable when downgrading to focus; browser exit restores state', async ({ page }) => {
  const frame = await fixture(page, 'hosted-tsx')
  await frame.getByRole('button', { name: 'Fullscreen', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true)
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await expect(page.getByTestId('display-confirm')).toBeVisible()
  await page.getByTestId('display-confirm').click()
  await expect(frame.locator('#mode')).toHaveText('focus')
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(false)
  await frame.getByRole('button', { name: 'Fullscreen', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true)
  await page.evaluate(() => document.exitFullscreen())
  await expect(frame.locator('#mode')).toHaveText('normal')
  await expect(page.locator('.app-sidebar')).toBeVisible()
})

test('a document replacement cancels confirmation while its lazy dialog is still loading', async ({ page }) => {
  let releaseDialog!: () => void
  let markRequested!: () => void
  const heldDialog = new Promise<void>(resolve => { releaseDialog = resolve })
  const dialogRequested = new Promise<void>(resolve => { markRequested = resolve })
  await page.route('**/PluginDisplayDialog-*.js', async route => {
    markRequested()
    await heldDialog
    await route.continue()
  })
  try {
    const frame = await fixture(page, 'static')
    await frame.getByRole('button', { name: 'Focus', exact: true }).click()
    await dialogRequested
    const token = await frame.locator('#document-token').textContent()
    await frame.locator('body').evaluate(() => location.reload())
    await expect(frame.locator('#document-token')).not.toHaveText(token!)
    releaseDialog()
    await expect(page.getByRole('dialog')).toBeHidden()
    await frame.getByRole('button', { name: 'Focus', exact: true }).click()
    await page.getByTestId('display-confirm').click()
    await expect(frame.locator('#status')).toHaveText('applied')
    await page.getByTestId('display-exit').click()
    await expect(page.locator('.app-sidebar')).toBeVisible()
  } finally {
    releaseDialog()
  }
})

for (const kind of ['static', 'hosted-tsx'] as const) {
  test(`${kind}: iframe Escape exits manual focus after document reload`, async ({ page }) => {
    const frame = await fixture(page, kind, true)
    await page.getByTestId('focus-enter').click()
    await expect(frame.locator('#mode')).toHaveText('focus')
    const token = await frame.locator('#document-token').textContent()
    await frame.locator('body').evaluate(() => location.reload())
    await expect(frame.locator('#document-token')).not.toHaveText(token!)
    await expect(frame.locator('#mode')).toHaveText('focus')
    await frame.getByRole('textbox').press('Escape')
    await expect(page.locator('.app-sidebar')).toBeVisible()
    await expect(frame.locator('#mode')).toHaveText('normal')
  })
}

test('a rejected confirmation chunk fails the request instead of leaving it busy', async ({ page }) => {
  await page.route('**/PluginDisplayDialog-*.js', route => route.abort())
  const frame = await fixture(page, 'static')
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await expect(frame.locator('#status')).toHaveText('failed')
  await expect(page.getByText('Failed to load plugin UI', { exact: true })).toBeVisible()
  await expect(page.locator('.app-sidebar')).toBeVisible()
})

test('a timed out confirmation load releases the request and a fresh request can retry', async ({ page }) => {
  let release!: () => void
  let requested!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  const started = new Promise<void>(resolve => { requested = resolve })
  await page.route('**/PluginDisplayDialog-*.js', async route => {
    requested()
    await held
    await route.continue()
  })
  try {
    const frame = await fixture(page, 'static')
    await frame.getByRole('button', { name: 'Focus', exact: true }).click()
    await started
    await expect(frame.locator('#status')).toHaveText('failed', { timeout: 20_000 })
    const downloaded = page.waitForResponse(response => response.url().includes('/PluginDisplayDialog-') && response.url().endsWith('.js'))
    release()
    await downloaded
    await frame.getByRole('button', { name: 'Focus', exact: true }).click()
    await page.getByTestId('display-confirm').click()
    await expect(frame.locator('#status')).toHaveText('applied')
    await page.getByTestId('display-exit').click()
  } finally { release() }
})

test('native Escape closes a hosted Modal before leaving focus mode', async ({ page }) => {
  const panelSource = `import { Page, Button, Input, Modal, useState, useEffect } from '@neko/plugin-ui'
export default function Panel(props) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState('normal')
  useEffect(() => props.api.display.onStateChange(state => setMode(state.mode)), [])
  return <Page>
    <Button onClick={() => props.api.display.requestMode('focus')}>Focus</Button>
    <Button onClick={() => setOpen(true)}>Open modal</Button>
    <Modal title="Local modal" open={open} onClose={() => setOpen(false)}><Input /></Modal>
    <p id="mode">{mode}</p>
  </Page>
}`
  const frame = await fixture(page, 'hosted-tsx', false, panelSource)
  await frame.getByRole('button', { name: 'Focus', exact: true }).click()
  await page.getByTestId('display-confirm').click()
  await expect(frame.locator('#mode')).toHaveText('focus')
  await frame.getByRole('button', { name: 'Open modal', exact: true }).click()
  await expect(frame.getByRole('dialog', { name: 'Local modal' })).toBeVisible()
  await frame.getByRole('textbox').press('Escape')
  await expect(frame.getByRole('dialog', { name: 'Local modal' })).toBeHidden()
  await expect(page.locator('.app-sidebar')).toBeHidden()
  await expect(frame.locator('#mode')).toHaveText('focus')
  await frame.getByRole('button', { name: 'Focus', exact: true }).press('Escape')
  await expect(page.locator('.app-sidebar')).toBeVisible()
  await expect(frame.locator('#mode')).toHaveText('normal')
})
