import { expect, test, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

const BOOT_SHELL_ID = 'plugin-manager-boot-shell'

async function pauseMainModule(page: Page) {
  let releaseMainModule: (() => void) | undefined
  const mainModuleGate = new Promise<void>((resolve) => {
    releaseMainModule = resolve
  })
  await page.route('**/ui/assets/index-*.js', async (route) => {
    await mainModuleGate
    await route.continue()
  })
  return () => releaseMainModule?.()
}

test.beforeEach(async ({ page }) => {
  await stubCorePluginManagerApis(page)
})

test('redirects unknown routes and completes the boot shell handoff', async ({ page }) => {
  await page.goto(`${PREVIEW_ORIGIN}/ui/route-that-does-not-exist`)

  await expect(page).toHaveURL(`${PREVIEW_ORIGIN}/ui/`)
  await expect(page.locator('.dashboard')).toBeVisible()
  await expect(page.locator(`#${BOOT_SHELL_ID}`)).toHaveCount(0)
})

test('shows a theme-matched shell until the plugin manager layout is ready', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('neko-dark-mode', 'true')
    ;(window as typeof window & { __pluginManagerBlankGap?: boolean }).__pluginManagerBlankGap =
      false
    let shellObserved = false
    const observer = new MutationObserver(() => {
      const shell = document.getElementById('plugin-manager-boot-shell')
      const layout = document.querySelector('.app-root')
      shellObserved ||= shell !== null
      const layoutStyle = layout ? window.getComputedStyle(layout) : null
      const layoutBounds = layout?.getBoundingClientRect()
      const layoutCanPaint = Boolean(
        layout &&
          layoutStyle &&
          layoutBounds &&
          layoutStyle.display !== 'none' &&
          layoutStyle.visibility !== 'hidden' &&
          layoutStyle.opacity !== '0' &&
          layoutBounds.width > 0 &&
          layoutBounds.height > 0
      )
      if (shellObserved && shell === null && !layoutCanPaint) {
        ;(window as typeof window & { __pluginManagerBlankGap?: boolean }).__pluginManagerBlankGap =
          true
      }
    })
    observer.observe(document.documentElement, { childList: true, subtree: true })
  })
  const releaseMainModule = await pauseMainModule(page)

  await page.goto(`${PREVIEW_ORIGIN}/ui/`, { waitUntil: 'commit' })

  const shell = page.locator(`#${BOOT_SHELL_ID}`)
  await expect(shell).toBeVisible()
  await expect(shell).toContainText('N.E.K.O')
  await expect(shell).toHaveCSS('background-color', 'rgb(24, 24, 24)')

  releaseMainModule()

  await expect(page.locator('.app-root')).toBeVisible()
  await expect(shell).toHaveCount(0)
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as typeof window & { __pluginManagerBlankGap?: boolean }).__pluginManagerBlankGap
      )
    )
    .toBe(false)
})

test('uses the system dark theme when no stored theme exists', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  const releaseMainModule = await pauseMainModule(page)

  await page.goto(`${PREVIEW_ORIGIN}/ui/`, { waitUntil: 'commit' })

  const shell = page.locator(`#${BOOT_SHELL_ID}`)
  await expect(shell).toBeVisible()
  await expect(shell).toHaveCSS('background-color', 'rgb(24, 24, 24)')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')

  releaseMainModule()
  await expect(page.locator('.app-root')).toBeVisible()
})

test('uses the stored light theme instead of the system dark theme', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(() => {
    window.localStorage.setItem('neko-dark-mode', 'false')
  })
  const releaseMainModule = await pauseMainModule(page)

  await page.goto(`${PREVIEW_ORIGIN}/ui/`, { waitUntil: 'commit' })

  const shell = page.locator(`#${BOOT_SHELL_ID}`)
  await expect(shell).toBeVisible()
  await expect(shell).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await expect(page.locator('html')).not.toHaveClass(/dark/)
  await expect(page.locator('html')).not.toHaveAttribute('data-theme', 'dark')

  releaseMainModule()
  await expect(page.locator('.app-root')).toBeVisible()
})
