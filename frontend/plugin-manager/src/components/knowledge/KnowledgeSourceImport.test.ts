// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import enUS from '@/i18n/locales/en-US'
import type { KnowledgePack } from '@/api/knowledge'

const api = vi.hoisted(() => ({ previewSource: vi.fn(), commitSource: vi.fn(), discardSourcePreview: vi.fn() }))
vi.mock('@/api/knowledge', async (original) => ({
  ...(await original<typeof import('@/api/knowledge')>()), knowledgeApi: api,
}))
import KnowledgeSourceImport from './KnowledgeSourceImport.vue'

const NativeMutationObserver = globalThis.MutationObserver
let cleanup: (() => void) | undefined
async function flush() { for (let index = 0; index < 10; index++) { await Promise.resolve(); await nextTick() } }

async function mount(pack: KnowledgePack | null = null, packs: KnowledgePack[] = []) {
  const root = document.createElement('div')
  document.body.append(root)
  const state = reactive({ modelValue: true, file: new File(['private text'], 'private.txt'), pack, packs })
  const app = createApp({ render: () => h(KnowledgeSourceImport, { ...state, 'onUpdate:modelValue': value => { state.modelValue = value } }) })
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': enUS } }))
  app.mount(root)
  cleanup = () => { app.unmount(); root.remove() }
  await flush()
  return state
}

function button(text: string) {
  const match = [...document.querySelectorAll<HTMLButtonElement>('button')].find(row => row.textContent?.trim() === text)
  expect(match, text).toBeTruthy()
  return match!
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  api.previewSource.mockResolvedValue({ ok: true, pack_id: 'imp.test', preview: {
    preview_id: 'preview-1', doc_id: 'doc-1', sha256: 'digest', name: 'private.txt',
    chars: 12, content: 'private text', sections: [],
  } })
  api.commitSource.mockResolvedValue({ ok: true, pack_id: 'imp.test', job_id: 'job-1', state: 'queued' })
  api.discardSourcePreview.mockResolvedValue({ ok: true, deleted: true })
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals() })

describe('source import privacy and identity', () => {
  it('deletes the preview files when the preview is cancelled', async () => {
    const state = await mount()
    expect(api.previewSource).not.toHaveBeenCalled()
    button('Cancel').click()
    await flush()
    // Nothing was previewed, so there is nothing to delete.
    expect(api.discardSourcePreview).not.toHaveBeenCalled()
    state.modelValue = true
    await flush()
    button('Preview document').click()
    await flush()
    button('Cancel').click()
    await flush()
    expect(state.modelValue).toBe(false)
    expect(api.commitSource).not.toHaveBeenCalled()
    expect(api.discardSourcePreview).toHaveBeenCalledTimes(1)
    expect(api.discardSourcePreview).toHaveBeenCalledWith({ preview_id: 'preview-1' })
  })

  it('deletes a replaced preview and one that finishes after cancellation', async () => {
    const state = await mount()
    button('Preview document').click()
    await flush()
    // Choosing another file replaces the shown preview.
    state.file = new File(['other text'], 'other.txt')
    await flush()
    expect(api.discardSourcePreview).toHaveBeenCalledWith({ preview_id: 'preview-1' })
    let finish!: (value: unknown) => void
    api.previewSource.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    button('Preview document').click()
    await flush()
    button('Cancel').click()
    finish({ ok: true, pack_id: 'imp.test', preview: { preview_id: 'preview-late', doc_id: 'doc-2', sha256: 'late',
      name: 'other.txt', chars: 10, content: 'other text', sections: [] } })
    await flush()
    expect(api.discardSourcePreview).toHaveBeenCalledWith({ preview_id: 'preview-late' })
  })

  it.each([true, false])('defers discarding a preview invalidated during its commit (commit succeeds: %s)', async succeeds => {
    const state = await mount()
    button('Preview document').click()
    await flush()
    let finish!: (value: unknown) => void
    let fail!: (error: unknown) => void
    api.commitSource.mockReturnValueOnce(new Promise((resolve, reject) => { finish = resolve; fail = reject }))
    button('Confirm import').click()
    await flush()
    state.file = new File(['other text'], 'other.txt')
    await flush()
    expect(api.discardSourcePreview).not.toHaveBeenCalled()
    if (succeeds) finish({ ok: true, pack_id: 'imp.test', job_id: 'job-1', state: 'queued' })
    else fail(new Error('network'))
    await flush()
    // The server keeps a preview the commit already froze, so one late request is safe.
    expect(api.discardSourcePreview).toHaveBeenCalledTimes(1)
    expect(api.discardSourcePreview).toHaveBeenCalledWith({ preview_id: 'preview-1' })
  })

  it('discards only the preview whose own commit settled when commits overlap', async () => {
    const state = await mount()
    button('Preview document').click()
    await flush()
    const settle: Array<(value: unknown) => void> = []
    api.commitSource.mockImplementation(() => new Promise(resolve => { settle.push(resolve) }))
    button('Confirm import').click()
    await flush()
    // A new file invalidates preview-1 while its commit is still in flight.
    state.file = new File(['other text'], 'other.txt')
    await flush()
    api.previewSource.mockResolvedValueOnce({ ok: true, pack_id: 'imp.test', preview: { preview_id: 'preview-2',
      doc_id: 'doc-2', sha256: 'second', name: 'other.txt', chars: 10, content: 'other text', sections: [] } })
    button('Preview document').click()
    await flush()
    button('Confirm import').click()
    await flush()
    expect(api.commitSource).toHaveBeenCalledTimes(2)
    settle[0]!({ ok: true, pack_id: 'imp.test', job_id: 'job-1', state: 'queued' })
    await flush()
    expect(api.discardSourcePreview).toHaveBeenCalledTimes(1)
    expect(api.discardSourcePreview).toHaveBeenCalledWith({ preview_id: 'preview-1' })
    // preview-2 is never discarded while its own commit is still in flight;
    // a request after it settles is safe because the server keeps frozen previews.
    settle[1]!({ ok: true, pack_id: 'imp.test', job_id: 'job-2', state: 'queued' })
    await flush()
    expect(api.discardSourcePreview.mock.calls.slice(1).every(([value]) => value.preview_id === 'preview-2')).toBe(true)
  })

  it('keeps a committed preview when the dialog closes', async () => {
    const state = await mount()
    button('Preview document').click()
    await flush()
    button('Confirm import').click()
    await flush()
    expect(api.commitSource).toHaveBeenCalledTimes(1)
    expect(state.modelValue).toBe(false)
    expect(api.discardSourcePreview).not.toHaveBeenCalled()
  })

  it('offers only packs with local source provenance as append destinations', async () => {
    await mount(null, [
      { pack_id: 'imp.local', origin: 'imported', sources_managed: true, source: { name: 'Local originals' } } as KnowledgePack,
      { pack_id: 'imp.offline', origin: 'imported', sources_managed: false, source: { name: 'Finished offline package' } } as KnowledgePack,
    ])
    document.querySelector<HTMLElement>('.el-select__wrapper')!.click()
    await flush()
    expect(document.body.textContent).toContain('Local originals')
    expect(document.body.textContent).not.toContain('Finished offline package')
  })

  it('defaults new documents to disabled after a local preview', async () => {
    await mount()
    button('Preview document').click()
    await flush()
    expect(api.previewSource).toHaveBeenCalledWith(expect.any(File), { filename: 'private.txt', name: 'private' })
    expect(document.querySelector('[data-testid="source-preview"]')?.textContent).toBe('private text')
    const checkbox = document.querySelector<HTMLInputElement>('[data-testid="source-enable-after-import"] input')!
    expect(checkbox.checked).toBe(false)
    button('Confirm import').click()
    await flush()
    expect(api.commitSource).toHaveBeenCalledWith({ preview_id: 'preview-1', sha256: 'digest', enabled: false, enabled_confirmed: false })
  })

  it('requires explicit acknowledgement before adding data to an enabled pack', async () => {
    await mount({ pack_id: 'imp.existing', enabled: true, origin: 'imported', source: { name: 'Existing' } } as KnowledgePack)
    button('Preview document').click()
    await flush()
    expect(button('Confirm import').disabled).toBe(true)
    expect(api.commitSource).not.toHaveBeenCalled()
    document.querySelector<HTMLElement>('[data-testid="source-privacy-confirm"]')!.click()
    await flush()
    expect(button('Confirm import').disabled).toBe(false)
    button('Confirm import').click()
    await flush()
    expect(api.commitSource).toHaveBeenCalledWith({ preview_id: 'preview-1', sha256: 'digest', enabled: true, enabled_confirmed: true })
  })

  it('invalidates the preview when its selected destination disappears', async () => {
    const pack = { pack_id: 'imp.existing', enabled: true, origin: 'imported', sources_managed: true, source: { name: 'Existing' } } as KnowledgePack
    const state = await mount(null, [pack])
    document.querySelector<HTMLElement>('.el-select__wrapper')!.click()
    await flush()
    const option = [...document.querySelectorAll<HTMLElement>('.el-select-dropdown__item')].find(row => row.textContent?.trim() === 'Existing')!
    option.click()
    await flush()
    button('Preview document').click()
    await flush()
    expect(document.querySelector('[data-testid="source-preview"]')).not.toBeNull()
    state.packs = []
    await flush()
    expect(document.querySelector('[data-testid="source-preview"]')).toBeNull()
    expect(button('Preview document').disabled).toBe(true)
    expect(api.commitSource).not.toHaveBeenCalled()
  })

  it('records the new-pack enable opt-in independently of the requested switch', async () => {
    await mount()
    button('Preview document').click()
    await flush()
    document.querySelector<HTMLElement>('[data-testid="source-enable-after-import"]')!.click()
    await flush()
    button('Confirm import').click()
    await flush()
    expect(api.commitSource).toHaveBeenCalledWith({ preview_id: 'preview-1', sha256: 'digest', enabled: true, enabled_confirmed: true })
  })

  it('drops a stale preview when another file is selected', async () => {
    let finish!: (value: unknown) => void
    api.previewSource.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const state = await mount()
    button('Preview document').click()
    await flush()
    state.file = new File(['replacement'], 'replacement.txt')
    await flush()
    finish({ preview: { content: 'stale private text' } })
    await flush()
    expect(document.querySelector('[data-testid="source-preview"]')).toBeNull()
    expect(api.commitSource).not.toHaveBeenCalled()
  })

  it('keeps the preview when polling refreshes the same destination pack', async () => {
    const pack = { pack_id: 'imp.existing', enabled: false, origin: 'imported', source: { name: 'Existing' } } as KnowledgePack
    const state = await mount(pack)
    button('Preview document').click()
    await flush()
    state.pack = { ...pack, chunks_ready: 1 }
    await flush()
    expect(document.querySelector('[data-testid="source-preview"]')?.textContent).toBe('private text')
    expect(button('Confirm import').disabled).toBe(false)
  })

  it('drops a pending preview when its destination name changes', async () => {
    let finish!: (value: unknown) => void
    api.previewSource.mockReturnValue(new Promise(resolve => { finish = resolve }))
    await mount()
    button('Preview document').click()
    await flush()
    const name = document.querySelector<HTMLInputElement>('.el-input__inner')!
    name.value = 'Different destination'
    name.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    finish({ preview: { content: 'stale destination text' } })
    await flush()
    expect(document.querySelector('[data-testid="source-preview"]')).toBeNull()
    expect(api.commitSource).not.toHaveBeenCalled()
  })
})
