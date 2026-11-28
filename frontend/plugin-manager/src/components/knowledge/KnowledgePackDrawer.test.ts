// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import enUS from '@/i18n/locales/en-US'
import { KnowledgeApiError, type KnowledgePack, type KnowledgePackJob } from '@/api/knowledge'

const api = vi.hoisted(() => ({ sources: vi.fn(), sourceVersions: vi.fn(), restoreSource: vi.fn(), removeSource: vi.fn(), exportPack: vi.fn(), deleteExport: vi.fn(), artifact: vi.fn(), publish: vi.fn() }))
vi.mock('@/api/knowledge', async (original) => ({ ...(await original<typeof import('@/api/knowledge')>()), knowledgeApi: api }))
import KnowledgePackDrawer from './KnowledgePackDrawer.vue'

const NativeMutationObserver = globalThis.MutationObserver
// Element Plus returns the action string at runtime but declares an input-data intersection.
const confirmAction = 'confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>
let cleanup: (() => void) | undefined
async function flush() { for (let index = 0; index < 10; index++) { await Promise.resolve(); await nextTick() } }
async function flushDialogTransition() {
  // Vue applies v-show after its two animation frames; microtasks alone can
  // leave a closed dialog visibly present and hide a late-close regression.
  await flush()
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  await flush()
}
function buttons(label: string) { return [...document.querySelectorAll<HTMLButtonElement>('button')].filter(row => row.textContent?.trim() === label) }

async function mount(jobs: KnowledgePackJob[] = [], extra: Partial<KnowledgePack> = {}) {
  const root = document.createElement('div')
  document.body.append(root)
  const pack = { pack_id: 'imp.test', source: { name: 'Notes', homepage: '', license: '' }, enabled: false,
    origin: 'imported', sources_managed: true, chunks_ready: 2, chunks_total: 2, export_ready: true, vector_state: 'complete', ...extra } as KnowledgePack
  const state = reactive({ modelValue: true, pack: pack as KnowledgePack | null, jobs, onUpdated: vi.fn() })
  const app = createApp({ render: () => h(KnowledgePackDrawer, state) })
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': enUS } }))
  app.mount(root)
  cleanup = () => { app.unmount(); root.remove() }
  await flush()
  return state
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  api.sources.mockResolvedValue({ ok: true, sources: [{ doc_id: 'doc-1', name: 'Notes.txt', chars: 30, current_version: 2, versions_count: 2 }] })
  api.sourceVersions.mockResolvedValue({ ok: true, versions: [
    { version: 1, name: 'Notes.txt', chars: 20, sha256: 'old', created_at: '', current: false },
    { version: 2, name: 'Notes.txt', chars: 30, sha256: 'new', created_at: '', current: true },
  ] })
  api.restoreSource.mockResolvedValue({ ok: true, state: 'queued' })
  api.removeSource.mockResolvedValue({ ok: true })
  api.exportPack.mockResolvedValue({ ok: true, state: 'queued' })
  api.deleteExport.mockResolvedValue({ ok: true, artifact_retained: false })
  api.publish.mockResolvedValue({ ok: true, share: { id: 10 } })
})
afterEach(async () => {
  // ElMessage toasts outlive the app and auto-close after 3s; left alone, that
  // leave transition can fire after happy-dom is torn down and surface as an
  // unhandled `requestAnimationFrame is not defined` that fails the run.
  ElMessage.closeAll()
  await flushDialogTransition()
  cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals(); vi.restoreAllMocks()
})

describe('pack documents, versions and publication', () => {
  it.each([null, { pack_id: 'imp.other', source: { name: 'Other' }, sources_managed: true } as KnowledgePack])('does not delete a document after the confirmation target changes (%s)', async nextPack => {
    let confirm!: (value: Awaited<ReturnType<typeof ElMessageBox.confirm>>) => void
    vi.spyOn(ElMessageBox, 'confirm').mockReturnValue(new Promise(resolve => { confirm = resolve }))
    const state = await mount()
    buttons('Remove')[0]!.click()
    await flush()
    state.pack = nextPack
    await flush()
    confirm(confirmAction)
    await flush()
    expect(api.removeSource).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain(enUS.knowledge.reasons.not_found)
  })

  it('keeps a frozen publication request when switching between artifacts', async () => {
    api.publish.mockRejectedValue(new KnowledgeApiError('knowledge_timeout'))
    await mount(['a', 'b'].map(id => ({ job_id: `export-${id}`, pack_id: 'imp.test', kind: 'pack_export', state: 'active',
      result: { artifact_id: id.repeat(64), format_version: 2, name: `Artifact ${id}` } } as KnowledgePackJob)))
    buttons('Publish to market')[0]!.click()
    await flush()
    const title = document.querySelector<HTMLInputElement>('.el-dialog input')!
    title.value = 'Frozen A'
    title.dispatchEvent(new Event('input', { bubbles: true }))
    const description = document.querySelector<HTMLTextAreaElement>('.el-dialog textarea')!
    description.value = 'Original description'
    description.dispatchEvent(new Event('input', { bubbles: true }))
    document.querySelector<HTMLElement>('[data-testid="knowledge-publish-confirm"]')!.click()
    await flush()
    buttons('Publish to market').at(-1)!.click()
    await flush()
    const original = api.publish.mock.calls[0]![0]
    buttons('Cancel').at(-1)!.click()
    await flush()
    buttons('Publish to market')[1]!.click()
    await flush()
    buttons('Cancel').at(-1)!.click()
    await flush()
    buttons('Publish to market')[0]!.click()
    await flush()
    expect(document.querySelector<HTMLInputElement>('.el-dialog input')!.value).toBe('Frozen A')
    expect(document.querySelector<HTMLTextAreaElement>('.el-dialog textarea')!.value).toBe('Original description')
    expect(document.querySelector<HTMLInputElement>('.el-dialog input')!.disabled).toBe(true)
    expect(buttons('Publish to market').at(-1)!.disabled).toBe(true)
    document.querySelector<HTMLElement>('[data-testid="knowledge-publish-confirm"]')!.click()
    await flush()
    buttons('Publish to market').at(-1)!.click()
    await flush()
    expect(api.publish.mock.calls[1]![0]).toEqual(original)
  })

  it('keeps the Unicode boundary of a default publication title', async () => {
    await mount([{ job_id: 'emoji', pack_id: 'imp.test', kind: 'pack_export', state: 'active',
      result: { artifact_id: 'a'.repeat(64), format_version: 2, name: 'a'.repeat(119) + '😺suffix' } } as KnowledgePackJob])
    buttons('Publish to market')[0]!.click()
    await flush()
    const title = document.querySelector<HTMLInputElement>('.el-dialog input')!.value
    expect(title).toBe('a'.repeat(119) + '😺')
    expect(() => encodeURIComponent(title)).not.toThrow()
  })
  it.each(['builtin', 'market', 'imported'] as const)('opens %s pack exports without requesting absent source provenance', async origin => {
    await mount([], { origin, sources_managed: false })
    expect(api.sources).not.toHaveBeenCalled()
    expect(buttons('Export data package')).toHaveLength(1)
    expect(document.querySelectorAll('.export-actions button')).toHaveLength(1)
    expect(buttons('Add document')).toHaveLength(0)
  })

  it('restores a historical document through the same pack without deleting versions', async () => {
    const response = { ok: true, pack_id: 'imp.test', job_id: 'restored-job', state: 'queued' }
    api.restoreSource.mockResolvedValue(response)
    const state = await mount()
    expect(api.sources).toHaveBeenCalledWith('imp.test')
    buttons('Version history')[0]!.click()
    await flush()
    expect(api.sourceVersions).toHaveBeenCalledWith({ pack_id: 'imp.test', doc_id: 'doc-1' })
    const restore = buttons('Restore')
    expect(restore).toHaveLength(2)
    expect(restore[1]!.disabled).toBe(true)
    expect(document.querySelector('[data-testid="source-restore-privacy-confirm"]')).toBeNull()
    restore[0]!.click()
    await flush()
    expect(api.restoreSource).toHaveBeenCalledWith({ pack_id: 'imp.test', doc_id: 'doc-1', version: 1, enabled_confirmed: false })
    expect(state.onUpdated).toHaveBeenCalledExactlyOnceWith(response)
    await flushDialogTransition()
    expect(document.querySelector<HTMLElement>('.el-dialog')!.closest<HTMLElement>('.el-overlay')!.style.display).toBe('none')
  })

  it.each(['success', 'failure'])('keeps another document history open after a pending restore finishes with %s', async outcome => {
    let finish!: (result: object) => void
    let fail!: (error: unknown) => void
    api.restoreSource.mockReturnValueOnce(new Promise((resolve, reject) => { finish = resolve; fail = reject }))
    api.sources.mockResolvedValue({ ok: true, sources: [
      { doc_id: 'doc-1', name: 'Notes.txt', chars: 30, current_version: 2, versions_count: 2 },
      { doc_id: 'doc-2', name: 'Other.txt', chars: 40, current_version: 4, versions_count: 2 },
    ] })
    api.sourceVersions.mockImplementation(async ({ doc_id }) => ({ ok: true, versions: [
      { version: doc_id === 'doc-1' ? 1 : 3, name: doc_id === 'doc-1' ? 'Notes.txt' : 'Other.txt', chars: 20, sha256: 'old', created_at: '', current: false },
    ] }))
    const state = await mount()
    buttons('Version history')[0]!.click()
    await flush()
    buttons('Restore')[0]!.click()
    await flush()
    expect(api.restoreSource).toHaveBeenCalledExactlyOnceWith({ pack_id: 'imp.test', doc_id: 'doc-1', version: 1, enabled_confirmed: false })
    // Navigate through the real dialog while the earlier API request is pending.
    document.querySelector<HTMLElement>('.el-dialog__headerbtn')!.click()
    await flushDialogTransition()
    buttons('Version history')[1]!.click()
    await flushDialogTransition()
    const dialog = document.querySelector<HTMLElement>('.el-dialog')!
    const overlay = dialog.closest<HTMLElement>('.el-overlay')!
    expect(overlay.style.display).not.toBe('none')
    expect(dialog.textContent).toContain('Other.txt')
    expect(api.sourceVersions).toHaveBeenLastCalledWith({ pack_id: 'imp.test', doc_id: 'doc-2' })
    expect(buttons('Restore')[0]!.disabled).toBe(true)
    if (outcome === 'success') finish({ ok: true, state: 'queued' })
    else fail(new KnowledgeApiError('operation_failed'))
    await flush()
    await flushDialogTransition()
    expect(overlay.style.display).not.toBe('none')
    expect(dialog.textContent).toContain('Other.txt')
    expect(dialog.textContent).not.toContain('Notes.txt')
    expect(buttons('Restore')[0]!.disabled).toBe(false)
    expect(api.sourceVersions).toHaveBeenCalledTimes(2)
    if (outcome === 'success') {
      expect(state.onUpdated).toHaveBeenCalledTimes(1)
      expect(api.sources).toHaveBeenCalledTimes(2)
      expect(document.body.textContent).toContain(enUS.knowledge.sourceQueued)
    } else {
      expect(state.onUpdated).not.toHaveBeenCalled()
      expect(api.sources).toHaveBeenCalledTimes(1)
      expect(document.body.textContent).toContain(enUS.knowledge.reasons.operation_failed)
    }
  })

  it('requires explicit acknowledgement before restoring history into an enabled pack', async () => {
    const state = await mount([], { enabled: true })
    buttons('Version history')[0]!.click()
    await flush()
    const restore = buttons('Restore')[0]!
    expect(restore.disabled).toBe(true)
    restore.click()
    await flush()
    expect(api.restoreSource).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain(enUS.knowledge.enabledPackDisclosure)
    const acknowledgement = document.querySelector<HTMLElement>('[data-testid="source-restore-privacy-confirm"]')!
    expect(acknowledgement.textContent).toContain(enUS.knowledge.acknowledgeEnabledPack)
    acknowledgement.click()
    await flush()
    expect(restore.disabled).toBe(false)
    expect(buttons('Restore')[1]!.disabled).toBe(true)
    acknowledgement.click()
    await flush()
    expect(restore.disabled).toBe(true)
    restore.click()
    expect(api.restoreSource).not.toHaveBeenCalled()
    acknowledgement.click()
    await flush()
    restore.click()
    await flush()
    expect(api.restoreSource).toHaveBeenCalledExactlyOnceWith({ pack_id: 'imp.test', doc_id: 'doc-1', version: 1, enabled_confirmed: true })
    expect(state.pack?.enabled).toBe(true)
  })

  it('clears history acknowledgement after cancelling and reopening', async () => {
    await mount([], { enabled: true })
    buttons('Version history')[0]!.click()
    await flush()
    document.querySelector<HTMLElement>('[data-testid="source-restore-privacy-confirm"]')!.click()
    await flush()
    document.querySelector<HTMLElement>('.el-dialog__headerbtn')!.click()
    await flush()
    expect(api.restoreSource).not.toHaveBeenCalled()
    buttons('Version history')[0]!.click()
    await flush()
    expect(document.querySelector<HTMLInputElement>('[data-testid="source-restore-privacy-confirm"] input')!.checked).toBe(false)
    expect(buttons('Restore')[0]!.disabled).toBe(true)
  })

  it('requires fresh history acknowledgement when polling changes the pack switch', async () => {
    const state = await mount([], { enabled: true })
    buttons('Version history')[0]!.click()
    await flush()
    document.querySelector<HTMLElement>('[data-testid="source-restore-privacy-confirm"]')!.click()
    await flush()
    state.pack!.enabled = false
    await flush()
    expect(document.querySelector('[data-testid="source-restore-privacy-confirm"]')).toBeNull()
    state.pack!.enabled = true
    await flush()
    expect(document.querySelector<HTMLInputElement>('[data-testid="source-restore-privacy-confirm"] input')!.checked).toBe(false)
    expect(buttons('Restore')[0]!.disabled).toBe(true)
    expect(api.restoreSource).not.toHaveBeenCalled()
  })

  it('keeps removed documents reachable for history recovery without active pack operations', async () => {
    api.sources.mockResolvedValue({ ok: true, sources: [{ doc_id: 'doc-1', name: 'Notes.txt', chars: 30, current_version: null, removed: true, versions_count: 2 }] })
    await mount([], { retained: true, vector_state: 'none' })
    expect(buttons('Add document')).toHaveLength(0)
    expect(buttons('Export data package')).toHaveLength(0)
    expect(buttons('Replace')).toHaveLength(0)
    expect(buttons('Version history')).toHaveLength(1)
    buttons('Version history')[0]!.click()
    await flush()
    expect(api.sourceVersions).toHaveBeenCalledWith({ pack_id: 'imp.test', doc_id: 'doc-1' })
  })

  it('exports a finished package once while the request is pending', async () => {
    let finish!: (result: object) => void
    api.exportPack.mockReturnValue(new Promise(resolve => { finish = resolve }))
    await mount()
    expect(document.querySelectorAll('.export-panel input')).toHaveLength(0)
    const button = buttons('Export data package')[0]!
    button.click()
    await flush()
    expect(button.disabled).toBe(true)
    button.click()
    expect(api.exportPack).toHaveBeenCalledTimes(1)
    expect(api.exportPack).toHaveBeenCalledWith({ pack_id: 'imp.test' })
    finish({ ok: true, state: 'queued' })
    await flush()
    expect(button.disabled).toBe(false)
  })

  it.each([
    { local_embedding: false, vector_state: 'off', chunks_ready: 2 },
    { local_embedding: false, vector_state: 'off', chunks_ready: 0 },
    { local_embedding: true, vector_state: 'waiting', chunks_ready: 0 },
  ] as const)('exports verified stored vectors independently of the current inference state (%s)', async state => {
    await mount([], { ...state, export_ready: true })
    const button = buttons('Export data package')[0]!
    expect(button.disabled).toBe(false)
    expect(document.body.textContent).toContain(enUS.knowledge.exportReadyHint)
    button.click()
    await flush()
    expect(api.exportPack).toHaveBeenCalledWith({ pack_id: 'imp.test' })
  })

  it.each([[1, 2, true], [0, 2, true], [0, 0, false], [2, 2, false]])('keeps unverified exports unavailable independently of current-model counts (%s/%s)', async (ready, total, missing) => {
    // Current-model counts alone cannot certify one complete stored provenance specification.
    await mount([], { local_embedding: false, vector_state: 'off', export_ready: false, chunks_ready: ready, chunks_total: total })
    const button = buttons('Export data package')[0]!
    expect(button.disabled).toBe(true)
    // Turning local vectors on only helps when vectors are actually missing.
    const hint = document.querySelector('.export-readiness')?.textContent
    expect(hint).toContain(missing ? enUS.knowledge.exportLocalVectorsOff : enUS.knowledge.exportVectorWaiting)
    button.click()
    expect(api.exportPack).not.toHaveBeenCalled()
  })

  it.each(['paused', 'waiting', 'building'] as const)('keeps the existing readiness hint when local vectors are on and the state is %s', async vector_state => {
    await mount([], { local_embedding: true, vector_state, export_ready: false, chunks_ready: 1 })
    expect(document.querySelector('.export-readiness')?.textContent).toContain(enUS.knowledge.exportVectorWaiting)
    expect(buttons('Export data package')[0]!.disabled).toBe(true)
  })

  it('explains an unavailable pack before suggesting that local vectors be enabled', async () => {
    await mount([], { broken: true, local_embedding: false, vector_state: 'off', export_ready: false })
    expect(document.querySelector('.export-readiness')?.textContent).toContain(enUS.knowledge.brokenHint)
    expect(buttons('Export data package')[0]!.disabled).toBe(true)
    expect(api.exportPack).not.toHaveBeenCalled()
  })

  it('waits for vectors without offering a text-only export', async () => {
    await mount([], { vector_state: 'building', export_ready: false, chunks_ready: 1 })
    expect(document.querySelectorAll('.export-actions button')).toHaveLength(1)
    const button = buttons('Export data package')[0]!
    expect(button.disabled).toBe(true)
    button.click()
    expect(api.exportPack).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('The data package can be exported once all its vectors are ready.')
  })

  it('shows frozen pack names, time and size without opaque task or release identities', async () => {
    const job = { job_id: 'private-task-identity', pack_id: 'imp.test', kind: 'pack_export', state: 'active',
      created_at: '2026-11-19T07:00:00Z', result: {
        artifact_id: 'a'.repeat(64), name: 'Published notes', filename: 'imp.test.auto-private-release.neko-knowledge.zip',
        format_version: 2, bytes: 1536, version: 'auto-private-release',
      } } as KnowledgePackJob
    await mount([job], { source: { name: 'Renamed notes', homepage: '', license: '' } })
    const row = document.querySelector('.artifact-row')!
    expect(row.querySelector('.artifact-name')!.textContent).toBe('Published notes')
    expect(row.textContent).toContain('2026-11-19')
    expect(row.textContent).toContain('1.5 KiB')
    expect(row.textContent).toContain('Exported')
    expect(row.textContent).not.toContain('private-task-identity')
    expect(row.textContent).not.toContain('auto-private-release')
    buttons('Publish to market')[0]!.click()
    await flush()
    expect(document.querySelector<HTMLInputElement>('.el-dialog input')!.value).toBe('Published notes')
  })

  it('labels pending exports with the pack name rather than a task identifier', async () => {
    await mount([{ job_id: 'pending-private-id', pack_id: 'imp.test', kind: 'pack_export', state: 'queued',
      created_at: '2026-11-19T07:00:00Z' } as KnowledgePackJob])
    const row = document.querySelector('.artifact-row')!
    expect(row.querySelector('.artifact-name')!.textContent).toBe('Notes')
    expect(row.textContent).not.toContain('pending-private-id')
    expect(row.querySelectorAll('button')).toHaveLength(0)
  })

  it.each([false, true])('confirms deletion and hides the row only after the server succeeds (retained=%s)', async retained => {
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue(confirmAction)
    let finish!: (result: object) => void
    api.deleteExport.mockReturnValue(new Promise(resolve => { finish = resolve }))
    await mount([{ job_id: 'export-delete', pack_id: 'imp.test', kind: 'pack_export', state: 'active',
      created_at: '2026-11-19T07:00:00Z', result: {
        artifact_id: 'd'.repeat(64), name: 'Published notes', format_version: 2, bytes: 1024,
      } } as KnowledgePackJob])
    buttons('Delete')[0]!.click()
    await flush()
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Published notes'), 'Delete this export', expect.any(Object))
    expect(confirm.mock.calls[0]![0]).toContain('Original documents, version history and knowledge-base content stay intact.')
    expect(api.deleteExport).toHaveBeenCalledWith({ job_id: 'export-delete' })
    expect(document.querySelectorAll('.artifact-row')).toHaveLength(1)
    expect(buttons('Download')[0]!.disabled).toBe(true)
    expect(buttons('Publish to market')[0]!.disabled).toBe(true)
    buttons('Delete')[0]!.click()
    expect(api.deleteExport).toHaveBeenCalledTimes(1)
    finish({ ok: true, job_id: 'export-delete', artifact_retained: retained })
    await flush()
    // Parent props may still hold the old polling snapshot; do not resurrect it.
    expect(document.querySelectorAll('.artifact-row')).toHaveLength(0)
    expect(document.querySelector('.export-count')!.textContent).toBe('0')
    if (retained) expect(document.body.textContent).toContain('The file was retained because the knowledge base still uses it.')
  })

  it('keeps the export and makes no delete request when confirmation is cancelled', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel')
    await mount([{ job_id: 'export-cancel', pack_id: 'imp.test', kind: 'pack_export', state: 'active', result: {
      artifact_id: 'e'.repeat(64), format_version: 2,
    } } as KnowledgePackJob])
    buttons('Delete')[0]!.click()
    await flush()
    expect(api.deleteExport).not.toHaveBeenCalled()
    expect(document.querySelectorAll('.artifact-row')).toHaveLength(1)
    expect(buttons('Delete')[0]!.disabled).toBe(false)
  })

  it('keeps the export available for retry when deletion fails', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue(confirmAction)
    api.deleteExport.mockRejectedValue(new KnowledgeApiError('operation_failed'))
    await mount([{ job_id: 'export-failed-delete', pack_id: 'imp.test', kind: 'pack_export', state: 'active', result: {
      artifact_id: 'f'.repeat(64), format_version: 2,
    } } as KnowledgePackJob])
    buttons('Delete')[0]!.click()
    await flush()
    expect(document.querySelectorAll('.artifact-row')).toHaveLength(1)
    expect(buttons('Delete')[0]!.disabled).toBe(false)
    expect(buttons('Download')[0]!.disabled).toBe(false)
  })

  it('downloads older artifacts using the server response name', async () => {
    const filename = 'Published notes_2026-11-19_15-00-00.neko-knowledge.zip'
    api.artifact.mockResolvedValue({ blob: new Blob(['archive']), filename })
    const objectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:export-fixture')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function(this: HTMLAnchorElement) {
      expect(this.download).toBe(filename)
    })
    try {
      await mount([{ job_id: 'old', pack_id: 'imp.test', kind: 'pack_export', state: 'active', result: {
        artifact_id: 'a'.repeat(64), filename: 'imp.test.auto-old.neko-knowledge.zip', format_version: 2, bytes: 1024,
      } } as KnowledgePackJob])
      buttons('Download')[0]!.click()
      await flush()
      expect(api.artifact).toHaveBeenCalledWith('a'.repeat(64))
      expect(click).toHaveBeenCalledTimes(1)
    } finally { click.mockRestore(); objectUrl.mockRestore() }
  })

  it('does not expose downloads for a retained text-only export', async () => {
    // A durable task from before the format removal can still arrive over JSON.
    await mount([{ job_id: 'retired-export', pack_id: 'imp.test', kind: 'pack_export', state: 'active', result: {
      artifact_id: 'c'.repeat(64), filename: 'notes.neko-knowledge.json', format_version: 1,
    } } as unknown as KnowledgePackJob])
    expect(document.querySelectorAll('.artifact-row')).toHaveLength(0)
    expect(buttons('Download')).toHaveLength(0)
    expect(buttons('Publish to market')).toHaveLength(0)
    expect(buttons('Export data package')).toHaveLength(1)
  })

  it('requires public-scope confirmation and keeps the same request on retry', async () => {
    api.publish.mockRejectedValueOnce(new KnowledgeApiError('market_publication_failed')).mockResolvedValue({ ok: true, share: { id: 10 } })
    await mount([{ job_id: 'export-1', pack_id: 'imp.test', kind: 'pack_export', state: 'active', result: {
      artifact_id: 'a'.repeat(64), sha256: 'a'.repeat(64), bytes: 1024, filename: 'notes.neko-knowledge.zip', format_version: 2, pack_id: 'imp.test', version: '1',
    } } as KnowledgePackJob])
    buttons('Publish to market')[0]!.click()
    await flush()
    expect(buttons('Delete')[0]!.disabled).toBe(true)
    expect(buttons('Publish to market').at(-1)!.disabled).toBe(true)
    expect(api.publish).not.toHaveBeenCalled()
    document.querySelector<HTMLElement>('[data-testid="knowledge-publish-confirm"]')!.click()
    await flush()
    buttons('Publish to market').at(-1)!.click()
    await flush()
    const first = api.publish.mock.calls[0]![0]
    expect(first).toMatchObject({ pack_id: 'imp.test', artifact_id: 'a'.repeat(64), publish_confirmed: true, title: 'Notes', description: '' })
    expect(first.client_request_id).toMatch(/^[0-9a-f-]{36}$/)
    buttons('Publish to market').at(-1)!.click()
    await flush()
    expect(api.publish.mock.calls[1]![0]).toEqual(first)
  })

  it('bounds the default publication title to the market contract', async () => {
    await mount([{ job_id: 'export-long', pack_id: 'imp.test', kind: 'pack_export', state: 'active', result: {
      artifact_id: 'b'.repeat(64), sha256: 'b'.repeat(64), bytes: 1024, filename: 'notes.neko-knowledge.zip', format_version: 2, pack_id: 'imp.test', version: '1',
    } } as KnowledgePackJob], { source: { name: 'x'.repeat(200), homepage: '', license: '' } })
    buttons('Publish to market')[0]!.click()
    await flush()
    const title = document.querySelector<HTMLInputElement>('.el-dialog input')!
    expect(title.maxLength).toBe(120)
    expect(title.value).toHaveLength(120)
    document.querySelector<HTMLElement>('[data-testid="knowledge-publish-confirm"]')!.click()
    await flush()
    buttons('Publish to market').at(-1)!.click()
    await flush()
    expect(api.publish.mock.calls[0]![0].title).toHaveLength(120)
  })
})
