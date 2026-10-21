import { expect, openVisualPage, test } from './fixtures'

for (const sample of [
  { name: 'dashboard-light', path: '/', dark: false, width: 1440, height: 960 },
  { name: 'plugins-light', path: '/plugins', dark: false, width: 1440, height: 960 },
  { name: 'plugins-dark', path: '/plugins', dark: true, width: 1440, height: 960 },
  { name: 'plugins-narrow', path: '/plugins', dark: false, width: 600, height: 960 },
] as const) {
  test(sample.name, async ({ page }) => {
    await page.setViewportSize({ width: sample.width, height: sample.height })
    await openVisualPage(page, sample.path, sample.dark)
    await expect(page).toHaveScreenshot(`${sample.name}.png`)
  })
}
