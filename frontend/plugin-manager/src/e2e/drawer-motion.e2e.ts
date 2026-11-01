import { expect, type Page } from '@playwright/test'
import { PREVIEW_ORIGIN, appearanceTest, installCardLayoutFixture } from './plugin-manager-test-helpers'

type Rect = { x: number; y: number; w: number; h: number }
type Frame = { t: number; compact: boolean; running: boolean; rects: (Rect | null)[] }
type Recording = {
  labels: string[]
  frames: Frame[]
  // Index of the first frame rendered after the click that opens or closes the drawer.
  clickFrame: number
  secondClickFrame: number | null
  filterSamples: { t: number; heights: number[] }[]
}
type RecordOptions = {
  toggle: string
  panel: string
  duration: number
  secondClickAt: number | null
  denseFilter?: boolean
}

const COMPONENTS = ['x', 'y', 'w', 'h'] as const
// Layout rounds to subpixels, so settled rects may differ by a fraction of a pixel.
const TOLERANCE = 0.5
// 480ms animation plus up to 96ms of card stagger, then 300ms of settled frames to compare.
const RUN_MS = 1100
const SETTLE_MS = 300
const SNAP_TRAVEL_MIN = 20
const SNAP_SHARE = 0.35
const MARKET_TOGGLE = '[data-yui-guide-id="plugin-list-market-toggle"]'
const MARKET_PANEL = '.plugin-workbench__rail--market .plugin-workbench__rail-inner[data-motion="panel"]'
const PACKAGE_TOGGLE = '.workbench-header [data-yui-guide-id="plugin-list-package-panel-toggle"]'
const PACKAGE_PANEL = '.plugin-workbench__rail--package .plugin-workbench__rail-inner[data-motion="panel"]'

function rectAt(frame: Frame, index: number): Rect | null {
  return frame.rects[index] ?? null
}

// Largest change of any single component between two rects.
function stepSize(from: Rect, to: Rect) {
  return Math.max(...COMPONENTS.map(key => Math.abs(to[key] - from[key])))
}

// Null (not rendered) matches null only; rendered rects match within tolerance.
function sameRect(a: Rect | null, b: Rect | null, tolerance: number) {
  if (!a || !b) return a === b
  return COMPONENTS.every(key => Math.abs(a[key] - b[key]) <= tolerance)
}

// Samples of one element in the frames where it is rendered.
function rendered(recording: Recording, index: number) {
  return recording.frames.flatMap((frame, frameIndex) => {
    const rect = rectAt(frame, index)
    return rect ? [{ frameIndex, rect }] : []
  })
}

function monotonicIssues(recording: Recording, index: number, label: string): string[] {
  const samples = rendered(recording, index)
  if (samples.length < 2) return []
  return COMPONENTS.flatMap(key => {
    const deltas = samples.slice(1).map((sample, i) => sample.rect[key] - samples[i]!.rect[key])
    if (deltas.every(delta => delta >= -TOLERANCE) || deltas.every(delta => delta <= TOLERANCE)) return []
    const net = samples[samples.length - 1]!.rect[key] - samples[0]!.rect[key]
    const breakAt = deltas.findIndex(delta => (net >= 0 ? delta < -TOLERANCE : delta > TOLERANCE))
    return [`${label} ${key} reverses direction at frame ${samples[breakAt + 1]!.frameIndex}`]
  })
}

// Entering elements (not rendered at frame 0) are exempt from the snap check.
function snapIssues(recording: Recording, index: number, label: string): string[] {
  if (rectAt(recording.frames[0]!, index) === null) return []
  const samples = rendered(recording, index)
  const steps = samples.slice(1).map((sample, i) => ({
    frameIndex: sample.frameIndex,
    size: stepSize(samples[i]!.rect, sample.rect),
  }))
  const travel = steps.reduce((sum, step) => sum + step.size, 0)
  if (travel <= SNAP_TRAVEL_MIN) return []
  const largest = steps.reduce((a, b) => (b.size > a.size ? b : a), steps[0] ?? { frameIndex: 0, size: 0 })
  if (largest.size <= SNAP_SHARE * travel + TOLERANCE) return []
  return [
    `${label} jumps ${largest.size.toFixed(1)}px in one frame (frame ${largest.frameIndex}) of ${travel.toFixed(1)}px total travel`,
  ]
}

// The header picks its compact layout as part of the transition's end state, so
// it may flip only in the frame the transition starts (after the panel preload).
function compactIssues(recording: Recording): string[] {
  const flips = recording.frames.flatMap((frame, i) =>
    i > 0 && frame.compact !== recording.frames[i - 1]!.compact ? [i] : []
  )
  const started = recording.frames.findIndex(frame => frame.running)
  if (flips.length <= 1 && flips.every(frame => frame === started)) return []
  return [`header compact class flips at frames [${flips.join(', ')}], expected at most one flip at transition start frame ${started}`]
}

function settledIssues(recording: Recording): string[] {
  const { frames } = recording
  let lastRunning = -1
  frames.forEach((frame, i) => {
    if (frame.running) lastRunning = i
  })
  if (lastRunning < 0) return ['data-motion-running never appeared on .plugin-workbench']
  const settle = lastRunning + 1
  if (settle >= frames.length) return ['animation still running at the end of the recording']
  const reference = frames.find(frame => frame.t >= frames[settle]!.t + SETTLE_MS)
  if (!reference) return [`recording ends less than ${SETTLE_MS}ms after data-motion-running cleared`]
  return recording.labels.flatMap((label, index) => {
    for (let i = settle; i < frames.length; i++) {
      if (!sameRect(rectAt(frames[i]!, index), rectAt(reference, index), TOLERANCE)) {
        return [`${label} still changes at frame ${i} after data-motion-running cleared`]
      }
    }
    return []
  })
}

// Opening or closing must be monotonic, snap-free, and settle into a fixed layout.
function smoothRunIssues(recording: Recording): string[] {
  return [
    ...recording.labels.flatMap((label, index) => [
      ...monotonicIssues(recording, index, label),
      ...snapIssues(recording, index, label),
    ]),
    ...compactIssues(recording),
    ...settledIssues(recording),
  ]
}

// Reversal must not teleport: the forward run up to the reversal is snap-free,
// and every later step is within 2x the forward run's largest step plus 2px.
// The reversal restarts the easing from the drawn position, so it may move as
// fast as the forward run did right after the click, not only as fast as its
// decelerated last steps.
function reversalIssues(recording: Recording, reversalFrame: number): string[] {
  const { frames } = recording
  const step = (frameIndex: number, index: number) => {
    const before = frames[frameIndex - 1]?.rects[index] ?? null
    const after = frames[frameIndex]?.rects[index] ?? null
    return before && after ? stepSize(before, after) : 0
  }
  return recording.labels.flatMap((label, index) => {
    let forward = 0
    let forwardFrame = recording.clickFrame
    let travel = 0
    for (let frame = recording.clickFrame; frame < reversalFrame; frame++) {
      const delta = step(frame, index)
      travel += delta
      if (delta > forward) {
        forward = delta
        forwardFrame = frame
      }
    }
    if (travel > SNAP_TRAVEL_MIN && forward > SNAP_SHARE * travel + TOLERANCE) {
      return [
        `${label} jumps ${forward.toFixed(1)}px at frame ${forwardFrame} before reversal of ${travel.toFixed(1)}px forward travel`,
      ]
    }
    for (let frame = reversalFrame; frame < frames.length; frame++) {
      const delta = step(frame, index)
      if (delta > 2 * forward + 2) {
        return [
          `${label} jumps ${delta.toFixed(1)}px at frame ${frame} after reversal (forward max ${forward.toFixed(1)}px)`,
        ]
      }
    }
    return []
  })
}

// The reversed run must end exactly where it started, with the animation over.
function returnIssues(recording: Recording): string[] {
  const first = recording.frames[0]!
  const last = recording.frames[recording.frames.length - 1]!
  const issues = recording.labels.flatMap((label, index) => {
    const start = rectAt(first, index)
    const end = rectAt(last, index)
    return sameRect(start, end, 1)
      ? []
      : [`${label} ends at ${JSON.stringify(end)}, not its pre-open rect ${JSON.stringify(start)}`]
  })
  if (last.running) issues.push('data-motion-running is still set at the last frame')
  return issues
}

// Seek the real Web Animations in fixed 16ms steps. Wall-clock RAF gaps on a
// busy runner are missing observations, not evidence of a geometry snap.
// The page clock advances the production cleanup timers on that same timeline.
async function recordDrawerMotion(
  page: Page,
  options: RecordOptions,
  onFrame?: (time: number) => Promise<void>,
): Promise<Recording> {
  await page.clock.install()
  try {
    await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000))
    const recorder = await page.evaluateHandle(({ toggle, panel, secondClickAt, denseFilter }) => {
      const workbench = document.querySelector<HTMLElement>('.plugin-workbench')!
      const header = document.querySelector<HTMLElement>('.workbench-header')!
      const tracked: Element[] = []
      const track = (element: Element | null) => {
        if (element && !tracked.includes(element)) tracked.push(element)
      }
      for (const atom of workbench.querySelectorAll('[data-motion]')) {
        if (!atom.parentElement?.closest('[data-motion]') && atom.getClientRects().length)
          track(atom)
      }
      track(document.querySelector(panel))
      for (const surface of [
        ...workbench.querySelectorAll('.grid-section__item [data-reflow="surface"]'),
      ].slice(0, 4)) {
        track(surface)
      }
      const labels = tracked.map((element, index) => {
        const name =
          element.getAttribute('data-yui-guide-id') ||
          element.getAttribute('class')?.split(' ')[0] ||
          element.tagName.toLowerCase()
        return `${name} (${element.getAttribute('data-motion') ?? 'data-reflow=surface'}) #${index}`
      })
      const rectOf = (element: Element): Rect | null => {
        const box = element.getBoundingClientRect()
        return box.width > 0 && box.height > 0
          ? { x: box.x, y: box.y, w: box.width, h: box.height }
          : null
      }
      const clickToggle = () => document.querySelector<HTMLElement>(toggle)!.click()

      const animations: { animation: Animation; started: number; end: number }[] = []
      const nativeAnimate = Element.prototype.animate
      Element.prototype.animate = function (keyframes, timing) {
        const animation = nativeAnimate.call(this, keyframes, timing)
        animation.pause()
        animation.currentTime = 0
        const end = animation.effect!.getComputedTiming().endTime
        if (typeof end !== 'number' || !Number.isFinite(end))
          throw new Error('Expected finite motion timing')
        animations.push({ animation, started: performance.now(), end })
        return animation
      }
      const filter = workbench.querySelector<HTMLElement>('.plugin-workbench__main .wb-filter-bar')!
      const filterRows = [filter, ...filter.querySelectorAll<HTMLElement>(
        '.wb-filter-bar__controls, .wb-filter-bar__footer',
      )]
      const recording: Recording = { labels, frames: [], clickFrame: -1, secondClickFrame: null, filterSamples: [] }
      const start = performance.now()
      let sampledAt = start
      let clickTime = 0
      return {
        recording,
        advance() {
          const now = performance.now()
          // Flex line breaking can change for less than one display frame.
          // Seek every millisecond in the browser, without 480 protocol calls.
          if (denseFilter) {
            for (let time = sampledAt + 1; time <= now; time++) {
              let active = false
              for (const { animation, started, end } of animations) {
                if (animation.playState === 'idle' || animation.playState === 'finished' || time < started) continue
                animation.currentTime = Math.min(time - started, end)
                active = true
              }
              if (active) recording.filterSamples.push({
                t: time - start,
                heights: filterRows.map(element => element.offsetHeight),
              })
            }
            sampledAt = now
          }
          for (const { animation, started, end } of animations) {
            if (animation.playState === 'idle' || animation.playState === 'finished') continue
            const elapsed = performance.now() - started
            if (elapsed >= end) animation.finish()
            else animation.currentTime = elapsed
          }
        },
        transitionStarted() {
          return workbench.hasAttribute('data-motion-running')
        },
        sample() {
          const t = performance.now() - start
          recording.frames.push({
            t,
            compact: header.classList.contains('workbench-header--compact'),
            running: workbench.hasAttribute('data-motion-running'),
            rects: tracked.map(rectOf),
          })
          if (recording.clickFrame < 0) {
            clickToggle()
            recording.clickFrame = recording.frames.length
            clickTime = t
          } else if (
            secondClickAt !== null &&
            recording.secondClickFrame === null &&
            t - clickTime >= secondClickAt
          ) {
            clickToggle()
            recording.secondClickFrame = recording.frames.length
          }
        },
        restore() {
          Element.prototype.animate = nativeAnimate
          for (const { animation } of animations)
            if (animation.playState === 'paused') animation.play()
        },
      }
    }, options)
    try {
      for (let time = 0; time <= options.duration + 16; time += 16) {
        await recorder.evaluate((state) => state.advance())
        // Let the production finished handlers retire animations before sampling.
        await recorder.evaluate((state) => state.sample())
        // A first open loads its panel module over the real network before the
        // transition starts. Hold the page clock until it does, or a slow chunk
        // spends the recording's virtual time before anything moves.
        if (time === 0) {
          await expect
            .poll(() => recorder.evaluate((state) => state.transitionStarted()), {
              message: 'drawer transition started after the click',
            })
            .toBe(true)
        }
        await onFrame?.(time)
        if (time < options.duration) await page.clock.runFor(16)
      }
      return await recorder.evaluate((state) => state.recording)
    } finally {
      await recorder.evaluate((state) => state.restore())
      await recorder.dispose()
    }
  } finally {
    await page.clock.resume()
  }
}

async function openPluginWorkbench(page: Page, width = 1600) {
  await installCardLayoutFixture(page)
  // The fixture reports a newer version; keep the boot update popup off the header.
  await page.route('**/market/catalog/api/v1/plugins/latest-versions?*', route =>
    route.fulfill({ json: { items: [{ plugin_id: '42', version: '1.0.0' }] } })
  )
  await installMarketCatalog(page)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  // At this width opening a drawer flips the header to its compact layout.
  await page.setViewportSize({ width, height: 1000 })
  // The boot update check starts a while after mount; wait for its request so
  // its label swap cannot land inside a recording.
  const bootCheck = page.waitForRequest('**/market/catalog/api/v1/plugins/latest-versions?*')
  await page.goto(`${PREVIEW_ORIGIN}/ui/plugins`)
  await expect(page.locator('.plugin-card')).toHaveCount(8)
  await expect(page.locator('.plugin-workbench')).toBeVisible()
  await bootCheck
  await expect(page.locator('[data-yui-guide-id="plugin-list-update-check"]')).not.toHaveClass(/is-loading/)
  // Cards enter with their own animation on load; record only from a settled layout.
  await page.waitForFunction(() => document.getAnimations().length === 0)
}

// Let a fully opened drawer finish its open animation before recording the close.
async function openDrawerFully(page: Page, toggle: string, panel: string) {
  await page.locator(toggle).click()
  await expect(page.locator(panel)).toBeVisible()
  await page.waitForTimeout(1000)
}

async function installMarketCatalog(page: Page) {
  await page.route('**/market/catalog/api/v1/plugins?*', route => route.fulfill({ json: {
    items: [
      { id: 101, slug: 'fixture-one', name: 'Fixture One', description: 'First catalog item', is_featured: true },
      { id: 102, slug: 'fixture-two', name: 'Fixture Two', description: 'Second catalog item', is_featured: false },
    ],
    total: 2, page: 1, page_size: 100, total_pages: 1,
  } }))
}

function expectStableFilterRows(recording: Recording) {
  expect(recording.filterSamples.length, 'dense samples cover the running transition').toBeGreaterThanOrEqual(480)
  const final = recording.filterSamples.at(-1)!.heights
  const changes = recording.filterSamples.filter(sample =>
    sample.heights.some((height, index) => Math.abs(height - final[index]!) > TOLERANCE),
  )
  expect(changes, 'filter, controls and footer keep their laid-out heights throughout the animation').toEqual([])
}

async function expectMotionCleanedUp(page: Page) {
  await expect(page.locator('.plugin-workbench')).not.toHaveAttribute('data-motion-running')
  // The package panel retains a finished CSS entrance with fill: both.
  // Layout-owned Web Animations must be cancelled, independently of that CSS.
  expect(await page.evaluate(() => document.getAnimations().filter(animation =>
    !(animation instanceof CSSAnimation) && !(animation instanceof CSSTransition),
  ).map(animation => (animation.effect as KeyframeEffect).target?.nodeName))).toEqual([])
  expect(await page.locator('.plugin-workbench__main .wb-filter-bar__input').evaluate(element =>
    element.getAttribute('style'),
  )).toBeNull()
}

appearanceTest('market drawer opens with monotonic, snap-free geometry that settles', async ({ page }) => {
  await openPluginWorkbench(page)
  const recording = await recordDrawerMotion(page, {
    toggle: MARKET_TOGGLE,
    panel: MARKET_PANEL,
    duration: RUN_MS,
    secondClickAt: null,
  })
  expect(smoothRunIssues(recording), 'market open geometry').toEqual([])
})

appearanceTest('market drawer closes with monotonic, snap-free geometry that settles', async ({ page }) => {
  await openPluginWorkbench(page)
  await openDrawerFully(page, MARKET_TOGGLE, MARKET_PANEL)
  const recording = await recordDrawerMotion(page, {
    toggle: MARKET_TOGGLE,
    panel: MARKET_PANEL,
    duration: RUN_MS,
    secondClickAt: null,
  })
  expect(smoothRunIssues(recording), 'market close geometry').toEqual([])
})

appearanceTest('reversing the market drawer mid-open stays continuous and returns to the closed layout', async ({ page }) => {
  await openPluginWorkbench(page)
  const recording = await recordDrawerMotion(page, {
    toggle: MARKET_TOGGLE,
    panel: MARKET_PANEL,
    duration: RUN_MS,
    secondClickAt: 200,
  })
  const reversal = recording.secondClickFrame
  expect(reversal, 'second click ran').not.toBeNull()
  expect(recording.frames[reversal!]!.running, 'second click landed mid-animation').toBe(true)
  expect(reversalIssues(recording, reversal!), 'reversal continuity').toEqual([])
  expect(returnIssues(recording), 'reversal end state').toEqual([])
})

appearanceTest('a second toggle click during the first panel preload cancels the open', async ({ page }) => {
  let releasePanel!: () => void
  const panelHeld = new Promise<void>(resolve => { releasePanel = resolve })
  await page.route('**/assets/MarketPanel-*.js', async route => {
    await panelHeld
    await route.continue()
  })
  await openPluginWorkbench(page)
  const panelRequested = page.waitForRequest('**/assets/MarketPanel-*.js')
  await page.locator(MARKET_TOGGLE).click()
  await panelRequested
  await page.locator(MARKET_TOGGLE).click()
  releasePanel()
  await page.waitForResponse('**/assets/MarketPanel-*.js')
  await page.waitForTimeout(1000)
  await expect(page.locator(MARKET_PANEL)).toBeHidden()
  await expect(page.locator('.plugin-workbench')).not.toHaveClass(/plugin-workbench--market-open/)
})

appearanceTest('package drawer opens with monotonic, snap-free geometry that settles', async ({ page }) => {
  await openPluginWorkbench(page)
  const recording = await recordDrawerMotion(page, {
    toggle: PACKAGE_TOGGLE,
    panel: PACKAGE_PANEL,
    duration: RUN_MS,
    secondClickAt: null,
  })
  expect(smoothRunIssues(recording), 'package open geometry').toEqual([])
})

appearanceTest('package drawer closes with monotonic, snap-free geometry that settles', async ({ page }) => {
  await openPluginWorkbench(page)
  await openDrawerFully(page, PACKAGE_TOGGLE, PACKAGE_PANEL)
  const recording = await recordDrawerMotion(page, {
    toggle: PACKAGE_TOGGLE,
    panel: PACKAGE_PANEL,
    duration: RUN_MS,
    secondClickAt: null,
  })
  expect(smoothRunIssues(recording), 'package close geometry').toEqual([])
})

appearanceTest(
  'fixed-step sampling remains strict when the browser is CPU throttled',
  async ({ page }) => {
    await openPluginWorkbench(page)
    const session = await page.context().newCDPSession(page)
    try {
      await session.send('Emulation.setCPUThrottlingRate', { rate: 6 })
      const recording = await recordDrawerMotion(page, {
        toggle: PACKAGE_TOGGLE,
        panel: PACKAGE_PANEL,
        duration: RUN_MS,
        secondClickAt: null,
      })
      expect(
        recording.frames
          .slice(1)
          .every((frame, index) => frame.t - recording.frames[index]!.t === 16)
      ).toBe(true)
      expect(smoothRunIssues(recording)).toEqual([])
    } finally {
      await session.send('Emulation.setCPUThrottlingRate', { rate: 1 })
      await session.detach()
    }
  }
)

appearanceTest(
  'fixed-step sampling holds the clock while a slow first panel module loads',
  async ({ page }) => {
    // Far longer than the recording takes in real time to reach its 200ms second click.
    let panelRequests = 0
    await page.route('**/assets/MarketPanel-*.js', async route => {
      panelRequests++
      await new Promise(resolve => setTimeout(resolve, 500))
      await route.fallback()
    })
    await openPluginWorkbench(page)
    // A failed preload still opens the drawer, so prove the slow module really loaded.
    const panelResponse = page.waitForResponse('**/assets/MarketPanel-*.js')
    const recording = await recordDrawerMotion(page, {
      toggle: MARKET_TOGGLE,
      panel: MARKET_PANEL,
      duration: RUN_MS,
      secondClickAt: 200,
    })
    expect(panelRequests, 'MarketPanel module request was delayed').toBe(1)
    expect((await panelResponse).ok(), 'MarketPanel module loaded').toBe(true)
    // A module that loads but fails to evaluate renders the error placeholder instead.
    await expect(
      page.locator(`${MARKET_PANEL} [data-yui-guide-id="market-panel-root"]`),
      'MarketPanel component rendered'
    ).toBeAttached()
    const reversal = recording.secondClickFrame
    expect(reversal, 'second click ran').not.toBeNull()
    expect(recording.frames[reversal!]!.running, 'second click landed mid-animation').toBe(true)
    expect(reversalIssues(recording, reversal!), 'reversal continuity').toEqual([])
    expect(returnIssues(recording), 'reversal end state').toEqual([])
  }
)

appearanceTest(
  'fixed-step sampling still rejects genuinely discontinuous browser animations',
  async ({ page }) => {
    await openPluginWorkbench(page)
    const original = await page.evaluateHandle(() => Element.prototype.animate)
    await page.evaluate((nativeAnimate) => {
      Element.prototype.animate = function (keyframes, timing) {
        const options = typeof timing === 'number' ? { duration: timing } : timing
        return nativeAnimate.call(this, keyframes, { ...options, easing: 'steps(1, end)' })
      }
    }, original)
    try {
      const recording = await recordDrawerMotion(page, {
        toggle: PACKAGE_TOGGLE,
        panel: PACKAGE_PANEL,
        duration: RUN_MS,
        secondClickAt: null,
      })
      expect(smoothRunIssues(recording).some((issue) => issue.includes('jumps'))).toBe(true)
    } finally {
      await page.evaluate((nativeAnimate) => {
        Element.prototype.animate = nativeAnimate
      }, original)
      await original.dispose()
    }
  }
)

appearanceTest.describe('filter bar layout and interaction', () => {
  appearanceTest.use({ appLocale: 'zh-CN' })

  for (const width of [1472, 1280]) {
    appearanceTest(`filter rows stay stable at ${width}px during market open and close`, async ({ page }) => {
      await openPluginWorkbench(page, width)
      for (const direction of ['open', 'close']) {
        const recording = await recordDrawerMotion(page, {
          toggle: MARKET_TOGGLE, panel: MARKET_PANEL, duration: RUN_MS,
          secondClickAt: null, denseFilter: true,
        })
        expectStableFilterRows(recording)
        const input = recording.labels.findIndex(label => label.startsWith('wb-filter-bar__input '))
        expect(input).toBeGreaterThanOrEqual(0)
        expect(monotonicIssues(recording, input, `${direction} input`)).toEqual([])
        await expectMotionCleanedUp(page)
      }
    })
  }

  appearanceTest('filter rows stay stable when the market drawer reverses mid-open', async ({ page }) => {
    await openPluginWorkbench(page, 1472)
    const recording = await recordDrawerMotion(page, {
      toggle: MARKET_TOGGLE, panel: MARKET_PANEL, duration: RUN_MS,
      secondClickAt: 200, denseFilter: true,
    })
    expectStableFilterRows(recording)
    expect(recording.secondClickFrame).not.toBeNull()
    expect(reversalIssues(recording, recording.secondClickFrame!)).toEqual([])
    expect(returnIssues(recording)).toEqual([])
    await expectMotionCleanedUp(page)
  })

  appearanceTest('filter rows and the open layout survive a reversal mid-close', async ({ page }) => {
    await openPluginWorkbench(page, 1472)
    await openDrawerFully(page, MARKET_TOGGLE, MARKET_PANEL)
    const recording = await recordDrawerMotion(page, {
      toggle: MARKET_TOGGLE, panel: MARKET_PANEL, duration: RUN_MS,
      secondClickAt: 200, denseFilter: true,
    })
    expectStableFilterRows(recording)
    expect(recording.secondClickFrame).not.toBeNull()
    expect(reversalIssues(recording, recording.secondClickFrame!)).toEqual([])
    expect(returnIssues(recording)).toEqual([])
    await expectMotionCleanedUp(page)
  })

  appearanceTest('switching from market to package keeps filter rows and input focus stable', async ({ page }) => {
    await openPluginWorkbench(page, 1472)
    await openDrawerFully(page, MARKET_TOGGLE, MARKET_PANEL)
    const input = page.locator('.plugin-workbench__main .wb-filter-bar').getByRole('textbox')
    await input.focus()
    const recording = await recordDrawerMotion(page, {
      toggle: PACKAGE_TOGGLE, panel: PACKAGE_PANEL, duration: RUN_MS,
      secondClickAt: null, denseFilter: true,
    }, async time => {
      if ([128, 256, 400].includes(time)) await expect(input).toBeFocused()
    })
    expectStableFilterRows(recording)
    await expect(page.locator(MARKET_PANEL)).toBeHidden()
    await expect(page.locator(PACKAGE_PANEL)).toBeVisible()
    await expectMotionCleanedUp(page)
  })

  appearanceTest('input focus and selection survive both drawer directions and the clear button still works', async ({ page }) => {
    await openPluginWorkbench(page, 1472)
    const bar = page.locator('.plugin-workbench__main .wb-filter-bar')
    const input = bar.getByRole('textbox')
    await input.fill('猫娘小游戏')
    await page.waitForFunction(() => document.getAnimations().length === 0)
    for (const direction of ['open', 'close']) {
      await input.focus()
      await input.evaluate(element => (element as HTMLInputElement).setSelectionRange(1, 3))
      await recordDrawerMotion(page, {
        toggle: MARKET_TOGGLE, panel: MARKET_PANEL, duration: RUN_MS, secondClickAt: null,
      }, async time => {
        if (![128, 256, 400].includes(time)) return
        await expect(input, `${direction} at ${time}ms`).toBeFocused()
        await expect(input).toHaveValue('猫娘小游戏')
        expect(await input.evaluate(element => {
          const input = element as HTMLInputElement
          return [input.selectionStart, input.selectionEnd]
        })).toEqual([1, 3])
      })
      await expectMotionCleanedUp(page)
    }
    await input.hover()
    await bar.locator('.el-input__clear').click()
    await expect(input).toHaveValue('')
    await expect(page.locator('.plugin-workbench__main .plugin-card')).toHaveCount(8)
  })

  appearanceTest('composition commits and clear clicks work while the market drawer moves', async ({ page }) => {
    await openPluginWorkbench(page, 1472)
    const bar = page.locator('.plugin-workbench__main .wb-filter-bar')
    const input = bar.getByRole('textbox')
    const cards = page.locator('.plugin-workbench__main .plugin-card')
    await input.focus()
    await input.evaluate(element => {
      const input = element as HTMLInputElement
      input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
      input.value = '猫'
      input.dispatchEvent(new InputEvent('input', {
        bubbles: true, data: '猫', inputType: 'insertCompositionText', isComposing: true,
      }))
    })
    await recordDrawerMotion(page, {
      toggle: MARKET_TOGGLE, panel: MARKET_PANEL, duration: RUN_MS, secondClickAt: null,
    }, async time => {
      if (time === 128) {
        await expect(input).toBeFocused()
        await expect(input).toHaveValue('猫')
        await expect(cards).toHaveCount(8)
      }
      if (time === 256) {
        await input.evaluate(element => element.dispatchEvent(
          new CompositionEvent('compositionend', { bubbles: true, data: '猫' }),
        ))
      }
      if (time === 400) {
        await expect(cards).toHaveCount(1)
        const box = (await input.boundingBox())!
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
        const clear = bar.locator('.el-input__clear')
        await expect(clear).toBeVisible()
        const clearBox = (await clear.boundingBox())!
        await page.mouse.click(clearBox.x + clearBox.width / 2, clearBox.y + clearBox.height / 2)
        await expect(input).toHaveValue('')
      }
    })
    await expect(cards).toHaveCount(8)
    await expectMotionCleanedUp(page)
  })

  appearanceTest('exposed rules remain clickable before either transition ends and Tab returns to search', async ({ page }) => {
    await openPluginWorkbench(page, 1472)
    const trigger = page.locator('[data-yui-guide-id="plugin-list-filter-rules"]')
    const input = page.locator('.plugin-workbench__main .wb-filter-bar').getByRole('textbox')
    for (const direction of ['open', 'close']) {
      await recordDrawerMotion(page, {
        toggle: MARKET_TOGGLE, panel: MARKET_PANEL, duration: RUN_MS, secondClickAt: null,
      }, async time => {
        // Earlier in the open, the drawer's rail still covers this point.
        if (time === 256) {
          await expect(page.locator('.plugin-workbench'), direction).toHaveAttribute('data-motion-running')
          const box = (await trigger.boundingBox())!
          const hit = await trigger.evaluate(element => {
            const box = element.getBoundingClientRect()
            const target = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
            return { isTrigger: element.contains(target), target: target?.outerHTML.slice(0, 300) }
          })
          expect(hit.isTrigger, `the drawn rules button receives the pointer: ${JSON.stringify(hit)}`).toBe(true)
          await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
          await expect(page.locator('.wb-filter-rules-dropdown')).toBeVisible()
        }
        if (time === 400) {
          await page.keyboard.press('Escape')
          await expect(trigger).toBeFocused()
          await page.keyboard.press('Tab')
          await expect(input).toBeFocused()
        }
      })
      await expectMotionCleanedUp(page)
    }
  })

  for (const mode of ['embedded', 'standalone']) {
    appearanceTest(`market ${mode} search keeps narrow wrapping, filtering, clearing and rule focus`, async ({ page }) => {
      await openPluginWorkbench(page)
      if (mode === 'embedded') {
        await openDrawerFully(page, MARKET_TOGGLE, MARKET_PANEL)
        await page.setViewportSize({ width: 720, height: 1000 })
      } else {
        await page.goto(`${PREVIEW_ORIGIN}/ui/market`)
        await page.setViewportSize({ width: 320, height: 1000 })
      }
      const market = page.locator('[data-yui-guide-id="market-panel-root"]')
      const bar = market.locator('.wb-filter-bar')
      const input = bar.getByRole('textbox')
      await expect(market.getByText('Fixture One', { exact: true })).toBeVisible()
      await expect(market.getByText('Fixture Two', { exact: true })).toBeVisible()
      const layout = await bar.evaluate(element => {
        const controls = element.querySelector('.wb-filter-bar__controls')!.getBoundingClientRect()
        const rules = element.querySelector('.wb-filter-bar__rules-trigger')!.getBoundingClientRect()
        const input = element.querySelector('.wb-filter-bar__input')!.getBoundingClientRect()
        return { controls, rules, input }
      })
      expect(layout.input.top).toBeGreaterThanOrEqual(layout.rules.bottom)
      expect(layout.input.width).toBeGreaterThanOrEqual(200)
      expect(layout.input.right).toBeLessThanOrEqual(layout.controls.right + TOLERANCE)
      await input.fill('Fixture One')
      await expect(market.getByText('Fixture Two', { exact: true })).toHaveCount(0)
      await input.hover()
      await bar.locator('.el-input__clear').click()
      await expect(input).toHaveValue('')
      await expect(market.getByText('Fixture Two', { exact: true })).toBeVisible()
      const rules = bar.locator('.wb-filter-bar__rules-trigger')
      await rules.focus()
      await page.keyboard.press('Enter')
      const panel = page.locator('.wb-filter-rules-dropdown')
      await expect(panel.getByRole('button').first()).toBeFocused()
      await panel.getByRole('button').first().click()
      await expect(input).toBeFocused()
      await expect(input).not.toHaveValue('')
    })
  }
})
