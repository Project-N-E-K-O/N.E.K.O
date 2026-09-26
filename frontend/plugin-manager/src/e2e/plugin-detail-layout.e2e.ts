import { expect, test, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

/**
 * 面板高度契约的几何回归。
 *
 * 为什么必须有这条：撑满链一旦断掉**不会报错**，只会静默变形 ——
 *   * 面板退回内容高度（实测日志列表能长到 6732px），或
 *   * 反过来把长内容 tab 压到一屏，被 .el-card 默认的 overflow:hidden 直接裁掉。
 * 两种都只有真实布局引擎量得出来，jsdom / happy-dom 没有布局。
 *
 * 契约本身见 utils/constants.ts 与 PluginDetail 的 .plugin-detail--fill：
 * 撑满型 tab（面板/教程/日志）的高度来自容器，长内容 tab 仍由 .app-main 滚动。
 */

const PLUGIN_ID = 'layout_demo'

/** 入口点比一屏长：用来证明长内容 tab 没有被"撑满链"压扁、内容没被裁 */
const MANY_ENTRIES = Array.from({ length: 40 }, (_, index) => ({
  id: `entry_${index}`,
  name: `entry_${index}`,
  description: `第 ${index} 个入口点，用来把这一页撑得比一屏长。`,
}))

// 两个 panel + 两个 guide：这样 tab 内会渲染嵌套的 border-card 切换器，
// 也就是 chrome 最重、历史上外层溢出最大（180px）的那种形态。
const SURFACES = [
  { id: 'panel_main', kind: 'panel', mode: 'static', title: 'Panel', url: '/stub-panel.html', available: true },
  { id: 'panel_extra', kind: 'panel', mode: 'static', title: 'Panel 2', url: '/stub-panel.html', available: true },
  { id: 'guide_main', kind: 'guide', mode: 'static', title: 'Guide', url: '/stub-guide.html', available: true },
  { id: 'guide_extra', kind: 'guide', mode: 'static', title: 'Guide 2', url: '/stub-guide.html', available: true },
]

const LOGS = Array.from({ length: 200 }, (_, index) => ({
  timestamp: `2026-01-01T00:00:${String(index % 60).padStart(2, '0')}`,
  level: 'INFO',
  message: `日志第 ${index} 行，用来让内层滚动条有内容可滚。`,
}))

async function stubLayoutPluginApis(page: Page, logs: typeof LOGS = LOGS) {
  await stubCorePluginManagerApis(page)
  // 后注册的 route 先生效：覆盖 helpers 里的空插件列表
  await page.route('**/plugins?*', (route) =>
    route.fulfill({
      json: {
        plugins: [
          {
            id: PLUGIN_ID,
            name: '布局样本',
            description: '用于布局回归的样本插件。',
            version: '1.0.0',
            type: 'plugin',
            status: 'running',
            entries: MANY_ENTRIES,
          },
        ],
        message: '',
      },
    }),
  )
  await page.route('**/plugin/status', (route) =>
    route.fulfill({ json: { plugins: { [PLUGIN_ID]: 'running' } } }),
  )
  await page.route(`**/plugin/${PLUGIN_ID}/surfaces*`, (route) =>
    route.fulfill({ json: { surfaces: SURFACES, warnings: [] } }),
  )
  await page.route(`**/plugin/${PLUGIN_ID}/logs*`, (route) =>
    route.fulfill({
      json: { plugin_id: PLUGIN_ID, logs, total_lines: logs.length, returned_lines: logs.length },
    }),
  )
  await page.route('**/stub-panel.html', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<html><body>panel</body></html>' }),
  )
  await page.route('**/stub-guide.html', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<html><body>guide</body></html>' }),
  )
}

async function openDetail(page: Page, viewportHeight: number, logs: typeof LOGS = LOGS) {
  await page.setViewportSize({ width: 1000, height: viewportHeight })
  await stubLayoutPluginApis(page, logs)
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/${PLUGIN_ID}`, { waitUntil: 'load' })
  await page.waitForSelector('[data-yui-guide-id="plugin-detail-tabs"]', { timeout: 15_000 })
  await page.evaluate(() => document.getElementById('plugin-manager-boot-shell')?.remove())
}

async function measure(page: Page) {
  return page.evaluate(() => {
    const main = document.querySelector('.app-main') as HTMLElement
    // 必须用子代选择器：多 surface 时 tab 内部还会渲染一层嵌套的 .el-tab-pane，
    // 后代匹配会取到嵌套里那个隐藏 pane（高度 0）。
    const pane = document.querySelector(
      '[data-yui-guide-id="plugin-detail-tabs"] > .el-tabs__content > .el-tab-pane:not([style*="display: none"])',
    ) as HTMLElement | null
    const panel = (pane?.querySelector('.hosted-surface-frame, .log-viewer') ?? null) as HTMLElement | null
    const box = (el: HTMLElement | null) =>
      el ? { top: Math.round(el.getBoundingClientRect().top), bottom: Math.round(el.getBoundingClientRect().bottom) } : null
    const mainBox = main.getBoundingClientRect()
    return {
      outerOverflow: main.scrollHeight - main.clientHeight,
      main: { top: Math.round(mainBox.top), bottom: Math.round(mainBox.bottom) },
      pane: box(pane),
      panel: box(panel),
      // 面板的直接宿主盒（.surface-section / plugin-detail-logs）。"贴合容器"必须拿它当参照，
      // 拿 .app-main 或 pane 当参照会被中间的卡片/工具栏 chrome 吃掉差值。
      panelHost: box(panel?.parentElement ?? null),
      panelScrollable: panel
        ? Array.from(panel.querySelectorAll('*')).some(
            (node) => node.scrollHeight > (node as HTMLElement).clientHeight + 1,
          )
        : false,
    }
  })
}

async function openTab(page: Page, name: string) {
  await page.click(`#tab-${name}`)
  await page.waitForTimeout(400)
}

// ── 撑满型 tab：高度来自容器，因此外层不该出现滚动条 ────────────────────────
// 尺寸矩阵：1340 = 用户当前窗口；768 = 常见笔记本；2000 = 超高窗口（超过旧实现那个
// 固定 1200px 上限，钉住面板随窗口继续长高而不是停在 1200px）。矮窗下限那档（620/400）
// 是下面单独的用例。
for (const height of [1340, 768, 2000]) {
  for (const tab of ['panel', 'guide', 'logs']) {
  test(`[h=${height}] 撑满型 tab「${tab}」不产生外层滚动条，且面板贴合容器`, async ({ page }) => {
    await openDetail(page, height)
    await openTab(page, tab)
    const m = await measure(page)

    expect(m.outerOverflow, `h=${height} tab=${tab} 外层 .app-main 溢出 ${m.outerOverflow}px（面板越界了？）`).toBe(0)
    expect(m.panel, `h=${tab} 没渲染出面板`).not.toBeNull()
    expect(m.panel!.bottom, `h=${height} tab=${tab} 面板底部超出 .app-main`).toBeLessThanOrEqual(m.main.bottom)
    // 可用性：常见窗口下面板不该小到没法用（日志面板自身工具栏就要 ~140px）
    expect(m.panel!.bottom - m.panel!.top, `h=${height} tab=${tab} 面板过矮`).toBeGreaterThanOrEqual(tab === 'guide' ? 240 : 300)
    // 真正的"贴合"：面板顶/底都要顶到宿主盒的两端。之前只断言"不超出 .app-main"，
    // 于是面板在 1600/2000 高的窗口里停在 1200px（下面空 103 / 503px）也能全绿。
    expect(m.panelHost, `h=${height} tab=${tab} 取不到面板宿主盒`).not.toBeNull()
    expect(m.panel!.top - m.panelHost!.top, `h=${height} tab=${tab} 面板上方有缝`).toBeLessThanOrEqual(1)
    expect(m.panelHost!.bottom - m.panel!.bottom, `h=${height} tab=${tab} 面板下方空 ${m.panelHost!.bottom - m.panel!.bottom}px（没填满容器）`).toBeLessThanOrEqual(1)
  })
  }
}

test('日志 tab 的内层滚动条仍然可用', async ({ page }) => {
  await openDetail(page, 1340)
  await openTab(page, 'logs')
  const m = await measure(page)

  expect(m.panelScrollable, '日志列表不再可滚（固定高度被 flex 吃掉？）').toBe(true)
})

// ── 长内容 tab：必须继续由 .app-main 滚动，不能被压到一屏后被裁 ──────────────
test('长内容 tab（入口点）仍由外层滚动，内容没有被裁', async ({ page }) => {
  await openDetail(page, 1340)
  await openTab(page, 'entries')
  const m = await measure(page)

  expect(m.outerOverflow, '长内容 tab 的外层不再可滚 —— 内容多半被压扁/裁掉了').toBeGreaterThan(0)

  // 滚到底之后，最后一个入口点必须真的可见（可达 = 没被 overflow:hidden 裁掉）
  const lastVisible = await page.evaluate(() => {
    const main = document.querySelector('.app-main') as HTMLElement
    main.scrollTop = main.scrollHeight
    const last = document.querySelector('[data-yui-guide-id="plugin-detail-entries"]') as HTMLElement
    return last.getBoundingClientRect().bottom <= main.getBoundingClientRect().bottom + 1
  })
  expect(lastVisible, '滚到底也看不到入口点列表末尾 —— 内容被裁了').toBe(true)
})

// ── 矮窗口：面板会撞到 min-height 下限，此时必须"可滚动到达"而不是被裁 ──────
// 400px 窗口：可用高度 268px < 宿主下限 440px → 下限生效、由页面滚动兜住
test('[h=400] 触发宿主下限时，面板底部仍可通过滚动到达（未被裁）', async ({ page }) => {
  await openDetail(page, 400)
  await openTab(page, 'logs')
  const m = await measure(page)

  expect(m.outerOverflow, '矮窗口下外层完全不可滚 —— 撞到 min-height 下限后面板被裁了').toBeGreaterThan(0)

  const panelBottomReachable = await page.evaluate(() => {
    const main = document.querySelector('.app-main') as HTMLElement
    main.scrollTop = main.scrollHeight
    const panel = document.querySelector('.log-viewer') as HTMLElement
    return panel.getBoundingClientRect().bottom <= main.getBoundingClientRect().bottom + 1
  })
  expect(panelBottomReachable, '滚到底也看不到日志面板底部').toBe(true)
})

// ── 矮窗：面板会变小（撞宿主下限），但必须仍然可用、且底部可达 ─────────────
test('[h=620] 矮窗下面板仍可用且底部可达', async ({ page }) => {
  await openDetail(page, 620)
  await openTab(page, 'logs')
  const m = await measure(page)

  expect(m.panel, '没渲染出面板').not.toBeNull()
  expect(m.panel!.bottom - m.panel!.top, '矮窗下面板被压到不可用').toBeGreaterThanOrEqual(200)
  const reachable = await page.evaluate(() => {
    const main = document.querySelector('.app-main') as HTMLElement
    main.scrollTop = main.scrollHeight
    const panel = document.querySelector('.log-viewer') as HTMLElement
    return panel.getBoundingClientRect().bottom <= main.getBoundingClientRect().bottom + 1
  })
  expect(reachable, '矮窗下滚到底也看不到面板').toBe(true)
})

// ── 内容形态：空日志（面板不该塌陷）──────────────────────────────────────
test('空日志时面板仍贴合容器，不塌陷', async ({ page }) => {
  await openDetail(page, 1340, [])
  await openTab(page, 'logs')
  const m = await measure(page)

  expect(m.outerOverflow, '空内容反而产生了外层滚动').toBe(0)
  expect(m.panel!.bottom - m.panel!.top, '空日志把面板弄塌了').toBeGreaterThanOrEqual(300)
})

// ── 内容形态：超长不换行的单行日志（考验横向溢出）─────────────────────────
test('超长单行日志不撑破布局（无横向溢出、无外层滚动）', async ({ page }) => {
  const longLine = 'X'.repeat(4000)
  await openDetail(page, 1340, [{ timestamp: '2026-01-01T00:00:00', level: 'INFO', message: longLine }])
  await openTab(page, 'logs')
  const m = await measure(page)
  const horizontal = await page.evaluate(() => {
    const main = document.querySelector('.app-main') as HTMLElement
    return main.scrollWidth - main.clientWidth
  })

  expect(horizontal, `超长单行把页面撑出了横向滚动 ${horizontal}px`).toBeLessThanOrEqual(1)
  expect(m.outerOverflow, '超长单行产生了纵向外层滚动').toBe(0)
})
