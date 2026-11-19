// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus, { ElMessageBox } from 'element-plus'
import enUS from '@/i18n/locales/en-US'
import { KnowledgeApiError, type KnowledgePack, type KnowledgePackJob } from '@/api/knowledge'

const api = vi.hoisted(() => ({ sources: vi.fn(), sourceVersions: vi.fn(), restoreSource: vi.fn(), exportPack: vi.fn(), deleteExport: vi.fn(), artifact: vi.fn(), publish: vi.fn() }))
vi.mock('@/api/knowledge', async (original) => ({ ...(await original<typeof import('@/api/knowledge')>()), knowledgeApi: api }))
import KnowledgePackDrawer from './KnowledgePackDrawer.vue'

const NativeMutationObserver = globalThis.MutationObserver
// Element Plus returns the action string at runtime but declares an input-data intersection.
const confirmAction = 'confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>
let cleanup: (() => void) | undefined
async function flush() { for (let index = 0; index < 10; index++) { await Promise.resolve(); await nextTick() } }
function buttons(label: string) { return [...document.querySelectorAll<HTMLButtonElement>('button')].filter(row => row.textContent?.trim() === label) }

async function mount(jobs: KnowledgePackJob[] = [], extra: Partial<KnowledgePack> = {}) {
  const root = document.createElement('div')
  document.body.append(root)
  const pack = { pack_id: 'imp.test', source: { name: 'Notes', homepage: '', license: '' }, enabled: false,
    origin: 'imported', sources_managed: true, chunks_ready: 2, chunks_total: 2, vector_state: 'complete', ...extra } as KnowledgePack
  const app = createApp(KnowledgePackDrawer, { modelValue: true, pack, jobs })
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': enUS } }))
  app.mount(root)
  cleanup = () => { app.unmount(); root.remove() }
  await flush()
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
  api.exportPack.mockResolvedValue({ ok: true, state: 'queued' })
  api.deleteExport.mockResolvedValue({ ok: true, artifact_retained: false })
  api.publish.mockResolvedValue({ ok: true, share: { id: 10 } })
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('pack documents, versions and publication', () => {
  it.each(['builtin', 'market', 'imported'] as const)('opens %s pack exports without requesting absent source provenance', async origin => {
    await mount([], { origin, sources_managed: false })
    expect(api.sources).not.toHaveBeenCalled()
    expect(buttons('Export data package')).toHaveLength(1)
    expect(document.querySelectorAll('.export-actions button')).toHaveLength(1)
    expect(buttons('Add document')).toHaveLength(0)
  })

  it('restores a historical document through the same pack without deleting versions', async () => {
    await mount()
    expect(api.sources).toHaveBeenCalledWith('imp.test')
    buttons('Version history')[0]!.click()
    await flush()
    expect(api.sourceVersions).toHaveBeenCalledWith({ pack_id: 'imp.test', doc_id: 'doc-1' })
    const restore = buttons('Restore')
    expect(restore).toHaveLength(2)
    expect(restore[1]!.disabled).toBe(true)
    restore[0]!.click()
    await flush()
    expect(api.restoreSource).toHaveBeenCalledWith({ pack_id: 'imp.test', doc_id: 'doc-1', version: 1 })
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

  it('waits for vectors without offering a text-only export', async () => {
    await mount([], { vector_state: 'building' })
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
