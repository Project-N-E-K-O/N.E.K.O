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

const pinnedProperties = ['position', 'left', 'top', 'width', 'height', 'margin', 'pointer-events']

/** Takes a leaving element out of flow at its current box so its siblings can
 * take its place immediately. All reads happen before the single write. */
export function pinInPlace(element: HTMLElement) {
  const { offsetLeft, offsetTop, offsetWidth, offsetHeight } = element
  Object.assign(element.style, {
    position: 'absolute',
    left: `${offsetLeft}px`, top: `${offsetTop}px`,
    width: `${offsetWidth}px`, height: `${offsetHeight}px`,
    margin: '0', pointerEvents: 'none',
  })
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

/** One owner per element. No RAF loop, persistent inline style, or layer hints.
 * A replacement samples only an already-running element, never an entire grid.
 * Both cancellation and completion release Vue's transition callback once. */
export function playMotion(element: HTMLElement, options: MotionOptions) {
  const frames = motionFrames(options.preset, options.leaving)
  if (active.has(element)) {
    const current = getComputedStyle(element)
    frames[0] = {
      opacity: current.opacity,
      ...(frames[0]?.transform ? { transform: current.transform } : {}),
    }
    cancelMotion(element)
  }
  if (document.hidden || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    || typeof element.animate !== 'function') {
    options.done?.()
    return
  }
  let animation: Animation
  const duration = options.leaving ? motionPolicy.quickDuration : motionPresets[options.preset].duration
  const delay = options.leaving ? 0 : staggerDelay(options.index)
  try {
    animation = element.animate(frames, {
      duration,
      delay,
      easing: options.leaving ? motionPolicy.exitEasing : motionPolicy.easing,
      fill: 'both',
    })
  } catch {
    options.done?.()
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
    options.done?.()
  }
  // Also covers a removed document or a browser that never settles finished.
  const timer = setTimeout(finish, duration + delay + 100)
  active.set(element, { finish })
  subscribe()
  void animation.finished.then(finish, finish)
}
