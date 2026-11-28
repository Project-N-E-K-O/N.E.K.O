// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import AuthoringPublishRepository from './AuthoringPublishRepository.vue'
import authoringEnUs from '@/i18n/authoring/en-US'

const api = vi.hoisted(() => ({
  applyProjectWorkflows: vi.fn(),
  previewProjectWorkflows: vi.fn(),
  createPluginProjectRepo: vi.fn(),
}))
vi.mock('@/api/pluginProjects', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/pluginProjects')>()),
  ...api,
}))

let unmount: (() => void) | undefined
afterEach(() => {
  unmount?.()
  unmount = undefined
  vi.clearAllMocks()
})

const settle = async () => {
  for (let i = 0; i < 5; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

function mount(onCreated: () => void) {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp({
    render: () => h(AuthoringPublishRepository, {
      projectId: 'p1', canCreate: false, notes: [], environment: 'source', onCreated,
    }),
  })
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { authoring: authoringEnUs } } }))
  app.mount(container)
  unmount = () => {
    app.unmount()
    container.remove()
  }
  return container
}

const button = (container: HTMLElement, text: string) =>
  [...container.querySelectorAll('button')].find((item) => item.textContent?.includes(text))!

describe('AuthoringPublishRepository workflow update', () => {
  it('refreshes the parent once the files are written, even if the follow-up preview fails', async () => {
    const created = vi.fn()
    api.previewProjectWorkflows
      .mockResolvedValueOnce([{ path: '.github/workflows/release.yml', status: 'UPGRADE' }])
      .mockRejectedValueOnce(new Error('network down'))
    api.applyProjectWorkflows.mockResolvedValue([{ path: '.github/workflows/release.yml', status: 'UPGRADE' }])
    const container = mount(created)

    button(container, authoringEnUs.workflowPreview).click()
    await settle()
    button(container, authoringEnUs.workflowApply).click()
    await settle()

    expect(api.applyProjectWorkflows).toHaveBeenCalledWith('p1')
    expect(created).toHaveBeenCalledTimes(1)
    // The written upgrade is no longer offered.
    expect(container.textContent).not.toContain('UPGRADE')
    expect(button(container, authoringEnUs.workflowApply)).toBeUndefined()
    // The failed preview is reported; the apply itself is not undone.
    expect(container.querySelector('.el-alert--error')).not.toBeNull()
  })

  it('refreshes the parent and the preview when the apply itself fails', async () => {
    // The server writes file by file: a failed apply may have changed some.
    const created = vi.fn()
    api.previewProjectWorkflows
      .mockResolvedValueOnce([{ path: '.github/workflows/release.yml', status: 'UPGRADE' }])
      .mockResolvedValueOnce([{ path: '.github/workflows/release.yml', status: 'CURRENT' }])
    api.applyProjectWorkflows.mockRejectedValue(new Error('disk full'))
    const container = mount(created)

    button(container, authoringEnUs.workflowPreview).click()
    await settle()
    button(container, authoringEnUs.workflowApply).click()
    await settle()

    expect(created).toHaveBeenCalledTimes(1)
    expect(api.previewProjectWorkflows).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('CURRENT')
    expect(container.querySelector('.el-alert--error')).not.toBeNull()
  })

  it('does not refresh the parent when the update stops on a conflict', async () => {
    const created = vi.fn()
    api.previewProjectWorkflows.mockResolvedValue([{ path: '.github/workflows/release.yml', status: 'UPGRADE' }])
    api.applyProjectWorkflows.mockResolvedValue([{ path: '.github/workflows/release.yml', status: 'CONFLICT' }])
    const container = mount(created)

    button(container, authoringEnUs.workflowPreview).click()
    await settle()
    button(container, authoringEnUs.workflowApply).click()
    await settle()

    expect(created).not.toHaveBeenCalled()
    expect(container.textContent).toContain('CONFLICT')
  })
})
