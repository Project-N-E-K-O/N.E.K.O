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
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('element-plus', () => ({
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
}
let unmount = () => {}
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(Runs)
  const container = defineComponent({ setup: (_, { slots }) => () => h('div', [slots.header?.(), slots.title?.(), slots.default?.()]) })
  for (const name of ['ElRow', 'ElCol', 'ElCard', 'ElAlert', 'ElEmpty', 'ElDescriptions', 'ElDescriptionsItem']) app.component(name, container)
  app.component('ElTable', defineComponent({ setup: () => () => h('div') }))
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
  })
})
afterEach(() => { unmount() })

describe('run history page request lifecycle', () => {
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
