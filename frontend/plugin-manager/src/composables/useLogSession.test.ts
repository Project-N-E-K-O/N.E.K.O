// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { useLogSession } from './useLogSession'
import { get as httpGet } from '@/api/index'
import * as logApi from '@/api/logs'
import Logs from '@/views/Logs.vue'
import type { LogEntry } from '@/types/api'

vi.mock('@/api/index', () => ({ get: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: '_server' } }) }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

class ControlledSocket {
  static instances: ControlledSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  close = vi.fn()
  constructor(public url: string) { ControlledSocket.instances.push(this) }
  open() { this.onopen?.() }
  send(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }) }
  disconnect() { this.onclose?.() }
}
const latest = () => ControlledSocket.instances.at(-1)!
const entry = (id: number): LogEntry => ({ id: `file:${id}`, timestamp: '2026-10-26T12:00:00', level: 'INFO', file: 'source', line: 1, message: `record-${id}` })
const cleanup: (() => void)[] = []
function mountSession(id = 'demo') {
  const pluginId = ref(id)
  let session!: ReturnType<typeof useLogSession>
  const app = createApp(defineComponent({ setup() {
    session = useLogSession(pluginId)
    return () => h('div', session.logs.value.length)
  } }))
  const root = document.createElement('div')
  app.mount(root)
  cleanup.push(() => app.unmount())
  return { session, pluginId, root }
}
beforeEach(() => {
  vi.useFakeTimers()
  ControlledSocket.instances = []
  vi.stubGlobal('WebSocket', ControlledSocket)
})
afterEach(() => {
  cleanup.splice(0).forEach(dispose => dispose())
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('one log session per viewer', () => {
  it('keeps the viewer mounted when URL construction fails and allows an explicit retry', () => {
    vi.spyOn(logApi, 'getPluginLogStreamUrl').mockImplementationOnce(() => {
      throw new TypeError('Invalid URL')
    })
    const { session } = mountSession()
    expect(ControlledSocket.instances).toHaveLength(0)
    expect(session.loading.value).toBe(false)
    expect(session.connectionFailed.value).toBe(true)
    session.refresh()
    expect(ControlledSocket.instances).toHaveLength(1)
    latest().open()
    expect(session.connectionFailed.value).toBe(false)
  })

  it('requests 500 records through one stream and never overwrites them with an HTTP snapshot', async () => {
    const http = vi.mocked(httpGet)
    const { session, root } = mountSession()
    expect(new URL(latest().url).searchParams.get('lines')).toBe('500')
    latest().open()
    expect(session.loading.value).toBe(true)
    latest().send({ type: 'initial', logs: Array.from({ length: 500 }, (_, index) => entry(index)), log_file: 'demo.log' })
    await nextTick()
    expect(root.textContent).toBe('500')
    expect(session.loading.value).toBe(false)
    expect(session.logFile.value).toBe('demo.log')
    expect(http).not.toHaveBeenCalled()
    expect(ControlledSocket.instances).toHaveLength(1)
  })

  it('refreshes the full current query, ignores stale results and switches targets cleanly', async () => {
    const { session, pluginId } = mountSession()
    const stale = latest().onmessage!
    session.lines.value = 1000
    session.level.value = 'WARNING'
    session.search.value = ' needle & text '
    session.refresh()
    const params = new URL(latest().url).searchParams
    expect(Object.fromEntries(params)).toEqual({ lines: '1000', search_scope: 'all', level: 'WARNING', search: 'needle & text' })
    latest().send({ type: 'initial', logs: [entry(2)] })
    stale({ data: JSON.stringify({ type: 'initial', logs: [entry(1)] }) })
    expect(session.logs.value.map(log => log.id)).toEqual(['file:2'])
    pluginId.value = 'another plugin'
    await nextTick()
    expect(latest().url).toContain('/ws/logs/another%20plugin?')
    expect(session.logs.value).toEqual([])
  })

  it('keeps two viewers of the same plugin independent and derives count from the real list', async () => {
    const first = mountSession().session
    const firstSocket = latest()
    const second = mountSession().session
    const secondSocket = latest()
    firstSocket.send({ type: 'initial', logs: [entry(1)], total_lines: 1, returned_lines: 1 })
    secondSocket.send({ type: 'initial', logs: [entry(2)] })
    firstSocket.send({ type: 'append', logs: [entry(1), entry(3), entry(3)] })
    expect(first.logs.value.map(log => log.id)).toEqual(['file:1', 'file:3'])
    expect(second.logs.value.map(log => log.id)).toEqual(['file:2'])
    expect(first.logs.value.length).toBe(2)
    firstSocket.send({ type: 'initial', reset: true, logs: [entry(4)] })
    expect(first.logs.value).toEqual([entry(4)])
    await nextTick()
  })

  it('bounds the viewer buffer without collapsing repeated messages from older servers', () => {
    const { session } = mountSession()
    latest().send({ type: 'initial', logs: Array.from({ length: 5000 }, (_, index) => entry(index)) })
    const repeated = { ...entry(5000), id: undefined }
    latest().send({ type: 'append', logs: [repeated, repeated] })
    expect(session.logs.value).toHaveLength(5000)
    expect(session.logs.value.slice(-2)).toEqual([repeated, repeated])
  })

  it('shows read errors separately from connection state and recovers on a new snapshot', () => {
    const { session } = mountSession()
    latest().open()
    latest().send({ type: 'error', error: 'Cannot read log directory' })
    expect(session.isConnected.value).toBe(true)
    expect(session.loading.value).toBe(false)
    expect(session.error.value).toBe('Cannot read log directory')
    latest().send({ type: 'initial', logs: [] })
    expect(session.error.value).toBeNull()
  })

  it('reconnects using the applied query and cancels sockets and timers when unmounted', async () => {
    const { session } = mountSession()
    session.search.value = 'applied'
    session.refresh()
    const appliedUrl = latest().url
    session.search.value = 'draft'
    latest().disconnect()
    await vi.advanceTimersByTimeAsync(1200)
    expect(latest().url).toBe(appliedUrl)
    latest().disconnect()
    mountSession('active-on-unmount')
    cleanup.splice(0).forEach(dispose => dispose())
    const count = ControlledSocket.instances.length
    await vi.advanceTimersByTimeAsync(20000)
    expect(ControlledSocket.instances).toHaveLength(count)
    expect(vi.getTimerCount()).toBe(0)
    expect(latest().close).toHaveBeenCalledOnce()
  })

  it('reports exhausted reconnects and lets explicit refresh start a fresh session', async () => {
    const { session } = mountSession()
    for (let attempt = 0; attempt < 6; attempt++) {
      latest().disconnect()
      await vi.advanceTimersByTimeAsync(8000)
    }
    expect(session.connectionFailed.value).toBe(true)
    expect(session.loading.value).toBe(false)
    session.refresh()
    latest().open()
    expect(session.connectionFailed.value).toBe(false)
  })
})

describe('server log page uses the shared viewer session', () => {
  it('loads once and routes both refresh buttons through the viewer query', async () => {
    const root = document.createElement('div')
    const app = createApp(Logs)
    const container = defineComponent({ setup: (_, { slots }) => () => h('div', [slots.header?.(), slots.default?.()]) })
    for (const name of ['ElCard', 'ElSpace', 'ElTag', 'ElAlert', 'ElEmpty', 'ElSwitch', 'ElSelect', 'ElOption', 'ElInputNumber', 'ElIcon']) app.component(name, container)
    app.component('ElButton', defineComponent({ setup: (_, { attrs, slots }) => () => h('button', attrs, slots.default?.()) }))
    app.component('ElInput', defineComponent({
      props: ['modelValue'], emits: ['update:modelValue'],
      setup: (props, { attrs, emit }) => () => h('input', { ...attrs, value: props.modelValue, onInput: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).value) }),
    }))
    app.config.globalProperties.$t = (key: string, params?: unknown) => {
      const count = typeof params === 'object' && params !== null && 'count' in params ? params.count : undefined
      return count === undefined ? key : `${key}:${count}`
    }
    app.mount(root)
    cleanup.push(() => app.unmount())
    expect(ControlledSocket.instances).toHaveLength(1)
    latest().send({ type: 'initial', logs: [entry(1)] })
    await nextTick()
    const input = root.querySelector<HTMLInputElement>('[data-yui-guide-id="log-search"]')!
    input.value = 'current search'
    input.dispatchEvent(new Event('input'))
    await nextTick()
    root.querySelector<HTMLButtonElement>('[data-yui-guide-id="logs-refresh"]')!.click()
    expect(new URL(latest().url).searchParams.get('search')).toBe('current search')
    latest().send({ type: 'initial', logs: [{ ...entry(1), message: 'current search first' }] })
    latest().send({ type: 'append', logs: [{ ...entry(2), message: 'current search second' }] })
    await nextTick()
    expect(root.querySelector('[data-yui-guide-id="log-meta"]')?.textContent).toContain('logs.totalLogs:2')
    root.querySelector<HTMLButtonElement>('[data-yui-guide-id="log-refresh"]')!.click()
    expect(new URL(latest().url).searchParams.get('search')).toBe('current search')
    expect(ControlledSocket.instances).toHaveLength(3)
  })
})
