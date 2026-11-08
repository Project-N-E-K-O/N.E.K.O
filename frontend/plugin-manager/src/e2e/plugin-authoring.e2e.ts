import { expect, type Locator, type Page } from '@playwright/test'
import { appearanceTest, PREVIEW_ORIGIN } from './plugin-manager-test-helpers'

const DOCS_URL = 'https://project-neko.online/zh-CN/plugins/'
type Activation = 'left' | 'middle' | 'keyboard'
type ExternalProbe = {
  externalUrls: string[]
  scriptOpenCalls: number
  electronShell?: { openExternal: (url: string) => void }
}

// Native background-tab navigation uses the full browser rather than headless shell.
appearanceTest.use({ appLocale: 'zh-CN', channel: 'chromium' })

async function prepareTutorial(page: Page, electron: boolean) {
  await page.route('**/plugin-projects', route => route.fulfill({ json: [] }))
  await page.addInitScript(electron => {
    const probe = window as unknown as ExternalProbe
    probe.externalUrls = []
    probe.scriptOpenCalls = 0
    window.open = () => {
      probe.scriptOpenCalls++
      return null
    }
    if (electron) {
      probe.electronShell = { openExternal: url => { probe.externalUrls.push(url) } }
    }
  }, electron)
  await page.goto(`${PREVIEW_ORIGIN}/ui/workbench`)
  const link = page.getByRole('link', { name: '教程', exact: true })
  await expect(link).toHaveAttribute('href', DOCS_URL)
  return link
}

async function activate(link: Locator, activation: Activation) {
  if (activation === 'keyboard') await link.press('Enter')
  else await link.click({ button: activation })
}

for (const activation of ['left', 'middle', 'keyboard'] as const) {
  appearanceTest(`opens the tutorial with native browser ${activation} navigation`, async ({ page, context }) => {
    // Keep the documentation fixture local, including the new tab's first request.
    await context.route(DOCS_URL, route => route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><title>Plugin documentation</title><p>Documentation fixture</p>',
    }))
    const link = await prepareTutorial(page, false)
    const [docs] = await Promise.all([
      context.waitForEvent('page'),
      activate(link, activation),
    ])
    await docs.bringToFront()
    await docs.waitForLoadState('domcontentloaded')
    await expect(docs).toHaveURL(DOCS_URL)
    await expect(page).toHaveURL(`${PREVIEW_ORIGIN}/ui/workbench`)
    expect(await page.evaluate(() => (window as unknown as ExternalProbe).scriptOpenCalls)).toBe(0)
    await docs.close()
  })

  appearanceTest(`routes tutorial ${activation} activation through the Electron browser bridge`, async ({ page, context }) => {
    // Fail the test on embedded navigation while keeping an accidental request offline.
    await context.route(DOCS_URL, route => route.fulfill({ body: 'Unexpected embedded navigation' }))
    const link = await prepareTutorial(page, true)
    await activate(link, activation)
    await expect.poll(() => page.evaluate(() => (window as unknown as ExternalProbe).externalUrls)).toEqual([DOCS_URL])
    expect(await page.evaluate(() => (window as unknown as ExternalProbe).scriptOpenCalls)).toBe(0)
    expect(context.pages()).toHaveLength(1)
    await expect(page).toHaveURL(`${PREVIEW_ORIGIN}/ui/workbench`)
  })
}
