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
  await page.route('**/security/csrf-token', (route) =>
    route.fulfill({ json: { csrf_token: 'e2e-csrf-token' } })
  )
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

// Resolves the rendered text and background colours (including color-mix and
// translucent ancestors) in the browser, so default Element Plus styles cannot
// silently override intended contrast. Returns each selector whose first match
// falls below `minimum`; a selector with no match is reported as a failure.
export function contrastFailures(page: Page, selectors: string[], minimum = 4.5) {
  return page.evaluate(
    ({ selectors, minimum }) => {
      const context = document.createElement('canvas').getContext('2d', {
        willReadFrequently: true,
      })!
      const rgba = (value: string) => {
        context.clearRect(0, 0, 1, 1)
        context.fillStyle = value
        context.fillRect(0, 0, 1, 1)
        return Array.from(context.getImageData(0, 0, 1, 1).data)
      }
      const luminance = (rgb: number[]) =>
        rgb
          .slice(0, 3)
          .map((value) => {
            const v = value / 255
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
          })
          .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index]!, 0)
      const background = (element: Element | null): number[] => {
        if (!element) return [0, 0, 0]
        const color = rgba(getComputedStyle(element).backgroundColor)
        const alpha = color[3]! / 255
        if (alpha === 1) return color.slice(0, 3)
        const parent = background(element.parentElement)
        return color
          .slice(0, 3)
          .map((value, index) => value * alpha + parent[index]! * (1 - alpha))
      }
      return selectors.flatMap((selector) => {
        const element = document.querySelector(selector)
        if (!element) return [{ selector, foreground: '', background: [], ratio: 0 }]
        const foreground = getComputedStyle(element).color
        const bg = background(element)
        const fg = luminance(rgba(foreground))
        const bl = luminance(bg)
        const ratio = (Math.max(fg, bl) + 0.05) / (Math.min(fg, bl) + 0.05)
        return ratio < minimum ? [{ selector, foreground, background: bg, ratio }] : []
      })
    },
    { selectors, minimum }
  )
}
