// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { confirmDisplay } from './pluginDisplaySession'
import { providePluginDisplayPage, usePluginDisplayFrame } from './usePluginDisplay'
import { usePluginDisplayDialog } from './usePluginDisplayDialog'

const mounted: Array<() => void> = []
function mountFrame(initialFocus = false) {
  const focused = ref(initialFocus),
    metadata = ref({ id: 'main' }),
    active = ref(true)
  const connect = vi.fn(),
    notify = vi.fn(),
    reply = vi.fn()
  let display!: ReturnType<typeof usePluginDisplayFrame>
  const Child = defineComponent({
    setup() {
      const target = ref<HTMLElement | null>(null)
      display = usePluginDisplayFrame({
        target,
        identity: () => metadata.value.id,
        active: () => active.value,
        name: () => 'Example',
        notify,
        connect,
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
  let documentId = '',
    connectionId = ''
  function register(id: string) {
    documentId = id
    display.handleMessage({ type: 'neko-plugin-display-ready', documentId }, reply)
    connectionId = connect.mock.calls.at(-1)![0]
    display.handleMessage({ type: 'neko-plugin-display-ready', documentId, connectionId }, reply)
    const acknowledgement = reply.mock.calls.at(-1)![0]
    reply.mockClear()
    return { documentId, connectionId, acknowledgement }
  }
  function send(data: object) {
    return display.handleMessage({ documentId, connectionId, ...data }, reply)
  }
  return { focused, metadata, active, display, connect, notify, reply, register, send }
}
afterEach(async () => {
  mounted.splice(0).forEach((unmount) => unmount())
  await nextTick()
})

describe('plugin display frame documents', () => {
  it('preserves equivalent metadata and publishes availability changes without an owned session', async () => {
    const { focused, metadata, active, register, send, reply, notify } = mountFrame()
    register('initial')
    active.value = false
    expect(notify.mock.calls.at(-1)![0].supportedModes).toEqual([])
    active.value = true
    expect(notify.mock.calls.at(-1)![0].supportedModes).toContain('focus')
    send({
      type: 'neko-hosted-surface-request',
      requestId: 'focus',
      method: 'display.requestMode',
      payload: { mode: 'focus' },
    })
    confirmDisplay()
    await vi.waitFor(() => expect(reply).toHaveBeenCalledOnce())
    metadata.value = { id: 'main' }
    await nextTick()
    expect(focused.value).toBe(true)
    active.value = false
    await nextTick()
    expect(focused.value).toBe(false)
  })
  it('rejects old document traffic after the load handshake starts a new connection', async () => {
    const { focused, display, register, send, reply } = mountFrame()
    const old = register('old')
    send({
      type: 'neko-hosted-surface-request',
      requestId: 'old-request',
      method: 'display.requestMode',
      payload: { mode: 'focus' },
    })
    expect(usePluginDisplayDialog().visible.value).toBe(true)
    display.connectDocument()
    const fresh = register('new')
    expect(fresh.connectionId).not.toBe(old.connectionId)
    display.handleMessage({ type: 'neko-plugin-display-dispose', ...old }, reply)
    display.handleMessage(
      {
        type: 'neko-hosted-surface-request',
        ...old,
        requestId: 'late-request',
        method: 'display.requestMode',
        payload: { mode: 'focus' },
      },
      reply
    )
    expect(usePluginDisplayDialog().visible.value).toBe(false)
    send({
      type: 'neko-hosted-surface-request',
      requestId: 'new-request',
      method: 'display.requestMode',
      payload: { mode: 'focus' },
    })
    confirmDisplay()
    await vi.waitFor(() => expect(reply).toHaveBeenCalledOnce())
    expect(focused.value).toBe(true)
    expect(reply).toHaveBeenCalledWith(
      expect.objectContaining({
        documentId: 'new',
        connectionId: fresh.connectionId,
        result: expect.objectContaining({ status: 'applied' }),
      })
    )
    send({ type: 'neko-plugin-display-dispose' })
    expect(focused.value).toBe(false)
  })
  it('re-registers the same document when its early ready was reset by iframe load', async () => {
    const { display, register, send, reply } = mountFrame()
    const early = register('same-document')
    display.connectDocument()
    display.handleMessage(
      {
        type: 'neko-plugin-display-ready',
        documentId: early.documentId,
        connectionId: early.connectionId,
      },
      reply
    )
    expect(reply).not.toHaveBeenCalled()
    const fresh = register('same-document')
    expect(fresh.connectionId).not.toBe(early.connectionId)
    send({ type: 'neko-hosted-surface-request', requestId: 'query', method: 'display.getState' })
    await vi.waitFor(() => expect(reply).toHaveBeenCalledOnce())
    expect(reply.mock.calls[0]![0].result.mode).toBe('normal')
  })
  it('publishes manual focus on registration and lets iframe Escape exit it after reload', async () => {
    const { focused, display, register, send, reply } = mountFrame(true)
    expect(register('initial').acknowledgement.state.mode).toBe('focus')
    display.connectDocument()
    expect(register('reloaded').acknowledgement.state.mode).toBe('focus')
    send({
      type: 'neko-hosted-surface-request',
      requestId: 'escape',
      method: 'display.exit',
      payload: { reason: 'escape' },
    })
    await vi.waitFor(() => expect(reply).toHaveBeenCalledOnce())
    expect(focused.value).toBe(false)
    expect(reply.mock.calls[0]![0].result.state.mode).toBe('normal')
  })
  it('leaves unrelated action cancellation to the existing bridge handler', async () => {
    const { register, send, reply } = mountFrame()
    register('initial')
    expect(send({ type: 'neko-hosted-surface-cancel', requestId: 'action-request' })).toBe(false)
    send({
      type: 'neko-hosted-surface-request',
      requestId: 'display-request',
      method: 'display.requestMode',
      payload: { mode: 'focus' },
    })
    expect(send({ type: 'neko-hosted-surface-cancel', requestId: 'display-request' })).toBe(true)
    await vi.waitFor(() => expect(reply).toHaveBeenCalledOnce())
    expect(reply.mock.calls[0]![0].result.status).toBe('cancelled')
  })
})
