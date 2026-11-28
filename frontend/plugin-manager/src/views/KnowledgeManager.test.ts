// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus, { ElMessageBox } from 'element-plus'
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
  retryPackJob: vi.fn(),
  discardPackJob: vi.fn(),
  setPackAutoContext: vi.fn(),
  setPackIndexPolicy: vi.fn(),
  setPackMaterialType: vi.fn(),
  setPackEnabled: vi.fn(),
  removePack: vi.fn(),
  previewSource: vi.fn(),
  commitSource: vi.fn(),
  discardSourcePreview: vi.fn(),
  sources: vi.fn(),
  sourceVersions: vi.fn(),
  restoreSource: vi.fn(),
  removeSource: vi.fn(),
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

function pack(enabled = false, packId = 'imp.test', digest = 'a'.repeat(64)) {
  return {
    pack_id: packId, pack_sha256: digest, source: { name: 'My notes', homepage: '', license: '' },
    policy_revision: { epoch: 'fixture-process', counter: 0 },
    origin: 'imported', enabled, auto_context: false, local_embedding: true,
    declared_material_type: 'knowledge', material_type_override: null, effective_material_type: 'knowledge',
    entries: 1, disabled_entries: 0, chunks_total: 1, chunks_ready: 0, chunks_failed: 0,
    vector_state: 'waiting', export_ready: false, broken: false, subscription: null, installed_at: '', updated_at: '',
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
  api.previewSource.mockResolvedValue({ ok: true, pack_id: 'imp.source', preview: {
    preview_id: 'preview-source', doc_id: 'doc-source', sha256: 'source-digest',
    name: 'source.txt', chars: 12, content: 'Source notes.', sections: [],
  } })
  api.discardSourcePreview.mockResolvedValue({ ok: true, deleted: false })
  api.sources.mockResolvedValue({ ok: true, sources: [
    { doc_id: 'doc-source', name: 'source.txt', chars: 12, current_version: 2, versions_count: 2 },
  ] })
  api.sourceVersions.mockResolvedValue({ ok: true, versions: [
    { version: 1, name: 'source.txt', chars: 10, sha256: 'old', created_at: '', current: false },
  ] })
})

afterEach(() => {
  cleanup?.()
  cleanup = undefined
  document.body.innerHTML = ''
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
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
  it.each([true, false])('warns about retained content before removing an unavailable pack (confirm=%s)', async confirmed => {
    api.packs.mockResolvedValue({ packs: [{ ...pack(), broken: true, entries: 0, vector_state: 'unavailable' }] })
    let accept!: (value: Awaited<ReturnType<typeof ElMessageBox.confirm>>) => void
    let cancel!: (reason: string) => void
    const confirmation = vi.spyOn(ElMessageBox, 'confirm').mockReturnValue(new Promise((resolve, reject) => {
      accept = resolve
      cancel = reject
    }))
    api.removePack.mockResolvedValue({ ok: true })
    const container = await mount()
    await openTab('packs')
    const remove = [...container.querySelectorAll<HTMLButtonElement>('.packs-table button')]
      .find(button => button.textContent?.trim() === enUS.knowledge.remove)!
    remove.click()
    await flush()
    // Zero is a usability projection, not a count of retained content at risk.
    const message = confirmation.mock.calls[0]![0]
    expect(message).toContain('My notes')
    expect(message).toContain('retained entries will be removed from the knowledge base')
    expect(message).toContain('Original documents and version history are kept')
    expect(message).not.toContain('0 entries')
    expect(api.removePack).not.toHaveBeenCalled()
    if (confirmed) accept('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    else cancel('cancel')
    await flush()
    if (confirmed) expect(api.removePack).toHaveBeenCalledWith({ pack_id: 'imp.test' })
    else expect(api.removePack).not.toHaveBeenCalled()
  })

  it('opens retained exports after removing their pack', async () => {
    // Removing a pack does not remove its completed task or artifact identity.
    let installed = true
    api.packs.mockImplementation(async () => ({ packs: installed ? [pack()] : [] }))
    api.removePack.mockImplementation(async () => { installed = false; return { ok: true } })
    api.packJobs.mockResolvedValue({ jobs: [{ job_id: 'retained', pack_id: 'imp.test', kind: 'pack_export',
      state: 'active', created_at: '2026-11-19T07:00:00Z', result: { artifact_id: 'f'.repeat(64),
        name: 'Retained after removal', format_version: 2, bytes: 1024 } } as KnowledgePackJob] })
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    const container = await mount()
    await openTab('packs')
    const remove = [...container.querySelectorAll<HTMLButtonElement>('.packs-table button')]
      .find(button => button.textContent?.trim() === enUS.knowledge.remove)!
    remove.click()
    await flush()
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      'Remove data pack My notes? Its 1 entries will be deleted.', enUS.common.warning,
      expect.objectContaining({ confirmButtonText: enUS.knowledge.remove }),
    )
    expect(api.removePack).toHaveBeenCalledWith({ pack_id: 'imp.test' })
    const exports = container.querySelector<HTMLButtonElement>('[data-testid="knowledge-completed-exports"]')
    expect(exports).toBeTruthy()
    exports!.click()
    await flush()
    expect(document.querySelector('.artifact-name')?.textContent).toBe('Retained after removal')
    expect(document.querySelectorAll('.artifact-actions button')).toHaveLength(3)
    expect(api.sources).not.toHaveBeenCalled()
  })

  it('shows broken vector data as unavailable instead of unfinished work', async () => {
    api.packs.mockResolvedValue({ packs: [{ ...pack(), broken: true, vector_state: 'unavailable',
      entries: 0, chunks_total: 0, chunks_ready: 0 }] })
    const container = await mount()
    await openTab('packs')
    const tag = container.querySelector('.pack-count--left .el-tag')!
    expect(tag.textContent).toBe(enUS.knowledge.brokenTag)
    expect(tag.classList.contains('el-tag--danger')).toBe(true)
  })

  it.each([
    { operation: 'new', outcome: 'active', staleFirst: false },
    { operation: 'append', outcome: 'active', staleFirst: false },
    { operation: 'restore', outcome: 'active', staleFirst: false },
    { operation: 'remove', outcome: 'active', staleFirst: false },
    { operation: 'new', outcome: 'failed', staleFirst: false },
    { operation: 'new', outcome: 'active', staleFirst: true },
  ] as const)('refreshes a fast source job after stale overview reads (%s)', async ({ operation, outcome, staleFirst }) => {
    vi.useFakeTimers()
    const targetId = operation === 'new' ? 'imp.source' : 'imp.test'
    const initial = operation === 'new' ? [] : [{ ...pack(), sources_managed: true, chunks_total: 0, vector_state: 'disabled', local_embedding: false }]
    api.packs.mockResolvedValue({ ok: true, packs: initial })
    let finishJobs!: (value: object) => void
    const admitted = { ok: true, pack_id: targetId, job_id: 'source-new', state: 'queued', entries_total: 1, chunks_total: 0 }
    const admit = async () => {
      // The mutation admits a job, then overview's packs/status reads still
      // see the old catalog while its parallel jobs read waits for completion.
      api.packJobs.mockReturnValueOnce(new Promise(resolve => { finishJobs = resolve }))
      return admitted
    }
    api.commitSource.mockImplementation(admit)
    api.restoreSource.mockImplementation(admit)
    api.removeSource.mockImplementation(admit)
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    const container = await mount()
    await openTab('packs')
    const clickButton = async (text: string) => {
      const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(row => row.textContent?.trim() === text)
      expect(button, text).toBeTruthy()
      button!.click()
      await flush()
    }
    if (operation !== 'new') await clickButton(enUS.knowledge.managePack)
    if (operation === 'new' || operation === 'append') {
      const inputs = [...document.querySelectorAll<HTMLInputElement>('input[type="file"]')]
      const input = operation === 'new' ? inputs[0]! : inputs.at(-1)!
      Object.defineProperty(input, 'files', { value: [new File(['Source notes.'], 'source.txt')], configurable: true })
      input.dispatchEvent(new Event('change', { bubbles: true }))
      await flush()
      await clickButton(enUS.knowledge.previewDocument)
      await clickButton(enUS.knowledge.sourceCommit)
    } else if (operation === 'restore') {
      await clickButton(enUS.knowledge.sourceHistory)
      await clickButton(enUS.knowledge.restoreSource)
    } else {
      const remove = [...document.querySelectorAll<HTMLButtonElement>('.el-drawer button')]
        .find(row => row.textContent?.trim() === enUS.knowledge.remove)!
      expect(remove).toBeTruthy()
      remove.click()
      await flush()
    }
    expect(finishJobs).toBeTypeOf('function')
    const loadsBeforeCompletion = api.packs.mock.calls.length
    expect(container.textContent).not.toContain(`${operation} finished`)
    // No vectors or other running jobs can rescue this race with later polling.
    api.packs.mockResolvedValue({ ok: true, packs: outcome === 'active' ? [{ ...pack(false, targetId), source: { name: `${operation} finished` },
      chunks_total: 0, vector_state: 'disabled', local_embedding: false }] : initial })
    const terminal = { ...job(outcome), job_id: 'source-new', pack_id: targetId, kind: 'import_commit',
      reason: outcome === 'failed' ? 'source_confirmation_required' : '' }
    if (staleFirst) {
      finishJobs({ ok: true, jobs: [] })
      await flush()
      expect(container.querySelector('[data-job-id="source-new"]')?.textContent).toContain(enUS.knowledge.jobQueued)
      api.packJobs.mockResolvedValue({ ok: true, jobs: [terminal] })
      await vi.advanceTimersByTimeAsync(2_100)
    } else finishJobs({ ok: true, jobs: [terminal] })
    await flush()
    expect(api.packs.mock.calls.length).toBeGreaterThan(loadsBeforeCompletion)
    if (outcome === 'active') expect(container.textContent).toContain(`${operation} finished`)
    else {
      expect(container.textContent).not.toContain(`${operation} finished`)
      expect(document.body.textContent).toContain(enUS.knowledge.reasons.source_confirmation_required)
    }
  })

  it.each(['active', 'unchanged', 'failed', 'failed-with-empty-list'] as const)('does not invent queued work for a source response that is already %s', async result => {
    vi.useFakeTimers()
    const failed = result === 'failed' || result === 'failed-with-empty-list'
    api.commitSource.mockImplementation(async () => {
      if (result === 'failed') api.packJobs.mockRejectedValue(new KnowledgeApiError('knowledge_error'))
      // The empty-list variant is a stale successful snapshot, rather than
      // a rejected refresh; both must retain the admitted terminal reason.
      return { ok: true, pack_id: 'imp.source', state: failed ? 'failed' : 'active',
        ...(result === 'unchanged' ? { unchanged: true } : { job_id: 'source-terminal' }),
        reason: failed ? 'knowledge_stopping' : '' }
    })
    const container = await mount()
    await openTab('packs')
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!
    Object.defineProperty(input, 'files', { value: [new File(['Source notes.'], 'source.txt')], configurable: true })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await flush()
    for (const label of [enUS.knowledge.previewDocument, enUS.knowledge.sourceCommit]) {
      const action = [...document.querySelectorAll<HTMLButtonElement>('button')].find(row => row.textContent?.trim() === label)!
      action.click()
      await flush()
    }
    expect(container.textContent).not.toContain(enUS.knowledge.jobQueued)
    if (failed) {
      expect(container.querySelector('[data-job-id="source-terminal"]')?.textContent).toContain(enUS.knowledge.reasons.knowledge_stopping)
    } else expect(container.querySelector('[data-job-id="source-terminal"]')).toBeNull()
    const loads = api.packJobs.mock.calls.length
    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    expect(api.packJobs).toHaveBeenCalledTimes(loads)
  })

  it('does not register or refresh a source admission after the manager unmounts', async () => {
    let finish!: (value: object) => void
    api.commitSource.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const container = await mount()
    await openTab('packs')
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!
    Object.defineProperty(input, 'files', { value: [new File(['Source notes.'], 'source.txt')], configurable: true })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await flush()
    for (const label of [enUS.knowledge.previewDocument, enUS.knowledge.sourceCommit]) {
      const action = [...document.querySelectorAll<HTMLButtonElement>('button')].find(row => row.textContent?.trim() === label)!
      action.click()
      await flush()
    }
    expect(api.commitSource).toHaveBeenCalledTimes(1)
    const calls = [api.packs.mock.calls.length, api.packJobs.mock.calls.length]
    cleanup?.()
    cleanup = undefined
    finish({ ok: true, pack_id: 'imp.source', job_id: 'late-admission', state: 'queued' })
    await flush()
    expect([api.packs.mock.calls.length, api.packJobs.mock.calls.length]).toEqual(calls)
  })

  it('offers frozen retry only for failed or cancelled jobs, never a waiting job', async () => {
    api.packJobs.mockResolvedValue({ ok: true, jobs: [
      { ...job('failed'), reason: 'process_interrupted' },
      { ...job('cancelled'), job_id: 'cancelled-1' },
      { ...job('building'), job_id: 'busy-1', reason: 'knowledge_busy' },
      { ...job('queued'), job_id: 'queued-1' },
    ] })
    api.retryPackJob.mockResolvedValue({ ok: true, job_id: 'retry-1', state: 'queued' })
    const container = await mount()
    await openTab('packs')
    const retry = (id: string) => [...container.querySelectorAll<HTMLButtonElement>(`[data-job-id="${id}"] button`)]
      .find(button => button.textContent?.trim() === 'Retry task')
    expect(retry('job-1')).toBeTruthy()
    expect(retry('cancelled-1')).toBeTruthy()
    expect(retry('busy-1')).toBeUndefined()
    expect(retry('queued-1')).toBeUndefined()
    retry('job-1')!.click()
    await flush()
    expect(api.retryPackJob).toHaveBeenCalledWith({ job_id: 'job-1' })
    expect(api.retryPackJob).toHaveBeenCalledTimes(1)
    retry('cancelled-1')!.click()
    await flush()
    expect(api.retryPackJob).toHaveBeenLastCalledWith({ job_id: 'cancelled-1' })
  })

  it('does not revive retry or discard actions from a stale post-retry polling snapshot', async () => {
    vi.useFakeTimers()
    api.packJobs.mockResolvedValue({ ok: true, jobs: [job('failed')] })
    api.retryPackJob.mockResolvedValue({ ok: true, job_id: 'retry-new', state: 'queued' })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLButtonElement>('[data-job-id="job-1"] button')!.click()
    await flush()
    expect(api.retryPackJob).toHaveBeenCalledTimes(1)
    expect(container.querySelector('[data-job-id="job-1"]')).toBeNull()
    const newTask = container.querySelector('[data-job-id="retry-new"]')
    expect(newTask).not.toBeNull()
    expect(newTask!.textContent).toContain(enUS.knowledge.jobQueued)
    expect(newTask!.querySelector('button')!.textContent).toContain(enUS.knowledge.cancelJob)
    expect(api.discardPackJob).not.toHaveBeenCalled()
    const beforePoll = api.packJobs.mock.calls.length
    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    expect(api.packJobs.mock.calls.length).toBeGreaterThan(beforePoll)
    expect(container.querySelector('[data-job-id="retry-new"]')).not.toBeNull()
  })

  it.each(['knowledge_stopping', undefined])('preserves the admitted retry reason through failed and stale refreshes (%s)', async reason => {
    vi.useFakeTimers()
    api.packJobs.mockResolvedValue({ ok: true, jobs: [
      { ...job('failed'), reason: 'process_interrupted' },
      { ...job('building'), job_id: 'other-running' },
    ] })
    api.retryPackJob.mockResolvedValue({ ok: true, job_id: 'retry-terminal', state: 'failed', ...(reason ? { reason } : {}) })
    const container = await mount()
    await openTab('packs')
    api.packJobs.mockRejectedValueOnce(new KnowledgeApiError('knowledge_error'))
    container.querySelector<HTMLButtonElement>('[data-job-id="job-1"] button')!.click()
    await flush()
    const text = () => container.querySelector('[data-job-id="retry-terminal"]')!.textContent!
    expect(text()).toContain(enUS.knowledge.jobFailed)
    expect(text()).not.toContain(enUS.knowledge.reasons.process_interrupted)
    if (reason) expect(text()).toContain(enUS.knowledge.reasons.knowledge_stopping)
    const callsBeforePoll = api.packJobs.mock.calls.length
    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    expect(api.packJobs.mock.calls.length).toBeGreaterThan(callsBeforePoll)
    expect(text()).not.toContain(enUS.knowledge.reasons.process_interrupted)
    if (reason) expect(text()).toContain(enUS.knowledge.reasons.knowledge_stopping)
  })

  it('hands a retry over to the server snapshot after polling confirms its identity', async () => {
    vi.useFakeTimers()
    api.packJobs.mockResolvedValue({ ok: true, jobs: [job('failed')] })
    api.retryPackJob.mockResolvedValue({ ok: true, job_id: 'retry-new', state: 'queued' })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLButtonElement>('[data-job-id="job-1"] button')!.click()
    await flush()
    api.packJobs.mockResolvedValue({ ok: true, jobs: [{ ...job('building'), job_id: 'retry-new' }] })
    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    expect(container.querySelector('[data-job-id="retry-new"]')!.textContent).toContain(enUS.knowledge.jobBuilding)
    api.packJobs.mockResolvedValue({ ok: true, jobs: [{ ...job('active'), job_id: 'retry-new' }] })
    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    expect(container.querySelector('[data-job-id="retry-new"]')).toBeNull()
    const afterCompletion = api.packJobs.mock.calls.length
    await vi.advanceTimersByTimeAsync(4_200)
    await flush()
    expect(api.packJobs.mock.calls.length).toBe(afterCompletion)
  })

  it.each([true, false])('keeps an unconfirmed retry row discarded only after server success (%s)', async succeeds => {
    let freezeNextList = false
    let finishList!: (value: unknown) => void
    const stale = { ok: true, jobs: [job('failed')] }
    api.packJobs.mockImplementation(() => {
      if (!freezeNextList) return Promise.resolve(stale)
      freezeNextList = false
      return new Promise(resolve => { finishList = resolve })
    })
    api.retryPackJob.mockImplementation(async () => {
      freezeNextList = true
      return { ok: true, job_id: 'retry-cancelled', state: 'cancelled' }
    })
    if (succeeds) api.discardPackJob.mockResolvedValue({ ok: true })
    else api.discardPackJob.mockRejectedValue(new KnowledgeApiError('knowledge_busy'))
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLButtonElement>('[data-job-id="job-1"] button')!.click()
    await flush()
    expect(finishList).toBeTypeOf('function')
    const retryRow = () => container.querySelector('[data-job-id="retry-cancelled"]')
    expect(retryRow()).not.toBeNull()
    const discard = [...retryRow()!.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === enUS.knowledge.discardJob)!
    discard.click()
    await flush()
    expect(api.discardPackJob).toHaveBeenCalledWith({ job_id: 'retry-cancelled' })
    expect(Boolean(retryRow())).toBe(!succeeds)
    // Neither the in-flight snapshot nor a later refresh may revive a successful discard.
    finishList(stale)
    await flush()
    container.querySelector<HTMLButtonElement>('.page-heading button')!.click()
    await flush()
    expect(Boolean(retryRow())).toBe(!succeeds)
  })

  it('keeps a failed retry available for another attempt', async () => {
    api.packJobs.mockResolvedValue({ ok: true, jobs: [job('failed')] })
    api.retryPackJob.mockRejectedValue(new KnowledgeApiError('knowledge_busy'))
    const container = await mount()
    await openTab('packs')
    const retry = container.querySelector<HTMLButtonElement>('[data-job-id="job-1"] button')!
    retry.click()
    await flush()
    expect(retry.disabled).toBe(false)
  })

  it.each(['failed', 'cancelled'] as const)('keeps an unchanged %s retry visible and discardable', async state => {
    api.packJobs.mockResolvedValue({ ok: true, jobs: [job(state)] })
    api.retryPackJob.mockResolvedValue({ ok: true, unchanged: true, pack_id: 'fixture-pack' })
    api.discardPackJob.mockResolvedValue({ ok: true })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLButtonElement>('[data-job-id="job-1"] button')!.click()
    await flush()
    // No replacement job took ownership of this retained failure or its input.
    const row = container.querySelector('[data-job-id="job-1"]')
    expect(row).not.toBeNull()
    expect(document.body.textContent).toContain(enUS.knowledge.importUnchanged.replace('{name}', 'fixture-pack'))
    const discard = [...row!.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === enUS.knowledge.discardJob)!
    expect(discard.disabled).toBe(false)
    discard.click()
    await flush()
    expect(api.discardPackJob).toHaveBeenCalledExactlyOnceWith({ job_id: 'job-1' })
    expect(container.querySelector('[data-job-id="job-1"]')).toBeNull()
  })

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

  it.each([
    { globalEnabled: true, requested: false, saved: false },
    { globalEnabled: false, requested: true, saved: true },
    { globalEnabled: true, requested: true, saved: false },
  ])('keeps the saved pack toggle independent of global availability after a failed refresh (%s)', async ({ globalEnabled, requested, saved }) => {
    let acknowledge!: (value: Awaited<ReturnType<typeof ElMessageBox.confirm>>) => void
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockReturnValue(new Promise(resolve => { acknowledge = resolve }))
    api.status.mockResolvedValue(readyStatus(globalEnabled))
    api.packs.mockResolvedValue({ ok: true, packs: [pack(!requested)] })
    api.setPackEnabled.mockResolvedValue({ ok: true, pack_id: 'imp.test', enabled: globalEnabled, pack_enabled: saved, policy_revision: { epoch: 'fixture-process', counter: 1 } })
    const container = await mount()
    await openTab('packs')
    expect(container.textContent).toContain('Imported documents')
    const toggle = container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!
    expect(toggle.querySelector('input')!.getAttribute('aria-checked')).toBe(String(!requested))
    const initialPackLoads = api.packs.mock.calls.length
    api.packs.mockRejectedValue(new KnowledgeApiError('knowledge_error'))
    toggle.click()
    await flush()
    if (requested) {
      expect(confirm).toHaveBeenCalledWith(enUS.knowledge.cloudDisclosure, enUS.common.warning, { type: 'warning' })
      expect(api.setPackEnabled).not.toHaveBeenCalled()
      acknowledge('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    } else {
      expect(confirm).not.toHaveBeenCalled()
    }
    await flush()
    expect(api.setPackEnabled).toHaveBeenCalledExactlyOnceWith({
      pack_id: 'imp.test', enabled: requested, ...(requested ? { expected_pack_sha256: 'a'.repeat(64) } : {}),
    })
    expect(api.packs).toHaveBeenCalledTimes(initialPackLoads + 1)
    // A failed refresh leaves this response as the visible source of truth.
    expect(toggle.querySelector('input')!.getAttribute('aria-checked')).toBe(String(saved))
    expect(container.querySelector('[data-testid="knowledge-global-switch"] input')!.getAttribute('aria-checked')).toBe(String(globalEnabled))
    expect(api.setPackAutoContext).not.toHaveBeenCalled()
    expect(api.setPackIndexPolicy).not.toHaveBeenCalled()
    expect(container.querySelector<HTMLInputElement>('input[type="file"]')!.accept).toContain('.pdf')
  })

  it('updates the current pack row and open drawer when a refresh replaces the saving row', async () => {
    let finish!: (value: object) => void
    api.setPackEnabled.mockReturnValue(new Promise(resolve => { finish = resolve }))
    api.packs.mockResolvedValue({ ok: true, packs: [pack(true)] })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!.click()
    await flush()
    const initialLoads = api.packs.mock.calls.length
    // This real refresh installs a new row object while the save is pending.
    api.packs.mockResolvedValue({ ok: true, packs: [pack(true)] })
    container.querySelector<HTMLButtonElement>('.page-heading button')!.click()
    await flush()
    expect(api.packs.mock.calls.length).toBeGreaterThan(initialLoads)
    const manage = [...container.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === enUS.knowledge.managePack)!
    manage.click()
    await flush()
    expect(document.querySelector('.pack-summary .el-tag')!.textContent).toBe(enUS.knowledge.entryEnabled)
    api.packs.mockRejectedValue(new KnowledgeApiError('knowledge_error'))
    finish({ ok: true, pack_id: 'imp.test', enabled: true, pack_enabled: false, policy_revision: { epoch: 'fixture-process', counter: 1 } })
    await flush()
    expect(container.querySelector<HTMLInputElement>('[data-testid="pack-enabled-imp.test"] input')!.getAttribute('aria-checked')).toBe('false')
    expect(document.querySelector('.pack-summary .el-tag')!.textContent).toBe(enUS.knowledge.entryDisabled)
  })

  it.each([true, false])('does not restore a missing pack or change another row after a save (%s)', async showOther => {
    let finish!: (value: object) => void
    api.setPackEnabled.mockReturnValue(new Promise(resolve => { finish = resolve }))
    api.packs.mockResolvedValue({ ok: true, packs: [pack(true)] })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!.click()
    await flush()
    api.packs.mockResolvedValue({ ok: true, packs: showOther ? [pack(true, 'other-pack')] : [] })
    container.querySelector<HTMLButtonElement>('.page-heading button')!.click()
    await flush()
    expect(container.querySelector('[data-testid="pack-enabled-imp.test"]')).toBeNull()
    api.packs.mockRejectedValue(new KnowledgeApiError('knowledge_error'))
    finish({ ok: true, pack_id: 'imp.test', enabled: true, pack_enabled: false, policy_revision: { epoch: 'fixture-process', counter: 1 } })
    await flush()
    expect(container.querySelector('[data-testid="pack-enabled-imp.test"]')).toBeNull()
    if (showOther) expect(container.querySelector<HTMLInputElement>('[data-testid="pack-enabled-other-pack"] input')!.getAttribute('aria-checked')).toBe('true')
  })

  it('binds enabling to the confirmed body and requires a fresh confirmation after replacement', async () => {
    const oldDigest = 'a'.repeat(64), newDigest = 'b'.repeat(64)
    let acknowledge!: (value: Awaited<ReturnType<typeof ElMessageBox.confirm>>) => void
    const confirm = vi.spyOn(ElMessageBox, 'confirm')
      .mockReturnValueOnce(new Promise(resolve => { acknowledge = resolve }))
      .mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    api.packs.mockResolvedValue({ ok: true, packs: [pack(false, 'imp.test', oldDigest)] })
    api.setPackEnabled.mockImplementation(async data => {
      if (data.expected_pack_sha256 !== newDigest) throw new KnowledgeApiError('pack_content_changed')
      return { ok: true, pack_id: 'imp.test', enabled: true, pack_enabled: true, policy_revision: { epoch: 'fixture-process', counter: 1 } }
    })
    const container = await mount()
    await openTab('packs')
    const toggle = () => container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!
    toggle().click()
    await flush()
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(api.setPackEnabled).not.toHaveBeenCalled()
    // Another window replaces the same ID while the original confirmation is open.
    api.packs.mockResolvedValue({ ok: true, packs: [pack(false, 'imp.test', newDigest)] })
    container.querySelector<HTMLButtonElement>('.page-heading button')!.click()
    await flush()
    const loadsBeforeRejection = api.packs.mock.calls.length
    acknowledge('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    await flush()
    expect(api.setPackEnabled).toHaveBeenCalledExactlyOnceWith({ pack_id: 'imp.test', enabled: true, expected_pack_sha256: oldDigest })
    expect(api.packs.mock.calls.length).toBeGreaterThan(loadsBeforeRejection)
    expect(toggle().querySelector('input')!.getAttribute('aria-checked')).toBe('false')
    expect(document.body.textContent).toContain(enUS.knowledge.reasons.pack_content_changed)
    expect(confirm).toHaveBeenCalledTimes(1)
    // Loading the new body never enables it automatically: the user must click and confirm again.
    toggle().click()
    await flush()
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(api.setPackEnabled).toHaveBeenLastCalledWith({ pack_id: 'imp.test', enabled: true, expected_pack_sha256: newDigest })
    expect(toggle().querySelector('input')!.getAttribute('aria-checked')).toBe('true')
  })

  it.each([true, false])('applies a delayed enable only to the same displayed body (%s)', async sameBody => {
    let finish!: (value: object) => void
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    api.setPackEnabled.mockReturnValue(new Promise(resolve => { finish = resolve }))
    api.packs.mockResolvedValue({ ok: true, packs: [pack()] })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!.click()
    await flush()
    api.packs.mockResolvedValue({ ok: true, packs: [pack(false, 'imp.test', (sameBody ? 'a' : 'b').repeat(64))] })
    container.querySelector<HTMLButtonElement>('.page-heading button')!.click()
    await flush()
    api.packs.mockRejectedValue(new KnowledgeApiError('knowledge_error'))
    finish({ ok: true, pack_id: 'imp.test', enabled: false, pack_enabled: true, policy_revision: { epoch: 'fixture-process', counter: 1 } })
    await flush()
    expect(container.querySelector<HTMLInputElement>('[data-testid="pack-enabled-imp.test"] input')!.getAttribute('aria-checked')).toBe(String(sameBody))
  })

  it.each([true, false])('applies a delayed disable only to the same displayed body (%s)', async sameBody => {
    let finish!: (value: object) => void
    const confirm = vi.spyOn(ElMessageBox, 'confirm')
    api.setPackEnabled.mockReturnValue(new Promise(resolve => { finish = resolve }))
    api.packs.mockResolvedValue({ ok: true, packs: [pack(true)] })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!.click()
    await flush()
    expect(api.setPackEnabled).toHaveBeenCalledExactlyOnceWith({ pack_id: 'imp.test', enabled: false })
    expect(confirm).not.toHaveBeenCalled()
    // Another window can install and enable a new body before the old disable
    // response arrives. A failed follow-up refresh must not hide its active state.
    api.packs.mockResolvedValue({ ok: true, packs: [pack(true, 'imp.test', (sameBody ? 'a' : 'b').repeat(64))] })
    container.querySelector<HTMLButtonElement>('.page-heading button')!.click()
    await flush()
    const manage = [...container.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === enUS.knowledge.managePack)!
    manage.click()
    await flush()
    api.packs.mockRejectedValue(new KnowledgeApiError('knowledge_error'))
    finish({ ok: true, pack_id: 'imp.test', enabled: true, pack_enabled: false, policy_revision: { epoch: 'fixture-process', counter: 1 } })
    await flush()
    expect(container.querySelector<HTMLInputElement>('[data-testid="pack-enabled-imp.test"] input')!.getAttribute('aria-checked')).toBe(String(!sameBody))
    expect(document.querySelector('.pack-summary .el-tag')!.textContent)
      .toBe(sameBody ? enUS.knowledge.entryDisabled : enUS.knowledge.entryEnabled)
  })

  it.each([
    { requested: true, counter: 0, epoch: 'fixture-process' },
    { requested: false, counter: 0, epoch: 'fixture-process' },
    { requested: true, counter: 1, epoch: 'fixture-process' },
    { requested: false, counter: 1, epoch: 'fixture-process' },
    { requested: true, counter: 2, epoch: 'fixture-process' },
    { requested: false, counter: 2, epoch: 'fixture-process' },
    { requested: true, counter: 0, epoch: 'restarted-process' },
    { requested: false, counter: 0, epoch: 'restarted-process' },
  ])('keeps the newer observed policy for the same body (%s)', async ({ requested, counter, epoch }) => {
    let finish!: (value: object) => void
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    api.setPackEnabled.mockReturnValue(new Promise(resolve => { finish = resolve }))
    api.packs.mockResolvedValue({ ok: true, packs: [pack(!requested)] })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!.click()
    await flush()
    // The same body can acquire a newer policy while the save response waits.
    // Timestamps intentionally do not distinguish any of these snapshots.
    api.packs.mockResolvedValue({ ok: true, packs: [{ ...pack(!requested), policy_revision: { epoch, counter } }] })
    container.querySelector<HTMLButtonElement>('.page-heading button')!.click()
    await flush()
    const manage = [...container.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === enUS.knowledge.managePack)!
    manage.click()
    await flush()
    api.packs.mockRejectedValue(new KnowledgeApiError('knowledge_error'))
    finish({ ok: true, pack_id: 'imp.test', pack_enabled: requested, policy_revision: { epoch: 'fixture-process', counter: 1 } })
    await flush()
    const expected = epoch === 'fixture-process' && counter <= 1 ? requested : !requested
    expect(container.querySelector<HTMLInputElement>('[data-testid="pack-enabled-imp.test"] input')!.getAttribute('aria-checked')).toBe(String(expected))
    expect(document.querySelector('.pack-summary .el-tag')!.textContent)
      .toBe(expected ? enUS.knowledge.entryEnabled : enUS.knowledge.entryDisabled)
  })

  it('reloads without enabling when a pack has no content identity', async () => {
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    api.packs.mockResolvedValue({ ok: true, packs: [{ ...pack(), pack_sha256: undefined }] })
    api.setPackEnabled.mockResolvedValue({ ok: true, pack_enabled: true })
    const container = await mount()
    await openTab('packs')
    const loads = api.packs.mock.calls.length
    container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!.click()
    await flush()
    expect(confirm).not.toHaveBeenCalled()
    expect(api.setPackEnabled).not.toHaveBeenCalled()
    expect(api.packs.mock.calls.length).toBeGreaterThan(loads)
    expect(document.body.textContent).toContain(enUS.knowledge.reasons.pack_content_changed)
    expect(container.querySelector<HTMLInputElement>('[data-testid="pack-enabled-imp.test"] input')!.getAttribute('aria-checked')).toBe('false')
  })

  it('does not enable a pack after the disclosure confirmation is cancelled', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel')
    api.packs.mockResolvedValue({ ok: true, packs: [pack()] })
    const container = await mount()
    await openTab('packs')
    container.querySelector<HTMLElement>('[data-testid="pack-enabled-imp.test"]')!.click()
    await flush()
    expect(api.setPackEnabled).not.toHaveBeenCalled()
    expect(container.querySelector<HTMLInputElement>('[data-testid="pack-enabled-imp.test"] input')!.getAttribute('aria-checked')).toBe('false')
  })
})

describe('KnowledgeManager job polling', () => {
  it.each(['failed', 'cancelled'] as const)('uses export-specific notification for a %s export', async state => {
    vi.useFakeTimers()
    api.packJobs.mockResolvedValueOnce({ ok: true, jobs: [{ ...job('building'), kind: 'pack_export' }] })
      .mockResolvedValue({ ok: true, jobs: [{ ...job(state), kind: 'pack_export', reason: 'knowledge_error' }] })
    await mount()
    await vi.advanceTimersByTimeAsync(2_100)
    await flush()
    expect(document.body.textContent).toContain(state === 'failed' ? 'Export of fixture-pack failed:' : 'Export of fixture-pack was cancelled')
    expect(document.body.textContent).not.toContain(state === 'failed' ? 'failed to import' : 'Import of fixture-pack was cancelled')
  })
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

  it.each([
    ['isComposing', { key: 'Process', keyCode: 229, isComposing: true }],
    ['keyCode 229 alone', { key: 'Enter', keyCode: 229 }],
  ])('does not search the catalog at the Enter that picks a word of an input method (%s)', async (_name, picking) => {
    const container = await mount()
    await openTab('catalog')
    const search = container.querySelector<HTMLInputElement>('.toolbar .el-input input')
    expect(search).toBeTruthy()
    search!.value = '被炉'
    search!.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    const before = api.entries.mock.calls.length
    // Only the way down says a word is being picked; let go, it is an Enter like any other.
    search!.dispatchEvent(new KeyboardEvent('keydown', { ...picking, bubbles: true }))
    search!.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', keyCode: 13, bubbles: true }))
    await flush()
    expect(api.entries.mock.calls.length).toBe(before)
    // Enter once more, with no word left to pick: now it searches.
    search!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }))
    search!.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', keyCode: 13, bubbles: true }))
    await flush()
    expect(api.entries.mock.calls.length).toBe(before + 1)
    expect(api.entries.mock.calls.at(-1)?.[0]).toMatchObject({ query: '被炉' })
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

  it('reloads the open catalog when a source change is already active on return', async () => {
    api.packs.mockResolvedValue({ ok: true, packs: [{ ...pack(), sources_managed: true }] })
    let finishRemoval!: (value: object) => void
    api.removeSource.mockReturnValue(new Promise(resolve => { finishRemoval = resolve }))
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    await mount()
    await openTab('packs')
    const manage = [...document.querySelectorAll<HTMLButtonElement>('button')].find(row => row.textContent?.trim() === enUS.knowledge.managePack)
    expect(manage).toBeTruthy()
    manage!.click()
    await flush()
    const remove = [...document.querySelectorAll<HTMLButtonElement>('.el-drawer button')]
      .find(row => row.textContent?.trim() === enUS.knowledge.remove)
    expect(remove).toBeTruthy()
    remove!.click()
    await flush()
    expect(api.removeSource).toHaveBeenCalledTimes(1)
    // The user moves to the catalog while the removal is still in flight and
    // reads the rows from before it.
    await openTab('catalog')
    const before = api.entries.mock.calls.length
    finishRemoval({ ok: true, pack_id: 'imp.test', job_id: 'source-removed', state: 'active', entries_total: 0, chunks_total: 0 })
    await flush()
    expect(api.entries.mock.calls.length).toBeGreaterThan(before)
  })
})
