import { motionFrames, motionPolicy, motionPresets, staggerDelay, type MotionPreset } from './policy'

type ActiveMotion = { finish: () => void }
const active = new Map<HTMLElement, ActiveMotion>()
let media: MediaQueryList | undefined

function settleAll() {
  if (document.hidden || media?.matches) {
    for (const motion of [...active.values()]) motion.finish()
  }
}

function subscribe() {
  if (active.size !== 1) return
  media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
  media?.addEventListener('change', settleAll)
  document.addEventListener('visibilitychange', settleAll)
}

function unsubscribe() {
  if (active.size) return
  media?.removeEventListener('change', settleAll)
  media = undefined
  document.removeEventListener('visibilitychange', settleAll)
}

export function cancelMotion(element: HTMLElement) {
  active.get(element)?.finish()
}

const pinnedProperties = ['position', 'left', 'top', 'width', 'height', 'margin', 'pointer-events', 'overflow']

export type PinBox = Pick<HTMLElement, 'offsetLeft' | 'offsetTop' | 'offsetWidth' | 'offsetHeight'>

export function measurePinBox(element: HTMLElement): PinBox {
  const { offsetLeft, offsetTop, offsetWidth, offsetHeight } = element
  return { offsetLeft, offsetTop, offsetWidth, offsetHeight }
}

/** Takes a leaving element out of flow at its current box so its siblings can
 * take its place immediately. All reads happen before the single write; pass a
 * box measured earlier when siblings may already have been pinned. */
export function pinInPlace(element: HTMLElement, box: PinBox = measurePinBox(element)) {
  const { offsetLeft, offsetTop, offsetWidth, offsetHeight } = box
  Object.assign(element.style, {
    position: 'absolute',
    left: `${offsetLeft}px`, top: `${offsetTop}px`,
    width: `${offsetWidth}px`, height: `${offsetHeight}px`,
    margin: '0', pointerEvents: 'none',
  })
}

/** Pins a leaving view at the frame its scroll container currently shows and
 * cuts it off at the container's bottom edge, so a tall view adds no scrollable
 * overflow (no transient scrollbar, no width jump) while the next view enters
 * at the top of the container. */
export function pinInScrollport(element: HTMLElement) {
  const scroller = element.parentElement
  const box = measurePinBox(element)
  if (!scroller) {
    pinInPlace(element, box)
    return
  }
  const { scrollTop, clientHeight } = scroller
  pinInPlace(element, {
    ...box,
    offsetTop: box.offsetTop - scrollTop,
    offsetHeight: Math.max(0, Math.min(box.offsetHeight, scrollTop + clientHeight - box.offsetTop)),
  })
  element.style.overflow = 'hidden'
  scroller.scrollTop = 0
}

export function releasePin(element: HTMLElement) {
  for (const property of pinnedProperties) element.style.removeProperty(property)
}

export type MotionOptions = {
  preset: MotionPreset
  index?: number
  leaving?: boolean
  done?: () => void
}

export function motionSuppressed() {
  return document.hidden || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
}

export type FrameTiming = { duration: number; delay?: number; easing: string }

/** Preset entrances and exits. A replacement samples only an already-running
 * element, never an entire grid. */
export function playMotion(element: HTMLElement, options: MotionOptions) {
  const frames = motionFrames(options.preset, options.leaving)
  if (active.has(element)) {
    const current = getComputedStyle(element)
    frames[0] = {
      opacity: current.opacity,
      ...(frames[0]?.transform ? { transform: current.transform } : {}),
    }
  }
  playFrames(element, frames, {
    duration: options.leaving ? motionPolicy.quickDuration : motionPresets[options.preset].duration,
    delay: options.leaving ? 0 : staggerDelay(options.index),
    easing: options.leaving ? motionPolicy.exitEasing : motionPolicy.easing,
  }, options.done)
}

/** One owner per element: a new animation finishes the running one first. No
 * RAF loop, persistent inline style, or layer hints. Both cancellation and
 * completion call `done` once. */
export function playFrames(element: HTMLElement, frames: Keyframe[], timing: FrameTiming, done?: () => void) {
  cancelMotion(element)
  if (motionSuppressed() || typeof element.animate !== 'function') {
    done?.()
    return
  }
  let animation: Animation
  const { duration, delay = 0 } = timing
  try {
    animation = element.animate(frames, { ...timing, delay, fill: 'both' })
  } catch {
    done?.()
    return
  }
  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    clearTimeout(timer)
    animation.cancel()
    active.delete(element)
    unsubscribe()
    done?.()
  }
  // Also covers a removed document or a browser that never settles finished.
  const timer = setTimeout(finish, duration + delay + 100)
  active.set(element, { finish })
  subscribe()
  void animation.finished.then(finish, finish)
}
