import type { Page } from '@playwright/test'

export const PREVIEW_ORIGIN = 'http://127.0.0.1:4173'

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
