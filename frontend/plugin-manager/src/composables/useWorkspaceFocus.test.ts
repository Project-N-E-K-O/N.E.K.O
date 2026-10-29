// @vitest-environment happy-dom

import { afterEach, describe, expect, it } from 'vitest'
import { createApp, defineComponent, nextTick, ref, type Ref } from 'vue'
import { useWorkspaceFocus, useWorkspaceFocusOwner } from './useWorkspaceFocus'

const mounted: Array<() => void> = []

function mountOwner(active: Ref<boolean>) {
  const container = document.createElement('div')
  const app = createApp(defineComponent({
    setup() {
      useWorkspaceFocusOwner(active)
      return () => null
    },
  }))
  app.mount(container)
  const unmount = () => app.unmount()
  mounted.push(unmount)
  return unmount
}

afterEach(() => {
  while (mounted.length) mounted.pop()!()
})

describe('useWorkspaceFocus', () => {
  const { focused } = useWorkspaceFocus()

  it('follows the owner while it is active', async () => {
    const active = ref(false)
    mountOwner(active)
    expect(focused.value).toBe(false)

    active.value = true
    await nextTick()
    expect(focused.value).toBe(true)

    active.value = false
    await nextTick()
    expect(focused.value).toBe(false)
  })

  it('releases focus when the owner unmounts', () => {
    const unmount = mountOwner(ref(true))
    expect(focused.value).toBe(true)

    unmount()
    expect(focused.value).toBe(false)
  })

  it('keeps focus claimed by the entering view when the leaving view unmounts afterwards', () => {
    const unmountLeaving = mountOwner(ref(true))
    mountOwner(ref(true))

    unmountLeaving()
    expect(focused.value).toBe(true)
  })
})
