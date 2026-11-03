// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, reactive } from 'vue'
import Runs from './Runs.vue'
import { RunsRequestError } from '@/utils/runsError'

const mocks = vi.hoisted(() => ({
  store: null as unknown as Record<string, unknown>,
  error: vi.fn(), success: vi.fn(), confirm: vi.fn(),
}))
vi.mock('@/stores/runs', () => ({ useRunsStore: () => mocks.store }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key, locale: { value: 'en-US' } }) }))
vi.mock('element-plus', async importOriginal => ({
  ...await importOriginal<typeof import('element-plus')>(),
  ElMessage: { error: mocks.error, success: mocks.success }, ElMessageBox: { confirm: mocks.confirm },
}))

function deferred<T = void>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

const store = () => mocks.store as unknown as {
  refreshRuns: ReturnType<typeof vi.fn>; cancelRun: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn>
  loadRun: ReturnType<typeof vi.fn>; loadExports: ReturnType<typeof vi.fn>
  selectRun: ReturnType<typeof vi.fn>
}
let unmount = () => {}
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(Runs)
  const container = defineComponent({ setup: (_, { slots }) => () => h('div', [slots.header?.(), slots.title?.(), slots.default?.()]) })
  for (const name of ['ElRow', 'ElCol', 'ElCard', 'ElAlert', 'ElEmpty', 'ElDescriptions', 'ElDescriptionsItem', 'ElIcon', 'ElProgress']) app.component(name, container)
  app.component('ElTag', container)
  app.component('ElButton', defineComponent({
    inheritAttrs: false,
    setup: (_, { attrs, slots }) => () => h('button', { ...attrs, onClick: attrs.onClick as () => void }, slots.default?.()),
  }))
  app.use({ install: instance => { instance.config.globalProperties.$t = (key: string) => key } })
  app.mount(root)
  unmount = () => { app.unmount(); root.remove(); unmount = () => {} }
  return root
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.store = reactive({
    connected: false, connecting: true, runs: [], selectedRunId: null, selectedRun: null, selectedExports: [], exportRevisions: {}, runRevisions: {},
    refreshRuns: vi.fn().mockResolvedValue(undefined), disconnect: vi.fn(),
    cancelRun: vi.fn(), loadRun: vi.fn().mockResolvedValue(undefined), loadExports: vi.fn().mockResolvedValue([]),
    selectRun: vi.fn((id: string) => {
      mocks.store.selectedRunId = id
      mocks.store.selectedRun = (mocks.store.runs as { run_id: string }[]).find(row => row.run_id === id)
    }),
  })
})
afterEach(() => { unmount() })

describe('run history page request lifecycle', () => {
  it('refreshes the list and selected record through one action, coalescing repeated clicks', async () => {
    mocks.store.connected = true
    mocks.store.runs = [{ run_id: 'r', plugin_id: 'p', entry_id: 'e', status: 'succeeded' }]
    const root = mount()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="r"]')!.click()
    await flush()
    const pending = deferred()
    store().refreshRuns.mockReturnValueOnce(pending.promise)
    const refresh = root.querySelector<HTMLButtonElement>('[data-yui-guide-id="runs-refresh"]')!
    expect(root.querySelectorAll('[data-yui-guide-id="runs-refresh"]')).toHaveLength(1)
    refresh.click()
    refresh.click()
    await flush()
    expect(store().refreshRuns).toHaveBeenCalledTimes(2)
    pending.resolve()
    await flush()
    expect(store().loadRun.mock.calls).toEqual([['r'], ['r']])
    expect(store().loadExports.mock.calls).toEqual([['r'], ['r']])
  })

  it('still refreshes the selected record when the list refresh fails', async () => {
    mocks.store.runs = [{ run_id: 'r', plugin_id: 'p', entry_id: 'e', status: 'succeeded' }]
    const root = mount()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="r"]')!.click()
    await flush()
    store().refreshRuns.mockRejectedValueOnce(new RunsRequestError('request_failed'))
    root.querySelector<HTMLButtonElement>('[data-yui-guide-id="runs-refresh"]')!.click()
    await flush()
    expect(root.textContent).toContain('runs.requestFailed')
    expect(store().loadRun).toHaveBeenCalledTimes(2)
    expect(store().loadExports).toHaveBeenCalledTimes(2)
  })

  it('reloads cached detail after a background run changes without any output event', async () => {
    const a = { run_id: 'a', plugin_id: 'p', entry_id: 'e', status: 'running', updated_at: 1 }
    const b = { run_id: 'b', plugin_id: 'p', entry_id: 'other', status: 'running', updated_at: 1 }
    mocks.store.runs = [a, b]
    const root = mount()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="a"]')!.click()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="b"]')!.click()
    await flush()
    const current = (mocks.store.runs as typeof a[])[0]!
    current.status = 'failed'
    current.updated_at = 2
    ;(mocks.store.runRevisions as Record<string, number>).a = 1
    store().loadRun.mockImplementationOnce(async () => {
      Object.assign(current, { error: { message: 'background failure' }, finished_at: 2, started_at: 1 })
    })
    root.querySelector<HTMLButtonElement>('[data-run-id="a"]')!.click()
    await flush()
    expect(store().loadRun.mock.calls).toEqual([['a'], ['b'], ['a']])
    expect(root.querySelector('.run-error')?.textContent).toContain('background failure')
  })

  it('queues a fresh detail when returning to an in-flight run after a background status change', async () => {
    const pending = deferred()
    const a = { run_id: 'a', entry_id: 'entry', status: 'running', updated_at: 1 }
    mocks.store.runs = [a, { run_id: 'b', entry_id: 'other', status: 'running', updated_at: 1 }]
    store().loadRun.mockReturnValueOnce(pending.promise).mockImplementationOnce(async () => {
      Object.assign((mocks.store.runs as typeof a[])[0]!, { error: { message: 'complete failure details' }, finished_at: 2 })
    })
    const root = mount()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="a"]')!.click()
    root.querySelector<HTMLButtonElement>('[data-run-id="b"]')!.click()
    Object.assign((mocks.store.runs as typeof a[])[0]!, { status: 'failed', updated_at: 2 })
    ;(mocks.store.runRevisions as Record<string, number>).a = 1
    root.querySelector<HTMLButtonElement>('[data-run-id="a"]')!.click()
    pending.resolve()
    await flush()
    expect(store().loadRun.mock.calls).toEqual([['a'], ['a']])
    expect(root.querySelector('.run-error')?.textContent).toContain('complete failure details')
  })

  it('reloads a selected queued run when it starts so the start time is not left unknown', async () => {
    mocks.store.runs = [{ run_id: 'a', status: 'queued', updated_at: 1 }]
    const root = mount()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="a"]')!.click()
    await flush()
    const current = (mocks.store.runs as Record<string, unknown>[])[0]!
    store().loadRun.mockImplementationOnce(async () => { current.started_at = 1 })
    Object.assign(current, { status: 'running', updated_at: 2 })
    ;(mocks.store.runRevisions as Record<string, number>).a = 1
    await flush()
    expect(store().loadRun.mock.calls).toEqual([['a'], ['a']])
    expect(current.started_at).toBe(1)
  })

  it('loads only the latest row selected while the previous detail load is pending', async () => {
    const detail = deferred()
    const exports = deferred<unknown[]>()
    mocks.store.runs = ['a', 'b', 'c'].map(run_id => ({ run_id, status: 'running', updated_at: 1 }))
    store().loadRun.mockReturnValueOnce(detail.promise)
    store().loadExports.mockReturnValueOnce(exports.promise)
    const root = mount()
    await flush()
    const rows = root.querySelectorAll<HTMLElement>('button[data-run-id]')
    rows[0]!.click()
    rows[1]!.click()
    rows[2]!.click()
    detail.reject(new RunsRequestError('request_failed'))
    await flush()
    expect(store().loadRun.mock.calls).toEqual([['a']])
    exports.resolve([])
    await flush()
    expect(store().selectRun.mock.calls).toEqual([['a'], ['b'], ['c']])
    expect(store().loadRun.mock.calls).toEqual([['a'], ['c']])
    expect(store().loadExports.mock.calls).toEqual([['a'], ['c']])
    expect(mocks.store.selectedRun).toMatchObject({ run_id: 'c' })
  })

  it('discards a pending row change when the user returns to the row already loading', async () => {
    const detail = deferred()
    mocks.store.runs = ['a', 'b'].map(run_id => ({ run_id, status: 'running', updated_at: 1 }))
    store().loadRun.mockReturnValueOnce(detail.promise)
    const root = mount()
    await flush()
    const rows = root.querySelectorAll<HTMLElement>('button[data-run-id]')
    rows[0]!.click()
    rows[1]!.click()
    rows[0]!.click()
    detail.resolve()
    await flush()
    expect(store().selectRun.mock.calls).toEqual([['a'], ['b'], ['a']])
    expect(store().loadRun.mock.calls).toEqual([['a']])
    expect(mocks.store.selectedRun).toMatchObject({ run_id: 'a' })
  })

  it('discards pending row changes and late errors when the page is unmounted', async () => {
    const detail = deferred()
    mocks.store.runs = ['a', 'b'].map(run_id => ({ run_id, status: 'running', updated_at: 1 }))
    store().loadRun.mockReturnValueOnce(detail.promise)
    const root = mount()
    await flush()
    const rows = root.querySelectorAll<HTMLElement>('button[data-run-id]')
    rows[0]!.click()
    rows[1]!.click()
    unmount()
    detail.reject(new RunsRequestError('connection_closed'))
    await flush()
    expect(store().selectRun.mock.calls).toEqual([['a'], ['b']])
    expect(store().loadRun.mock.calls).toEqual([['a']])
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it.each(['loadRun', 'loadExports'] as const)('retries the same actual table row after %s fails without duplicating successful loads', async method => {
    mocks.store.runs = [{ run_id: 'r', plugin_id: 'p', entry_id: 'e', status: 'running', updated_at: 1 }]
    store()[method].mockRejectedValueOnce(new RunsRequestError('connection_closed'))
    const root = mount()
    await flush()
    const row = root.querySelector<HTMLElement>('button[data-run-id]')!
    expect(row).not.toBeNull()
    row.click()
    await flush()
    expect(store().loadRun).toHaveBeenCalledTimes(1)
    expect(store().loadExports).toHaveBeenCalledTimes(1)
    expect(root.textContent).toContain('runs.connectionClosed')
    row.click()
    await flush()
    expect(store().loadRun).toHaveBeenCalledTimes(method === 'loadRun' ? 2 : 1)
    expect(store().loadExports).toHaveBeenCalledTimes(method === 'loadExports' ? 2 : 1)
    row.click()
    await flush()
    expect(store().loadRun).toHaveBeenCalledTimes(method === 'loadRun' ? 2 : 1)
    expect(store().loadExports).toHaveBeenCalledTimes(method === 'loadExports' ? 2 : 1)
  })

  it('waits for both detail requests to settle before allowing another selection load', async () => {
    const exports = deferred<unknown[]>()
    mocks.store.runs = [{ run_id: 'r', plugin_id: 'p', entry_id: 'e', status: 'running', updated_at: 1 }]
    store().loadRun.mockRejectedValueOnce(new RunsRequestError('request_failed'))
    store().loadExports.mockReturnValueOnce(exports.promise)
    const root = mount()
    await flush()
    const row = root.querySelector<HTMLElement>('button[data-run-id]')!
    row.click()
    await flush()
    row.click()
    expect(store().loadRun).toHaveBeenCalledTimes(1)
    expect(store().loadExports).toHaveBeenCalledTimes(1)
    exports.resolve([])
    await flush()
    row.click()
    await flush()
    expect(store().loadRun).toHaveBeenCalledTimes(2)
    expect(store().loadExports).toHaveBeenCalledTimes(1)
  })

  it('keeps confirmed cancellation success when the subsequent refresh fails', async () => {
    mocks.confirm.mockResolvedValue(undefined)
    mocks.store.selectedRunId = 'r'
    mocks.store.selectedRun = { run_id: 'r', status: 'running' }
    store().cancelRun.mockResolvedValue(undefined)
    store().loadRun.mockRejectedValue(new RunsRequestError('connection_closed'))
    const root = mount()
    const cancel = [...root.querySelectorAll('button')].find(button => button.textContent === 'runs.cancel')!
    cancel.click()
    await flush()
    expect(store().cancelRun).toHaveBeenCalledExactlyOnceWith('r')
    expect(root.textContent).toContain('runs.connectionClosed')
    expect(mocks.success).toHaveBeenCalledExactlyOnceWith('runs.cancelSuccess')
  })

  it('keeps uncertain cancellation blocked when a detail read issued before it completes late', async () => {
    const detail = deferred()
    mocks.confirm.mockResolvedValue(undefined)
    mocks.store.selectedRunId = 'r'
    mocks.store.selectedRun = { run_id: 'r', status: 'running' }
    store().loadRun.mockReturnValueOnce(detail.promise)
    store().cancelRun.mockRejectedValueOnce(new RunsRequestError('outcome_unknown'))
    const root = mount()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-yui-guide-id="runs-detail-cancel"]')!.click()
    await flush()
    detail.resolve()
    await flush()
    expect(root.querySelector('[data-yui-guide-id="runs-detail-cancel"]')).toBeNull()
    expect(root.textContent).toContain('runs.operationOutcomeUnknown')
    root.querySelector<HTMLButtonElement>('[data-yui-guide-id="runs-refresh"]')!.click()
    await flush()
    expect(root.querySelector('[data-yui-guide-id="runs-detail-cancel"]')).not.toBeNull()
  })

  it('does not surface an old selection failure on a newly selected record', async () => {
    const detail = deferred()
    mocks.store.runs = ['a', 'b'].map(run_id => ({ run_id, entry_id: run_id, status: 'succeeded', updated_at: 1 }))
    store().loadRun.mockReturnValueOnce(detail.promise)
    const root = mount()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="a"]')!.click()
    root.querySelector<HTMLButtonElement>('[data-run-id="b"]')!.click()
    await flush()
    expect(root.querySelector('.run-summary h2')?.textContent).toBe('b')
    detail.reject(new RunsRequestError('request_failed'))
    await flush()
    expect(root.querySelector('.detail-feedback')).toBeNull()
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('does not toast after navigating away while the initial connection is pending', async () => {
    const pending = deferred()
    store().refreshRuns.mockReturnValue(pending.promise)
    mount()
    unmount()
    pending.reject(new RunsRequestError('cancelled'))
    await flush()
    expect(store().disconnect).toHaveBeenCalledOnce()
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it.each([
    ['connection_failed', 'runs.connectionFailed'],
    ['connection_timeout', 'runs.connectionTimeout'],
  ] as const)('still displays a localized genuine %s failure', async (code, key) => {
    store().refreshRuns.mockRejectedValue(new RunsRequestError(code))
    const root = mount()
    await flush()
    expect(root.textContent).toContain(key)
  })

  it('does not send a cancellation after the confirmation dialog outlives the page', async () => {
    const confirmation = deferred()
    mocks.confirm.mockReturnValue(confirmation.promise)
    mocks.store.selectedRunId = 'r'
    mocks.store.selectedRun = { run_id: 'r', status: 'running' }
    const root = mount()
    const cancel = [...root.querySelectorAll('button')].find(button => button.textContent === 'runs.cancel')!
    cancel.click()
    expect(mocks.confirm).toHaveBeenCalledOnce()
    unmount()
    confirmation.resolve()
    await flush()
    expect(store().cancelRun).not.toHaveBeenCalled()
    expect(mocks.success).not.toHaveBeenCalled()
  })

  it('asks for status confirmation when a cancellation reply is lost', async () => {
    mocks.confirm.mockResolvedValue(undefined)
    mocks.store.selectedRunId = 'r'
    mocks.store.selectedRun = { run_id: 'r', status: 'running' }
    store().cancelRun.mockRejectedValue(new RunsRequestError('outcome_unknown'))
    const root = mount()
    const cancel = [...root.querySelectorAll('button')].find(button => button.textContent === 'runs.cancel')!
    cancel.click()
    await flush()
    expect(store().cancelRun).toHaveBeenCalledExactlyOnceWith('r')
    expect(mocks.error).toHaveBeenCalledExactlyOnceWith('runs.operationOutcomeUnknown')
    expect(mocks.success).not.toHaveBeenCalled()
  })
  it('retains an export refresh when selection returns to the in-flight row', async () => {
    const detail = deferred()
    const exports = deferred<unknown[]>()
    mocks.store.runs = ['a', 'b'].map(run_id => ({ run_id, status: 'running', updated_at: 1 }))
    store().loadRun.mockReturnValueOnce(detail.promise)
    store().loadExports.mockReturnValueOnce(exports.promise)
    const root = mount()
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="a"]')!.click()
    ;(mocks.store.exportRevisions as Record<string, number>).a = 1
    await flush()
    root.querySelector<HTMLButtonElement>('[data-run-id="b"]')!.click()
    root.querySelector<HTMLButtonElement>('[data-run-id="a"]')!.click()
    detail.resolve(); exports.resolve([])
    await flush()
    expect(store().loadRun.mock.calls).toEqual([['a'], ['a']])
    expect(store().loadExports.mock.calls).toEqual([['a'], ['a']])
  })

})
