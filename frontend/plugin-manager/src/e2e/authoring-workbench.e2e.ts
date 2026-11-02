import { expect, type Page } from '@playwright/test'
import {
  appearanceTest as test,
  collectVueResolutionWarnings,
  PREVIEW_ORIGIN,
} from './plugin-manager-test-helpers'

test.use({ appLocale: 'zh-CN' })
const url = `${PREVIEW_ORIGIN}/ui/development/workbench`

// HTTP fixtures exercise the real creation UI without writing the tester's disk.
test.beforeEach(async ({ page }) => {
  let mounts = 0
  await page.route('**/plugins/development/mount', (route) => {
    mounts += 1
    expect(route.request().postDataJSON()).toEqual({
      source_dir: 'E:/Plugin Projects/starlight_helper',
    })
    if (mounts === 1) return route.fulfill({ status: 409, json: { detail: 'Mount conflict' } })
    return route.fulfill({
      json: {
        plugin_id: 'starlight_helper',
        registration_id: 'mounted-source',
        revision: 1,
        source_dir: 'E:/Plugin Projects/starlight_helper',
        error: null,
      },
    })
  })
  await page.route('**/plugins/development', (route) =>
    route.fulfill({
      json: {
        enabled: true,
        registrations: [
          {
            plugin_id: 'starlight_helper',
            registration_id: 'mounted-source',
            revision: 1,
            name: '星光助手',
            source_dir: 'E:/Plugin Projects/starlight_helper',
            runtime_alive: false,
          },
        ],
      },
    })
  )
  for (const action of ['preview', 'create']) {
    await page.route(`**/plugin-projects/${action}`, (route) => {
      const body = route.request().postDataJSON()
      if (action === 'create' && body.parent_dir === 'D:/Projects')
        return route.fulfill({
          status: 409,
          json: { detail: { code: 'PROJECT_EXISTS', message: 'Already exists' } },
        })
      return route.fulfill({
        status: action === 'create' ? 201 : 200,
        json: {
          plugin_id: body.plugin_id,
          name: body.name,
          description: body.description,
          author: body.author,
          plugin_type: body.plugin_type,
          version: '0.1.0',
          entry: `plugin.plugins.${body.plugin_id}:StarlightHelperPlugin`,
          project_dir: `${body.parent_dir}/${body.plugin_id}`,
          files: [
            'plugin.toml',
            'config.example.toml',
            '__init__.py',
            'pyproject.toml',
            'README.md',
            'tests/test_smoke.py',
            '.gitignore',
            '.vscode/settings.json',
            '.vscode/tasks.json',
            'ruff.toml',
            '.github/workflows/verify.yml',
            '.github/workflows/release.yml',
            '.git/',
          ],
          created: action === 'create',
        },
      })
    })
  }
})

test('picks the parent directory through the local backend and mounts the real project path', async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.assign(window, { nekoHost: undefined })
    localStorage.setItem('neko.pluginDevelopment.guide.v1.dismissed', '1')
  })
  const picks: unknown[] = []
  await page.route('**/plugin-projects/pick-directory', (route) => {
    picks.push(route.request().postDataJSON())
    return route.fulfill({
      json: { ok: true, cancelled: picks.length === 2, selected_root: picks.length === 2 ? '' : 'E:/Plugin Projects' },
    })
  })
  await page.goto(url)
  await page.getByRole('button', { name: '新建插件', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: '选择文件夹' }).click()
  await expect(dialog.getByLabel('父目录', { exact: true })).toHaveValue('E:/Plugin Projects')
  // Cancelling a later selection keeps the earlier absolute path.
  await dialog.getByRole('button', { name: '选择文件夹' }).click()
  await expect(dialog.getByLabel('父目录', { exact: true })).toHaveValue('E:/Plugin Projects')
  expect(picks).toEqual([{ start_path: '' }, { start_path: 'E:/Plugin Projects' }])
  await dialog.getByLabel('插件名称', { exact: true }).fill('星光助手')
  await dialog.getByLabel('插件 ID', { exact: true }).fill('starlight_helper')
  await dialog.getByRole('button', { name: '预览项目', exact: true }).click()
  await expect(dialog.locator('pre')).toContainText('E:/Plugin Projects/starlight_helper')
  await dialog.getByRole('button', { name: '创建插件', exact: true }).click()
  await expect(dialog.getByText('插件项目已创建', { exact: true })).toBeVisible()
  await expect(dialog.getByRole('button', { name: '打开文件夹' })).toBeVisible()
  await dialog.getByRole('button', { name: '挂载到开发模式' }).click()
  // The first mount attempt conflicts in the shared fixture; the retry succeeds.
  await dialog.getByRole('button', { name: '挂载到开发模式' }).click()
  await expect(dialog.getByText('插件项目已创建并挂载', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await verifyRetainedProject(page)
})

async function verifyRetainedProject(page: Page) {
  const card = page.locator('.authoring-project-card[data-project-kind="created"]')
  await expect(card).toHaveCount(1)
  await expect(card).toContainText('星光助手')
  await expect(card).toContainText('E:/Plugin Projects/starlight_helper')
  await card.getByRole('button', { name: '项目资料', exact: true }).click()
  await expect(page.locator('.authoring-details')).toContainText(
    'plugin.plugins.starlight_helper:StarlightHelperPlugin'
  )
  await expect(page.getByRole('button', { name: '运行基础检查', exact: true })).toHaveCount(0)
  await expect(page.getByRole('tab')).toHaveCount(0)
  // Refresh a direct detail link requesting a demo tab. Real projects stay
  // in the read-only details view and cannot enter any simulated operation.
  const detailUrl = new URL(page.url())
  detailUrl.searchParams.set('tab', 'delivery')
  await page.goto(detailUrl.toString())
  await expect(page.locator('.authoring-details')).toContainText(
    'E:/Plugin Projects/starlight_helper'
  )
  await expect(page.getByRole('tab')).toHaveCount(0)
  await expect(page.locator('.authoring-release-stages')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '运行基础检查', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '我的插件', exact: true }).click()
  await page.reload()
  await expect(card).toHaveCount(1)
  await expect(card).toContainText('E:/Plugin Projects/starlight_helper')
}

async function openProject(page: Page, name: string, tab: string) {
  const studio = page.getByTestId('authoring-workbench')
  const back = studio.getByRole('button', { name: '我的插件', exact: true })
  if (await back.isVisible()) await back.click()
  await studio.getByRole('button', { name, exact: true }).click()
  await studio.getByRole('tab', { name: tab, exact: true }).click()
}

test('previews and creates a project without loading it into the demo runtime', async ({
  page,
}) => {
  const warnings = collectVueResolutionWarnings(page)
  const mutations: string[] = []
  await page.addInitScript(() => {
    localStorage.setItem('neko.pluginDevelopment.guide.v1.dismissed', '1')
    Object.assign(window, {
      nekoHost: { pickDirectory: async () => ({ cancelled: false, selected_root: 'D:/Projects' }) },
    })
  })
  page.on('request', (request) => {
    if (request.method() !== 'GET') mutations.push(new URL(request.url()).pathname)
  })
  await page.goto(url)
  const studio = page.getByTestId('authoring-workbench')
  await expect(studio.locator('.authoring-toolbar .authoring-actions button')).toHaveText([
    '教程',
    '新建插件',
  ])
  await studio
    .getByRole('radiogroup', { name: '筛选', exact: true })
    .getByText('待发布', { exact: true })
    .click()
  await studio.getByRole('textbox', { name: '搜索名称、ID 或简介…' }).fill('备忘')
  await studio.getByRole('button', { name: '新建插件', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: '选择文件夹' }).click()
  await expect(dialog.getByLabel('父目录', { exact: true })).toHaveValue('D:/Projects')
  await dialog.getByLabel('插件名称', { exact: true }).fill('星光助手')
  await dialog.getByLabel('插件 ID', { exact: true }).fill('starlight_helper')
  await dialog.getByRole('button', { name: '预览项目', exact: true }).click()
  await expect(dialog.locator('pre')).toContainText('D:/Projects/starlight_helper')
  await expect(dialog.locator('code')).toContainText(
    'plugin.plugins.starlight_helper:StarlightHelperPlugin'
  )
  await dialog.getByRole('button', { name: '创建插件', exact: true }).click()
  await expect(dialog.getByText('目标目录已存在，请更换插件 ID 或父目录。')).toBeVisible()
  await expect(studio.locator('.authoring-project-card[data-project-kind="created"]')).toHaveCount(
    0
  )
  await expect(dialog.getByLabel('插件名称', { exact: true })).toHaveValue('星光助手')
  await dialog.getByRole('button', { name: '上一步' }).click()
  await dialog.getByLabel('父目录', { exact: true }).fill('E:/Plugin Projects')
  await dialog.getByRole('button', { name: '预览项目', exact: true }).click()
  await dialog.getByRole('button', { name: '创建插件', exact: true }).click()
  await expect(dialog.getByText('插件项目已创建')).toBeVisible()
  await expect(dialog.locator('pre')).toContainText('E:/Plugin Projects/starlight_helper')
  await expect(dialog.getByRole('button', { name: '打开文件夹' })).toBeVisible()
  await expect(studio.locator('.authoring-project-card')).toHaveCount(4)
  await expect(dialog.getByRole('button', { name: '创建插件', exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: '挂载到开发模式' }).click()
  await expect(dialog.getByText('挂载失败：Mount conflict', { exact: true })).toBeVisible()
  await expect(dialog.getByText('插件项目已创建', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '挂载到开发模式' }).click()
  await expect(dialog.getByText('插件项目已创建并挂载')).toBeVisible()
  await dialog.getByRole('button', { name: '前往开发模式' }).click()
  await expect(page).toHaveURL(/\/development$/)
  await expect(page.locator('.development-card')).toContainText(
    'E:/Plugin Projects/starlight_helper'
  )
  await page.goto(url)
  await verifyRetainedProject(page)
  expect(
    mutations.every(
      (path) => path.startsWith('/plugin-projects/') || path === '/plugins/development/mount'
    )
  ).toBe(true)
  expect(mutations.filter((path) => path === '/plugins/development/mount')).toHaveLength(2)
  expect(mutations.filter((path) => path === '/plugin-projects/create')).toHaveLength(2)
  expect(warnings).toEqual([])
})

test('keeps findings stale until rechecked', async ({ page }) => {
  await page.goto(url)
  await expect(page.locator('.authoring-project-card')).toHaveCount(3)
  await openProject(page, '天气助手', '检查结果')
  const studio = page.getByTestId('authoring-workbench')
  await expect(studio.getByText('Python 依赖尚未准备好', { exact: true })).toBeVisible()
  await studio.getByRole('button', { name: '查看修复后的情景' }).click()
  await expect(studio.getByText('此报告对应较早的源码快照，请重新运行检查。')).toBeVisible()
  await studio.getByRole('button', { name: '运行基础检查', exact: true }).last().click()
  await expect(studio.getByText('本次演示报告没有发现问题')).toBeVisible()
  await expect(studio.locator('.authoring-report-summary h3')).toHaveText('已通过')
})

test('supports four palettes, narrow layouts, tutorials and creation dialogs', async ({ page }) => {
  const warnings = collectVueResolutionWarnings(page)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(url)
  await expect(page.locator('.authoring-project-card')).toHaveCount(3)
  await expect(page.locator('.sidebar-brand__logo')).toHaveJSProperty('complete', true)
  for (const palette of ['light-paper', 'dark-neko', 'dark-graphite', 'light-neko']) {
    await page.evaluate((value) => {
      const [mode, name] = value.split('-')
      document.documentElement.classList.toggle('dark', mode === 'dark')
      document.documentElement.setAttribute(`data-${mode}-palette`, name!)
    }, palette)
    await page.screenshot({ path: `test-results/authoring-${palette}.png`, animations: 'disabled' })
  }
  await page.evaluate(() => {
    document.documentElement.classList.add('dark')
    document.documentElement.setAttribute('data-dark-palette', 'graphite')
  })
  await openProject(page, '天气助手', '检查结果')
  await expect(page.locator('.authoring-finding')).toHaveCount(2)
  await page.screenshot({ path: 'test-results/authoring-checks.png', animations: 'disabled' })
  await openProject(page, '备忘助手', '打包发布')
  await expect(page.locator('.authoring-release-stages')).toBeVisible()
  await page.screenshot({ path: 'test-results/authoring-delivery.png', animations: 'disabled' })
  await page.getByRole('button', { name: '教程', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('分享你的插件')
  await page
    .getByRole('dialog')
    .getByRole('button', { name: /close|关闭/i })
    .click()
  await page.setViewportSize({ width: 760, height: 640 })
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.screenshot({ path: 'test-results/authoring-narrow.png', animations: 'disabled' })
  expect(
    await page
      .locator('.authoring-workbench')
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
  ).toBe(true)
  await page.getByRole('button', { name: '我的插件', exact: true }).click()
  await page.getByRole('button', { name: '新建插件', exact: true }).first().click()
  await page.getByLabel('父目录', { exact: true }).fill('E:/Projects')
  await page.getByLabel('插件名称', { exact: true }).fill('Narrow demo')
  await page.getByLabel('插件 ID', { exact: true }).fill('narrow_demo')
  await page.getByRole('button', { name: '预览项目', exact: true }).click()
  const create = page.getByRole('button', { name: '创建插件' })
  await create.scrollIntoViewIfNeeded()
  await expect(create).toBeInViewport()
  await expect(page.getByRole('dialog').getByRole('heading')).toBeInViewport()
  await page.screenshot({
    path: 'test-results/authoring-create-narrow.png',
    animations: 'disabled',
  })
  expect(warnings).toEqual([])
})

test.describe('English narrow workspace', () => {
  test.use({ appLocale: 'en-US' })

  test('keeps long controls and the wizard reachable on a small screen', async ({ page }) => {
    await page.setViewportSize({ width: 480, height: 720 })
    await page.goto(`${url}?project=memo_assistant&tab=delivery`)
    await expect(page.locator('.authoring-release-stages')).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Package & publish', exact: true })).toBeInViewport()
    expect(
      await page
        .locator('.authoring-workbench')
        .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
    ).toBe(true)
    await page.screenshot({
      path: 'test-results/authoring-english-mobile.png',
      animations: 'disabled',
    })
    await page.getByRole('button', { name: 'My projects', exact: true }).click()
    await page.getByRole('button', { name: 'New plugin', exact: true }).click()
    await page.getByLabel('Parent directory', { exact: true }).fill('E:/Projects')
    await page.getByLabel('Name', { exact: true }).fill('A small screen example')
    await page.getByLabel('Plugin ID', { exact: true }).fill('small_screen')
    await page.getByRole('button', { name: 'Preview project', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Create plugin' })).toBeInViewport()
    await expect(page.getByRole('dialog').getByRole('heading')).toBeInViewport()
  })
})
