// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, reactive } from 'vue'
import Runs from './Runs.vue'
import { RunsRequestError } from '@/utils/runsError'
import { ElTable, ElTableColumn } from 'element-plus'

const mocks = vi.hoisted(() => ({
  store: null as unknown as Record<string, unknown>,
  error: vi.fn(), success: vi.fn(), confirm: vi.fn(),
}))
vi.mock('@/stores/runs', () => ({ useRunsStore: () => mocks.store }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('element-plus', async importOriginal => ({
  ...await importOriginal<typeof import('element-plus')>(),
  ElMessage: { error: mocks.error, success: mocks.success }, ElMessageBox: { confirm: mocks.confirm },
}))

function deferred() {
  let resolve!: () => void
  let reject!: (error: unknown) => void
  const promise = new Promise<void>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

const store = () => mocks.store as unknown as {
  refreshRuns: ReturnType<typeof vi.fn>; cancelRun: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn>
  loadRun: ReturnType<typeof vi.fn>; loadExports: ReturnType<typeof vi.fn>
  selectRun: ReturnType<typeof vi.fn>
}
let unmount = () => {}
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

function mount(realTable = false) {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(Runs)
  const container = defineComponent({ setup: (_, { slots }) => () => h('div', [slots.header?.(), slots.title?.(), slots.default?.()]) })
  for (const name of ['ElRow', 'ElCol', 'ElCard', 'ElAlert', 'ElEmpty', 'ElDescriptions', 'ElDescriptionsItem']) app.component(name, container)
  app.component('ElTable', realTable ? ElTable : defineComponent({ setup: () => () => h('div') }))
  app.component('ElTableColumn', ElTableColumn)
  app.component('ElTag', container)
  app.component('ElButton', defineComponent({
    inheritAttrs: false,
    setup: (_, { attrs, slots }) => () => h('button', { onClick: attrs.onClick as () => void }, slots.default?.()),
  }))
  app.use({ install: instance => { instance.config.globalProperties.$t = (key: string) => key } })
  app.mount(root)
  unmount = () => { app.unmount(); root.remove(); unmount = () => {} }
  return root
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.store = reactive({
    connected: false, connecting: true, runs: [], selectedRun: null, selectedExports: [],
    refreshRuns: vi.fn().mockResolvedValue(undefined), disconnect: vi.fn(),
    cancelRun: vi.fn(), loadRun: vi.fn().mockResolvedValue(undefined), loadExports: vi.fn().mockResolvedValue([]),
    selectRun: vi.fn((id: string) => {
      mocks.store.selectedRun = (mocks.store.runs as { run_id: string }[]).find(row => row.run_id === id)
    }),
  })
})
afterEach(() => { unmount() })

describe('run history page request lifecycle', () => {
  it('loads only the latest row selected while the previous detail load is pending', async () => {
    const detail = deferred()
    const exports = deferred()
    mocks.store.runs = ['a', 'b', 'c'].map(run_id => ({ run_id, status: 'running', updated_at: 1 }))
    store().loadRun.mockReturnValueOnce(detail.promise)
    store().loadExports.mockReturnValueOnce(exports.promise)
    const root = mount(true)
    await flush()
    const rows = root.querySelectorAll<HTMLElement>('.el-table__body tbody tr')
    rows[0]!.click()
    rows[1]!.click()
    rows[2]!.click()
    detail.reject(new RunsRequestError('request_failed'))
    await flush()
    expect(store().loadRun.mock.calls).toEqual([['a']])
    exports.resolve()
    await flush()
    expect(store().selectRun.mock.calls).toEqual([['a'], ['c']])
    expect(store().loadRun.mock.calls).toEqual([['a'], ['c']])
    expect(store().loadExports.mock.calls).toEqual([['a'], ['c']])
    expect(mocks.store.selectedRun).toMatchObject({ run_id: 'c' })
  })

  it('discards a pending row change when the user returns to the row already loading', async () => {
    const detail = deferred()
    mocks.store.runs = ['a', 'b'].map(run_id => ({ run_id, status: 'running', updated_at: 1 }))
    store().loadRun.mockReturnValueOnce(detail.promise)
    const root = mount(true)
    await flush()
    const rows = root.querySelectorAll<HTMLElement>('.el-table__body tbody tr')
    rows[0]!.click()
    rows[1]!.click()
    rows[0]!.click()
    detail.resolve()
    await flush()
    expect(store().selectRun.mock.calls).toEqual([['a']])
    expect(store().loadRun.mock.calls).toEqual([['a']])
    expect(mocks.store.selectedRun).toMatchObject({ run_id: 'a' })
  })

  it('discards pending row changes and late errors when the page is unmounted', async () => {
    const detail = deferred()
    mocks.store.runs = ['a', 'b'].map(run_id => ({ run_id, status: 'running', updated_at: 1 }))
    store().loadRun.mockReturnValueOnce(detail.promise)
    const root = mount(true)
    await flush()
    const rows = root.querySelectorAll<HTMLElement>('.el-table__body tbody tr')
    rows[0]!.click()
    rows[1]!.click()
    unmount()
    detail.reject(new RunsRequestError('connection_closed'))
    await flush()
    expect(store().selectRun.mock.calls).toEqual([['a']])
    expect(store().loadRun.mock.calls).toEqual([['a']])
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it.each(['loadRun', 'loadExports'] as const)('retries the same actual table row after %s fails without duplicating successful loads', async method => {
    mocks.store.runs = [{ run_id: 'r', plugin_id: 'p', entry_id: 'e', status: 'running', updated_at: 1 }]
    store()[method].mockRejectedValueOnce(new RunsRequestError('connection_closed'))
    const root = mount(true)
    await flush()
    const row = root.querySelector<HTMLElement>('.el-table__body tbody tr')!
    expect(row).not.toBeNull()
    row.click()
    await flush()
    expect(store().loadRun).toHaveBeenCalledTimes(1)
    expect(store().loadExports).toHaveBeenCalledTimes(1)
    expect(mocks.error).toHaveBeenCalledExactlyOnceWith('runs.connectionClosed')
    row.click()
    await flush()
    expect(store().loadRun).toHaveBeenCalledTimes(2)
    expect(store().loadExports).toHaveBeenCalledTimes(2)
    row.click()
    await flush()
    expect(store().loadRun).toHaveBeenCalledTimes(2)
    expect(store().loadExports).toHaveBeenCalledTimes(2)
  })

  it('waits for both detail requests to settle before allowing another selection load', async () => {
    const exports = deferred()
    mocks.store.runs = [{ run_id: 'r', plugin_id: 'p', entry_id: 'e', status: 'running', updated_at: 1 }]
    store().loadRun.mockRejectedValueOnce(new RunsRequestError('request_failed'))
    store().loadExports.mockReturnValueOnce(exports.promise)
    const root = mount(true)
    await flush()
    const row = root.querySelector<HTMLElement>('.el-table__body tbody tr')!
    row.click()
    await flush()
    row.click()
    expect(store().loadRun).toHaveBeenCalledTimes(1)
    expect(store().loadExports).toHaveBeenCalledTimes(1)
    exports.resolve()
    await flush()
    row.click()
    await flush()
    expect(store().loadRun).toHaveBeenCalledTimes(2)
    expect(store().loadExports).toHaveBeenCalledTimes(2)
  })

  it('keeps confirmed cancellation success when the subsequent refresh fails', async () => {
    mocks.confirm.mockResolvedValue(undefined)
    mocks.store.selectedRun = { run_id: 'r', status: 'running' }
    store().cancelRun.mockResolvedValue(undefined)
    store().loadRun.mockRejectedValue(new RunsRequestError('connection_closed'))
    const root = mount()
    const cancel = [...root.querySelectorAll('button')].find(button => button.textContent === 'runs.cancel')!
    cancel.click()
    await flush()
    expect(store().cancelRun).toHaveBeenCalledExactlyOnceWith('r')
    expect(mocks.error).toHaveBeenCalledExactlyOnceWith('runs.connectionClosed')
    expect(mocks.success).toHaveBeenCalledExactlyOnceWith('runs.cancelSuccess')
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
    mount()
    await flush()
    expect(mocks.error).toHaveBeenCalledExactlyOnceWith(key)
  })

  it('does not send a cancellation after the confirmation dialog outlives the page', async () => {
    const confirmation = deferred()
    mocks.confirm.mockReturnValue(confirmation.promise)
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
})
