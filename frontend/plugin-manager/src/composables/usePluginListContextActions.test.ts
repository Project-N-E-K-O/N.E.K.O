import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessage } from 'element-plus'

import { deletePlugin } from '@/api/plugins'
import { usePluginListContextActions } from './usePluginListContextActions'

const syncRegistryAndFetch = vi.fn()
const fetchPluginStatus = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
    locale: { value: 'en-US' },
  }),
}))

vi.mock('@/stores/plugin', () => ({
  usePluginStore: () => ({
    syncRegistryAndFetch,
    fetchPluginStatus,
    start: vi.fn(),
    stop: vi.fn(),
    reload: vi.fn(),
  }),
}))

vi.mock('@/api/plugins', () => ({
  deletePlugin: vi.fn(),
}))

vi.mock('@/api/pluginCli', () => ({
  buildPluginCli: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
  ElMessageBox: {
    confirm: vi.fn(),
  },
}))

describe('usePluginListContextActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    syncRegistryAndFetch.mockResolvedValue(undefined)
    fetchPluginStatus.mockResolvedValue(undefined)
  })

  it('warns when managed deletion falls back to a builtin that did not start', async () => {
    vi.mocked(deletePlugin).mockResolvedValue({
      success: true,
      plugin_id: 'demo',
      plugin_dir: 'plugin-installations/demo',
      deleted_from_disk: true,
      builtin_preserved: true,
      user_data_preserved: true,
      deletion_scope: 'user_overlay',
      fallback_to_builtin: true,
      fallback_runtime_started: false,
      fallback_runtime_error: 'internal runtime detail',
      message: 'deleted',
    })
    const actions = usePluginListContextActions()
    const deleteAction = actions.buildActions({ id: 'demo', status: 'stopped' } as any)
      .find(action => action.id === 'delete')!

    await actions.executeAction(deleteAction, { id: 'demo', status: 'stopped' } as any)

    expect(ElMessage.warning).toHaveBeenCalledWith(
      'messages.pluginRevertedToBuiltinNotStarted',
    )
    expect(ElMessage.success).not.toHaveBeenCalled()
  })
})
