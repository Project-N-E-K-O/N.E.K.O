import { nextTick, onBeforeUnmount } from 'vue'
import {
  cancelMotion, measurePinBox, motionSuppressed, pinInPlace, playFrames, playMotion, releasePin,
  type FrameTiming, type MotionOptions, type PinBox,
} from './runtime'
import { motionPolicy } from './policy'

type GridPreview = {
  /** Reads where each item will be laid out; call while the change is applied. */
  target(): void
  /** Starts the glides, each ending with the callback `depart` returns. */
  play(depart: () => () => void): void
  /** The change took effect: drop the held frames and record the new boxes. */
  finish(): void
  /** The change was abandoned: glide every item back to its slot. */
  cancel(): void
}

// Every mounted grid, for layout changes that glide ahead of taking effect.
const previewers = new Set<() => GridPreview | null>()
// Starting a glide abandons the previous one: it never commits, and its items
// glide on from where they are drawn.
let abandonGlide: (() => void) | null = null

/** Plays a layout change before it takes effect: every mounted grid glides to
 * the boxes the change lays its items out in, and only once all have arrived
 * does `commit` make the change real, so a drawer opening plays as its closing
 * reversed. `apply` and `revert` toggle the change synchronously for measuring
 * (no frame is drawn in between); `commit` applies it through Vue. Each of
 * `frames` (containers the change narrows) is pushed in step until its
 * leading edge reaches the new one, its trailing overhang clipped, so its
 * contents travel with it unscaled. Returns a cancel that glides everything
 * back and never commits. */
export function glideToLayout(change: {
  apply(): void
  revert(): void
  commit(): void
  /** A container whose left edge moves with the change while its right edge
   * stays: it is clipped from the left in step, and its `header` (outside the
   * grids, laid out live) narrows with it, so right-aligned content holds. */
  shell?: { element: HTMLElement; header: HTMLElement }
}) {
  abandonGlide?.()
  let settled = false
  abandonGlide = () => { settled = true }
  // Settle the shell first, so the grids inside measure from where it is laid
  // out; it continues from where it is drawn.
  const shell = change.shell && (() => {
    const { element, header } = change.shell
    const clip = getComputedStyle(element).clipPath
    const margin = getComputedStyle(header).marginLeft
    cancelMotion(element)
    cancelMotion(header)
    return {
      element, header,
      drawn: { clip: clip === 'none' ? 'inset(0px)' : clip, margin },
      rest: Number.parseFloat(getComputedStyle(header).marginLeft) || 0,
      from: element.getBoundingClientRect(),
      to: new DOMRect(),
    }
  })()
  const previews = [...previewers].map(begin => begin()).filter(preview => preview !== null)
  change.apply()
  for (const preview of previews) preview.target()
  if (shell) shell.to = shell.element.getBoundingClientRect()
  change.revert()
  // Held at one until every glide has started, so a glide that ends at once
  // cannot commit early.
  let pending = 1
  const arrive = () => {
    if (--pending || settled) return
    settled = true
    change.commit()
    void nextTick(() => { for (const preview of previews) preview.finish() })
  }
  const depart = () => {
    pending++
    return arrive
  }
  // The cards' timing, without stagger, so the shell and items move as one.
  const timing = { duration: motionPolicy.reflowDuration, easing: motionPolicy.reflowEasing }
  for (const preview of previews) preview.play(depart)
  if (shell) {
    const inset = Math.max(shell.to.left - shell.from.left, 0)
    playFrames(shell.element, [{ clipPath: shell.drawn.clip }, { clipPath: `inset(0px 0px 0px ${inset}px)` }], timing, depart())
    playFrames(shell.header, [{ marginLeft: shell.drawn.margin }, { marginLeft: `${shell.rest + inset}px` }], timing, depart())
  }
  arrive()
  return () => {
    if (settled) return
    settled = true
    for (const preview of previews) preview.cancel()
    if (!shell) return
    const { element, header, rest } = shell
    playFrames(element, [{ clipPath: getComputedStyle(element).clipPath }, { clipPath: 'inset(0px)' }], timing)
    playFrames(header, [{ marginLeft: getComputedStyle(header).marginLeft }, { marginLeft: `${rest}px` }], timing)
  }
}

/** Vue owns DOM identity and FLIP moves; the motion runtime owns entrances,
 * exits and cancellation. Section entrances animate bounded visible children,
 * never the height or transform of a potentially enormous list container. */
export function useGridMotionController(options: {
  phase?: () => 'initial' | 'filter'
  /** The grid element, for layout changes that glide ahead (glideToLayout). */
  grid?: () => HTMLElement | null | undefined
} = {}) {
  // Keyed by run: a superseded run's `done` fires inside the replacing
  // playMotion and must not drop the replacement's ownership.
  const owned = new Map<HTMLElement, object>()
  const animatedLeaves = new WeakSet<HTMLElement>()

  function visible(node: HTMLElement, rect = node.getBoundingClientRect()) {
    const parent = node.closest('.grid-section')?.parentElement?.getBoundingClientRect()
    return rect.bottom > Math.max(0, parent?.top ?? 0)
      && rect.top < Math.min(window.innerHeight, parent?.bottom ?? window.innerHeight)
      && rect.right > 0 && rect.left < window.innerWidth
  }

  function motionIndex(node: HTMLElement) {
    return Number(node.dataset.motionIndex || 0)
  }

  // The index check needs no layout, so bulk removals never reach `visible`.
  function animatable(node: HTMLElement) {
    return motionIndex(node) < motionPolicy.maxItems && visible(node)
  }

  // Vue runs before-leave once per removed item, interleaved with our pins: a
  // later item measured after an earlier pin has already slid into the freed
  // slot. Measure every candidate once, before the first pin of the patch.
  let leaveCandidates: Map<HTMLElement, PinBox | null> | null = null
  function measureLeaveCandidates(node: HTMLElement) {
    if (leaveCandidates) return leaveCandidates
    const candidates = new Map<HTMLElement, PinBox | null>()
    for (const child of node.parentElement?.children ?? [node]) {
      if (!(child instanceof HTMLElement) || animatedLeaves.has(child)) continue
      if (motionIndex(child) >= motionPolicy.maxItems) continue
      candidates.set(child, visible(child) ? measurePinBox(child) : null)
    }
    leaveCandidates = candidates
    queueMicrotask(() => { leaveCandidates = null })
    return candidates
  }

  function own(node: HTMLElement, done?: () => void) {
    const token = {}
    owned.set(node, token)
    return () => {
      if (owned.get(node) === token) owned.delete(node)
      done?.()
    }
  }

  function run(node: HTMLElement, options: MotionOptions) {
    playMotion(node, { ...options, done: own(node, options.done) })
  }

  // Shared layout motion. Column changes and width steps (a drawer opening,
  // the window maximizing) would make every card jump to its new slot and
  // size at once. Instead each item glides from the box it was last drawn
  // in: the item translates, its surface (`data-reflow="surface"`: border,
  // fill, shadow) scales with counter-scaled corners, and its content
  // (`data-reflow="content"`) is laid out at the final size and only clipped
  // to the scaling surface, so text is never squashed. Parts pinned to the
  // trailing or bottom edge (`data-reflow="end"` / `"bottom"`) follow that
  // edge. Items without both layers keep snapping.
  // Small per-frame width changes (the sidebar transition, a dragged window
  // edge) keep following the layout: a reflow restarted every frame would
  // hold each card near its start frame until the width settles.
  let columns = 0
  let width = 0
  // The slot each item was last laid out in; a gliding item keeps the slot it
  // is heading to, since its drawn box is offset by the running glide.
  let laidBoxes = new Map<HTMLElement, DOMRect>()
  const gliding = new Set<HTMLElement>()
  let syncFrame = 0

  type ReflowParts = NonNullable<ReturnType<typeof reflowParts>>
  function reflowParts(node: HTMLElement) {
    const surface = node.querySelector<HTMLElement>('[data-reflow="surface"]')
    const content = node.querySelector<HTMLElement>('[data-reflow="content"]')
    if (!surface || !content) return null
    const edge = (name: string) => [...node.querySelectorAll<HTMLElement>(`[data-reflow="${name}"]`)]
    return { surface, content, ends: edge('end'), bottoms: edge('bottom') }
  }

  /** Grid-relative, so scrolling between two resizes does not skew it. */
  function boxIn(grid: DOMRect, element: HTMLElement) {
    const rect = element.getBoundingClientRect()
    return new DOMRect(rect.left - grid.left, rect.top - grid.top, rect.width, rect.height)
  }

  /** Every layer of one item shares its timing; items start in reading order. */
  function reflowTiming(order: number): FrameTiming {
    return {
      duration: motionPolicy.reflowDuration,
      delay: Math.min(order * motionPolicy.reflowStagger, motionPolicy.reflowMaxDelay),
      easing: motionPolicy.reflowEasing,
    }
  }

  function sameBox(a: DOMRect, b: DOMRect) {
    return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height
  }

  /** Glides one item between two boxes while it stays laid out in `laid`. */
  function glide(
    node: HTMLElement, parts: ReflowParts,
    laid: DOMRect, from: DOMRect, to: DOMRect,
    timing: FrameTiming, done: () => void,
  ) {
    const { surface, content, ends, bottoms } = parts
    const style = getComputedStyle(surface)
    const radius = Number.parseFloat(style.borderTopLeftRadius) || 0
    // Clip to the inside of the surface's border, so the content fill never
    // covers it; negative while shrinking, so edge parts slid out stay drawn.
    const border = Number.parseFloat(style.borderRightWidth) || 0
    const at = (box: DOMRect) => {
      if (sameBox(box, laid)) {
        return {
          item: 'none', end: 'none', bottom: 'none',
          surface: { transform: 'none', borderRadius: `${radius}px` },
          content: { clipPath: `inset(0 round ${radius}px)` },
        }
      }
      const sx = box.width / laid.width
      const sy = box.height / laid.height
      return {
        item: `translate(${box.x - laid.x}px, ${box.y - laid.y}px)`,
        end: `translateX(${box.width - laid.width}px)`,
        bottom: `translateY(${box.height - laid.height}px)`,
        surface: { transform: `scale(${sx}, ${sy})`, borderRadius: `${radius / sx}px / ${radius / sy}px` },
        content: {
          clipPath: `inset(0 ${laid.width - box.width + border}px ${laid.height - box.height + border}px 0 round ${radius}px)`,
        },
      }
    }
    const start = at(from)
    const end = at(to)
    const slide = (element: HTMLElement, from: string, to: string, done?: () => void) =>
      playFrames(element, [{ transform: from }, { transform: to }], timing, own(element, done))
    slide(node, start.item, end.item, done)
    playFrames(surface, [start.surface, end.surface], timing, own(surface))
    playFrames(content, [start.content, end.content], timing, own(content))
    if (from.width !== laid.width || to.width !== laid.width) {
      for (const part of ends) slide(part, start.end, end.end)
    }
    if (from.height !== laid.height || to.height !== laid.height) {
      for (const part of bottoms) slide(part, start.bottom, end.bottom)
    }
  }

  function reflow(grid: HTMLElement, items: HTMLElement[]) {
    const frame = grid.getBoundingClientRect()
    const plans = items.flatMap(node => {
      const laid = laidBoxes.get(node)
      const parts = laid && reflowParts(node)
      if (!parts) return []
      // A glide reversed midway (a drawer closed while opening) continues from
      // where it is drawn; read its offset before settling it.
      const drawn = gliding.has(node) ? boxIn(frame, parts.surface) : null
      return [{ node, laid, drawn, parts }]
    })
    // Settle running reflows first, so every target is a plain layout box.
    for (const { node } of plans) cancel(node)
    const onScreen = (box: DOMRect) => new DOMRect(box.x + frame.left, box.y + frame.top, box.width, box.height)
    let order = 0
    for (const { node, laid, drawn, parts } of plans) {
      const to = boxIn(frame, parts.surface)
      if (!laid.width || !to.width) continue
      // `drawn` is the new slot offset and scaled by the settled glide; the
      // last frame showed that same offset on the slot it was heading to.
      const from = drawn
        ? new DOMRect(
            laid.x + drawn.x - to.x, laid.y + drawn.y - to.y,
            laid.width * drawn.width / to.width, laid.height * drawn.height / to.height,
          )
        : laid
      if (!(visible(node, onScreen(from)) || visible(node, onScreen(to)))) continue
      if (sameBox(from, to)) continue
      laidBoxes.set(node, to)
      gliding.add(node)
      glide(node, parts, to, from, to, reflowTiming(order++), () => {
        gliding.delete(node)
        syncColumns(grid)
      })
    }
  }

  /** One grid's part of glideToLayout. Its items stay laid out in their
   * current slots and glide to the measured targets, holding there until the
   * change takes effect; glides share one timing so they arrive together. */
  function preview(): GridPreview | null {
    const grid = options.grid?.()
    if (!grid || !columns) return null
    const items = [...grid.children].filter((child): child is HTMLElement =>
      child instanceof HTMLElement && !animatedLeaves.has(child))
    if (items.length > motionPolicy.largeList || motionSuppressed()) return null
    const frame = grid.getBoundingClientRect()
    // Continue from where each item is drawn, then settle it to read its slot.
    const plans = items.flatMap(node => {
      const parts = reflowParts(node)
      return parts ? [{ node, parts, drawn: boxIn(frame, parts.surface) }] : []
    })
    for (const { node } of plans) cancel(node)
    const laid = new Map(plans.map(({ node, parts }) => [node, boxIn(frame, parts.surface)]))
    const targets = new Map<HTMLElement, DOMRect>()
    const onScreen = (box: DOMRect) => new DOMRect(box.x + frame.left, box.y + frame.top, box.width, box.height)
    const back = () => {
      let order = 0
      for (const { node, parts } of plans) {
        const slot = laid.get(node)!
        const drawn = boxIn(frame, parts.surface)
        cancel(node)
        if (!slot.width || sameBox(drawn, slot) || !visible(node, onScreen(slot))) continue
        glide(node, parts, slot, drawn, slot, reflowTiming(order++), () => syncColumns(grid))
      }
    }
    return {
      target() {
        // Relative to where the grid is now: it moves with the change itself
        // (a drawer docking on its left).
        for (const { node, parts } of plans) targets.set(node, boxIn(frame, parts.surface))
      },
      play(depart) {
        const timing = reflowTiming(0)
        for (const { node, parts, drawn } of plans) {
          const slot = laid.get(node)!
          const to = targets.get(node)!
          if (!slot.width || !to.width) continue
          if (!(visible(node, onScreen(drawn)) || visible(node, onScreen(to)))) continue
          if (sameBox(drawn, slot) && sameBox(to, slot)) continue
          glide(node, parts, slot, drawn, to, timing, depart())
        }
      },
      finish() {
        // Record the layout the items arrived at before the resize observer
        // sees it, so it finds nothing left to reflow.
        width = grid.clientWidth
        columns = width ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0
        const settled = grid.getBoundingClientRect()
        laidBoxes = new Map(plans.map(({ node, parts }) => [node, boxIn(settled, parts.surface)]))
      },
      cancel: back,
    }
  }
  previewers.add(preview)

  function measure(grid: HTMLElement, animate: boolean) {
    cancelAnimationFrame(syncFrame)
    // A width change is the resize observer's to animate; animation frames
    // run before observations, so a sync must not record it first.
    if (!animate && grid.clientWidth !== width) return
    const previous = width
    width = grid.clientWidth
    const next = width ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0
    const items = [...grid.children].filter((child): child is HTMLElement =>
      child instanceof HTMLElement && !animatedLeaves.has(child))
    // Large lists never move their items (and skip rendering off-screen
    // cards), and suppressed motion never plays: neither needs any boxes.
    const still = items.length > motionPolicy.largeList || motionSuppressed()
    const relaid = next !== columns || Math.abs(width - previous) >= motionPolicy.reflowStep
    if (animate && columns && next && relaid && !still) reflow(grid, items)
    columns = next
    const boxes = new Map<HTMLElement, DOMRect>()
    if (next && !still) {
      const frame = grid.getBoundingClientRect()
      for (const node of items) {
        const surface = reflowParts(node)?.surface
        const box = gliding.has(node) ? laidBoxes.get(node) : surface && boxIn(frame, surface)
        if (box) boxes.set(node, box)
      }
    }
    laidBoxes = boxes
  }

  /** Call from the grid's resize observer: animates a column count change or
   * a width step. */
  function reflowColumns(grid: HTMLElement) {
    measure(grid, true)
  }

  /** Call after Vue patched the grid. Item and layout changes belong to the
   * TransitionGroup; this only re-records the boxes, once per frame, before
   * that frame's resize observations. */
  function syncColumns(grid: HTMLElement) {
    cancelAnimationFrame(syncFrame)
    syncFrame = requestAnimationFrame(() => measure(grid, false))
  }

  function section(element: Element, done: () => void, leaving = false) {
    // The section owns one animation. Items are owned exclusively by the
    // nested TransitionGroup; never animate the same element from both hooks.
    const node = element as HTMLElement
    run(node, {
      preset: options.phase?.() === 'filter' ? 'quiet' : 'section',
      index: 0,
      leaving,
      done,
    })
  }

  function preset(): MotionOptions['preset'] {
    return options.phase?.() === 'filter' ? 'quiet' : 'item'
  }

  function enterItem(element: Element, done: () => void) {
    const node = element as HTMLElement
    if (!animatable(node)) { done(); return }
    run(node, { preset: preset(), index: motionIndex(node), done })
  }

  function leaveItem(element: Element, done: () => void) {
    const node = element as HTMLElement
    if (!animatedLeaves.has(node)) { done(); return }
    run(node, { preset: preset(), leaving: true, done })
  }

  function pinLeavingItem(element: Element) {
    const node = element as HTMLElement
    if (motionIndex(node) >= motionPolicy.maxItems) return
    const box = measureLeaveCandidates(node).get(node)
    if (!box) return
    animatedLeaves.add(node)
    pinInPlace(node, box)
  }

  function clearLeavingItemStyles(element: Element) {
    const node = element as HTMLElement
    animatedLeaves.delete(node)
    releasePin(node)
  }

  function cancel(element: Element) {
    const node = element as HTMLElement
    cancelMotion(node)
    for (const child of [...owned.keys()]) {
      if (node.contains(child)) cancelMotion(child)
    }
  }

  onBeforeUnmount(() => {
    previewers.delete(preview)
    // Cancelling a glide schedules a sync; cancel that frame afterwards.
    for (const node of [...owned.keys()]) cancelMotion(node)
    cancelAnimationFrame(syncFrame)
  })
  return {
    enterSection: (el: Element, done: () => void) => section(el, done),
    leaveSection: (el: Element, done: () => void) => section(el, done, true),
    enterItem,
    leaveItem,
    pinLeavingItem, clearLeavingItemStyles, cancel, reflowColumns, syncColumns,
  }
}
