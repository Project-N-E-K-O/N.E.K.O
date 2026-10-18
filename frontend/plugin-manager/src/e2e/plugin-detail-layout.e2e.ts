import { expect, test, type Page, type Route } from '@playwright/test'
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

for (const sample of [
  { locale: 'pt', phase: 'resources', width: 280 },
  { locale: 'ru', phase: 'context', width: 320 },
] as const) {
  test(`[slow narrow] ${sample.locale} ${sample.phase} 短面板提示不重叠，顶部和日志按钮可滚动到达`, async ({ page }) => {
    await page.setViewportSize({ width: 640, height: 620 })
    await page.addInitScript((locale) => window.localStorage.setItem('locale', locale), sample.locale)
    await page.clock.install()
    await stubLayoutPluginApis(page)
    await page.route(`**/plugin/${PLUGIN_ID}/surfaces*`, (route) => route.fulfill({ json: {
      surfaces: [{ id: 'panel_main', kind: 'panel', mode: 'hosted-tsx', entry: 'ui/panel.tsx', available: true }],
      warnings: [],
    } }))
    let release!: () => void
    const pending = new Promise<void>((resolve) => { release = resolve })
    let waiting!: () => void
    const requestWaiting = new Promise<void>((resolve) => { waiting = resolve })
    const heldHandlers: Array<Promise<void>> = []
    const fulfill = async (route: Route, phase: typeof sample.phase, payload: unknown) => {
      let finished!: () => void
      const completion = new Promise<void>((resolve) => { finished = resolve })
      if (sample.phase === phase) { heldHandlers.push(completion); waiting() }
      try {
        if (sample.phase === phase) await pending
        await route.fulfill({ json: payload })
      } finally { finished() }
    }
    await page.route(`**/plugin/${PLUGIN_ID}/hosted-ui/source*`, (route) => fulfill(route, 'resources', {
      source: 'export default function Panel() { return <div>ready</div> }', dependencies: [],
    }))
    await page.route(`**/plugin/${PLUGIN_ID}/hosted-ui/context*`, (route) => fulfill(route, 'context', {
      state: {}, actions: [], entries: [],
    }))
    try {
      await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/${PLUGIN_ID}`, { waitUntil: 'load' })
      await page.waitForSelector('[data-yui-guide-id="plugin-detail-tabs"]', { timeout: 15_000 })
      await page.evaluate(() => document.getElementById('plugin-manager-boot-shell')?.remove())
      await page.locator('#tab-panel').click()
      await expect(page.locator('#tab-panel')).toHaveAttribute('aria-selected', 'true')
      // Constrain the real PluginDetail host, rather than mounting a standalone
      // copy of HostedSurfaceFrame. Short embedded hosts need inner scrolling.
      await page.addStyleTag({ content: `[data-yui-guide-id="plugin-detail-panel"] { width: ${sample.width}px; height: 300px !important; flex: 0 0 300px !important; min-height: 0 !important; }` })
      await requestWaiting
      await page.clock.runFor(3000)
      const placeholder = page.getByTestId('surface-placeholder')
      await expect(page.getByTestId('surface-load-slow')).toBeVisible()
      await expect(placeholder).toHaveAttribute('data-load-phase', sample.phase)
      const boxes = await placeholder.evaluate((element) => {
        const slow = element.querySelector('[data-testid="surface-load-slow"]')!.getBoundingClientRect()
        const actions = element.querySelector('.hosted-surface-frame__load-actions')!.getBoundingClientRect()
        return { slowBottom: slow.bottom, actionsTop: actions.top }
      })
      expect(boxes.slowBottom, '换行后的慢加载文案与按钮区域重叠').toBeLessThanOrEqual(boxes.actionsTop + 1)
      const titleReachable = await placeholder.evaluate((element) => {
        element.scrollTop = 0
        const box = element.getBoundingClientRect()
        const title = element.querySelector('h3')!.getBoundingClientRect()
        return title.top >= box.top && title.bottom <= box.bottom
      })
      expect(titleReachable, '滚动到顶部仍看不到阶段标题').toBe(true)
      await placeholder.evaluate((element) => { element.scrollTop = element.scrollHeight })
      const logsReachable = await page.getByTestId('surface-open-logs').evaluate((button) => {
        const box = button.closest('[data-testid="surface-placeholder"]')!.getBoundingClientRect()
        const target = button.getBoundingClientRect()
        return target.top >= box.top && target.bottom <= box.bottom && target.left >= box.left && target.right <= box.right
      })
      expect(logsReachable, '滚动到底仍无法到达日志按钮').toBe(true)
      await page.getByTestId('surface-open-logs').click()
      await expect(page.locator('#tab-logs')).toHaveAttribute('aria-selected', 'true')
      await expect(page.locator('.log-viewer')).toBeVisible()
    } finally {
      release()
      await Promise.all(heldHandlers)
    }
  })
}

test('[phase actions] 阶段切换保留已出现按钮、焦点和日志入口', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 768 })
  await page.clock.install()
  await stubLayoutPluginApis(page)
  await page.route(`**/plugin/${PLUGIN_ID}/surfaces*`, (route) => route.fulfill({ json: {
    surfaces: [{ id: 'panel_main', kind: 'panel', mode: 'hosted-tsx', entry: 'ui/panel.tsx', available: true }],
    warnings: [],
  } }))
  let releaseSource!: () => void
  let releaseContext!: () => void
  const source = new Promise<void>((resolve) => { releaseSource = resolve })
  const context = new Promise<void>((resolve) => { releaseContext = resolve })
  let sourceWaiting!: () => void
  let contextWaiting!: () => void
  const sourceRequested = new Promise<void>((resolve) => { sourceWaiting = resolve })
  const contextRequested = new Promise<void>((resolve) => { contextWaiting = resolve })
  const handlers: Array<Promise<void>> = []
  const hold = async (route: Route, wait: Promise<void>, ready: () => void, payload: unknown) => {
    let finished!: () => void
    handlers.push(new Promise<void>((resolve) => { finished = resolve }))
    ready()
    try { await wait; await route.fulfill({ json: payload }) } finally { finished() }
  }
  await page.route(`**/plugin/${PLUGIN_ID}/hosted-ui/source*`, (route) => hold(route, source, sourceWaiting, {
    source: 'export default function Panel() { return <div>ready</div> }', dependencies: [],
  }))
  await page.route(`**/plugin/${PLUGIN_ID}/hosted-ui/context*`, (route) => hold(route, context, contextWaiting, {
    state: {}, actions: [], entries: [],
  }))
  try {
    await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/${PLUGIN_ID}`, { waitUntil: 'load' })
    await page.locator('#tab-panel').click()
    await sourceRequested
    await page.clock.runFor(5000)
    const retry = page.getByTestId('surface-retry')
    await expect(retry).toBeVisible()
    const originalButton = await retry.elementHandle()
    await retry.focus()
    releaseSource()
    await contextRequested
    await expect(page.getByTestId('surface-placeholder')).toHaveAttribute('data-load-phase', 'context')
    await expect(page.getByTestId('surface-load-slow')).toHaveCount(0)
    expect(await originalButton!.evaluate((button) => button.isConnected)).toBe(true)
    await expect(retry).toBeFocused()
    await page.getByTestId('surface-open-logs').click()
    await expect(page.locator('#tab-logs')).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.log-viewer')).toBeVisible()
  } finally {
    releaseSource()
    releaseContext()
    await Promise.all(handlers)
  }
})

test('[retry narrow] ru 短面板从错误页重试时重置滚动位置，阶段标题保持可见', async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 620 })
  await page.addInitScript(() => window.localStorage.setItem('locale', 'ru'))
  await page.clock.install()
  await stubLayoutPluginApis(page)
  await page.route(`**/plugin/${PLUGIN_ID}/surfaces*`, (route) => route.fulfill({ json: {
    surfaces: [{ id: 'panel_main', kind: 'panel', mode: 'hosted-tsx', entry: 'ui/panel.tsx', available: true }],
    warnings: [],
  } }))
  let sourceRequests = 0
  let contextRequests = 0
  await page.route(`**/plugin/${PLUGIN_ID}/hosted-ui/source*`, (route) => {
    sourceRequests += 1
    return route.fulfill({ json: { source: 'export default function Panel() { return <div>ready</div> }', dependencies: [] } })
  })
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  let waiting!: () => void
  const retryWaiting = new Promise<void>((resolve) => { waiting = resolve })
  const heldHandlers: Array<Promise<void>> = []
  await page.route(`**/plugin/${PLUGIN_ID}/hosted-ui/context*`, async (route) => {
    contextRequests += 1
    if (contextRequests === 1) {
      await route.fulfill({ status: 500, json: { detail: 'Request failed for https://api.example.test/v1/chat/completions?deployment=independent-research-agent-local-build' } })
      return
    }
    let finished!: () => void
    heldHandlers.push(new Promise<void>((resolve) => { finished = resolve }))
    waiting()
    try {
      await pending
      await route.fulfill({ json: { state: {}, actions: [], entries: [] } })
    } finally { finished() }
  })
  try {
    await page.goto(`${PREVIEW_ORIGIN}/ui/plugins/${PLUGIN_ID}`, { waitUntil: 'load' })
    await page.waitForSelector('[data-yui-guide-id="plugin-detail-tabs"]', { timeout: 15_000 })
    await page.evaluate(() => document.getElementById('plugin-manager-boot-shell')?.remove())
    await page.locator('#tab-panel').click()
    await page.addStyleTag({ content: '[data-yui-guide-id="plugin-detail-panel"] { width: 280px; height: 300px !important; flex: 0 0 300px !important; min-height: 0 !important; }' })
    await expect(page.getByTestId('surface-reload-page')).toBeVisible()
    const oldPlaceholder = await page.getByTestId('surface-placeholder').elementHandle()
    expect(oldPlaceholder).not.toBeNull()
    await oldPlaceholder!.evaluate((element) => { element.scrollTop = element.scrollHeight })
    expect(await oldPlaceholder!.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
    await page.getByTestId('surface-retry').click()
    await retryWaiting
    expect(sourceRequests).toBe(2)
    expect(contextRequests).toBe(2)
    const placeholder = page.getByTestId('surface-placeholder')
    await expect(placeholder).toHaveAttribute('data-load-phase', 'context')
    const titleState = () => placeholder.evaluate((element) => {
      const box = element.getBoundingClientRect()
      const title = element.querySelector('h3')!.getBoundingClientRect()
      return { scrollTop: element.scrollTop, visible: title.top >= box.top && title.bottom <= box.bottom }
    })
    // Do not scroll after retry: this guards the position the user actually sees.
    expect(await titleState()).toEqual({ scrollTop: 0, visible: true })
    expect(await oldPlaceholder!.evaluate((element) => element.isConnected), '重试应替换旧占位 DOM，避免继承错误页的滚动位置').toBe(false)
    const retryPlaceholder = await placeholder.elementHandle()
    await page.clock.runFor(3000)
    await expect(page.getByTestId('surface-load-slow')).toBeVisible()
    expect(await retryPlaceholder!.evaluate((element) => element.isConnected), '同一加载过程切换到慢提示无需替换占位 DOM').toBe(true)
    expect(await titleState()).toEqual({ scrollTop: 0, visible: true })
  } finally {
    release()
    await Promise.all(heldHandlers)
  }
})
