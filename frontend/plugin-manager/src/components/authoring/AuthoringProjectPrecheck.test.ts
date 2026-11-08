// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import Precheck from './AuthoringProjectPrecheck.vue'
import { authoringMessages } from '@/i18n/authoring'
import { pythonPackageMessages } from '@/i18n/python-packages'
import type { PrecheckReport } from '@/api/pluginProjects'

const api = vi.hoisted(() => ({ precheckPluginProject: vi.fn() }))
vi.mock('@/api/pluginProjects', async (original) => ({
  ...(await original<typeof import('@/api/pluginProjects')>()),
  ...api,
}))
const en = authoringMessages['en-US']
let app: ReturnType<typeof createApp> | undefined
async function flush() {
  for (let n = 0; n < 12; n++) {
    await Promise.resolve()
    await nextTick()
  }
}
async function mount() {
  const props = reactive({ projectId: 'p1', dependencyRevision: 1 })
  const host = document.createElement('div')
  document.body.append(host)
  app = createApp({ render: () => h(Precheck, props) })
    .use(ElementPlus)
    .use(
      createI18n({
        legacy: false,
        locale: 'en-US',
        messages: {
          'en-US': { authoring: en, pythonPackages: pythonPackageMessages['en-US'] },
        },
      })
    )
  app.mount(host)
  await flush()
  return props
}
afterEach(() => {
  app?.unmount()
  document.body.innerHTML = ''
  vi.resetAllMocks()
})
describe('authoring precheck dependency revision', () => {
  it.each(['success', 'error'] as const)(
    'discards stale %s results and releases the run button',
    async (outcome) => {
      let resolve!: (value: PrecheckReport) => void
      let reject!: (cause: Error) => void
      api.precheckPluginProject.mockReturnValueOnce(
        new Promise((done, fail) => {
          resolve = done
          reject = fail
        })
      )
      const props = await mount()
      document.querySelector<HTMLButtonElement>('button')!.click()
      await flush()
      props.dependencyRevision += 1
      await flush()
      if (outcome === 'success')
        resolve({ passed: true, environment_issues: [], stages: [] } as unknown as PrecheckReport)
      else reject(new Error('Stale precheck error'))
      await flush()
      expect(document.body.textContent).not.toContain(en.precheckPassed)
      expect(document.body.textContent).not.toContain('Stale precheck error')
      expect(document.querySelector<HTMLButtonElement>('button')!.textContent).toContain(
        en.precheckRun
      )
      api.precheckPluginProject.mockResolvedValue({
        passed: true,
        environment_issues: [],
        stages: [],
      })
      document.querySelector<HTMLButtonElement>('button')!.click()
      await flush()
      expect(api.precheckPluginProject).toHaveBeenCalledTimes(2)
      expect(document.body.textContent).toContain(en.precheckPassed)
    }
  )
})
