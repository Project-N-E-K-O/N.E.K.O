// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"enableIframePageLoading":true}}
// happy-dom 21 默认不加载 iframe 页面（SSRF 修复），iframe.contentWindow 为空；这里测的就是
// 宿主与插件 iframe 的通信，只在本文件恢复 <=20.x 的行为。本文件的 iframe 只允许 data:/about:/blob: URL 或
// srcdoc（宿主生成的内容）：assertNoNetworkIframes 在挂载、换 surface/URL 和每条用例结束时检查，违反即失败。
// （没有用 happy-dom 的 fetch 拦截器预先拦截：拦截器总会被 await，改变 iframe 加载时序，
// 会让本文件里断言计时器数量的用例失效。）

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Teleport, createApp, defineComponent, h, nextTick, reactive, ref } from 'vue'
import HostedSurfaceFrame from '../HostedSurfaceFrame.vue'
import type { PluginUiSurface } from '@/types/api'

const apiMocks = vi.hoisted(() => ({
  callPluginHostedSurfaceAction: vi.fn(),
  getPluginHostedSurfaceContext: vi.fn(),
  getPluginHostedSurfaceSource: vi.fn(),
  parseHostedDocument: vi.fn(),
}))

vi.mock('@/api/plugins', () => apiMocks)

const elementPlusMocks = vi.hoisted(() => ({
  ElMessage: vi.fn(),
  ElMessageBox: {
    confirm: vi.fn(() => Promise.resolve('confirm')),
    close: vi.fn(),
  },
}))

vi.mock('element-plus', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...elementPlusMocks,
}))

const localeRef = ref('zh-CN')
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: localeRef,
    t: (key: string) => key,
  }),
}))

vi.mock('@element-plus/icons-vue', () => ({
  Document: defineComponent(() => () => h('span')),
  Loading: defineComponent(() => () => h('span')),
  WarningFilled: defineComponent(() => () => h('span')),
}))

type MountedFrame = {
  container: HTMLDivElement
  errors: ReturnType<typeof vi.fn>
  openLogs: ReturnType<typeof vi.fn>
  dispatchRequest: (data: Record<string, unknown>) => void
  postMessage: ReturnType<typeof vi.fn>
  iframe: () => HTMLIFrameElement | null
  parseDocumentDialog: () => HTMLElement | null
  confirmParseDocument: () => Promise<void>
  cancelParseDocument: () => Promise<void>
  closeParseDocumentDialog: () => Promise<void>
  setActive: (active: boolean) => Promise<void>
  setActivationRevision: (revision: number) => Promise<void>
  setSurface: (surface: PluginUiSurface) => Promise<void>
  setSurfaceUrl: (url: string) => Promise<void>
  refreshContext: () => Promise<void>
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

function makeHostedSurface(id = 'main'): PluginUiSurface {
  return { ...makeSurface(id), mode: 'hosted-tsx', entry: 'ui/panel.tsx', url: undefined }
}

const hostedSource = {
  source: 'export default function Panel(props) { return <div>{props.state?.label || "ready"}</div> }',
  dependencies: [],
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

// Protocols that never hit the network, so they are safe with iframe page loading enabled.
const NON_NETWORK_IFRAME_PROTOCOLS = new Set(['data:', 'about:', 'blob:'])

function assertNoNetworkIframes(container: ParentNode) {
  for (const frame of container.querySelectorAll('iframe')) {
    const src = frame.getAttribute('src')
    if (src && !NON_NETWORK_IFRAME_PROTOCOLS.has(new URL(src, location.href).protocol)) {
      throw new Error(`iframe page loading is enabled in this file; use a data:/about:/blob: URL or srcdoc, got ${src}`)
    }
  }
}

async function mountFrame(initialSurface = makeSurface(), options: { requireIframe?: boolean } = {}): Promise<MountedFrame> {
  const state = reactive({
    pluginId: 'study_companion',
    surface: initialSurface,
    active: false,
    activationRevision: 0,
  })
  const container = document.createElement('div')
  document.body.appendChild(container)
  const frameRef = ref<{ refreshContext: () => Promise<void> } | null>(null)
  const errors = vi.fn()
  const openLogs = vi.fn()
  const app = createApp(defineComponent({
    setup() {
      return () => h(HostedSurfaceFrame, {
        ref: frameRef,
        pluginId: state.pluginId,
        surface: state.surface,
        active: state.active,
        activationRevision: state.activationRevision,
        onError: errors,
        onOpenLogs: openLogs,
      })
    },
  }))
  const elementStub = defineComponent(() => () => h('div'))
  app.component('el-alert', elementStub)
  app.component('el-icon', elementStub)
  app.component('el-tag', elementStub)
  app.component('el-button', defineComponent({
    setup(_props, { slots }) {
      return () => h('button', null, slots.default?.())
    },
  }))
  app.component('el-dialog', defineComponent({
    inheritAttrs: false,
    props: {
      modelValue: { type: Boolean, default: false },
      title: { type: String, default: '' },
      appendTo: { type: [String, Object], default: 'body' },
      appendToBody: { type: Boolean, default: false },
      beforeClose: { type: Function, default: undefined },
    },
    emits: ['update:modelValue', 'close'],
    setup(props, { slots, emit, attrs }) {
      return () => (props.modelValue
        ? h(
            Teleport,
            {
              to: props.appendTo as string | HTMLElement,
              disabled: props.appendTo !== 'body' ? false : !props.appendToBody,
            },
            [
              h('div', { ...attrs, role: 'dialog', 'aria-label': props.title }, [
                h('button', {
                  type: 'button',
                  'data-testid': 'el-dialog-header-close',
                  onClick: () => {
                    if (typeof props.beforeClose === 'function') {
                      props.beforeClose(() => {
                        emit('close')
                        emit('update:modelValue', false)
                      })
                      return
                    }
                    emit('close')
                    emit('update:modelValue', false)
                  },
                }, 'close'),
                slots.default?.(),
                slots.footer?.(),
              ]),
            ],
          )
        : null)
    },
  }))
  app.mount(container)
  await nextTick()
  await flushPromises()
  if (initialSurface.mode === 'hosted-tsx' || initialSurface.mode === 'markdown') {
    await vi.dynamicImportSettled()
    await nextTick()
  }

  let iframe: HTMLIFrameElement | null
  try {
    assertNoNetworkIframes(container)
    iframe = container.querySelector('iframe') as HTMLIFrameElement | null
    if (options.requireIframe !== false && !iframe?.contentWindow) throw new Error('Hosted surface iframe was not mounted')
  } catch (error) {
    // 还没登记进 mountedFrames，afterEach 清理不到，这里自己卸载。
    app.unmount()
    container.remove()
    throw error
  }
  const iframeWindow = iframe?.contentWindow
  const postMessage = vi.fn()
  if (iframeWindow) iframeWindow.postMessage = postMessage

  let active = true
  const mounted: MountedFrame = {
    container,
    errors,
    openLogs,
    dispatchRequest(data) {
      if (!iframeWindow) throw new Error('Cannot dispatch an iframe request before the iframe mounts')
      window.dispatchEvent(new MessageEvent('message', {
        data,
        origin: 'null',
        source: iframeWindow,
      }))
    },
    postMessage,
    iframe: () => container.querySelector('iframe') as HTMLIFrameElement | null,
    parseDocumentDialog: () => document.querySelector<HTMLElement>('[data-testid="parse-document-confirm-dialog"]'),
    async confirmParseDocument() {
      const btn = document.querySelector<HTMLButtonElement>('[data-testid="parse-document-confirm-ok"]')
      if (!btn) throw new Error('Expected parseDocument confirm button')
      btn.click()
      await flushPromises()
    },
    async cancelParseDocument() {
      const btn = document.querySelector<HTMLButtonElement>('[data-testid="parse-document-confirm-cancel"]')
      if (!btn) throw new Error('Expected parseDocument cancel button')
      btn.click()
      await flushPromises()
    },
    async closeParseDocumentDialog() {
      const btn = document.querySelector<HTMLButtonElement>('[data-testid="el-dialog-header-close"]')
      if (!btn) throw new Error('Expected el-dialog header close button')
      btn.click()
      await flushPromises()
    },
    async setActive(active) {
      state.active = active
      await nextTick()
    },
    async setActivationRevision(revision) {
      state.activationRevision = revision
      await nextTick()
    },
    async setSurface(surface) {
      state.surface = surface
      await nextTick()
      assertNoNetworkIframes(container)
      const currentIframe = container.querySelector('iframe') as HTMLIFrameElement | null
      if (currentIframe?.contentWindow) currentIframe.contentWindow.postMessage = postMessage
    },
    async setSurfaceUrl(url) {
      state.surface.url = url
      await nextTick()
      assertNoNetworkIframes(container)
    },
    async refreshContext() {
      await frameRef.value?.refreshContext()
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
    elementPlusMocks.ElMessageBox.confirm.mockReset()
    elementPlusMocks.ElMessageBox.confirm.mockResolvedValue('confirm')
    elementPlusMocks.ElMessageBox.close.mockReset()
    localeRef.value = 'zh-CN'
  })

  afterEach(() => {
    try {
      // 兜底：测试过程中重渲染出来的 iframe 也要满足同一约束。
      assertNoNetworkIframes(document.body)
    } finally {
      while (mountedFrames.length > 0) mountedFrames.pop()?.unmount()
      vi.useRealTimers()
    }
  })

  it('reloads a static iframe on a locale change but not on a title change', async () => {
    const frame = await mountFrame()
    const original = frame.iframe()

    await frame.setSurface({ ...makeSurface(), title: '伴学' })
    expect(frame.iframe()).toBe(original)
    expect(frame.iframe()?.getAttribute('title')).toBe('伴学')

    localeRef.value = 'en-US'
    await nextTick()
    expect(frame.iframe()).not.toBe(original)
  })

  it('follows the app locale for markdown without asking to apply it', async () => {
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue({ source: 'Guide', dependencies: [] })
    const frame = await mountFrame({ ...makeSurface('onboarding'), kind: 'docs', mode: 'markdown', url: undefined } as PluginUiSurface)
    await flushPromises()

    localeRef.value = 'en-US'
    await nextTick()
    expect(document.querySelector('[data-testid="surface-locale-pending"]')).toBeNull()
    await flushPromises()

    expect(apiMocks.getPluginHostedSurfaceSource).toHaveBeenLastCalledWith(
      'study_companion',
      expect.objectContaining({ locale: 'en-US' }),
      expect.anything(),
    )
    expect(frame.iframe()?.getAttribute('srcdoc')).toContain('lang="en-US"')
  })

  it('keeps a hosted TSX document on a locale change until the user applies it', async () => {
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue({
      source: 'export default function Panel() { return null }',
      dependencies: [],
    })
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({ entries: [] })
    await mountFrame({ ...makeSurface('note-exporter'), mode: 'hosted-tsx', url: undefined })
    await flushPromises()
    const loads = apiMocks.getPluginHostedSurfaceSource.mock.calls.length

    localeRef.value = 'en-US'
    await nextTick()
    await flushPromises()

    expect(apiMocks.getPluginHostedSurfaceSource).toHaveBeenCalledTimes(loads)
    expect(document.querySelector('[data-testid="surface-locale-pending"]')).not.toBeNull()
  })

  it('preserves native modal dialogs for static surfaces and sandboxes hosted srcdoc without allow-same-origin', async () => {
    const staticFrame = await mountFrame()
    const staticTokens = staticFrame.iframe()?.getAttribute('sandbox')?.split(/\s+/) || []
    expect(staticTokens).toContain('allow-modals')
    expect(staticTokens).toContain('allow-same-origin')

    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(hostedSource)
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({ state: { label: 'ready' } })
    const hostedFrame = await mountFrame(makeHostedSurface())
    const hostedTokens = hostedFrame.iframe()?.getAttribute('sandbox')?.split(/\s+/) || []
    expect(hostedTokens).toContain('allow-scripts')
    expect(hostedTokens).not.toContain('allow-same-origin')
  })

  it('rejects mismatched origins on static surface postMessage', async () => {
    const frame = await mountFrame()
    const iframeWindow = frame.iframe()?.contentWindow
    expect(iframeWindow).toBeTruthy()

    window.dispatchEvent(new MessageEvent('message', {
      data: callRequest({ requestId: 'spoof-origin' }),
      origin: window.location.origin,
      source: iframeWindow,
    }))
    window.dispatchEvent(new MessageEvent('message', {
      data: callRequest({ requestId: 'evil-origin' }),
      origin: 'https://evil.example',
      source: iframeWindow,
    }))
    await flushPromises()

    expect(apiMocks.callPluginHostedSurfaceAction).not.toHaveBeenCalled()
    expect(frame.postMessage).not.toHaveBeenCalled()
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

  it('notifies an active iframe on load and revision changes without remounting it', async () => {
    const frame = await mountFrame()
    const iframe = frame.iframe()
    await frame.setActive(true)
    frame.postMessage.mockClear()

    iframe?.dispatchEvent(new Event('load'))
    expect(frame.postMessage).toHaveBeenLastCalledWith({
      type: 'neko-hosted-surface-activated',
      payload: { surfaceId: 'main', revision: 0 },
    }, '*')

    frame.postMessage.mockClear()
    await frame.setActivationRevision(4)
    expect(frame.iframe()).toBe(iframe)
    expect(frame.postMessage).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledWith({
      type: 'neko-hosted-surface-activated',
      payload: { surfaceId: 'main', revision: 4 },
    }, '*')
  })

  it('waits until a surface is active before sending its latest revision', async () => {
    const frame = await mountFrame()
    await frame.setActivationRevision(9)
    expect(frame.postMessage).not.toHaveBeenCalled()

    await frame.setActive(true)
    expect(frame.postMessage).toHaveBeenCalledWith({
      type: 'neko-hosted-surface-activated',
      payload: { surfaceId: 'main', revision: 9 },
    }, '*')
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

  it('aborts only the hosted action named by a child cancellation message', async () => {
    const signals: AbortSignal[] = []
    apiMocks.callPluginHostedSurfaceAction.mockImplementation(
      (_pluginId, _actionId, _args, options) => {
        signals.push(options.signal)
        return new Promise((_resolve, reject) => {
          options.signal?.addEventListener('abort', () => reject(new Error('canceled')))
        })
      },
    )
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest({ requestId: 'action-first' }))
    frame.dispatchRequest(callRequest({ requestId: 'action-second' }))
    await flushPromises()
    frame.dispatchRequest({ type: 'neko-hosted-surface-cancel', requestId: 'action-first' })
    await flushPromises()

    expect(signals).toHaveLength(2)
    expect(signals[0]?.aborted).toBe(true)
    expect(signals[1]?.aborted).toBe(false)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('aborts an in-flight hosted action when the surface changes', async () => {
    let signal: AbortSignal | undefined
    apiMocks.callPluginHostedSurfaceAction.mockImplementation(
      (_pluginId, _actionId, _args, options) => {
        signal = options.signal
        return new Promise((_resolve, reject) => {
          options.signal?.addEventListener('abort', () => reject(new Error('canceled')))
        })
      },
    )
    const frame = await mountFrame()

    frame.dispatchRequest(callRequest())
    await flushPromises()
    await frame.setSurface(makeSurface('secondary'))
    await flushPromises()

    expect(signal?.aborted).toBe(true)
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
    await frame.confirmParseDocument()

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
    await frame.confirmParseDocument()

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
    await frame.confirmParseDocument()

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
    await flushPromises()
    await frame.confirmParseDocument()
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()

    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ ok: false, code: 'document_parse_timeout' }),
      '*',
    )
  })

  it('rejects forged parseDocument requests when host confirmation is cancelled', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'secret.pdf', { type: 'application/pdf' }),
      { requestId: 'forged-1', userInitiated: true },
    ))
    await flushPromises()
    expect(frame.parseDocumentDialog()).not.toBeNull()

    await frame.cancelParseDocument()

    expect(frame.parseDocumentDialog()).toBeNull()
    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        requestId: 'forged-1',
        ok: false,
        code: 'document_parse_permission_denied',
      }),
      '*',
    )

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'secret2.pdf', { type: 'application/pdf' }),
      { requestId: 'forged-2', userInitiated: true },
    ))
    await flushPromises()
    expect(frame.parseDocumentDialog()).not.toBeNull()

    await frame.closeParseDocumentDialog()

    expect(frame.parseDocumentDialog()).toBeNull()
    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        requestId: 'forged-2',
        ok: false,
        code: 'document_parse_permission_denied',
      }),
      '*',
    )
  })

  it('prevents stacked confirm dialogs on concurrent parseDocument floods', async () => {
    apiMocks.parseHostedDocument.mockResolvedValue({
      ok: true,
      document: { name: 'first.pdf', sourceType: 'pdf', content: 'ok' },
    })
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'first.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-1', userInitiated: true },
    ))
    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'second.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-2', userInitiated: true },
    ))
    await flushPromises()

    expect(document.querySelectorAll('[data-testid="parse-document-confirm-dialog"]')).toHaveLength(1)
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        requestId: 'parse-2',
        ok: false,
        code: 'document_parse_permission_denied',
      }),
      '*',
    )

    await frame.confirmParseDocument()
    expect(frame.parseDocumentDialog()).toBeNull()
    expect(apiMocks.parseHostedDocument).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'parse-1', ok: true }),
      '*',
    )
  })

  it('closes confirm dialog and does not upload if surface changes, unmounts, or request cancels during host confirmation', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-surface-change', userInitiated: true },
    ))
    await flushPromises()
    expect(frame.parseDocumentDialog()).not.toBeNull()
    await frame.setSurface({ ...makeDocumentSurface(), id: 'secondary' })
    await flushPromises()

    expect(frame.parseDocumentDialog()).toBeNull()
    expect(elementPlusMocks.ElMessageBox.close).not.toHaveBeenCalled()
    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
    expect(frame.postMessage).not.toHaveBeenCalled()

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes2.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-cancel-during-confirm', userInitiated: true },
    ))
    await flushPromises()
    expect(frame.parseDocumentDialog()).not.toBeNull()
    frame.dispatchRequest({ type: 'neko-hosted-surface-cancel', requestId: 'parse-cancel-during-confirm' })
    await flushPromises()

    expect(frame.parseDocumentDialog()).toBeNull()
    expect(elementPlusMocks.ElMessageBox.close).not.toHaveBeenCalled()
    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
    expect(frame.postMessage).not.toHaveBeenCalled()

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes3.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-unmount-during-confirm', userInitiated: true },
    ))
    await flushPromises()
    expect(frame.parseDocumentDialog()).not.toBeNull()
    frame.unmount()
    await flushPromises()

    expect(frame.parseDocumentDialog()).toBeNull()
    expect(elementPlusMocks.ElMessageBox.close).not.toHaveBeenCalled()
    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
  })

  it('rejects with timeout and skips upload when confirmation settles after deadline expires', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'late.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-expired-confirm', userInitiated: true, timeoutMs: 30000 },
    ))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    await frame.confirmParseDocument()

    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        requestId: 'parse-expired-confirm',
        ok: false,
        code: 'document_parse_timeout',
      }),
      '*',
    )
  })

  it('carries remaining deadline across host confirmation into parseHostedDocument', async () => {
    apiMocks.parseHostedDocument.mockResolvedValue({
      ok: true,
      document: { name: 'notes.pdf', sourceType: 'pdf', content: 'ok' },
    })
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-remaining-deadline', userInitiated: true, timeoutMs: 30000 },
    ))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10000)
    await frame.confirmParseDocument()

    expect(apiMocks.parseHostedDocument).toHaveBeenCalledWith(
      expect.any(File),
      expect.objectContaining({ timeoutMs: 20000 }),
    )
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'parse-remaining-deadline', ok: true }),
      '*',
    )
  })

  it('releases the parseDocument confirmation gate when cancelled while confirm dialog is open', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['first'], 'first.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-cancelled-gate', userInitiated: true },
    ))
    await flushPromises()
    expect(frame.parseDocumentDialog()).not.toBeNull()

    frame.dispatchRequest({ type: 'neko-hosted-surface-cancel', requestId: 'parse-cancelled-gate' })
    await flushPromises()
    expect(frame.parseDocumentDialog()).toBeNull()
    expect(elementPlusMocks.ElMessageBox.close).not.toHaveBeenCalled()
    expect(apiMocks.parseHostedDocument).not.toHaveBeenCalled()

    apiMocks.parseHostedDocument.mockResolvedValueOnce({
      ok: true,
      document: { name: 'second.pdf', sourceType: 'pdf', content: 'ok' },
    })

    frame.dispatchRequest(parseDocumentRequest(
      new File(['second'], 'second.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-after-cancel', userInitiated: true },
    ))
    await flushPromises()
    expect(frame.parseDocumentDialog()).not.toBeNull()
    await frame.confirmParseDocument()

    expect(frame.parseDocumentDialog()).toBeNull()
    expect(apiMocks.parseHostedDocument).toHaveBeenCalledTimes(1)
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'parse-after-cancel', ok: true }),
      '*',
    )
    expect(frame.postMessage).not.toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'parse-cancelled-gate' }),
      '*',
    )
  })

  it('cancelling parseDocument confirmation closes only its own dialog without closing applyDocumentLocale ElMessageBox', async () => {
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(hostedSource)
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({ state: { label: 'ready' } })
    const frame = await mountFrame({ ...makeHostedSurface(), permissions: ['document:parse'] })
    await flushPromises()

    localeRef.value = 'en-US'
    await flushPromises()

    const localeConfirm = deferred<string>()
    elementPlusMocks.ElMessageBox.confirm.mockReturnValueOnce(localeConfirm.promise as any)
    const applyButton = frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-apply-locale"]')
    expect(applyButton).not.toBeNull()
    applyButton!.click()
    await flushPromises()
    expect(elementPlusMocks.ElMessageBox.confirm).toHaveBeenCalledTimes(1)

    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'notes.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-while-locale-open', userInitiated: true },
    ))
    await flushPromises()
    expect(frame.parseDocumentDialog()).not.toBeNull()

    frame.dispatchRequest({ type: 'neko-hosted-surface-cancel', requestId: 'parse-while-locale-open' })
    await flushPromises()
    expect(frame.parseDocumentDialog()).toBeNull()
    expect(elementPlusMocks.ElMessageBox.close).not.toHaveBeenCalled()

    localeConfirm.resolve('confirm')
    await flushPromises()
    expect(apiMocks.getPluginHostedSurfaceSource).toHaveBeenLastCalledWith(
      'study_companion',
      expect.objectContaining({ locale: 'en-US' }),
      expect.anything(),
    )
  })

  it('mounts the parseDocument confirmation dialog inside displayTarget while fullscreen and in body otherwise', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())
    const displayTarget = frame.container.querySelector<HTMLElement>('.hosted-surface-frame')
    expect(displayTarget).not.toBeNull()

    const originalDescriptor = Object.getOwnPropertyDescriptor(document, 'fullscreenElement')
    let fullscreenEl: Element | null = null
    Object.defineProperty(document, 'fullscreenElement', {
      configurable: true,
      get: () => fullscreenEl,
    })

    try {
      frame.dispatchRequest(parseDocumentRequest(
        new File(['normal'], 'normal.pdf', { type: 'application/pdf' }),
        { requestId: 'parse-normal', userInitiated: true },
      ))
      await flushPromises()
      const normalDialog = frame.parseDocumentDialog()
      expect(normalDialog).not.toBeNull()
      expect(normalDialog?.parentElement).toBe(document.body)
      expect(displayTarget!.contains(normalDialog)).toBe(false)

      fullscreenEl = displayTarget
      document.dispatchEvent(new Event('fullscreenchange'))
      await flushPromises()
      const movedToFullscreen = frame.parseDocumentDialog()
      expect(movedToFullscreen).not.toBeNull()
      expect(movedToFullscreen?.parentElement).toBe(displayTarget)
      expect(displayTarget!.contains(movedToFullscreen)).toBe(true)

      await frame.cancelParseDocument()
      expect(frame.parseDocumentDialog()).toBeNull()

      apiMocks.parseHostedDocument.mockResolvedValueOnce({
        ok: true,
        document: { name: 'fullscreen.pdf', sourceType: 'pdf', content: 'ok' },
      })
      frame.dispatchRequest(parseDocumentRequest(
        new File(['fullscreen'], 'fullscreen.pdf', { type: 'application/pdf' }),
        { requestId: 'parse-fullscreen', userInitiated: true },
      ))
      await flushPromises()
      const openedInFullscreen = frame.parseDocumentDialog()
      expect(openedInFullscreen).not.toBeNull()
      expect(openedInFullscreen?.parentElement).toBe(displayTarget)

      fullscreenEl = null
      document.dispatchEvent(new Event('fullscreenchange'))
      await flushPromises()
      const restoredToBody = frame.parseDocumentDialog()
      expect(restoredToBody).not.toBeNull()
      expect(restoredToBody?.parentElement).toBe(document.body)

      await frame.confirmParseDocument()
      expect(frame.parseDocumentDialog()).toBeNull()
      expect(frame.postMessage).toHaveBeenCalledWith(
        expect.objectContaining({ requestId: 'parse-fullscreen', ok: true }),
        '*',
      )
    } finally {
      if (originalDescriptor) {
        Object.defineProperty(document, 'fullscreenElement', originalDescriptor)
      } else {
        delete (document as { fullscreenElement?: Element | null }).fullscreenElement
      }
    }
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
    await frame.confirmParseDocument()
    await frame.setSurface({ ...makeDocumentSurface(), id: 'secondary' })
    await flushPromises()

    expect(signal?.aborted).toBe(true)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('aborts only the document request named by a child cancellation message', async () => {
    const signals = new Map<string, AbortSignal>()
    apiMocks.parseHostedDocument.mockImplementation((file: File, options: { signal: AbortSignal }) => {
      signals.set(file.name, options.signal)
      return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('canceled'))))
    })
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())

    frame.dispatchRequest(parseDocumentRequest(
      new File(['first'], 'first.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-first', userInitiated: true },
    ))
    await flushPromises()
    await frame.confirmParseDocument()
    frame.dispatchRequest(parseDocumentRequest(
      new File(['second'], 'second.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-second', userInitiated: true },
    ))
    await flushPromises()
    await frame.confirmParseDocument()
    frame.dispatchRequest({ type: 'neko-hosted-surface-cancel', requestId: 'parse-first' })
    await flushPromises()

    expect(signals.get('first.pdf')?.aborted).toBe(true)
    expect(signals.get('second.pdf')?.aborted).toBe(false)
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('pushes fresh hosted context into a mounted TSX iframe', async () => {
    const initialContext = { entries: [] }
    const nextContext = { entries: [{ id: 'study_export_notes' }] }
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue({
      source: 'export default function Panel() { return null }',
      dependencies: [],
    })
    apiMocks.getPluginHostedSurfaceContext
      .mockResolvedValueOnce(initialContext)
      .mockResolvedValueOnce(nextContext)
    const frame = await mountFrame({ ...makeSurface('note-exporter'), mode: 'hosted-tsx', url: undefined })
    await flushPromises()
    if (frame.iframe()?.contentWindow) {
      frame.iframe()!.contentWindow!.postMessage = frame.postMessage as unknown as Window['postMessage']
    }
    frame.postMessage.mockClear()

    await frame.refreshContext()

    expect(frame.postMessage).toHaveBeenCalledWith({
      type: 'neko-hosted-surface-context',
      context: nextContext,
    }, '*')
  })

  it('passes the active locale when loading hosted source', async () => {
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue({
      source: 'Localized guide',
      dependencies: [],
    })
    const frame = await mountFrame({
      ...makeSurface('onboarding'),
      kind: 'docs',
      mode: 'markdown',
      url: undefined,
    } as PluginUiSurface)
    await flushPromises()

    expect(apiMocks.getPluginHostedSurfaceSource).toHaveBeenCalledWith(
      'study_companion',
      {
        kind: 'docs',
        id: 'onboarding',
        locale: 'zh-CN',
      },
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        suppressErrorMessage: true,
      }),
    )
    frame.unmount()
  })

  it('rebuilds markdown when the localized surface title arrives later', async () => {
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue({
      source: 'Localized guide body',
      dependencies: [],
    })
    const frame = await mountFrame({
      ...makeSurface('onboarding'),
      kind: 'docs',
      mode: 'markdown',
      title: 'Study Companion Onboarding',
      url: undefined,
    } as PluginUiSurface)
    await flushPromises()

    expect(frame.iframe()?.getAttribute('srcdoc')).toContain('Study Companion Onboarding')

    await frame.setSurface({
      ...makeSurface('onboarding'),
      kind: 'docs',
      mode: 'markdown',
      title: '伴学入门',
      url: undefined,
    } as PluginUiSurface)
    await flushPromises()

    expect(apiMocks.getPluginHostedSurfaceSource).toHaveBeenCalledTimes(2)
    expect(frame.iframe()?.getAttribute('srcdoc')).toContain('伴学入门')
    frame.unmount()
  })

  it('resets the phase deadline while retaining available actions and focus between stages', async () => {
    const source = deferred<typeof hostedSource>()
    const context = deferred<{ state: { label: string } }>()
    apiMocks.getPluginHostedSurfaceSource.mockReturnValue(source.promise)
    apiMocks.getPluginHostedSurfaceContext.mockReturnValue(context.promise)
    const frame = await mountFrame(makeHostedSurface(), { requireIframe: false })
    const placeholder = () => frame.container.querySelector<HTMLElement>('[data-testid="surface-placeholder"]')

    expect(frame.iframe()).toBeNull()
    expect(placeholder()?.dataset.loadPhase).toBe('resources')
    expect(frame.container.textContent).toContain('plugins.ui.loadingResources')
    expect(frame.container.querySelector('[data-testid="surface-retry"]')).toBeNull()
    expect(apiMocks.getPluginHostedSurfaceContext).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2999)
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).toBeNull()
    await vi.advanceTimersByTimeAsync(1)
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).not.toBeNull()
    expect(vi.getTimerCount()).toBe(0)
    const retry = frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-retry"]')!
    const logs = frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-open-logs"]')!
    retry.focus()
    frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-open-logs"]')!.click()
    expect(frame.openLogs).toHaveBeenCalledOnce()

    source.resolve(hostedSource)
    await flushPromises()
    expect(apiMocks.getPluginHostedSurfaceContext).toHaveBeenCalledOnce()
    expect(placeholder()?.dataset.loadPhase).toBe('context')
    expect(frame.container.textContent).toContain('plugins.ui.loadingContext')
    expect(frame.iframe()).toBeNull()
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).toBeNull()
    expect(frame.container.querySelector('[data-testid="surface-retry"]')).toBe(retry)
    expect(frame.container.querySelector('[data-testid="surface-open-logs"]')).toBe(logs)
    expect(document.activeElement).toBe(retry)
    logs.click()
    expect(frame.openLogs).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2999)
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).toBeNull()
    await vi.advanceTimersByTimeAsync(1)
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).not.toBeNull()

    context.resolve({ state: { label: 'loaded after context' } })
    await flushPromises()
    expect(frame.iframe()?.srcdoc).toContain('loaded after context')
    expect(placeholder()).toBeNull()
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
    expect(frame.errors).not.toHaveBeenCalled()
  })

  it('keeps the per-phase threshold when successive waits each take less than three seconds', async () => {
    const source = deferred<typeof hostedSource>()
    const context = deferred<{ state: { label: string } }>()
    apiMocks.getPluginHostedSurfaceSource.mockReturnValue(source.promise)
    apiMocks.getPluginHostedSurfaceContext.mockReturnValue(context.promise)
    const frame = await mountFrame(makeHostedSurface(), { requireIframe: false })
    await vi.advanceTimersByTimeAsync(2900)
    source.resolve(hostedSource)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2900)
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).toBeNull()
    expect(frame.container.querySelector('[data-testid="surface-retry"]')).toBeNull()
    context.resolve({ state: { label: 'ready' } })
    await flushPromises()
    expect(frame.iframe()?.srcdoc).toContain('ready')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('retries immediately after a slow resources phase, resets actions, and ignores retired context', async () => {
    const source = deferred<typeof hostedSource>()
    const oldContext = deferred<{ state: { label: string } }>()
    const newContext = deferred<{ state: { label: string } }>()
    apiMocks.getPluginHostedSurfaceSource.mockReturnValueOnce(source.promise).mockResolvedValue(hostedSource)
    apiMocks.getPluginHostedSurfaceContext.mockReturnValueOnce(oldContext.promise).mockReturnValueOnce(newContext.promise)
    const frame = await mountFrame(makeHostedSurface(), { requireIframe: false })
    await vi.advanceTimersByTimeAsync(5000)
    const retry = frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-retry"]')!
    source.resolve(hostedSource)
    await flushPromises()
    const oldSignal = apiMocks.getPluginHostedSurfaceContext.mock.calls[0]![2].signal as AbortSignal
    expect(frame.container.querySelector('[data-testid="surface-retry"]')).toBe(retry)
    retry.click()
    await flushPromises()
    expect(oldSignal.aborted).toBe(true)
    expect(apiMocks.getPluginHostedSurfaceSource).toHaveBeenCalledTimes(2)
    expect(apiMocks.getPluginHostedSurfaceContext).toHaveBeenCalledTimes(2)
    expect(frame.container.querySelector('[data-testid="surface-retry"]')).toBeNull()
    expect(frame.container.querySelector('[data-testid="surface-open-logs"]')).toBeNull()
    oldContext.reject(new Error('retired context'))
    await flushPromises()
    expect(frame.errors).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(1)
    await vi.advanceTimersByTimeAsync(3000)
    expect(frame.container.querySelector('[data-testid="surface-retry"]')).not.toBeNull()
    newContext.resolve({ state: { label: 'replacement' } })
    await flushPromises()
    expect(frame.iframe()?.srcdoc).toContain('replacement')
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['resolve', 'reject'] as const)('retries a slow context request, aborts its controller, and ignores an old %s', async (settlement) => {
    const oldContext = deferred<{ state: { label: string } }>()
    const newContext = deferred<{ state: { label: string } }>()
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(hostedSource)
    apiMocks.getPluginHostedSurfaceContext.mockReturnValueOnce(oldContext.promise).mockReturnValueOnce(newContext.promise)
    const frame = await mountFrame(makeHostedSurface(), { requireIframe: false })
    const oldSignal = apiMocks.getPluginHostedSurfaceContext.mock.calls[0]![2].signal as AbortSignal
    await vi.advanceTimersByTimeAsync(3000)
    frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-retry"]')!.click()
    await flushPromises()

    expect(oldSignal.aborted).toBe(true)
    expect(apiMocks.getPluginHostedSurfaceSource).toHaveBeenCalledTimes(2)
    expect(apiMocks.getPluginHostedSurfaceContext).toHaveBeenCalledTimes(2)
    const newSignal = apiMocks.getPluginHostedSurfaceContext.mock.calls[1]![2].signal as AbortSignal
    expect(newSignal.aborted).toBe(false)
    if (settlement === 'resolve') oldContext.resolve({ state: { label: 'obsolete context' } })
    else oldContext.reject(new Error('obsolete context rejection'))
    await flushPromises()
    expect(frame.iframe()).toBeNull()
    expect(frame.container.querySelector<HTMLElement>('[data-testid="surface-placeholder"]')?.dataset.loadPhase).toBe('context')
    expect(frame.errors).not.toHaveBeenCalled()
    expect(newSignal.aborted).toBe(false)
    expect(vi.getTimerCount()).toBe(1)
    await vi.advanceTimersByTimeAsync(3000)
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).not.toBeNull()

    newContext.resolve({ state: { label: 'replacement context' } })
    await flushPromises()
    expect(frame.iframe()?.srcdoc).toContain('replacement context')
    expect(frame.iframe()?.srcdoc).not.toContain('obsolete context')
    expect(frame.errors).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['resolve', 'reject'] as const)('retries a slow source request and ignores its late %s after the replacement mounts', async (settlement) => {
    const oldSource = deferred<typeof hostedSource>()
    apiMocks.getPluginHostedSurfaceSource.mockReturnValueOnce(oldSource.promise).mockResolvedValueOnce(hostedSource)
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({ state: { label: 'replacement source' } })
    const frame = await mountFrame(makeHostedSurface(), { requireIframe: false })
    const oldSignal = apiMocks.getPluginHostedSurfaceSource.mock.calls[0]![2].signal as AbortSignal
    await vi.advanceTimersByTimeAsync(3000)
    frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-retry"]')!.click()
    await flushPromises()
    await flushPromises()
    const replacement = frame.iframe()
    expect(replacement).not.toBeNull()
    expect(oldSignal.aborted).toBe(true)
    if (settlement === 'resolve') oldSource.resolve({ source: 'export default function Panel() { throw Error("obsolete source") }', dependencies: [] })
    else oldSource.reject(new Error('obsolete source rejection'))
    await flushPromises()
    expect(frame.iframe()).toBe(replacement)
    expect(frame.iframe()?.srcdoc).toContain('replacement source')
    expect(apiMocks.getPluginHostedSurfaceContext).toHaveBeenCalledOnce()
    expect(frame.errors).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['resources', 'context'] as const)('retires the %s wait and its timer when replaced by a static surface', async (phase) => {
    const pendingSource = deferred<typeof hostedSource>()
    const pendingContext = deferred<{ state: { label: string } }>()
    apiMocks.getPluginHostedSurfaceSource.mockReturnValue(phase === 'resources' ? pendingSource.promise : Promise.resolve(hostedSource))
    apiMocks.getPluginHostedSurfaceContext.mockReturnValue(pendingContext.promise)
    const frame = await mountFrame(makeHostedSurface(), { requireIframe: false })
    const config = phase === 'resources' ? apiMocks.getPluginHostedSurfaceSource.mock.calls[0]![2] : apiMocks.getPluginHostedSurfaceContext.mock.calls[0]![2]
    expect(vi.getTimerCount()).toBe(1)
    await frame.setSurface(makeSurface('replacement-static'))
    const replacement = frame.iframe()
    expect(config.signal.aborted).toBe(true)
    // happy-dom schedules a zero-delay load task for the new static iframe.
    await vi.advanceTimersByTimeAsync(0)
    expect(vi.getTimerCount()).toBe(0)
    pendingSource.resolve(hostedSource)
    if (phase === 'context') pendingContext.reject(new Error('retired request'))
    else pendingContext.resolve({ state: { label: 'unused context' } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3000)
    expect(frame.iframe()).toBe(replacement)
    expect(frame.errors).not.toHaveBeenCalled()
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).toBeNull()
  })

  it.each(['resources', 'context'] as const)('aborts the %s request and clears its timer on unmount', async (phase) => {
    const source = deferred<typeof hostedSource>()
    const context = deferred<{ state: { label: string } }>()
    apiMocks.getPluginHostedSurfaceSource.mockReturnValue(phase === 'resources' ? source.promise : Promise.resolve(hostedSource))
    apiMocks.getPluginHostedSurfaceContext.mockReturnValue(context.promise)
    const frame = await mountFrame(makeHostedSurface(), { requireIframe: false })
    const signal = (phase === 'resources' ? apiMocks.getPluginHostedSurfaceSource.mock.calls[0]![2] : apiMocks.getPluginHostedSurfaceContext.mock.calls[0]![2]).signal as AbortSignal
    expect(vi.getTimerCount()).toBe(1)
    frame.unmount()
    expect(signal.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    source.resolve(hostedSource)
    if (phase === 'context') context.reject(new Error('disposed request'))
    else context.resolve({ state: { label: 'unused context' } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3000)
    expect(frame.errors).not.toHaveBeenCalled()
  })

  it.each(['resources', 'context', 'render'] as const)('keeps the failed %s stage visible, clears its timer, and offers retry plus logs', async (phase) => {
    const failure = new Error(`${phase} failed`)
    if (phase === 'resources') apiMocks.getPluginHostedSurfaceSource.mockRejectedValueOnce(failure)
    else apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(phase === 'render' ? { source: 'export default function(', dependencies: [] } : hostedSource)
    apiMocks.getPluginHostedSurfaceContext.mockImplementation(() => phase === 'context' ? Promise.reject(failure) : Promise.resolve({ state: {} }))
    const frame = await mountFrame(makeHostedSurface(), { requireIframe: false })
    expect(frame.iframe()).toBeNull()
    expect(frame.container.querySelector<HTMLElement>('[data-testid="surface-placeholder"]')?.dataset.loadPhase).toBe(phase)
    expect(frame.errors).toHaveBeenCalledOnce()
    expect(frame.container.textContent).toContain('plugins.ui.loadStageError')
    expect(vi.getTimerCount()).toBe(0)
    expect(frame.container.querySelector('[data-testid="surface-retry"]')).not.toBeNull()
    expect(frame.container.querySelector('[data-testid="surface-open-logs"]')).not.toBeNull()
    frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-open-logs"]')!.click()
    expect(frame.openLogs).toHaveBeenCalledOnce()

    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(hostedSource)
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({ state: { label: 'recovered' } })
    frame.container.querySelector<HTMLButtonElement>('[data-testid="surface-retry"]')!.click()
    await flushPromises()
    await flushPromises()
    expect(frame.iframe()?.srcdoc).toContain('recovered')
    expect(frame.container.querySelector('[data-testid="surface-placeholder"]')).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps the mounted iframe identity during a slow context refresh', async () => {
    const refresh = deferred<{ state: { label: string } }>()
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(hostedSource)
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValueOnce({ state: { label: 'initial' } }).mockReturnValueOnce(refresh.promise)
    const frame = await mountFrame(makeHostedSurface())
    const original = frame.iframe()!
    original.dataset.localDraft = 'unsaved user draft'
    const refreshing = frame.refreshContext()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(frame.iframe()).toBe(original)
    expect(frame.iframe()?.dataset.localDraft).toBe('unsaved user draft')
    expect(frame.container.querySelector('[data-testid="surface-placeholder"]')).toBeNull()
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).toBeNull()
    expect(apiMocks.getPluginHostedSurfaceSource).toHaveBeenCalledOnce()
    refresh.resolve({ state: { label: 'fresh' } })
    await refreshing
    expect(frame.iframe()).toBe(original)
    expect(frame.postMessage).toHaveBeenCalledWith({ type: 'neko-hosted-surface-context', context: { state: { label: 'fresh' } } }, '*')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('shows slow Markdown resource loading, renders after source arrives, and never requests context', async () => {
    const source = deferred<{ source: string; dependencies: [] }>()
    apiMocks.getPluginHostedSurfaceSource.mockReturnValue(source.promise)
    const frame = await mountFrame({ ...makeSurface(), kind: 'guide', mode: 'markdown', entry: 'guide.md', url: undefined }, { requireIframe: false })
    expect(frame.iframe()).toBeNull()
    expect(frame.container.querySelector<HTMLElement>('[data-testid="surface-placeholder"]')?.dataset.loadPhase).toBe('resources')
    await vi.advanceTimersByTimeAsync(3000)
    expect(frame.container.querySelector('[data-testid="surface-load-slow"]')).not.toBeNull()
    source.resolve({ source: '# Markdown ready', dependencies: [] })
    await flushPromises()
    expect(frame.iframe()?.srcdoc).toContain('<h1>Markdown ready</h1>')
    expect(apiMocks.getPluginHostedSurfaceContext).not.toHaveBeenCalled()
    expect(frame.container.querySelector('[data-testid="surface-placeholder"]')).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('HostedSurfaceFrame open-path scope and action confirmation', () => {
  const pluginRoot = '/home/u/neko/plugins/study_companion'
  const dataRoot = '/home/u/.neko/plugins/study_companion/data'
  let nekoOpenPath: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    apiMocks.callPluginHostedSurfaceAction.mockReset()
    apiMocks.getPluginHostedSurfaceContext.mockReset()
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({ paths: { plugin_dir: pluginRoot, data_dir: dataRoot } })
    apiMocks.getPluginHostedSurfaceSource.mockReset()
    apiMocks.parseHostedDocument.mockReset()
    elementPlusMocks.ElMessage.mockReset()
    localeRef.value = 'zh-CN'
    nekoOpenPath = vi.fn(() => Promise.resolve({ ok: true }))
    Object.defineProperty(window, 'nekoHost', { value: { openPath: nekoOpenPath }, configurable: true })
  })

  afterEach(() => {
    try {
      assertNoNetworkIframes(document.body)
    } finally {
      while (mountedFrames.length > 0) mountedFrames.pop()?.unmount()
      Reflect.deleteProperty(window, 'nekoHost')
      vi.useRealTimers()
    }
  })

  function openPathMessage(path: string) {
    return { type: 'neko-hosted-surface-open-path', payload: { path } }
  }

  function confirmRequest(options: { requestId?: string; message?: string; userInitiated?: boolean; timeoutMs?: number } = {}) {
    return {
      type: 'neko-hosted-surface-request',
      requestId: options.requestId || 'confirm-1',
      method: 'confirm',
      userInitiated: options.userInitiated !== false,
      timeoutMs: options.timeoutMs ?? 125000,
      payload: options.message === undefined ? {} : { message: options.message },
    }
  }

  const actionDialog = () => document.querySelector<HTMLElement>('[data-testid="action-confirm-dialog"]')
  function clickDialogButton(testId: string) {
    const button = document.querySelector<HTMLButtonElement>(`[data-testid="${testId}"]`)
    if (!button) throw new Error(`Expected ${testId}`)
    button.click()
  }
  const lastMessageCall = () => elementPlusMocks.ElMessage.mock.calls.at(-1)?.[0]

  it.each([
    `${pluginRoot}/exports/report.txt`,
    `${dataRoot}/out`,
    `${dataRoot}/`,
    `${pluginRoot}/a/../b.txt`,
  ])('opens %s inside the plugin or data directory', async (path) => {
    const frame = await mountFrame()
    frame.dispatchRequest(openPathMessage(path))
    await flushPromises()
    await flushPromises()

    expect(nekoOpenPath).toHaveBeenCalledOnce()
    expect(nekoOpenPath.mock.calls[0]![0].path.startsWith(pluginRoot) || nekoOpenPath.mock.calls[0]![0].path.startsWith(dataRoot)).toBe(true)
    expect(elementPlusMocks.ElMessage).not.toHaveBeenCalled()
  })

  it.each([
    '/etc/passwd',
    '/home/u/neko/plugins/study_companion_evil/x',
    `${pluginRoot}/../other_plugin/secret`,
    '/home/u/.neko/plugins/study_companion/config/plugin.toml',
  ])('refuses %s outside the plugin scope and tells the user', async (path) => {
    const frame = await mountFrame()
    frame.dispatchRequest(openPathMessage(path))
    await flushPromises()
    await flushPromises()

    expect(nekoOpenPath).not.toHaveBeenCalled()
    expect(lastMessageCall()).toMatchObject({ type: 'warning', message: 'plugins.ui.openPathOutOfScope' })
  })

  it('also accepts the unresolved plugin directory reported by the host', async () => {
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({
      paths: { plugin_dir: '/real/study_companion', plugin_dir_unresolved: pluginRoot, data_dir: dataRoot },
    })
    const frame = await mountFrame()
    frame.dispatchRequest(openPathMessage(`${pluginRoot}/out.txt`))
    await flushPromises()
    await flushPromises()

    expect(nekoOpenPath).toHaveBeenCalledWith({ path: `${pluginRoot}/out.txt` })
  })

  it('opens exactly the checked path, spaces included', async () => {
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({ paths: { plugin_dir: '/home/u/plugin ', data_dir: null } })
    const frame = await mountFrame()
    frame.dispatchRequest(openPathMessage('/home/u/plugin/x'))
    await flushPromises()
    await flushPromises()
    expect(nekoOpenPath).not.toHaveBeenCalled()

    frame.dispatchRequest(openPathMessage('/home/u/plugin /x '))
    await flushPromises()
    await flushPromises()
    expect(nekoOpenPath).toHaveBeenCalledWith({ path: '/home/u/plugin /x ' })
  })

  it('takes the scope from the host context read, not from the panel message', async () => {
    const frame = await mountFrame()
    frame.dispatchRequest({
      type: 'neko-hosted-surface-open-path',
      payload: { path: '/etc/passwd', paths: { plugin_dir: '/etc', data_dir: '/' } },
    })
    await flushPromises()
    await flushPromises()

    expect(apiMocks.getPluginHostedSurfaceContext).toHaveBeenCalledOnce()
    expect(nekoOpenPath).not.toHaveBeenCalled()
  })

  it('refuses everything when the host cannot read the scope', async () => {
    apiMocks.getPluginHostedSurfaceContext.mockRejectedValue(new Error('offline'))
    const frame = await mountFrame()
    frame.dispatchRequest(openPathMessage(`${pluginRoot}/x`))
    await flushPromises()
    await flushPromises()

    expect(nekoOpenPath).not.toHaveBeenCalled()
    expect(lastMessageCall()).toMatchObject({ type: 'error', message: 'plugins.ui.openPathFailed' })
  })

  it('ignores open-path messages from another window', async () => {
    const frame = await mountFrame()
    window.dispatchEvent(new MessageEvent('message', { data: openPathMessage(`${pluginRoot}/x`), origin: 'null', source: window }))
    await flushPromises()
    await flushPromises()

    expect(frame.postMessage).not.toHaveBeenCalled()
    expect(apiMocks.getPluginHostedSurfaceContext).not.toHaveBeenCalled()
    expect(nekoOpenPath).not.toHaveBeenCalled()
  })

  it('tells the user when opening fails', async () => {
    nekoOpenPath.mockResolvedValue({ ok: false, error: 'not found' })
    const frame = await mountFrame()
    frame.dispatchRequest(openPathMessage(`${pluginRoot}/missing.txt`))
    await flushPromises()
    await flushPromises()

    expect(lastMessageCall()).toMatchObject({ type: 'error', message: 'plugins.ui.openPathFailed' })
  })

  it('tells the user when there is no desktop bridge', async () => {
    Reflect.deleteProperty(window, 'nekoHost')
    const frame = await mountFrame()
    frame.dispatchRequest(openPathMessage(`${pluginRoot}/x`))
    await flushPromises()
    await flushPromises()

    expect(lastMessageCall()).toMatchObject({ type: 'warning', message: 'plugins.ui.openPathNoBridge' })
  })

  it('reuses the scope from the hosted-tsx context load', async () => {
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(hostedSource)
    const frame = await mountFrame(makeHostedSurface())
    await flushPromises()
    expect(apiMocks.getPluginHostedSurfaceContext).toHaveBeenCalledOnce()
    frame.dispatchRequest(openPathMessage(`${dataRoot}/x.txt`))
    await flushPromises()
    await flushPromises()

    expect(apiMocks.getPluginHostedSurfaceContext).toHaveBeenCalledOnce()
    expect(nekoOpenPath).toHaveBeenCalledWith({ path: `${dataRoot}/x.txt` })
  })

  it('never forwards the host-only paths into the panel', async () => {
    apiMocks.getPluginHostedSurfaceContext.mockResolvedValue({
      state: { label: 'context-marker' },
      paths: { plugin_dir: pluginRoot, data_dir: dataRoot },
    })
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(hostedSource)
    const frame = await mountFrame(makeHostedSurface())
    await flushPromises()
    const srcdoc = frame.iframe()?.srcdoc || ''
    expect(srcdoc).toContain('context-marker')
    expect(srcdoc).not.toContain(dataRoot)
    expect(srcdoc).not.toContain(pluginRoot)

    frame.dispatchRequest({ type: 'neko-hosted-surface-request', requestId: 'refresh-1', method: 'refresh', payload: {} })
    await flushPromises()
    await flushPromises()
    await frame.refreshContext()
    await flushPromises()

    const refreshed = frame.postMessage.mock.calls.find(([message]) => message?.requestId === 'refresh-1')?.[0]
    expect(refreshed).toMatchObject({ ok: true, result: { state: { label: 'context-marker' } } })
    expect(refreshed.result).not.toHaveProperty('paths')
    const pushed = frame.postMessage.mock.calls.find(([message]) => message?.type === 'neko-hosted-surface-context')?.[0]
    expect(pushed?.context).toMatchObject({ state: { label: 'context-marker' } })
    expect(pushed.context).not.toHaveProperty('paths')
    expect(JSON.stringify(frame.postMessage.mock.calls)).not.toContain(dataRoot)

    // The host still has the scope after stripping it from the panel's copy.
    frame.dispatchRequest(openPathMessage(`${dataRoot}/x.txt`))
    await flushPromises()
    await flushPromises()
    expect(nekoOpenPath).toHaveBeenCalledWith({ path: `${dataRoot}/x.txt` })
  })

  it('keeps the hosted-tsx panel sandbox without allow-modals', async () => {
    apiMocks.getPluginHostedSurfaceSource.mockResolvedValue(hostedSource)
    const frame = await mountFrame(makeHostedSurface())
    expect(frame.iframe()?.getAttribute('sandbox')).toBe('allow-scripts')
  })

  it('shows the plugin confirm text as plain text and answers confirmed', async () => {
    const frame = await mountFrame()
    frame.dispatchRequest(confirmRequest({ message: '<img src=x onerror=alert(1)>Delete?' }))
    await flushPromises()

    const dialog = actionDialog()
    expect(dialog).not.toBeNull()
    expect(dialog!.getAttribute('aria-label')).toBe('plugins.ui.actionConfirmTitle')
    expect(dialog!.querySelector('.hosted-surface-frame__confirm-message')?.textContent).toBe('<img src=x onerror=alert(1)>Delete?')
    expect(dialog!.querySelector('img')).toBeNull()
    clickDialogButton('action-confirm-ok')
    await flushPromises()

    expect(actionDialog()).toBeNull()
    expect(frame.postMessage).toHaveBeenCalledWith(
      { type: 'neko-hosted-surface-response', requestId: 'confirm-1', ok: true, result: { confirmed: true } },
      '*',
    )
  })

  it('uses the translated default text and answers not confirmed on cancel', async () => {
    const frame = await mountFrame()
    frame.dispatchRequest(confirmRequest())
    await flushPromises()
    expect(actionDialog()?.textContent).toContain('plugins.ui.actionConfirmDefault')
    clickDialogButton('action-confirm-cancel')
    await flushPromises()

    expect(frame.postMessage).toHaveBeenCalledWith(
      { type: 'neko-hosted-surface-response', requestId: 'confirm-1', ok: true, result: { confirmed: false } },
      '*',
    )
  })

  it('closes the dialog at its deadline, answers timeout, and tells the user', async () => {
    const frame = await mountFrame()
    frame.dispatchRequest(confirmRequest({ timeoutMs: 125000 }))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(119999)
    expect(actionDialog()).not.toBeNull()
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()

    expect(actionDialog()).toBeNull()
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'confirm-1', ok: false, code: 'action_confirm_timeout' }),
      '*',
    )
    expect(frame.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ ok: true }), '*')
    expect(lastMessageCall()).toMatchObject({ type: 'warning', message: 'plugins.ui.actionConfirmTimeout' })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('drops the dialog when the panel cancels the request', async () => {
    const frame = await mountFrame()
    frame.dispatchRequest(confirmRequest())
    await flushPromises()
    const timersWithDialog = vi.getTimerCount()
    frame.dispatchRequest({ type: 'neko-hosted-surface-cancel', requestId: 'confirm-1' })
    await flushPromises()

    expect(actionDialog()).toBeNull()
    expect(frame.postMessage).not.toHaveBeenCalled()
    // The dialog deadline timer is released with it.
    expect(vi.getTimerCount()).toBe(timersWithDialog - 1)
  })

  it('drops the dialog on unmount without answering', async () => {
    const frame = await mountFrame()
    frame.dispatchRequest(confirmRequest())
    await flushPromises()
    frame.unmount()
    await flushPromises()

    expect(actionDialog()).toBeNull()
    expect(frame.postMessage).not.toHaveBeenCalled()
  })

  it('serializes confirmations with document parsing', async () => {
    const frame = await mountFrame()
    frame.dispatchRequest(confirmRequest({ requestId: 'confirm-a' }))
    await flushPromises()
    frame.dispatchRequest(confirmRequest({ requestId: 'confirm-b' }))
    await flushPromises()

    expect(document.querySelectorAll('[data-testid="action-confirm-dialog"]')).toHaveLength(1)
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'confirm-b', ok: false, code: 'action_confirm_pending' }),
      '*',
    )
  })

  it('mounts host messages inside the fullscreen element while fullscreen', async () => {
    const frame = await mountFrame()
    const displayTarget = frame.container.querySelector<HTMLElement>('.hosted-surface-frame')
    const originalDescriptor = Object.getOwnPropertyDescriptor(document, 'fullscreenElement')
    let fullscreenEl: Element | null = null
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => fullscreenEl })
    try {
      frame.dispatchRequest(openPathMessage('/etc/passwd'))
      await flushPromises()
      await flushPromises()
      expect(lastMessageCall()).toMatchObject({ appendTo: document.body })

      fullscreenEl = displayTarget
      frame.dispatchRequest(openPathMessage('/etc/hosts'))
      await flushPromises()
      await flushPromises()
      expect(lastMessageCall()).toMatchObject({ message: 'plugins.ui.openPathOutOfScope' })
      expect(lastMessageCall().appendTo).toBe(displayTarget)
      // Grouping would reuse the earlier body-mounted message instead of mounting here.
      expect(lastMessageCall()).not.toHaveProperty('grouping')
    } finally {
      if (originalDescriptor) Object.defineProperty(document, 'fullscreenElement', originalDescriptor)
      else delete (document as { fullscreenElement?: Element | null }).fullscreenElement
    }
  })

  it('reports a generic pending confirmation to document parsing while an action confirm is open', async () => {
    const frame = await mountFrame()
    await frame.setSurface(makeDocumentSurface())
    frame.dispatchRequest(confirmRequest())
    await flushPromises()
    frame.dispatchRequest(parseDocumentRequest(
      new File(['pdf'], 'a.pdf', { type: 'application/pdf' }),
      { requestId: 'parse-while-action', userInitiated: true },
    ))
    await flushPromises()

    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        requestId: 'parse-while-action',
        ok: false,
        error: 'Another confirmation is already pending.',
        code: 'document_parse_permission_denied',
      }),
      '*',
    )
    expect(actionDialog()).not.toBeNull()
  })

  it('refuses confirmations not started by a user action', async () => {
    const frame = await mountFrame()
    frame.dispatchRequest(confirmRequest({ userInitiated: false }))
    await flushPromises()

    expect(actionDialog()).toBeNull()
    expect(frame.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ requestId: 'confirm-1', ok: false, code: 'action_confirm_not_user_initiated' }),
      '*',
    )
  })

  it('ignores confirmation requests from another window', async () => {
    await mountFrame()
    window.dispatchEvent(new MessageEvent('message', { data: confirmRequest(), origin: 'null', source: window }))
    await flushPromises()

    expect(actionDialog()).toBeNull()
  })
})
