// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import PluginAuthoring from './PluginAuthoring.vue'
import { authoringMessages } from '@/i18n/authoring'
import type { PluginProjectResult } from '@/api/pluginProjects'

const api = vi.hoisted(() => ({
  previewPluginProject: vi.fn(),
  createPluginProject: vi.fn(),
  pickProjectDirectory: vi.fn(),
  openProjectDirectory: vi.fn(),
  mountDevelopment: vi.fn(),
  push: vi.fn(),
}))
vi.mock('@/api/pluginProjects', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/pluginProjects')>()),
  previewPluginProject: api.previewPluginProject,
  createPluginProject: api.createPluginProject,
  pickProjectDirectory: api.pickProjectDirectory,
  openProjectDirectory: api.openProjectDirectory,
}))
vi.mock('@/api/development', () => ({ mountDevelopment: api.mountDevelopment }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: api.push }),
}))

const project: PluginProjectResult = {
  plugin_id: 'hello_world',
  name: 'Real project',
  description: 'Created on disk',
  author: 'Developer',
  plugin_type: 'plugin',
  version: '0.1.0',
  entry: 'plugin.plugins.hello_world:HelloWorldPlugin',
  project_dir: 'E:/Projects/hello_world',
  files: ['plugin.toml', '__init__.py', '.git/'],
  created: true,
}
const NativeMutationObserver = globalThis.MutationObserver
let cleanup: (() => void) | undefined

async function flush() {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

async function mount() {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp(PluginAuthoring)
  app.use(ElementPlus)
  app.use(
    createI18n({
      legacy: false,
      locale: 'en-US',
      messages: {
        'en-US': {
          authoring: authoringMessages['en-US'],
          common: { cancel: 'Cancel', close: 'Close', noData: 'No data' },
        },
      },
    })
  )
  app.mount(container)
  cleanup = () => {
    app.unmount()
    container.remove()
  }
  await flush()
  return container
}

async function click(text: string) {
  const button = [...document.querySelectorAll('button')].find(
    (element) => element.textContent?.trim() === text
  )
  expect(button, `Missing button: ${text}`).toBeTruthy()
  button!.click()
  await flush()
}

async function fill(id: string, value: string) {
  const input = document.getElementById(id) as HTMLInputElement
  expect(input).toBeTruthy()
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flush()
}

beforeEach(() => {
  localStorage.clear()
  vi.resetAllMocks()
  vi.stubGlobal(
    'MutationObserver',
    class extends NativeMutationObserver {
      constructor(callback: MutationCallback) {
        super(callback)
        markRaw(this)
      }
    }
  )
  api.previewPluginProject.mockResolvedValue({ ...project, created: false })
  api.createPluginProject.mockResolvedValue(project)
  api.pickProjectDirectory.mockResolvedValue('E:/Projects')
  api.mountDevelopment.mockResolvedValue({})
})

afterEach(() => {
  cleanup?.()
  cleanup = undefined
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('production authoring workbench', () => {
  it('keeps all locale keys consistent after removing simulation messages', () => {
    const keys = Object.keys(authoringMessages['en-US']).sort()
    for (const messages of Object.values(authoringMessages)) {
      expect(Object.keys(messages).sort()).toEqual(keys)
      expect(messages).not.toHaveProperty('createDemo')
      expect(messages).not.toHaveProperty('simulateFailure')
    }
  })

  it('starts empty in development too, without simulated controls', async () => {
    const container = await mount()
    expect(container.querySelectorAll('.authoring-project-card')).toHaveLength(0)
    expect(container.querySelector('.authoring-tabs')).toBeNull()
    expect(container.textContent).not.toContain('Weather helper')
    expect(api.previewPluginProject).not.toHaveBeenCalled()
    expect(api.mountDevelopment).not.toHaveBeenCalled()
  })

  it('previews, creates, opens and mounts a real project through the APIs', async () => {
    await mount()
    await click('New plugin')
    await click('Choose folder')
    await fill('authoring-name', project.name)
    await fill('authoring-id', project.plugin_id)
    await click('Preview project')
    expect(api.previewPluginProject).toHaveBeenCalledWith(
      expect.objectContaining({
        parent_dir: 'E:/Projects',
        plugin_id: project.plugin_id,
      })
    )
    expect(api.createPluginProject).not.toHaveBeenCalled()
    await click('Create plugin')
    expect(api.createPluginProject).toHaveBeenCalledOnce()
    expect(document.querySelectorAll('.authoring-project-card')).toHaveLength(1)
    expect(JSON.parse(localStorage.getItem('neko.pluginAuthoring.projects.v1')!)).toEqual([
      expect.objectContaining({ source: project.project_dir, id: project.plugin_id }),
    ])
    await click('Open folder')
    expect(api.openProjectDirectory).toHaveBeenCalledWith(project.project_dir)
    await click('Mount in development mode')
    expect(api.mountDevelopment).toHaveBeenCalledWith(project.project_dir)
    await click('Open development mode')
    expect(api.push).toHaveBeenCalledWith('/development')
  })

  it('allows the same plugin ID in another folder and surfaces preview errors', async () => {
    localStorage.setItem(
      'neko.pluginAuthoring.projects.v1',
      JSON.stringify([
        {
          key: 'existing',
          id: project.plugin_id,
          name: project.name,
          description: '',
          author: '',
          type: 'plugin',
          version: '0.1.0',
          source: 'F:/Other/hello_world',
          entry: project.entry,
          createdAt: 1,
        },
      ])
    )
    api.previewPluginProject.mockRejectedValue({
      response: { data: { detail: { code: 'PROJECT_EXISTS' } } },
    })
    await mount()
    await click('New plugin')
    await fill('authoring-parent', 'E:/Projects')
    await fill('authoring-name', project.name)
    await fill('authoring-id', project.plugin_id)
    await click('Preview project')
    expect(api.previewPluginProject).toHaveBeenCalledOnce()
    expect(document.body.textContent).toContain(authoringMessages['en-US'].directoryExists)
    expect(api.createPluginProject).not.toHaveBeenCalled()
    expect(document.querySelectorAll('.authoring-project-card')).toHaveLength(1)
  })

  it('shows a localized message for creation failures instead of the server diagnostic', async () => {
    api.createPluginProject.mockRejectedValue({
      response: { data: { detail: { code: 'GIT_INIT_FAILED', message: 'fatal: raw git output' } } },
    })
    await mount()
    await click('New plugin')
    await fill('authoring-parent', 'E:/Projects')
    await fill('authoring-name', project.name)
    await fill('authoring-id', project.plugin_id)
    await click('Preview project')
    await click('Create plugin')
    expect(document.body.textContent).toContain(authoringMessages['en-US'].gitInitFailed)
    expect(document.body.textContent).not.toContain('fatal: raw git output')
    expect(document.querySelectorAll('.authoring-project-card')).toHaveLength(0)
  })
})
