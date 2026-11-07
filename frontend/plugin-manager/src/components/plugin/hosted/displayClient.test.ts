// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Window } from 'happy-dom'
import { readFileSync } from 'node:fs'
import { buildUiKitBundle } from './uiKitBundle'

const runtimeSource = buildUiKitBundle().runtime
import type { PluginDisplayApi } from '../../../../../../plugin/sdk/hosted-ui'

const staticSource = readFileSync(
  new URL('../../../../../../static/plugin-ui-sdk/display.js', import.meta.url),
  'utf8'
)
const windows: Window[] = []
function client(kind: 'static' | 'tsx') {
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
    `${source}\nreturn ${kind === 'static' ? 'window.NekoPluginDisplay' : 'window.NekoUiKit.api.display'};`
  )(
    child,
    child.document,
    parent,
    child.setTimeout.bind(child),
    child.clearTimeout.bind(child),
    undefined
  ) as PluginDisplayApi
  function respond(
    request: { requestId?: string; documentId?: string },
    result: unknown,
    documentId = request.documentId
  ) {
    child.dispatchEvent(
      new child.MessageEvent('message', {
        source: parent,
        origin: parent.location.origin,
        data: { ...request, type: 'neko-hosted-surface-response', documentId, ok: true, result },
      })
    )
  }
  return { child, parent, api, post, respond }
}
afterEach(() => {
  windows.splice(0).forEach((window) => window.close())
})

describe('shared display document lifecycle', () => {
  const kind = 'static' as const
  it('exits focus on iframe Escape and respects a locally handled Escape', async () => {
    const { child, api, post, respond } = client(kind)
    child.dispatchEvent(new child.Event('pageshow'))
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape' }))
    await Promise.resolve()
    expect(post).toHaveBeenCalledTimes(1)
    const entering = api.requestMode('focus')
    respond(post.mock.calls.at(-1)![0] as { requestId: string; documentId: string }, {
      status: 'applied',
      state: { mode: 'focus', supportedModes: ['focus'] },
    })
    await entering
    post.mockClear()
    const handled = new child.KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
    child.dispatchEvent(handled)
    handled.preventDefault()
    await Promise.resolve()
    expect(post).not.toHaveBeenCalled()
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape' }))
    await Promise.resolve()
    const exit = post.mock.calls.at(-1)![0] as { requestId: string; documentId: string }
    expect(exit).toMatchObject({ method: 'display.exit' })
    respond(exit, { status: 'applied', state: { mode: 'normal', supportedModes: ['focus'] } })
    child.dispatchEvent(new child.KeyboardEvent('keydown', { key: 'Escape' }))
    await Promise.resolve()
    expect(post).toHaveBeenCalledTimes(1)
  })
  it('disposes an active document even without a pending display request', () => {
    const { child, post } = client(kind)
    child.dispatchEvent(new child.Event('pageshow'))
    const { documentId } = post.mock.calls[0]![0] as { documentId: string }
    post.mockClear()
    child.dispatchEvent(new child.Event('pagehide'))
    expect(post).toHaveBeenCalledWith(
      { type: 'neko-plugin-display-dispose', documentId },
      'https://host.example'
    )
  })
  it('cancels confirmation but rejects a disposed readonly query with AbortError', async () => {
    const { child, api } = client(kind)
    child.dispatchEvent(new child.Event('pageshow'))
    const confirmation = api.requestMode('fullscreen')
    const query = api.getState()
    const rejected = expect(query).rejects.toMatchObject({ name: 'AbortError' })
    child.dispatchEvent(new child.Event('pagehide'))
    expect((await confirmation).status).toBe('cancelled')
    await rejected
  })
})

describe.each(['static', 'tsx'] as const)('%s display client adapter', (kind) => {
  it('queues early requests until pageshow and ignores replies from an old document', async () => {
    const { child, api, post, respond } = client(kind)
    const promise = api.requestMode('focus')
    expect(post).not.toHaveBeenCalled()
    child.dispatchEvent(new child.Event('pageshow'))
    expect(post).toHaveBeenCalledTimes(2)
    const ready = post.mock.calls[0]![0]
    const request = post.mock.calls[1]![0] as { requestId: string; documentId: string }
    expect(ready).toMatchObject({
      type: 'neko-plugin-display-ready',
      documentId: request.documentId,
    })
    let settled = false
    void promise.then(() => {
      settled = true
    })
    respond(request, { status: 'applied' }, 'old-document')
    await Promise.resolve()
    expect(settled).toBe(false)
    respond(request, { status: 'applied', state: { mode: 'focus', supportedModes: ['focus'] } })
    expect((await promise).status).toBe('applied')
  })
})
