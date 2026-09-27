/** Semantic presets. Route content is animated inside a transition-owned node,
 * so its small transform does not move the app shell's fixed controls. */
export const motionPolicy = {
  easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
  exitEasing: 'cubic-bezier(0.4, 0, 1, 1)',
  quickDuration: 140,
  staggerStep: 24,
  maxDelay: 120,
  maxItems: 12,
  largeList: 80,
} as const

export const motionPresets = {
  page: { duration: 220, distance: 8, scale: 0.995, opacity: 0 },
  section: { duration: 300, distance: 12, scale: 1, opacity: 0 },
  card: { duration: 300, distance: 14, scale: 0.97, opacity: 0 },
  item: { duration: 220, distance: 8, scale: 0.985, opacity: 0 },
  quiet: { duration: 140, distance: 3, scale: 1, opacity: 0 },
} as const

export type MotionPreset = keyof typeof motionPresets

export function staggerDelay(index = 0) {
  return Math.min(Math.max(0, index) * motionPolicy.staggerStep, motionPolicy.maxDelay)
}

export function motionFrames(preset: MotionPreset, leaving = false): Keyframe[] {
  const spec = motionPresets[preset]
  const from: Keyframe = { opacity: spec.opacity }
  const to: Keyframe = { opacity: 1 }
  from.transform = `translate3d(0, ${leaving ? -spec.distance : spec.distance}px, 0) scale(${spec.scale})`
  to.transform = 'translate3d(0, 0, 0) scale(1)'
  return leaving ? [to, from] : [from, to]
}

