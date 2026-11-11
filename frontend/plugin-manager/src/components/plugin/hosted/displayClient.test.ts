// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Window } from 'happy-dom'
import { readFileSync } from 'node:fs'
import { buildUiKitBundle } from './uiKitBundle'
import type { PluginDisplayApi, PluginDisplayState } from '../../../../../../plugin/sdk/hosted-ui'

const runtimeSource = buildUiKitBundle().runtime
const staticSource = readFileSync(
  new URL('../../../../../../static/plugin-ui-sdk/display.js', import.meta.url),
  'utf8'
)
const windows: Window[] = []
type Request = { requestId: string; documentId: string; connectionId: string; method?: string }
function client(kind: 'static' | 'tsx', ambientModule?: { exports: object }) {
  const child = new Window({
    url: kind === 'static' ? 'https://host.example/plugin/example/ui/' : 'about:blank',
  })
  const parent = new Window({ url: 'https://host.example/ui/' })
  windows.push(child, parent)
  Object.defineProperty(child, 'parent', { value: parent })
  Object.defineProperty(child.document, 'readyState', { value: 'loading' })
  Object.defineProperty(child, '__NEKO_PAYLOAD', {
    value: { host: { origin: parent.location.origin } },
  })
  const post = vi.spyOn(parent, 'postMessage').mockImplementation(() => {})
  const source = kind === 'static' ? staticSource : runtimeSource
  const api = new Function(
    'window',
    'document',
    'parent',
    'setTimeout',
    'clearTimeout',
    'module',
    source +
      '\nreturn ' +
      (kind === 'static' ? 'window.NekoPluginDisplay' : 'window.NekoUiKit.api.display') +
      ';'
  )(
    child,
    child.document,
    parent,
    child.setTimeout.bind(child),
    child.clearTimeout.bind(child),
    ambientModule
  ) as PluginDisplayApi
  function receive(data: object) {
    child.dispatchEvent(
      new child.MessageEvent('message', { source: parent, origin: parent.location.origin, data })
    )
  }
  function connect(
    connectionId = 'host-1',
    state: PluginDisplayState = { mode: 'normal', supportedModes: ['focus'] }
  ) {
    receive({ type: 'neko-plugin-display-connect', connectionId })
    const ready = post.mock.calls.at(-1)![0] as Request
    receive({
      type: 'neko-plugin-display-connected',
      documentId: ready.documentId,
      connectionId,
      state,
    })
    return { documentId: ready.documentId, connectionId }
  }
  function show(state?: PluginDisplayState) {
    child.dispatchEvent(new child.Event('pageshow'))
    return connect('host-1', state)
  }
  function respond(request: Request, result: unknown, extra: object = {}) {
    receive({ ...request, type: 'neko-hosted-surface-response', ok: true, result, ...extra })
  }
  return { child, api, post, receive, connect, show, respond }
}
afterEach(() => windows.splice(0).forEach((window) => window.close()))

const flushKeyboard = () => new Promise<void>((resolve) => setTimeout(resolve, 0))

describe('shared display document lifecycle', () => {
  it('exits manual focus on unhandled Escape, respects handled Escape, and sends only one pending exit', async () => {
    const { child, post, show, respond } = client('static')
    show({ mode: 'focus', supportedModes: ['focus'] })
    post.mockClear()
    const handled = new child.KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
    child.dispatchEvent(handled)
    handled.preventDefault()
    await flushKeyboard()
    expect(post).not.toHaveBeenCalled()
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape' }))
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape', repeat: true }))
    await flushKeyboard()
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0]![0]).toMatchObject({
      method: 'display.exit',
      payload: { reason: 'escape' },
    })
    respond(post.mock.calls[0]![0] as Request, {
      status: 'failed',
      state: { mode: 'focus', supportedModes: ['focus'] },
    })
    await flushKeyboard()
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape' }))
    await flushKeyboard()
    expect(post).toHaveBeenCalledTimes(2)
    respond(post.mock.calls[1]![0] as Request, {
      status: 'applied',
      state: { mode: 'normal', supportedModes: ['focus'] },
    })
  })
  it('disposes a document, cancels confirmation, and aborts readonly queries', async () => {
    const { child, api, post, show } = client('static')
    const identity = show()
    const confirmation = api.requestMode('fullscreen')
    const query = api.getState()
    const rejected = expect(query).rejects.toMatchObject({ name: 'AbortError' })
    child.dispatchEvent(new child.Event('pagehide'))
    expect(post).toHaveBeenLastCalledWith(
      { type: 'neko-plugin-display-dispose', ...identity },
      'https://host.example'
    )
    expect((await confirmation).status).toBe('cancelled')
    await rejected
  })
  it('does not apply a deferred Escape to a replacement connection', async () => {
    const { child, post, show, connect, respond } = client('static')
    show({ mode: 'focus', supportedModes: ['focus'] })
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape' }))
    connect('host-2', { mode: 'focus', supportedModes: ['focus'] })
    post.mockClear()
    await flushKeyboard()
    expect(post).not.toHaveBeenCalled()
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape' }))
    await flushKeyboard()
    expect(post).toHaveBeenCalledOnce()
    respond(post.mock.calls[0]![0] as Request, {
      status: 'applied',
      state: { mode: 'normal', supportedModes: ['focus'] },
    })
  })
  it('leaves a fullscreen Escape to the browser when the host restores prior focus', async () => {
    const { child, post, show, receive } = client('static')
    const identity = show({ mode: 'fullscreen', supportedModes: ['focus', 'fullscreen'] })
    post.mockClear()
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape' }))
    receive({
      type: 'neko-plugin-display-state',
      ...identity,
      state: { mode: 'focus', supportedModes: ['focus', 'fullscreen'] },
    })
    await flushKeyboard()
    expect(post).not.toHaveBeenCalled()
  })
  it('installs the classic-script API even when a browser page has CommonJS globals', () => {
    const exports = {}
    const ambient = { exports }
    const { api } = client('static', ambient)
    expect(api.requestMode).toBeTypeOf('function')
    expect(ambient.exports).toBe(exports)
  })
})

describe.each(['static', 'tsx'] as const)('%s display client adapter', (kind) => {
  it('queues requests until the host acknowledges and ignores stale document replies', async () => {
    const { child, api, post, connect, respond } = client(kind)
    const pending = api.requestMode('focus')
    expect(post).not.toHaveBeenCalled()
    child.dispatchEvent(new child.Event('pageshow'))
    expect(post).toHaveBeenCalledTimes(1)
    connect()
    const request = post.mock.calls.at(-1)![0] as Request
    expect(request).toMatchObject({ method: 'display.requestMode', connectionId: 'host-1' })
    let settled = false
    void pending.then(() => {
      settled = true
    })
    respond(request, { status: 'applied' }, { documentId: 'old-document' })
    await Promise.resolve()
    expect(settled).toBe(false)
    respond(request, { status: 'applied', state: { mode: 'focus', supportedModes: ['focus'] } })
    expect((await pending).status).toBe('applied')
  })
  it('reconnects after a host load reset and releases requests from the old connection', async () => {
    const { api, post, show, connect, respond } = client(kind)
    show()
    const old = api.requestMode('focus')
    connect('host-2')
    expect((await old).status).toBe('cancelled')
    const query = api.getState()
    const request = post.mock.calls.at(-1)![0] as Request
    let settled = false
    void query.then(() => {
      settled = true
    })
    respond(request, { mode: 'focus', supportedModes: ['focus'] }, { connectionId: 'host-1' })
    await Promise.resolve()
    expect(settled).toBe(false)
    respond(request, { mode: 'normal', supportedModes: ['focus'] })
    expect((await query).mode).toBe('normal')
  })
  it('refreshes state listeners when a cached document resumes', () => {
    const { child, api, show, connect } = client(kind)
    const listener = vi.fn()
    api.onStateChange(listener)
    show({ mode: 'focus', supportedModes: ['focus'] })
    child.dispatchEvent(new child.Event('pagehide'))
    child.dispatchEvent(new child.Event('pageshow'))
    connect('host-2', { mode: 'normal', supportedModes: ['focus'] })
    expect(listener).toHaveBeenLastCalledWith({ mode: 'normal', supportedModes: ['focus'] })
  })
})
