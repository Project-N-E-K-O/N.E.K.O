// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import enUS from '@/i18n/locales/en-US'
import { KnowledgeApiError, type KnowledgeDiagnosticQuery, type KnowledgePackJob } from '@/api/knowledge'

const api = vi.hoisted(() => ({
  status: vi.fn(),
  entries: vi.fn(),
  entry: vi.fn(),
  packs: vi.fn(),
  packJobs: vi.fn(),
  diagnostics: vi.fn(),
  setEnabled: vi.fn(),
  setEntryDisabled: vi.fn(),
  importPack: vi.fn(),
  cancelPackJob: vi.fn(),
  discardPackJob: vi.fn(),
  setPackAutoContext: vi.fn(),
  setPackIndexPolicy: vi.fn(),
  setPackMaterialType: vi.fn(),
  removePack: vi.fn(),
}))

vi.mock('@/api/knowledge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/knowledge')>()),
  knowledgeApi: api,
}))

import KnowledgeManager from './KnowledgeManager.vue'

const NativeMutationObserver = globalThis.MutationObserver
let cleanup: (() => void) | undefined

async function flush() {
  for (let i = 0; i < 8; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

async function mount() {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp(KnowledgeManager)
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': enUS } }))
  app.mount(container)
  cleanup = () => {
    app.unmount()
    container.remove()
  }
  await flush()
  return container
}

function readyStatus(enabled = true) {
  return {
    ok: true,
    ready: true,
    enabled,
    tool_available: enabled,
    status: {
      state: 'ready',
      error_code: '',
      embedding: { state: 'ready', model_id: 'fixture-model' },
      ready: true,
      enabled,
      tool_available: enabled,
      packs: 0,
      entries: 0,
      disabled_entries: 0,
      knowledge_packs: 0,
      corpus_packs: 0,
      knowledge_entries: 0,
      corpus_entries: 0,
      chunks_total: 0,
      chunks_ready: 0,
      chunks_failed: 0,
      indexed_percent: 0,
      broken_packs: [],
      sources: [],
    },
  }
}

function query(result: KnowledgeDiagnosticQuery['result'], extra: Partial<KnowledgeDiagnosticQuery> = {}) {
  return {
    timestamp: '2026-10-10T08:00:00Z',
    mode: 'lookup',
    result,
    retrieval_mode: '',
    hits: 0,
    entry_title: '',
    pack_id: '',
    elapsed_ms: 5,
    error_type: '',
    ...extra,
  }
}

function job(state: KnowledgePackJob['state']): KnowledgePackJob {
  return {
    job_id: 'job-1',
    pack_id: 'fixture-pack',
    state,
    reason: '',
    entries_total: 3,
    chunks_total: 4,
    created_at: '',
    updated_at: '',
  }
}

async function openTab(name: string) {
  const tab = document.querySelector<HTMLElement>(`#tab-${name}`)
  expect(tab, `missing tab ${name}`).toBeTruthy()
  tab!.click()
  await flush()
}

beforeEach(() => {
  // Element Plus stores observers in reactive state; happy-dom's private fields cannot be proxied.
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) {
      super(callback)
      markRaw(this)
    }
  })
  vi.clearAllMocks()
  api.status.mockResolvedValue(readyStatus())
  api.packs.mockResolvedValue({ ok: true, packs: [] })
  api.packJobs.mockResolvedValue({ ok: true, jobs: [] })
  api.entries.mockResolvedValue({ ok: true, total: 0, offset: 0, limit: 50, has_more: false, items: [] })
  api.diagnostics.mockResolvedValue({ ok: true, queries: [], index_batches: [] })
  api.setEnabled.mockResolvedValue({ ok: true, enabled: false })
})

afterEach(() => {
  cleanup?.()
  cleanup = undefined
  document.body.innerHTML = ''
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('KnowledgeManager diagnostics', () => {
  it('shows failure records and hides only misses until asked', async () => {
    api.diagnostics.mockResolvedValue({
      ok: true,
      queries: [
        query('timeout', { error_type: 'TimeoutError' }),
        query('miss'),
        query('error', { error_type: 'RuntimeError' }),
        query('matched', { entry_title: 'Fixture Entry', hits: 2, retrieval_mode: 'hybrid' }),
        query('busy'),
      ],
      index_batches: [],
    })
    const container = await mount()
    await openTab('diagnostics')

    const results = () =>
      [...container.querySelectorAll<HTMLElement>('.result-tag')].map((tag) => tag.dataset.result)
    expect(results()).toEqual(['timeout', 'error', 'matched', 'busy'])
    expect(container.textContent).toContain('TimeoutError')
    expect(container.textContent).toContain('Fixture Entry')
    expect(container.textContent).toContain('1 misses hidden')

    container.querySelector<HTMLInputElement>('[data-testid="knowledge-show-misses"] input')!.click()
    await flush()

    expect(results()).toEqual(['timeout', 'miss', 'error', 'matched', 'busy'])
  })
})

describe('KnowledgeManager overview', () => {
  it('posts the global switch and reflects the saved value', async () => {
    const container = await mount()
    const toggle = container.querySelector<HTMLElement>('[data-testid="knowledge-global-switch"]')
    expect(toggle).toBeTruthy()

    toggle!.click()
    await flush()

    expect(api.setEnabled).toHaveBeenCalledWith(false)
  })

  it('shows the error code when the service is unavailable', async () => {
    api.status.mockResolvedValue({
      ok: true,
      ready: false,
      enabled: true,
      tool_available: false,
      status: { state: 'unavailable', error_code: 'registry_invalid', embedding: { state: 'disabled', model_id: null } },
    })
    api.packs.mockRejectedValue(new KnowledgeApiError('knowledge_unavailable'))
    const container = await mount()

    expect(container.querySelector('[data-testid="knowledge-state"]')?.textContent).toContain('Unavailable')
    expect(container.textContent).toContain('The pack registry file is damaged or unreadable.')
  })
})

describe('KnowledgeManager job polling', () => {
  it('polls while an import is active and stops after unmount', async () => {
    vi.useFakeTimers()
    api.packJobs.mockResolvedValue({ ok: true, jobs: [job('building')] })
    await mount()
    const callsAfterMount = api.packJobs.mock.calls.length

    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    expect(api.packJobs.mock.calls.length).toBeGreaterThan(callsAfterMount)

    cleanup?.()
    cleanup = undefined
    const callsAfterUnmount = api.packJobs.mock.calls.length
    await vi.advanceTimersByTimeAsync(10_000)
    expect(api.packJobs.mock.calls.length).toBe(callsAfterUnmount)
  })

  it('stops polling once no job is queued or building', async () => {
    vi.useFakeTimers()
    api.packJobs
      .mockResolvedValueOnce({ ok: true, jobs: [job('queued')] })
      .mockResolvedValue({ ok: true, jobs: [job('active')] })
    await mount()

    const packsAfterMount = api.packs.mock.calls.length
    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    const callsAfterFinish = api.packJobs.mock.calls.length
    // one reload from the poll itself, one more after the job finished
    expect(api.packs.mock.calls.length).toBe(packsAfterMount + 2)

    await vi.advanceTimersByTimeAsync(30_000)
    expect(api.packJobs.mock.calls.length).toBe(callsAfterFinish)
  })

  it('keeps polling a starting service on the catalog tab and loads it once ready', async () => {
    vi.useFakeTimers()
    const starting = {
      ok: true,
      ready: false,
      enabled: false,
      tool_available: false,
      status: { state: 'starting', error_code: '', ready: false, enabled: false, tool_available: false },
    }
    api.status.mockResolvedValue(starting)
    api.entries.mockRejectedValue(new KnowledgeApiError('knowledge_starting'))
    await mount()
    await openTab('catalog')
    const entriesWhileStarting = api.entries.mock.calls.length

    api.status.mockResolvedValue(readyStatus())
    api.entries.mockResolvedValue({ ok: true, total: 0, offset: 0, limit: 50, has_more: false, items: [] })
    await vi.advanceTimersByTimeAsync(3_100)
    await flush()
    expect(api.entries.mock.calls.length).toBeGreaterThan(entriesWhileStarting)
  })

  it('reloads the catalog after toggling an entry', async () => {
    const entry = {
      pack_id: 'fixture-pack', title: 'Kotatsu', terms: {}, tags: [], summary: '',
      content_preview: 'heated table', material_type: 'knowledge',
      source: { name: 'Fixture', homepage: '', license: '' }, disabled: false,
    }
    api.entries.mockResolvedValue({ ok: true, total: 1, offset: 0, limit: 50, has_more: false, items: [entry] })
    api.setEntryDisabled.mockResolvedValue({ ok: true, pack_id: 'fixture-pack', disabled: true, disabled_entries: 1 })
    const container = await mount()
    await openTab('catalog')
    const before = api.entries.mock.calls.length
    const toggle = container.querySelector<HTMLElement>('.el-table .el-switch')
    expect(toggle).toBeTruthy()
    toggle!.click()
    await flush()
    expect(api.setEntryDisabled).toHaveBeenCalledWith({ pack_id: 'fixture-pack', title: 'Kotatsu', disabled: true })
    expect(api.entries.mock.calls.length).toBeGreaterThan(before)
  })

  it('reloads the open catalog when an import finishes', async () => {
    vi.useFakeTimers()
    api.packJobs
      .mockResolvedValueOnce({ ok: true, jobs: [job('building')] })
      .mockResolvedValue({ ok: true, jobs: [job('active')] })
    await mount()
    await openTab('catalog')
    const before = api.entries.mock.calls.length
    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    expect(api.entries.mock.calls.length).toBeGreaterThan(before)
  })
})
