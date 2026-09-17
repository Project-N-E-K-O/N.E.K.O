import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { chromium } from 'playwright'
import { checkConfigLayout } from './check-config-layout.mjs'

// Run with node --test scripts/check-config-layout.test.mjs after installing Chromium.
let browser
before(async () => {
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
  })
})
after(async () => browser?.close())

async function fixture(t, { reveal = true, covered = false } = {}) {
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } })
  t.after(() => page.close())
  await page.setContent(`
    <style>
      body { margin: 0; }
      .config-content { height: 200px; overflow: auto; }
      input { width: 120px; height: 24px; }
      button { height: 28px; }
      .cover { position: fixed; top: 0; left: 0; width: 100%; height: 220px; z-index: 100; }
    </style>
    <main class="app-main"><section class="plugin-config-editor">
      <div class="config-toolbar">Configuration</div>
      <div class="config-content">
        <input type="text" inputmode="decimal" aria-label="search.max_results" value="8">
        <div style="height:600px"></div>
        <input type="text" inputmode="decimal" aria-label="search.duckduckgo_fallback_delay_seconds" value="2">
      </div>
      <footer class="config-footer">
        <button>保存方案</button>
        <button onclick="document.querySelector('input').value='8'">撤销全部未保存修改</button>
      </footer>
    </section></main>
    ${covered ? '<div class="cover"></div>' : ''}
  `)
  await page.evaluate((reveal) => {
    window.fieldClicks = 0
    window.saveClicks = 0
    const pane = document.querySelector('.config-content')
    pane.addEventListener('click', (event) => {
      if (event.target instanceof HTMLInputElement) window.fieldClicks++
    })
    document
      .querySelector('.config-footer button')
      .addEventListener('click', () => window.saveClicks++)
    if (reveal)
      pane.addEventListener('focusin', (event) => event.target.scrollIntoView({ block: 'nearest' }))
  }, reveal)
  let resets = 0
  const viewport = {
    set: (size) => page.setViewportSize(size),
    reset: () => {
      resets++
      return page.setViewportSize({ width: 800, height: 600 })
    },
  }
  return { page, viewport, resetCount: () => resets }
}

async function assertCleaned(f) {
  assert.equal(await f.page.locator('input').first().inputValue(), '8')
  assert.equal(f.resetCount(), 1)
  assert.equal(await f.page.evaluate(() => window.saveClicks), 0)
}

test('checks both focus reveal and actual clicks without saving', async (t) => {
  const f = await fixture(t)
  const results = await checkConfigLayout({ playwright: f.page }, f.viewport, [
    { width: 800, height: 600 },
    { width: 600, height: 400, zoom: 1.25 },
  ])
  assert.equal(results.length, 2)
  assert.equal(await f.page.evaluate(() => window.fieldClicks), 4)
  await assertCleaned(f)
})

test('rejects missing focus reveal before a click can automatically scroll', async (t) => {
  const f = await fixture(t, { reveal: false })
  await assert.rejects(
    checkConfigLayout({ playwright: f.page }, f.viewport, [{ width: 800, height: 600 }]),
    /Unreachable field search.duckduckgo_fallback_delay_seconds/
  )
  await assertCleaned(f)
})

test('rejects a visible and focusable input covered by another element', async (t) => {
  const f = await fixture(t, { covered: true })
  await assert.rejects(
    checkConfigLayout({ playwright: f.page }, f.viewport, [{ width: 800, height: 600 }]),
    /Unreachable field search.max_results/
  )
  await assertCleaned(f)
})
