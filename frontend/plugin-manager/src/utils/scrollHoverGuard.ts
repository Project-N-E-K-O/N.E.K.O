export const SCROLLING_ATTRIBUTE = 'data-scrolling'
export const SCROLL_IDLE_MS = 150

/**
 * Marks the element that is currently scrolling with `data-scrolling` until
 * the scroll has been idle for `idleMs`. Cards sliding under a stationary
 * pointer otherwise flip `:hover` every frame, and each flip restarts their
 * lift and shadow transitions, which repaints and re-layerizes the whole grid.
 */
export function initScrollHoverGuard(root: Document = document, idleMs = SCROLL_IDLE_MS) {
  const timers = new Map<Element, ReturnType<typeof setTimeout>>()

  const handleScroll = (event: Event) => {
    const target = event.target === root ? root.documentElement : event.target
    if (!(target instanceof Element)) return
    const pending = timers.get(target)
    if (pending === undefined) target.setAttribute(SCROLLING_ATTRIBUTE, '')
    else clearTimeout(pending)
    timers.set(target, setTimeout(() => {
      timers.delete(target)
      target.removeAttribute(SCROLLING_ATTRIBUTE)
    }, idleMs))
  }

  root.addEventListener('scroll', handleScroll, { capture: true, passive: true })
  return () => {
    root.removeEventListener('scroll', handleScroll, { capture: true })
    for (const [element, timer] of timers) {
      clearTimeout(timer)
      element.removeAttribute(SCROLLING_ATTRIBUTE)
    }
    timers.clear()
  }
}
