export const MOTION_POLICY = {
  sectionEnterDurationMs: 300,
  sectionLeaveDurationMs: 260,
  sectionEnterOpacityDurationMs: 220,
  sectionLeaveOpacityDurationMs: 180,
  itemStaggerStepMs: 14,
  itemStaggerMaxItems: 6,
  itemBlurPx: 4,
} as const

export function getStaggerDelay(index: number, reducedMotion: boolean): number {
  if (reducedMotion) return 0
  return Math.min(Math.max(index, 0), MOTION_POLICY.itemStaggerMaxItems - 1)
    * MOTION_POLICY.itemStaggerStepMs
}
