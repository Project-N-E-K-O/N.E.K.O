import { expect, test, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

/**
 * 适配器界面页必须能显示"插件自己声明的 surface"，而不只是老的静态 UI。
 *
 * 背景：界面来源有两条路 —— 新式 surface（`[plugin.ui] panel`，hosted-tsx / markdown /
 * static，经 `/plugin/{id}/surfaces`）与老式静态 UI（`static/index.html`，经
 * `/plugin/{id}/ui-info` 的 `has_ui`）。适配器页原先只走后者，于是用 surface 声明界面、
 * 又没有 static 目录的适配器（实测 mcp_adapter）被误报"该插件没有自定义界面"，
 * 而同一个插件在详情页渲染正常。
 *
 * 这里钉住两件事：
 *   1. 有 surface → 渲染 HostedSurfaceFrame，"没有自定义界面"不得出现
 *   2. 真的没有任何界面 → 才允许出现那条提示（即提示不能变成"永远显示"或"永不显示"）
 */

const ADAPTER_ID = 'adapter_demo'
const NO_UI_ID = 'adapter_without_ui'

async function stubAdapterApis(page: Page, opts: { withSurface: boolean }) {
  await stubCorePluginManagerApis(page)
  await page.route('**/plugins?*', (route) =>
    route.fulfill({
      json: {
        plugins: [
          {
            id: ADAPTER_ID,
            name: '适配器样本',
            description: '用于适配器界面回归。',
            version: '1.0.0',
            type: 'adapter',
            status: 'running',
          },
          {
            id: NO_UI_ID,
            name: '无界面样本',
            description: '没有任何界面声明。',
            version: '1.0.0',
            type: 'adapter',
            status: 'running',
          },
        ],
        message: '',
      },
    }),
  )
  await page.route('**/plugin/status', (route) =>
    route.fulfill({ json: { plugins: { [ADAPTER_ID]: 'running', [NO_UI_ID]: 'running' } } }),
  )
  // 两个插件都先给"没有任何界面"的旧式回答，模拟没有 static/index.html
  await page.route('**/plugin/*/ui-info', (route) =>
    route.fulfill({ json: { has_ui: false, ui_path: null, static_dir: null, static_files: [] } }),
  )
  await page.route(`**/plugin/${ADAPTER_ID}/surfaces*`, (route) =>
    opts.withSurface
      ? route.fulfill({
          json: {
            surfaces: [
              { id: 'main', kind: 'panel', mode: 'static', title: 'Adapter', url: '/stub-adapter.html', available: true },
            ],
            warnings: [],
          },
        })
      : route.fulfill({ json: { surfaces: [], warnings: [] } }),
  )
  await page.route(`**/plugin/${NO_UI_ID}/surfaces*`, (route) =>
    route.fulfill({ json: { surfaces: [], warnings: [] } }),
  )
  await page.route('**/stub-adapter.html', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<html><body>adapter panel</body></html>' }),
  )
}

async function openAdapterPage(page: Page, id: string) {
  await page.goto(`${PREVIEW_ORIGIN}/ui/adapter/${id}/ui`, { waitUntil: 'load' })
  await page.waitForSelector('.adapter-ui', { timeout: 15_000 })
  await page.evaluate(() => document.getElementById('plugin-manager-boot-shell')?.remove())
  await page.waitForTimeout(800)
}

test('声明了 surface 的适配器渲染 HostedSurfaceFrame，而不是"没有自定义界面"', async ({ page }) => {
  await stubAdapterApis(page, { withSurface: true })
  await openAdapterPage(page, ADAPTER_ID)

  await expect(page.locator('[data-yui-guide-id="plugin-main"]')).toBeVisible()
  expect(await page.locator('.hosted-surface-frame').count(), '没渲染面板 frame').toBeGreaterThan(0)
  expect(await page.locator('.plugin-ui-frame').count(), '不该再走旧的 PluginUIFrame').toBe(0)
  // 用 DOM 钩子而不是文案：文案随 locale 变（CI 里是英文），拿它断言会假失败
  expect(await page.locator('.no-ui-overlay').count(), '有 surface 却报了"没有自定义界面"').toBe(0)
})

test('确实没有任何界面声明的适配器才显示"没有自定义界面"', async ({ page }) => {
  await stubAdapterApis(page, { withSurface: false })
  await openAdapterPage(page, NO_UI_ID)

  expect(await page.locator('.hosted-surface-frame').count(), '无界面却渲染了 frame').toBe(0)
  await expect(page.locator('.plugin-ui-frame')).toBeVisible()
  await expect(page.locator('.no-ui-overlay'), '真无界面时没有给出提示').toBeVisible()
})
