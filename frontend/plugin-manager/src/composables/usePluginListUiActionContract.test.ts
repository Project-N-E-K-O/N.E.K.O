// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessage } from 'element-plus'
import { deletePlugin } from '@/api/plugins'
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

vi.mock('@/i18n', () => ({
  i18n: { global: { t: (key: string) => key } },
  localeLoadState: { pending: null },
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

vi.mock('@/stores/pluginConsentState', () => ({
  usePluginConsentState: () => ({ forget: vi.fn() }),
}))

vi.mock('@/api/plugins', () => ({ deletePlugin: vi.fn() }))
vi.mock('@/api/pluginCli', () => ({ buildPluginCli: vi.fn() }))
vi.mock('@/utils/openExternal', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/utils/openExternal')>()),
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

  it('warns when deletion restores a builtin that cannot restart', async () => {
    vi.mocked(deletePlugin).mockResolvedValue({
      success: true,
      plugin_id: 'generic-plugin',
      plugin_dir: '/plugins/generic-plugin',
      deleted_from_disk: true,
      restored_builtin: true,
      restored_builtin_started: false,
      restored_builtin_restart_error: {
        code: 'PLUGIN_BUILTIN_RESTORE_START_FAILED',
        message: 'startup failed',
        error_type: 'RuntimeError',
      },
      message: 'Plugin deleted successfully',
    })
    const plugin = makePlugin({ id: 'open_detail', kind: 'builtin' })
    const { buildActions, executeAction } = usePluginListContextActions()
    const action = buildActions(plugin).find((candidate) => candidate.id === 'delete')

    await executeAction(action!, plugin)

    expect(ElMessage.warning).toHaveBeenCalledWith(
      'messages.pluginDeletedBuiltinRestartFailed',
    )
    expect(ElMessage.success).not.toHaveBeenCalledWith('messages.pluginDeleted')
  })

  it('warns when deletion could not clear the permission grants', async () => {
    vi.mocked(deletePlugin).mockResolvedValue({
      success: true,
      plugin_id: 'generic-plugin',
      plugin_dir: '/plugins/generic-plugin',
      deleted_from_disk: true,
      restored_builtin: false,
      restored_builtin_started: false,
      restored_builtin_restart_error: null,
      permission_warning: {
        code: 'PLUGIN_PERMISSIONS_CORRUPT',
        message: 'Permission records could not be read',
      },
      message: 'Plugin deleted successfully',
    })
    const plugin = makePlugin({ id: 'open_detail', kind: 'builtin' })
    const { buildActions, executeAction } = usePluginListContextActions()
    const action = buildActions(plugin).find((candidate) => candidate.id === 'delete')

    await executeAction(action!, plugin)

    expect(ElMessage.warning).toHaveBeenCalledWith(
      'messages.pluginDeletedPermissionsNotCleared',
    )
    expect(ElMessage.success).not.toHaveBeenCalledWith('messages.pluginDeleted')
  })

  it.each([
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    'file:///etc/passwd',
    '//evil.example/phish',
    'https://evil.example/phish',
  ])('blocks unsafe same_tab target %s', async (dangerousTarget) => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const plugin = makePlugin({
      id: 'open_ui',
      kind: 'ui',
      label: 'Unsafe UI',
      target: dangerousTarget,
      open_in: 'same_tab',
    })
    const { buildActions, executeAction } = usePluginListContextActions()
    const action = buildActions(plugin).find((candidate) => candidate.id === 'open_ui')

    await executeAction(action!, plugin)

    expect(open).not.toHaveBeenCalled()
    expect(mocks.openExternalUrl).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith('plugins.ui.unsafeTargetBlocked')
  })

  it.each([
    '//evil.example/phish',
    'https://evil.example/phish',
    'javascript:alert(1)',
  ])('blocks unsafe route target %s', async (dangerousRoute) => {
    const plugin = makePlugin({
      id: 'open_ui',
      kind: 'route',
      label: 'Unsafe Route',
      target: dangerousRoute,
    })
    const { buildActions, executeAction } = usePluginListContextActions()
    const action = buildActions(plugin).find((candidate) => candidate.id === 'open_ui')

    await executeAction(action!, plugin)

    expect(mocks.routerPush).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith('plugins.ui.unsafeTargetBlocked')
  })

  it('allows same-origin absolute URL for same_tab', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const sameOriginUrl = `${window.location.origin}/plugin/generic/ui/`
    const plugin = makePlugin({
      id: 'open_ui',
      kind: 'url',
      target: sameOriginUrl,
      open_in: 'same_tab',
    })
    const { buildActions, executeAction } = usePluginListContextActions()
    const action = buildActions(plugin).find((candidate) => candidate.id === 'open_ui')

    await executeAction(action!, plugin)

    expect(open).toHaveBeenCalledWith(sameOriginUrl, '_self')
  })
})
