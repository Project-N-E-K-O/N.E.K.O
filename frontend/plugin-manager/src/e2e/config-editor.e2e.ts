import { expect, test, type Locator, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

// Drives the real configuration editor against stubbed plugin APIs. The compact
// numeric field is covered here because its behaviour depends on the browser's
// treatment of the input element, which unit tests cannot reproduce.
const PLUGIN_ID = 'demo'
const NUMBER_FIELD = 'search.max_results'

async function stubConfigEditor(page: Page, modelCount = 0, bindingsReady = Promise.resolve()) {
  await stubCorePluginManagerApis(page)
  await page.routeWebSocket('**/ws/**', () => {})
  await page.route('**/plugins?*', (route) =>
    route.fulfill({
      json: {
        plugins: [
          {
            id: PLUGIN_ID,
            name: 'Demo',
            version: '1.0.0',
            description: 'demo plugin',
            status: 'running',
          },
        ],
        message: '',
      },
    })
  )
  await page.route('**/plugin/*/surfaces*', (route) =>
    route.fulfill({ json: { surfaces: [], warnings: [] } })
  )
  // Queries on these paths bypass the core stubs and would reach the dev proxy.
  await page.route('**/plugin/status*', (route) =>
    route.fulfill({ json: { plugins: { [PLUGIN_ID]: { status: { status: 'running' } } } } })
  )
  await page.route('**/plugin/metrics*', (route) =>
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
  await page.route('**/plugin/metrics/*', (route) =>
    route.fulfill({ json: { plugin_id: PLUGIN_ID, history: [] } })
  )
  await page.route('**/plugin/*/logs*', (route) =>
    route.fulfill({ json: { plugin_id: PLUGIN_ID, lines: [], total: 0 } })
  )
  await page.route('**/api/model-config/plugins/*/bindings', async (route) => {
    await bindingsReady
    await route.fulfill({
      json: {
        plugin_id: PLUGIN_ID,
        requirements: Object.fromEntries(
          Array.from({ length: modelCount }, (_, i) => [
            `usage_${i}`,
            {
              version: 0,
              label: `Model ${i}`,
              description: 'Text generation',
              required: true,
              capabilities: ['text'],
              slot_id: null,
              status: 'unbound',
            },
          ])
        ),
        bindings: {},
        ready: modelCount === 0,
      },
    })
  })
  await page.route('**/api/model-config/slots', (route) =>
    route.fulfill({ json: { schema_version: 1, slots: [] } })
  )
  // Registered after the generic config route so the specific paths win.
  await page.route('**/plugin/*/config', (route) =>
    route.fulfill({
      json: {
        plugin_id: PLUGIN_ID,
        config: { plugin: { id: PLUGIN_ID }, search: { max_results: 8 } },
        last_modified: '2026-01-01T00:00:00Z',
      },
    })
  )
  await page.route('**/plugin/*/config/base/effective', (route) =>
    route.fulfill({
      json: {
        plugin_id: PLUGIN_ID,
        config: {
          plugin: { id: PLUGIN_ID },
          search: { max_results: 8, duckduckgo_fallback_delay_seconds: 2 },
        },
      },
    })
  )
  await page.route('**/plugin/*/config/profiles', (route) =>
    route.fulfill({
      json: {
        plugin_id: PLUGIN_ID,
        profiles_path: 'profiles',
        profiles_exists: false,
        config_profiles: null,
      },
    })
  )
}

async function openConfigEditor(page: Page, modelCount = 0) {
  await page.addInitScript(() => window.localStorage.setItem('locale', 'zh-CN'))
  await stubConfigEditor(page, modelCount)
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/${PLUGIN_ID}?tab=config`)
  const field = page.locator(`input[aria-label="${NUMBER_FIELD}"]`)
  await expect(field).toBeVisible()
  return field
}

test('keeps every keystroke of a negative decimal number', async ({ page }) => {
  const field = await openConfigEditor(page)

  await field.click()
  await field.press('Control+a')
  await field.pressSequentially('-1.5')

  // A native number input reports "-" and "-1." as sanitised values, so the field
  // used to drop the sign and the decimal point instead of keeping them.
  await expect(field).toHaveValue('-1.5')
  await expect(page.locator('.config-footer .status-dot.dirty')).toBeVisible()
})

test('normalises an unfinished number when the field loses focus', async ({ page }) => {
  const field = await openConfigEditor(page)

  await field.click()
  await field.press('Control+a')
  await field.pressSequentially('2.5')
  await expect(field).toHaveValue('2.5')

  // An unfinished exponent is kept while typing, but it cannot be stored: the model
  // stays at the last committed number and blur restores it in the field.
  await field.press('End')
  await field.pressSequentially('e')
  await expect(field).toHaveValue('2.5e')
  await field.blur()
  await expect(field).toHaveValue('2.5')
})

// Check clipping and hit testing: toBeVisible alone also passes for controls
// painted outside an overflow:hidden ancestor.
async function expectReachable(control: Locator) {
  await expect
    .poll(() =>
      control.evaluate((element) => {
        const rect = element.getBoundingClientRect()
        let top = 0,
          left = 0,
          right = innerWidth,
          bottom = innerHeight
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const style = getComputedStyle(parent),
            bounds = parent.getBoundingClientRect()
          if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
            top = Math.max(top, bounds.top)
            bottom = Math.min(bottom, bounds.bottom)
          }
          if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
            left = Math.max(left, bounds.left)
            right = Math.min(right, bounds.right)
          }
        }
        const hit = document.elementFromPoint(
          rect.left + rect.width / 2,
          rect.top + rect.height / 2
        )
        return (
          rect.top >= top - 2 &&
          rect.bottom <= bottom + 2 &&
          rect.left >= left - 2 &&
          rect.right <= right + 2 &&
          !!hit &&
          element.contains(hit)
        )
      })
    )
    .toBe(true)
}

for (const modelCount of [0, 1, 6]) {
  test(`configuration controls remain reachable with ${modelCount} model requirements`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    const field = await openConfigEditor(page, modelCount)
    await expect(page.locator('.model-bindings__row')).toHaveCount(modelCount)
    await field.fill('9')
    const editor = page.locator('.plugin-config-editor')
    const save = page.getByRole('button', { name: '保存方案', exact: true })
    for (const size of [
      { width: 1440, height: 1000 },
      { width: 1280, height: 720 },
      { width: 800, height: 600 },
      { width: 640, height: 450 },
      { width: 1440, height: 1000 },
    ]) {
      await page.setViewportSize(size)
      // Give ResizeObserver and its queued measurement two painting frames.
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
          )
      )
      await field.evaluate((element) => (element as HTMLElement).focus({ preventScroll: true }))
      await expectReachable(field)
      await expect
        .poll(() =>
          page
            .locator('[data-yui-guide-id="plugin-main"]')
            .evaluate((element) => element.scrollWidth - element.clientWidth)
        )
        .toBeLessThanOrEqual(2)
      if (await editor.evaluate((element) => element.classList.contains('page-scroll'))) {
        await save.scrollIntoViewIfNeeded()
      }
      await expectReachable(save)
      await expect(field).toHaveValue('9')
      if (modelCount > 0) {
        const selection = page.locator('.model-bindings__selection .el-select').last()
        await selection.scrollIntoViewIfNeeded()
        await expectReachable(selection)
        await selection.click()
        await page.keyboard.press('Escape')
      }
    }
  })
}

test('remeasures when model requirements arrive and disappear after layout', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.addInitScript(() => window.localStorage.setItem('locale', 'zh-CN'))
  let release!: () => void
  const ready = new Promise<void>((resolve) => {
    release = resolve
  })
  await stubConfigEditor(page, 6, ready)
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/${PLUGIN_ID}?tab=config`)
  const editor = page.locator('.plugin-config-editor')
  const field = page.locator(`input[aria-label="${NUMBER_FIELD}"]`)
  await expect(field).toBeVisible()
  await expect(editor).not.toHaveClass(/page-scroll/)
  release()
  await expect(page.locator('.model-bindings__row')).toHaveCount(6)
  await expect(editor).toHaveClass(/page-scroll/)
  await field.evaluate((element) => (element as HTMLElement).focus({ preventScroll: true }))
  await expectReachable(field)
  await field.fill('9')
  await page.route('**/api/model-config/plugins/*/bindings', (route) =>
    route.fulfill({
      json: { plugin_id: PLUGIN_ID, requirements: {}, bindings: {}, ready: true },
    })
  )
  await page.locator('.model-bindings__actions button').click()
  await expect(page.locator('.model-bindings')).toHaveCount(0)
  await expect(editor).not.toHaveClass(/page-scroll/)
  await expect(field).toHaveValue('9')
  await expectReachable(page.getByRole('button', { name: '保存方案', exact: true }))
})
