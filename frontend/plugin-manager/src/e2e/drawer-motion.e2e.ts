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
}
type RecordOptions = {
  toggle: string
  panel: string
  duration: number
  secondClickAt: number | null
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

// Reversal must not teleport: every step is within 2x the recent steps plus 2px.
function reversalIssues(recording: Recording, reversalFrame: number): string[] {
  const { frames } = recording
  const step = (frameIndex: number, index: number) => {
    const before = frames[frameIndex - 1]?.rects[index] ?? null
    const after = frames[frameIndex]?.rects[index] ?? null
    return before && after ? stepSize(before, after) : 0
  }
  return recording.labels.flatMap((label, index) => {
    for (let frame = reversalFrame; frame < frames.length; frame++) {
      const delta = step(frame, index)
      const preceding = Math.max(step(frame - 1, index), step(frame - 2, index), step(frame - 3, index))
      if (delta > 2 * preceding + 2) {
        return [
          `${label} jumps ${delta.toFixed(1)}px at frame ${frame} after reversal (preceding max ${preceding.toFixed(1)}px)`,
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

// Runs inside the page: samples every frame, clicks the toggle after the first
// (pre-click) frame, and optionally clicks it again `secondClickAt` ms later.
function recordDrawerMotion(page: Page, options: RecordOptions) {
  return page.evaluate<Recording, RecordOptions>(async ({ toggle, panel, duration, secondClickAt }) => {
    const workbench = document.querySelector<HTMLElement>('.plugin-workbench')!
    const header = document.querySelector<HTMLElement>('.workbench-header')!
    const tracked: Element[] = []
    const track = (element: Element | null) => {
      if (element && !tracked.includes(element)) tracked.push(element)
    }
    for (const atom of workbench.querySelectorAll('[data-motion]')) {
      if (!atom.parentElement?.closest('[data-motion]') && atom.getClientRects().length) track(atom)
    }
    track(document.querySelector(panel))
    for (const surface of [...workbench.querySelectorAll('.grid-section__item [data-reflow="surface"]')].slice(0, 4)) {
      track(surface)
    }
    const labels = tracked.map((element, index) => {
      const name = element.getAttribute('data-yui-guide-id') || element.getAttribute('class')?.split(' ')[0] || element.tagName.toLowerCase()
      return `${name} (${element.getAttribute('data-motion') ?? 'data-reflow=surface'}) #${index}`
    })
    const rectOf = (element: Element): Rect | null => {
      const box = element.getBoundingClientRect()
      return box.width > 0 && box.height > 0 ? { x: box.x, y: box.y, w: box.width, h: box.height } : null
    }
    const clickToggle = () => document.querySelector<HTMLElement>(toggle)!.click()

    const frames: Frame[] = []
    const start = performance.now()
    let clickFrame = -1
    let clickTime = 0
    let secondClickFrame: number | null = null
    for (;;) {
      await new Promise(requestAnimationFrame)
      const t = performance.now() - start
      frames.push({
        t,
        compact: header.classList.contains('workbench-header--compact'),
        running: workbench.hasAttribute('data-motion-running'),
        rects: tracked.map(rectOf),
      })
      if (clickFrame < 0) {
        clickToggle()
        clickFrame = frames.length
        clickTime = t
      } else if (secondClickAt !== null && secondClickFrame === null && t - clickTime >= secondClickAt) {
        clickToggle()
        secondClickFrame = frames.length
      }
      if (t >= duration) break
    }
    return { labels, frames, clickFrame, secondClickFrame }
  }, options)
}

async function openPluginWorkbench(page: Page) {
  await installCardLayoutFixture(page)
  // The fixture reports a newer version; keep the boot update popup off the header.
  await page.route('**/market/catalog/api/v1/plugins/latest-versions?*', route =>
    route.fulfill({ json: { items: [{ plugin_id: '42', version: '1.0.0' }] } })
  )
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  // At this width opening a drawer flips the header to its compact layout.
  await page.setViewportSize({ width: 1600, height: 1000 })
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
