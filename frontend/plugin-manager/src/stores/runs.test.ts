// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRunsStore, type RunRecord } from './runs'
import { useConnectionStore } from './connection'

vi.mock('@/utils/constants', () => ({ API_BASE_URL: 'http://127.0.0.1:48916' }))
vi.mock('@/api/index', () => ({ post: vi.fn() }))

class Socket {
  static CONNECTING = 0
  static OPEN = 1
  get [Symbol.toStringTag]() { return 'WebSocket' }
  readyState = 0
  onopen: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  sent: { id: string; method: string; params: Record<string, unknown>; type: string }[] = []
  close = vi.fn(() => { this.readyState = 2 })
  send = vi.fn((data: string) => { this.sent.push(JSON.parse(data)) })
  constructor(readonly url: string) { sockets.push(this) }
  open() { this.readyState = 1; this.onopen?.() }
  closed(code = 1006) {
    this.readyState = 3
    this.onclose?.(new CloseEvent('close', { code, reason: 'probe close', wasClean: code === 1000 }))
  }
  message(value: unknown) { this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(value) })) }
  reply(method: string, result: unknown) {
    const request = this.sent.find(frame => frame.method === method)!
    this.message({ type: 'resp', id: request.id, ok: true, result })
  }
}

let sockets: Socket[]
let store: ReturnType<typeof useRunsStore>
const flush = () => vi.advanceTimersByTimeAsync(0)
const record = (id = 'r'): RunRecord => ({ run_id: id, plugin_id: 'p', entry_id: 'e', status: 'running', created_at: 1, updated_at: 1 })

async function openConnection() {
  const pending = store.connect()
  const socket = sockets.at(-1)!
  socket.open()
  await pending
  return socket
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  vi.stubGlobal('WebSocket', Socket)
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  sockets = []
  store = useRunsStore()
})

afterEach(() => {
  store.$dispose()
  expect(vi.getTimerCount()).toBe(0)
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('run connection lifecycle', () => {
  it('shares connection readiness across concurrent calls, then routes independent replies and events', async () => {
    let connectedCallers = 0
    const ready = store.connect().then(() => { connectedCallers++ })
    const concurrent = store.connect().then(() => { connectedCallers++ })
    const run = store.loadRun('r')
    const exports = store.loadExports('r')
    await flush()
    expect(connectedCallers).toBe(0)
    expect(sockets).toHaveLength(1)
    expect(sockets[0]!.sent).toHaveLength(0)
    sockets[0]!.open()
    await Promise.all([ready, concurrent])
    expect(connectedCallers).toBe(2)
    await flush()
    expect(sockets[0]!.sent.map(frame => frame.method)).toEqual(['run.get', 'export.list'])
    sockets[0]!.reply('export.list', { items: [] })
    sockets[0]!.reply('run.get', record())
    await expect(run).resolves.toEqual(record())
    await expect(exports).resolves.toEqual([])
    sockets[0]!.message({ type: 'ping' })
    expect(sockets[0]!.sent.at(-1)).toEqual({ type: 'pong' })
    sockets[0]!.message({ type: 'event', event: 'bus.change', data: { bus: 'runs', payload: { run_id: 'r', status: 'succeeded' } } })
    expect(store.runsById.r?.status).toBe('succeeded')
    const cancel = store.cancelRun('r')
    await flush()
    sockets[0]!.reply('run.cancel', { ...record(), status: 'cancel_requested' })
    await expect(cancel).resolves.toMatchObject({ status: 'cancel_requested' })
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['before', 'after'])('ignores old open/close/message callbacks %s the new connection opens', async timing => {
    const old = await openConnection()
    const stale = { open: old.onopen!, close: old.onclose!, message: old.onmessage! }
    store.disconnect()
    const pending = store.loadRun('fresh')
    const fresh = sockets[1]!
    const staleEvents = () => {
      stale.open()
      stale.close(new CloseEvent('close', { code: 1000 }))
      stale.message(new MessageEvent('message', { data: JSON.stringify({
        type: 'event', event: 'bus.change', data: { bus: 'runs', payload: record('stale') },
      }) }))
    }
    if (timing === 'before') {
      staleEvents()
      expect(store.connected).toBe(false)
      expect(store.connecting).toBe(true)
    }
    fresh.open()
    if (timing === 'after') staleEvents()
    await flush()
    stale.message(new MessageEvent('message', { data: JSON.stringify({ type: 'resp', id: fresh.sent[0]!.id, ok: true, result: record('forged') }) }))
    fresh.reply('run.get', record('fresh'))
    await expect(pending).resolves.toEqual(record('fresh'))
    expect(store.connected).toBe(true)
    expect(store.runsById.stale).toBeUndefined()
    expect(store.runsById.forged).toBeUndefined()
  })

  it('cancels every waiter on page exit without affecting an immediate reconnect', async () => {
    const first = store.connect().catch(error => error)
    const second = store.refreshRuns().catch(error => error)
    store.disconnect()
    store.disconnect()
    expect((await first).code).toBe('cancelled')
    expect((await second).code).toBe('cancelled')
    expect(sockets[0]!.close).toHaveBeenCalledOnce()
    expect(sockets[0]!.onclose).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
    await openConnection()
    expect(store.connected).toBe(true)
    expect(store.lastError).toBeNull()
  })

  it('closes a timed-out connection and ignores its late open during the next attempt', async () => {
    const pending = store.connect().catch(error => error)
    const old = sockets[0]!
    const lateOpen = old.onopen!
    await vi.advanceTimersByTimeAsync(5000)
    expect((await pending).code).toBe('connection_timeout')
    expect(old.close).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
    const retry = store.connect()
    lateOpen()
    expect(store.connected).toBe(false)
    sockets[1]!.open()
    await retry
    expect(store.connected).toBe(true)
  })

  it('reports real handshake failures with diagnostics without marking the whole service offline', async () => {
    const health = useConnectionStore()
    const disconnected = vi.spyOn(health, 'markDisconnected')
    for (let i = 0; i < 3; i++) {
      const pending = store.connect().catch(error => error)
      sockets.at(-1)!.closed()
      expect((await pending).code).toBe('connection_failed')
    }
    expect(console.warn).toHaveBeenCalledWith('[runs-ws]', expect.objectContaining({
      code: 'connection_failed', endpoint: 'ws://127.0.0.1:48916/ws/admin', closeCode: 1006,
    }))
    expect(disconnected).not.toHaveBeenCalled()
    expect(health.disconnected).toBe(false)
    expect(store.lastError).toBe('connection_failed')
    const interrupted = store.loadRun('r').catch(error => error)
    sockets.at(-1)!.open()
    sockets.at(-1)!.closed()
    expect((await interrupted).code).toBe('connection_closed')
  })

  it.each(['close', 'timeout'])('does not replay cancellation after a lost reply (%s)', async failure => {
    const socket = await openConnection()
    const cancel = store.cancelRun('r').catch(error => error)
    const read = store.loadRun('r').catch(error => error)
    await flush()
    if (failure === 'close') socket.closed()
    else await vi.advanceTimersByTimeAsync(5000)
    expect((await cancel).code).toBe('outcome_unknown')
    expect((await read).code).toBe(failure === 'close' ? 'connection_closed' : 'request_timeout')
    expect(sockets).toHaveLength(1)
    expect(socket.sent.filter(frame => frame.method === 'run.cancel')).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cleans up a failed send so future reads still work', async () => {
    const socket = await openConnection()
    socket.send.mockImplementationOnce(() => { throw new Error('send failed') })
    await expect(store.loadRun('r')).rejects.toMatchObject({ code: 'request_failed' })
    expect(vi.getTimerCount()).toBe(0)
    const retry = store.loadRun('r')
    await flush()
    socket.reply('run.get', record())
    await expect(retry).resolves.toEqual(record())
    const rejected = store.loadRun('gone').catch(error => error)
    await flush()
    socket.message({ type: 'resp', id: socket.sent.at(-1)!.id, ok: false, error: 'run not found' })
    expect((await rejected).code).toBe('request_failed')
    expect(console.warn).toHaveBeenCalledWith('[runs-ws]', expect.objectContaining({ serverError: 'run not found' }))
  })

  it('store disposal closes the connection and cancels pending RPCs', async () => {
    const socket = await openConnection()
    const pending = store.loadRun('r').catch(error => error)
    await flush()
    store.$dispose()
    expect((await pending).code).toBe('cancelled')
    expect(socket.close).toHaveBeenCalledOnce()
  })

  it('does not cache a response if the page disconnects just after the API resolves', async () => {
    const socket = await openConnection()
    const pending = store.loadRun('r').catch(error => error)
    await flush()
    socket.reply('run.get', record())
    // Let the API finish, but disconnect before the Store resumes its cache write.
    await Promise.resolve()
    store.disconnect()
    expect((await pending).code).toBe('cancelled')
    expect(store.runsById.r).toBeUndefined()
  })
})
