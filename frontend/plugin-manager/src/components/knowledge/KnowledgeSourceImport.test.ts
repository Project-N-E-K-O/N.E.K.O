// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import enUS from '@/i18n/locales/en-US'
import type { KnowledgePack } from '@/api/knowledge'

const api = vi.hoisted(() => ({ previewSource: vi.fn(), commitSource: vi.fn() }))
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
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals() })

describe('source import privacy and identity', () => {
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
    expect(api.commitSource).toHaveBeenCalledWith({ preview_id: 'preview-1', sha256: 'digest', enabled: false })
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
    expect(api.commitSource).toHaveBeenCalledWith({ preview_id: 'preview-1', sha256: 'digest', enabled: true })
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
