import type { ObjectDirective } from 'vue'
import { spring, interpolate } from 'popmotion'
import { boundedMemo } from '@/utils/boundedMemo'

export type EntranceSpec = { y: number; scale?: number; blur?: number; delay?: number; stiffness: number; damping: number; duration: number }
// Same Popmotion generator used by @vueuse/motion. Physics parameters take
// precedence over duration there; don't truncate a spring at the nominal duration.
export function sampleEntrance(spec: EntranceSpec) {
  const options = { stiffness: spec.stiffness, damping: spec.damping, duration: spec.duration }
  const y = spring({ ...options, from: spec.y, to: 0 })
  const opacity = spring({ ...options, from: 0, to: 1 })
  const scale = spring({ ...options, from: spec.scale ?? 1, to: 1 })
  // Popmotion animates complex strings on the 0..100 domain, not 0..1.
  const blur = spring({ ...options, from: 0, to: 100 })
  const blurString = interpolate([0, 100], [`blur(${spec.blur ?? 0}px)`, 'blur(0px)'], { clamp: false })
  const frames: Keyframe[] = []
  let duration = 0
  // 60 Hz keyframes are enough for a browser compositor to interpolate a
  // spring. Keep the same spring rest thresholds per property (opacity and
  // transform settle at different times).
  for (let t = 0; t <= 5000; t += 1000 / 60) {
    const ys = y.next(t), os = opacity.next(t), ss = scale.next(t), bs = blur.next(t)
    frames.push({ opacity: Math.max(0, Math.min(1, os.value)),
      transform: `translate3d(0px, ${ys.value}px, 0px) scale(${ss.value})`,
      ...(spec.blur !== undefined ? { filter: blurString(bs.value) } : {}) })
    duration = t
    if (ys.done && os.done && ss.done && (spec.blur === undefined || bs.done)) break
  }
  frames.forEach((frame, i) => { frame.offset = i / (frames.length - 1) })
  return { frames, duration }
}
const samples = boundedMemo(32, key => sampleEntrance(JSON.parse(key) as EntranceSpec))

const active = new WeakMap<HTMLElement, () => void>()
function finalStyle(el: HTMLElement, spec: EntranceSpec) {
  el.style.opacity = '1'
  el.style.transform = 'translate3d(0px, 0px, 0px) scale(1)'
  if (spec.blur !== undefined) el.style.filter = 'blur(0px)'
}
export const vEntrance: ObjectDirective<HTMLElement, EntranceSpec> = {
  beforeMount(el, { value }) {
    el.style.opacity = '0'
    el.style.transform = `translate3d(0px, ${value.y}px, 0px) scale(${value.scale ?? 1})`
    if (value.blur !== undefined) el.style.filter = `blur(${value.blur}px)`
  },
  mounted(el, { value }) {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (media.matches || typeof el.animate !== 'function') { finalStyle(el, value); return }
    const { delay = 0, ...curve } = value
    const { frames, duration } = samples(JSON.stringify(curve))
    let animation: Animation
    try { animation = el.animate(frames, { duration, delay, easing: 'linear', fill: 'both' }) }
    catch { finalStyle(el, value); return }
    const settle = () => {
      finalStyle(el, value)
      animation.cancel()
      media.removeEventListener('change', changed)
      active.delete(el)
    }
    const changed = () => { if (media.matches) settle() }
    media.addEventListener('change', changed)
    animation.finished.then(settle).catch(() => {})
    active.set(el, () => { media.removeEventListener('change', changed); animation.cancel() })
  },
  beforeUnmount(el) { active.get(el)?.(); active.delete(el) },
}
