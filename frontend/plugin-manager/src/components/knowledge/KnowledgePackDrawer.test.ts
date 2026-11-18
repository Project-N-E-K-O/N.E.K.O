// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import enUS from '@/i18n/locales/en-US'
import { KnowledgeApiError, type KnowledgePack, type KnowledgePackJob } from '@/api/knowledge'

const api = vi.hoisted(() => ({ sources: vi.fn(), sourceVersions: vi.fn(), restoreSource: vi.fn(), publish: vi.fn() }))
vi.mock('@/api/knowledge', async (original) => ({ ...(await original<typeof import('@/api/knowledge')>()), knowledgeApi: api }))
import KnowledgePackDrawer from './KnowledgePackDrawer.vue'

const NativeMutationObserver = globalThis.MutationObserver
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
  api.publish.mockResolvedValue({ ok: true, share: { id: 10 } })
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals() })

describe('pack documents, versions and publication', () => {
  it.each(['builtin', 'market', 'imported'] as const)('opens %s pack exports without requesting absent source provenance', async origin => {
    await mount([], { origin, sources_managed: false })
    expect(api.sources).not.toHaveBeenCalled()
    expect(buttons('Export text JSON (v1)')).toHaveLength(1)
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
    expect(buttons('Export text JSON (v1)')).toHaveLength(0)
    expect(buttons('Replace')).toHaveLength(0)
    expect(buttons('Version history')).toHaveLength(1)
    buttons('Version history')[0]!.click()
    await flush()
    expect(api.sourceVersions).toHaveBeenCalledWith({ pack_id: 'imp.test', doc_id: 'doc-1' })
  })

  it('requires public-scope confirmation and keeps the same request on retry', async () => {
    api.publish.mockRejectedValueOnce(new KnowledgeApiError('market_publication_failed')).mockResolvedValue({ ok: true, share: { id: 10 } })
    await mount([{ job_id: 'export-1', pack_id: 'imp.test', kind: 'pack_export', state: 'active', result: {
      artifact_id: 'a'.repeat(64), sha256: 'a'.repeat(64), bytes: 1024, filename: 'notes.neko-knowledge.zip', format_version: 2, pack_id: 'imp.test', version: '1',
    } } as KnowledgePackJob])
    buttons('Publish to market')[0]!.click()
    await flush()
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
