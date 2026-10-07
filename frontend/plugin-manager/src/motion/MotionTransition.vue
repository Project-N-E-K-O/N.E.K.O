<template>
  <Transition
    :mode="mode"
    :css="false"
    @enter="enter"
    @leave="leave"
    @leave-cancelled="leaveCancelled"
  >
    <slot />
  </Transition>
</template>

<script setup lang="ts">
import { cancelMotion, pinInPlace, playMotion, releasePin } from './runtime'
import type { MotionPreset } from './policy'

/** In `default` mode the next view enters while the previous one crossfades
 * out above its original box, so navigation never waits on an exit. The
 * transition must stay mounted (key its child, not this component), otherwise
 * Vue skips both hooks. An interrupted entrance is left running so the
 * following exit starts from the frame that is on screen. */
const props = withDefaults(defineProps<{
  mode?: 'in-out' | 'out-in' | 'default'
  preset?: MotionPreset
}>(), {
  mode: 'default',
  preset: 'page',
})

function enter(element: Element, done: () => void) {
  playMotion(element as HTMLElement, { preset: props.preset, done })
}

function leave(element: Element, done: () => void) {
  const node = element as HTMLElement
  if (props.mode === 'default') pinInPlace(node)
  node.inert = true
  playMotion(node, { preset: props.preset, leaving: true, done })
}

function leaveCancelled(element: Element) {
  const node = element as HTMLElement
  cancelMotion(node)
  releasePin(node)
  node.inert = false
}
</script>
