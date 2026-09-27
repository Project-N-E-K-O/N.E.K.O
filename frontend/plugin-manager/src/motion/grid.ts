import { onBeforeUnmount } from 'vue'
import { cancelMotion, pinInPlace, playMotion, releasePin, type MotionOptions } from './runtime'
import { motionPolicy } from './policy'

/** Vue owns DOM identity and FLIP moves; the motion runtime owns entrances,
 * exits and cancellation. Section entrances animate bounded visible children,
 * never the height or transform of a potentially enormous list container. */
export function useGridMotionController(options: {
  animateInitial?: () => boolean
  phase?: () => 'initial' | 'filter'
} = {}) {
  let firstSectionEntry = true
  const owned = new Set<HTMLElement>()
  const animatedLeaves = new WeakSet<HTMLElement>()

  function visible(node: HTMLElement) {
    const rect = node.getBoundingClientRect()
    const parent = node.closest('.grid-section')?.parentElement?.getBoundingClientRect()
    return rect.bottom > Math.max(0, parent?.top ?? 0)
      && rect.top < Math.min(window.innerHeight, parent?.bottom ?? window.innerHeight)
      && rect.right > 0 && rect.left < window.innerWidth
  }

  // The index check needs no layout, so bulk removals never reach `visible`.
  function animatable(node: HTMLElement) {
    return Number(node.dataset.motionIndex || 0) < motionPolicy.maxItems && visible(node)
  }

  function run(node: HTMLElement, options: MotionOptions) {
    owned.add(node)
    playMotion(node, { ...options, done: () => {
      owned.delete(node)
      options.done?.()
    } })
  }

  function section(element: Element, done: () => void, leaving = false) {
    if (!leaving) {
      const skip = firstSectionEntry && options.animateInitial?.() === false
      firstSectionEntry = false
      if (skip) { done(); return }
    }
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
    run(node, { preset: preset(), done })
  }

  function leaveItem(element: Element, done: () => void) {
    const node = element as HTMLElement
    if (!animatedLeaves.has(node)) { done(); return }
    run(node, { preset: preset(), leaving: true, done })
  }

  function pinLeavingItem(element: Element) {
    const node = element as HTMLElement
    if (!animatable(node)) return
    animatedLeaves.add(node)
    pinInPlace(node)
  }

  function clearLeavingItemStyles(element: Element) {
    const node = element as HTMLElement
    animatedLeaves.delete(node)
    releasePin(node)
  }

  function cancel(element: Element) {
    const node = element as HTMLElement
    cancelMotion(node)
    for (const child of [...owned]) {
      if (node.contains(child)) cancelMotion(child)
    }
  }

  onBeforeUnmount(() => { for (const node of [...owned]) cancelMotion(node) })
  return {
    enterSection: (el: Element, done: () => void) => section(el, done),
    leaveSection: (el: Element, done: () => void) => section(el, done, true),
    enterItem,
    leaveItem,
    pinLeavingItem, clearLeavingItemStyles, cancel,
  }
}
