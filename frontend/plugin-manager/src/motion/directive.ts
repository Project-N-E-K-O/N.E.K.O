import type { ObjectDirective } from 'vue'
import { cancelMotion, playMotion, type MotionOptions } from './runtime'

type Entrance = Omit<MotionOptions, 'leaving' | 'done'> & { key?: unknown }

/** Reactive text/status updates do not replay entrances. Only an explicit key
 * change (e.g. a new route path) replaces a running animation. */
export const vMotion: ObjectDirective<HTMLElement, Entrance> = {
  mounted: (element, { value }) => playMotion(element, value),
  updated(element, { value, oldValue }) {
    if (value.key !== oldValue?.key) playMotion(element, value)
  },
  beforeUnmount(element) {
    cancelMotion(element)
  },
}
