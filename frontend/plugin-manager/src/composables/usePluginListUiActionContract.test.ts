// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePluginListContextActions } from './usePluginListContextActions'
import type { PluginListAction, PluginMeta } from '@/types/api'

const mocks = vi.hoisted(() => ({
  openExternalUrl: vi.fn(),
  routerPush: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: mocks.routerPush,
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: { value: 'zh-CN' },
    t: (key: string) => key,
  }),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
  ElMessageBox: { confirm: vi.fn() },
}))

vi.mock('@/stores/plugin', () => ({
  usePluginStore: () => ({
    start: vi.fn(),
    stop: vi.fn(),
    reload: vi.fn(),
  }),
}))

vi.mock('@/api/plugins', () => ({ deletePlugin: vi.fn() }))
vi.mock('@/api/pluginCli', () => ({ buildPluginCli: vi.fn() }))
vi.mock('@/utils/openExternal', () => ({
  openExternalUrl: mocks.openExternalUrl,
}))

function makePlugin(action: PluginListAction): PluginMeta {
  return {
    id: 'generic-plugin',
    name: 'Generic plugin',
    description: 'Generic plugin description',
    version: '1.0.0',
    status: 'running',
    list_actions: [action],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('plugin list UI action navigation contract', () => {
  it('uses openExternalUrl for the default new-tab path', async () => {
    const plugin = makePlugin({
      id: 'open_ui',
      kind: 'ui',
      target: '/plugin/generic/ui/',
    })
    const { buildActions, executeAction } = usePluginListContextActions()
    const action = buildActions(plugin).find((candidate) => candidate.id === 'open_ui')
    expect(action).toBeDefined()

    await executeAction(action!, plugin)

    expect(mocks.openExternalUrl).toHaveBeenCalledWith('/plugin/generic/ui/')
    expect(mocks.routerPush).not.toHaveBeenCalled()
  })

  it('uses current-window navigation for same_tab', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const plugin = makePlugin({
      id: 'open_ui',
      kind: 'ui',
      target: '/plugin/generic/ui/',
      open_in: 'same_tab',
    })
    const { buildActions, executeAction } = usePluginListContextActions()
    const action = buildActions(plugin).find((candidate) => candidate.id === 'open_ui')

    await executeAction(action!, plugin)

    expect(open).toHaveBeenCalledWith('/plugin/generic/ui/', '_self')
    expect(mocks.openExternalUrl).not.toHaveBeenCalled()
  })
})
