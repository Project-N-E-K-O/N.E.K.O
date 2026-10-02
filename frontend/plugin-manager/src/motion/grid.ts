import { onBeforeUnmount } from 'vue'
import { cancelMotion, measurePinBox, pinInPlace, playMotion, releasePin, type MotionOptions, type PinBox } from './runtime'
import { motionPolicy } from './policy'

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

  function visible(node: HTMLElement) {
    const rect = node.getBoundingClientRect()
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

  function run(node: HTMLElement, options: MotionOptions) {
    const token = {}
    owned.set(node, token)
    playMotion(node, { ...options, done: () => {
      if (owned.get(node) === token) owned.delete(node)
      options.done?.()
    } })
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

  onBeforeUnmount(() => { for (const node of [...owned.keys()]) cancelMotion(node) })
  return {
    enterSection: (el: Element, done: () => void) => section(el, done),
    leaveSection: (el: Element, done: () => void) => section(el, done, true),
    enterItem,
    leaveItem,
    pinLeavingItem, clearLeavingItemStyles, cancel,
  }
}
