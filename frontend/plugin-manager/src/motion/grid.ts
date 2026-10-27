import { onBeforeUnmount } from 'vue'
import {
  cancelMotion, counterScaledRadius, measurePinBox, motionSuppressed, pinInPlace, playFrames, playMotion, releasePin,
  type FrameTiming, type MotionOptions, type PinBox,
} from './runtime'
import { motionPolicy } from './policy'
import { onLayoutTransition } from './scheduler'

/** Scroll offset of the nearest scrolling ancestor, or the window's. */
type Scroll = { x: number; y: number }

function scrollOf(node: HTMLElement): Scroll {
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent)
    if ([style.overflowX, style.overflowY].some(value => value === 'auto' || value === 'scroll')) {
      return { x: parent.scrollLeft, y: parent.scrollTop }
    }
  }
  return { x: window.scrollX, y: window.scrollY }
}

/** Scroll-content space: the client box plus the scroll offset. Boxes in it
 * stay put while the page scrolls, yet move with the grid when a layout change
 * shifts the grid itself (a drawer docking on its left, a header re-wrapping). */
function boxIn(element: HTMLElement, scroll: Scroll) {
  const rect = element.getBoundingClientRect()
  return new DOMRect(rect.left + scroll.x, rect.top + scroll.y, rect.width, rect.height)
}

/** The inverse of `boxIn`: a scroll-content box as a client box. */
function onScreen(box: DOMRect, scroll: Scroll) {
  return new DOMRect(box.x - scroll.x, box.y - scroll.y, box.width, box.height)
}

/** Vue owns DOM identity and FLIP moves; the motion runtime owns entrances,
 * exits and cancellation. Section entrances animate bounded visible children,
 * never the height or transform of a potentially enormous list container. */
export function useGridMotionController(options: {
  phase?: () => 'initial' | 'filter'
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
  // The grid last measured; layout transitions re-record its boxes.
  let lastGrid: HTMLElement | null = null

  type ReflowParts = NonNullable<ReturnType<typeof reflowParts>>
  function reflowParts(node: HTMLElement) {
    const surface = node.querySelector<HTMLElement>('[data-reflow="surface"]')
    const content = node.querySelector<HTMLElement>('[data-reflow="content"]')
    if (!surface || !content) return null
    const edge = (name: string) => [...node.querySelectorAll<HTMLElement>(`[data-reflow="${name}"]`)]
    return { surface, content, ends: edge('end'), bottoms: edge('bottom') }
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
        surface: { transform: `scale(${sx}, ${sy})`, borderRadius: counterScaledRadius(radius, sx, sy) },
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
    const scroll = scrollOf(grid)
    const plans = items.flatMap(node => {
      const laid = laidBoxes.get(node)
      const parts = laid && reflowParts(node)
      if (!parts) return []
      // A glide reversed midway (a drawer closed while opening) continues from
      // where it is drawn; read its offset before settling it.
      const drawn = gliding.has(node) ? boxIn(parts.surface, scroll) : null
      return [{ node, laid, drawn, parts }]
    })
    // Settle running reflows first, so every target is a plain layout box.
    for (const { node } of plans) cancel(node)
    let order = 0
    for (const { node, laid, drawn, parts } of plans) {
      const to = boxIn(parts.surface, scroll)
      if (!laid.width || !to.width) continue
      // `drawn` is the new slot offset and scaled by the settled glide; the
      // last frame showed that same offset on the slot it was heading to.
      const from = drawn
        ? new DOMRect(
            laid.x + drawn.x - to.x, laid.y + drawn.y - to.y,
            laid.width * drawn.width / to.width, laid.height * drawn.height / to.height,
          )
        : laid
      if (!(visible(node, onScreen(from, scroll)) || visible(node, onScreen(to, scroll)))) continue
      if (sameBox(from, to)) continue
      laidBoxes.set(node, to)
      gliding.add(node)
      // A reversed glide is already moving: it turns around at once instead of
      // holding still through a stagger delay.
      glide(node, parts, to, from, to, reflowTiming(drawn ? 0 : order++), () => {
        gliding.delete(node)
        syncColumns(grid)
      })
    }
  }

  /** Re-records the slots of the grid's settled items in the current frame,
   * just before a layout change moves the grid. Gliding items keep the slot
   * they are heading to. */
  function record(grid: HTMLElement) {
    if (!columns) return
    const items = layoutItems(grid)
    if (items.length > motionPolicy.largeList || motionSuppressed()) return
    const scroll = scrollOf(grid)
    for (const node of items) {
      if (gliding.has(node)) continue
      const surface = reflowParts(node)?.surface
      if (surface) laidBoxes.set(node, boxIn(surface, scroll))
    }
  }

  function layoutItems(grid: HTMLElement) {
    return [...grid.children].filter((child): child is HTMLElement =>
      child instanceof HTMLElement && !animatedLeaves.has(child))
  }

  function measure(grid: HTMLElement, animate: boolean) {
    cancelAnimationFrame(syncFrame)
    // A width change is the resize observer's to animate; animation frames
    // run before observations, so a sync must not record it first.
    if (!animate && grid.clientWidth !== width) return
    lastGrid = grid
    const previous = width
    width = grid.clientWidth
    const next = width ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0
    const items = layoutItems(grid)
    // Large lists never move their items (and skip rendering off-screen
    // cards), and suppressed motion never plays: neither needs any boxes.
    const still = items.length > motionPolicy.largeList || motionSuppressed()
    const relaid = next !== columns || Math.abs(width - previous) >= motionPolicy.reflowStep
    if (animate && columns && next && relaid && !still) reflow(grid, items)
    columns = next
    const boxes = new Map<HTMLElement, DOMRect>()
    if (next && !still) {
      const scroll = scrollOf(grid)
      for (const node of items) {
        const surface = reflowParts(node)?.surface
        const box = gliding.has(node) ? laidBoxes.get(node) : surface && boxIn(surface, scroll)
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

  // Boxes are fresh in the frame a layout change is about to move the grid in,
  // and the reflow starts with the transition instead of at the next resize
  // observation, which would show the end layout for a frame.
  const unlayout = onLayoutTransition(() => {
    const grid = lastGrid
    if (!grid) return
    record(grid)
    return () => measure(grid, true)
  })

  onBeforeUnmount(() => {
    unlayout()
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
