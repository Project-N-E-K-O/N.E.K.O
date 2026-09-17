import { expect, test, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

// Drives the real configuration editor against stubbed plugin APIs. The compact
// numeric field is covered here because its behaviour depends on the browser's
// treatment of the input element, which unit tests cannot reproduce.
const PLUGIN_ID = 'demo'
const NUMBER_FIELD = 'search.max_results'

async function stubConfigEditor(page: Page) {
  await stubCorePluginManagerApis(page)
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
  await page.route('**/plugin/*/logs*', (route) =>
    route.fulfill({ json: { plugin_id: PLUGIN_ID, lines: [], total: 0 } })
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

async function openConfigEditor(page: Page) {
  await page.addInitScript(() => window.localStorage.setItem('locale', 'zh-CN'))
  await stubConfigEditor(page)
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
