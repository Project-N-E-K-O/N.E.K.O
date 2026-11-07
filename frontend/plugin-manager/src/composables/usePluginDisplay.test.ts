// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { confirmDisplay } from './pluginDisplaySession'
import { providePluginDisplayPage, usePluginDisplayFrame } from './usePluginDisplay'
import { usePluginDisplayDialog } from './usePluginDisplayDialog'

const mounted: Array<() => void> = []
function mountFrame() {
  const focused = ref(false)
  const metadata = ref({ id: 'main' })
  const active = ref(true)
  let display!: ReturnType<typeof usePluginDisplayFrame>
  const Child = defineComponent({
    setup() {
      const target = ref<HTMLElement | null>(null)
      display = usePluginDisplayFrame({
        target,
        identity: () => metadata.value.id,
        active: () => active.value,
        name: () => 'Example',
        notify: vi.fn(),
      })
      return () => h('div', { ref: target })
    },
  })
  const root = document.createElement('div')
  document.body.appendChild(root)
  const app = createApp(
    defineComponent({
      setup() {
        providePluginDisplayPage(focused, () => 'Example')
        return () => h(Child)
      },
    })
  )
  app.mount(root)
  mounted.push(() => {
    app.unmount()
    root.remove()
  })
  return { focused, metadata, active, display }
}
afterEach(async () => {
  mounted.splice(0).forEach((unmount) => unmount())
  await nextTick()
})

describe('plugin display frame documents', () => {
  it('preserves equivalent metadata and releases the session when the panel becomes inactive', async () => {
    const { focused, metadata, active, display } = mountFrame()
    const reply = vi.fn()
    display.handleMessage({ type: 'neko-plugin-display-ready', documentId: 'initial' }, reply)
    display.handleMessage(
      {
        type: 'neko-hosted-surface-request',
        documentId: 'initial',
        requestId: 'initial-request',
        method: 'display.requestMode',
        payload: { mode: 'focus' },
      },
      reply
    )
    confirmDisplay()
    await vi.waitFor(() => expect(reply).toHaveBeenCalledOnce())
    metadata.value = { id: 'main' }
    await nextTick()
    expect(focused.value).toBe(true)
    active.value = false
    await nextTick()
    expect(focused.value).toBe(false)
  })

  it('invalidates old confirmations and ignores a replaced document until the new document requests its own session', async () => {
    const { focused, display } = mountFrame()
    const reply = vi.fn()
    display.handleMessage({ type: 'neko-plugin-display-ready', documentId: 'old' }, reply)
    display.handleMessage(
      {
        type: 'neko-hosted-surface-request',
        documentId: 'old',
        requestId: 'old-request',
        method: 'display.requestMode',
        payload: { mode: 'focus' },
      },
      reply
    )
    expect(usePluginDisplayDialog().visible.value).toBe(true)
    display.invalidateDocument()
    display.handleMessage({ type: 'neko-plugin-display-ready', documentId: 'new' }, reply)
    // Same-frame reloads reuse WindowProxy, so disposal and replies must also
    // carry a matching document identity.
    display.handleMessage({ type: 'neko-plugin-display-dispose', documentId: 'old' }, reply)
    display.handleMessage(
      {
        type: 'neko-hosted-surface-request',
        documentId: 'old',
        requestId: 'late-request',
        method: 'display.requestMode',
        payload: { mode: 'focus' },
      },
      reply
    )
    expect(usePluginDisplayDialog().visible.value).toBe(false)
    display.handleMessage(
      {
        type: 'neko-hosted-surface-request',
        documentId: 'new',
        requestId: 'new-request',
        method: 'display.requestMode',
        payload: { mode: 'focus' },
      },
      reply
    )
    confirmDisplay()
    await vi.waitFor(() => expect(reply).toHaveBeenCalledOnce())
    expect(focused.value).toBe(true)
    expect(reply).toHaveBeenCalledWith(
      expect.objectContaining({
        documentId: 'new',
        requestId: 'new-request',
        result: expect.objectContaining({ status: 'applied' }),
      })
    )
    display.handleMessage({ type: 'neko-plugin-display-dispose', documentId: 'new' }, reply)
    expect(focused.value).toBe(false)
  })
})
