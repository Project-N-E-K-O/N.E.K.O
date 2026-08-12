// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import HostedSurfaceFrame from '../HostedSurfaceFrame.vue'
import type { PluginUiSurface } from '@/types/api'

const apiMocks = vi.hoisted(() => ({
  callPluginHostedSurfaceAction: vi.fn(),
  getPluginHostedSurfaceContext: vi.fn(),
  getPluginHostedSurfaceSource: vi.fn(),
  parseHostedDocument: vi.fn(),
}))

vi.mock('@/api/plugins', () => apiMocks)

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: { value: 'zh-CN' },
    t: (key: string) => key,
  }),
}))

vi.mock('@element-plus/icons-vue', () => ({
  Document: defineComponent(() => () => h('span')),
  Loading: defineComponent(() => () => h('span')),
  WarningFilled: defineComponent(() => () => h('span')),
}))

type MountedFrame = {
  dispatchRequest: (data: Record<string, unknown>) => void
  postMessage: ReturnType<typeof vi.fn>
  setSurface: (surface: PluginUiSurface) => Promise<void>
  setSurfaceUrl: (url: string) => Promise<void>
  unmount: () => void
}

const mountedFrames: MountedFrame[] = []

function makeSurface(id = 'main', url = 'data:text/html,<html></html>'): PluginUiSurface {
  return {
    id,
    kind: 'panel',
    mode: 'static',
    title: 'Study companion',
    available: true,
    url,
  } as PluginUiSurface
}

function makeDocumentSurface(permissions = ['document:parse']): PluginUiSurface {
  return { ...makeSurface(), permissions }
}

function makeNotRunningError() {
  return Object.assign(new Error('Plugin is not running'), {
    response: {
      status: 409,
      data: { detail: 'Plugin is not running' },
      headers: { 'x-error-code': 'PLUGIN_NOT_RUNNING' },
    },
  })
}

function makeServerError() {
  return Object.assign(new Error('Internal failure'), {
    response: {
      status: 500,
      data: { detail: 'Internal failure' },
      headers: { 'x-error-code': 'PLUGIN_ACTION_FAILED' },
    },
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
}

async function mountFrame(): Promise<MountedFrame> {
  const state = reactive({
    pluginId: 'study_companion',
    surface: makeSurface(),
  })
  const container = document.createElement('div')
  document.body.appendChild(container)
  const app = createApp(defineComponent({
    setup() {
      return () => h(HostedSurfaceFrame, {
        pluginId: state.pluginId,
        surface: state.surface,
      })
    },
  }))
  const elementStub = defineComponent(() => () => h('div'))
  app.component('el-alert', elementStub)
  app.component('el-icon', elementStub)
  app.component('el-tag', elementStub)
  app.mount(container)
  await nextTick()

  const iframe = container.querySelector('iframe') as HTMLIFrameElement | null
  if (!iframe?.contentWindow) throw new Error('Hosted surface iframe was not mounted')
  const iframeWindow = iframe.contentWindow
  const postMessage = vi.fn()
  iframeWindow.postMessage = postMessage

  let active = true
  const mounted: MountedFrame = {
    dispatchRequest(data) {
      window.dispatchEvent(new MessageEvent('message', {
        data,
        origin: 'null',
        source: iframeWindow,
      }))
    },
    postMessage,
    async setSurface(surface) {
      state.surface = surface
      await nextTick()
      const currentIframe = container.querySelector('iframe') as HTMLIFrameElement | null
      if (currentIframe?.contentWindow) currentIframe.contentWindow.postMessage = postMessage
    },
    async setSurfaceUrl(url) {
      state.surface.url = url
      await nextTick()
    },
    unmount() {
      if (!active) return
      active = false
      app.unmount()
      container.remove()
    },
  }
  mountedFrames.push(mounted)
  return mounted
}

function callRequest(options: { userInitiated?: boolean; requestId?: string; timeoutMs?: number } = {}) {
  return {
    type: 'neko-hosted-surface-request',
    requestId: options.requestId || 'request-1',
    method: 'call',
    userInitiated: options.userInitiated === true,
    timeoutMs: options.timeoutMs,
    payload: {
      actionId: 'study_status',
      args: {},
    },
  }
}

function parseDocumentRequest(file: File, options: { userInitiated?: boolean; requestId?: string; timeoutMs?: number } = {}) {
  return {
    type: 'neko-hosted-surface-request',
    requestId: options.requestId || 'parse-1',
    method: 'parseDocument',
    userInitiated: options.userInitiated === true,
    timeoutMs: options.timeoutMs,
    payload: { file },
  }
}

describe('HostedSurfaceFrame automatic startup retry', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    apiMocks.callPluginHostedSurfaceAction.mockReset()
    apiMocks.getPluginHostedSurfaceContext.mockReset()
    apiMocks.getPluginHostedSurfaceSource.mockReset()
    apiMocks.parseHostedDocument.mockReset()
  })

  afterEach(() => {
    while (mountedFrames.length > 0) mountedFrames.pop()?.unmount()
    vi.useRealTimers()
  })

  it('retries an automatic PLUGIN_NOT_RUNNING response until the host becomes ready', async () => {
    apiMocks.callPluginHostedSurfaceAction
      .mockRejectedValueOnce(makeNotRunningError())
      .mockRejectedValueOnce(makeNotRunningError())
      .mockResolvedValueOnce({ running: true })
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(3)
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'request-1', ok: true, result: { running: true } }),
      '*',
    )
  })

  it('returns the real error once after the five startup retries are exhausted', async () => {
    const attemptTimes: number[] = []
    apiMocks.callPluginHostedSurfaceAction.mockImplementation(() => {
      attemptTimes.push(Date.now())
      return Promise.reject(makeNotRunningError())
    })
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(6)
    expect(attemptTimes).toEqual([0, 100, 300, 700, 1500, 3100])
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        requestId: 'request-1',
        ok: false,
        error: 'Plugin is not running',
        code: 'PLUGIN_NOT_RUNNING',
        status: 409,
      }),
      '*',
    )
  })

  it('does not retry a user-initiated request', async () => {
    apiMocks.callPluginHostedSurfaceAction.mockRejectedValue(makeNotRunningError())
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest({ userInitiated: true }))
    await flushPromises()
    await vi.runAllTimersAsync()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
  })

  it('does not retry an automatic request with a different error code', async () => {
    apiMocks.callPluginHostedSurfaceAction.mockRejectedValue(makeServerError())
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await flushPromises()
    await vi.runAllTimersAsync()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
  })

  it('keeps retries inside the iframe request deadline and passes only the remaining timeout', async () => {
    apiMocks.callPluginHostedSurfaceAction.mockRejectedValue(makeNotRunningError())
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest({ timeoutMs: 250 }))
    await vi.advanceTimersByTimeAsync(250)
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(2)
    expect(apiMocks.callPluginHostedSurfaceAction.mock.calls[0]?.[3]).toMatchObject({ timeoutMs: 250 })
    expect(apiMocks.callPluginHostedSurfaceAction.mock.calls[1]?.[3]).toMatchObject({ timeoutMs: 150 })
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ ok: false, code: 'PLUGIN_NOT_RUNNING' }),
      '*',
    )
  })

  it('does not start another retry when the remaining deadline cannot fit the backoff', async () => {
    apiMocks.callPluginHostedSurfaceAction.mockRejectedValue(makeNotRunningError())
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest({ timeoutMs: 150 }))
    await vi.advanceTimersByTimeAsync(150)
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(2)
    expect(apiMocks.callPluginHostedSurfaceAction.mock.calls[1]?.[3]).toMatchObject({ timeoutMs: 50 })
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
  })

  it('does not schedule a retry when the request deadline equals the first backoff', async () => {
    apiMocks.callPluginHostedSurfaceAction.mockRejectedValue(makeNotRunningError())
    const frame = await mountFrame()
    const existingTimerCount = vi.getTimerCount()

    frame.dispatchRequest(callRequest({ timeoutMs: 100 }))
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(existingTimerCount)
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ ok: false, code: 'PLUGIN_NOT_RUNNING' }),
      '*',
    )
  })

  it('does not send a late response after the component is unmounted', async () => {
    const pending = deferred<Record<string, unknown>>()
    apiMocks.callPluginHostedSurfaceAction.mockReturnValue(pending.promise)
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await flushPromises()
    frame.unmount()
    pending.resolve({ running: true })
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('does not schedule backoff when the surface changes before an in-flight not-running response', async () => {
    const pending = deferred<Record<string, unknown>>()
    apiMocks.callPluginHostedSurfaceAction.mockReturnValue(pending.promise)
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await flushPromises()
    await frame.setSurface(makeSurface('secondary'))
    const timerCountAfterSurfaceChange = vi.getTimerCount()
    pending.reject(makeNotRunningError())
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(timerCountAfterSurfaceChange)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('does not send a late response after the hosted surface changes', async () => {
    const pending = deferred<Record<string, unknown>>()
    apiMocks.callPluginHostedSurfaceAction.mockReturnValue(pending.promise)
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await flushPromises()
    await frame.setSurface(makeSurface('secondary'))
    pending.resolve({ running: true })
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('does not continue retrying when the hosted surface changes during backoff', async () => {
    apiMocks.callPluginHostedSurfaceAction.mockRejectedValue(makeNotRunningError())
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await flushPromises()
    await frame.setSurface(makeSurface('secondary'))
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('invalidates a retry when a static surface URL changes with the same id', async () => {
    apiMocks.callPluginHostedSurfaceAction.mockRejectedValue(makeNotRunningError())
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await flushPromises()
    await frame.setSurfaceUrl('data:text/html,<html>replacement</html>')
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('uploads an allowed PDF from a user action and returns the unwrapped document', async () => {
    const document = { name: 'notes.pdf', sourceType: 'pdf', content: 'notes' }
    apiMocks.parseHostedDocument.mockResolvedValue({ ok: true, document })
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes.pdf', { type: 'application/pdf' }),
      { userInitiated: true, timeoutMs: 5000 },
    ))
    await flushPromises()

    expect(apiMocks.parseHostedDocument).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'notes.pdf' }),
      expect.objectContaining({ timeoutMs: 5000, signal: expect.any(AbortSignal) }),
    )
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'parse-1', ok: true, result: document }),
      '*',
    )
  })

  it('allows an octet-stream DOCX and leaves signature validation to the backend', async () => {
    const document = { name: 'notes.docx', sourceType: 'docx', content: 'notes' }
    apiMocks.parseHostedDocument.mockResolvedValue({ ok: true, document })
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['docx'], 'notes.docx', { type: 'application/octet-stream' }),
      { userInitiated: true },
    ))
    await flushPromises()

    expect(apiMocks.parseHostedDocument).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'notes.docx' }),
      expect.any(Object),
    )
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'parse-1', ok: true, result: document }),
      '*',
    )
  })

  it('rejects document parsing without the dedicated surface permission', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface([]))

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes.pdf', { type: 'application/pdf' }),
      { userInitiated: true },
    ))
    await flushPromises()

    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ ok: false, code: 'document_parse_permission_denied' }),
      '*',
    )
  })

  it('rejects non-user-initiated and oversized document requests before upload', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())
    const pdf = new File(['pdf'], 'notes.pdf', { type: 'application/pdf' })

    frame.dispatchRequest(parseDocumentRequest(pdf))
    await flushPromises()
    expect(frame.postMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ ok: false, code: 'document_parse_permission_denied' }),
      '*',
    )

    const oversized = new File(['x'], 'large.pdf', { type: 'application/pdf' })
    Object.defineProperty(oversized, 'size', { value: 16 * 1024 * 1024 + 1 })
    frame.dispatchRequest(parseDocumentRequest(oversized, { requestId: 'parse-large', userInitiated: true }))
    await flushPromises()

    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
    expect(frame.postMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ requestId: 'parse-large', ok: false, code: 'document_too_large' }),
      '*',
    )
  })

  it('rejects unsupported document types before upload', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['text'], 'notes.txt', { type: 'text/plain' }),
      { userInitiated: true },
    ))
    await flushPromises()

    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ ok: false, code: 'unsupported_document' }),
      '*',
    )
  })

  it('preserves a backend document error code in the hosted response', async () => {
    apiMocks.parseHostedDocument.mockRejectedValue(Object.assign(new Error('No readable text'), {
      response: {
        status: 422,
        data: { detail: { code: 'no_readable_text', message: 'No readable text' } },
      },
    }))
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'scan.pdf', { type: 'application/pdf' }),
      { userInitiated: true },
    ))
    await flushPromises()

    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        ok: false,
        code: 'no_readable_text',
        error: 'No readable text',
        status: 422,
      }),
      '*',
    )
  })

  it('aborts and returns the document timeout error code at the request deadline', async () => {
    apiMocks.parseHostedDocument.mockImplementation((_file: File, options: { signal: AbortSignal }) => (
      new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('canceled'))))
    ))
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes.pdf', { type: 'application/pdf' }),
      { userInitiated: true, timeoutMs: 100 },
    ))
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()

    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ ok: false, code: 'document_parse_timeout' }),
      '*',
    )
  })

  it('aborts an in-flight upload when the surface changes', async () => {
    let signal: AbortSignal | undefined
    apiMocks.parseHostedDocument.mockImplementation((_file: File, options: { signal: AbortSignal }) => {
      signal = options.signal
      return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('canceled'))))
    })
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes.pdf', { type: 'application/pdf' }),
      { userInitiated: true },
    ))
    await flushPromises()
    await frame.setSurface({ ...makeDocumentSurface(), id: 'secondary' })
    await flushPromises()

    expect(signal?.aborted).toBe(true)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })
})
