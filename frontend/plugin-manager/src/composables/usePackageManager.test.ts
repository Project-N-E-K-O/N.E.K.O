import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePackageManager } from './usePackageManager'
import {
  getPluginCliPackages,
  getPluginCliPlugins,
  installPluginPackage,
  planPluginInstall,
  type PluginCliInstallPlanResponse,
  type PluginCliInstallResponse,
  type PluginCliPluginRef,
} from '@/api/pluginCli'
import { ElMessage, ElMessageBox } from 'element-plus'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: { value: 'zh-CN' },
    t: (key: string, params?: Record<string, unknown>) => `${key}${params ? JSON.stringify(params) : ''}`,
  }),
}))

const pluginRef: PluginCliPluginRef = {
  root_id: 'builtin',
  directory_name: 'demo_plugin',
  plugin_id: 'demo_plugin',
  label: 'Demo Plugin',
}

vi.mock('@/api/pluginCli', () => ({
  getPluginCliPlugins: vi.fn(async () => ({
    plugins: [],
    plugin_refs: [pluginRef],
  })),
  getPluginCliPackages: vi.fn(async () => ({
    packages: [],
    target_dir: '',
  })),
  analyzePluginBundle: vi.fn(),
  inspectPluginPackage: vi.fn(),
  buildPluginCli: vi.fn(),
  installPluginPackage: vi.fn(),
  planPluginInstall: vi.fn(),
  verifyPluginPackage: vi.fn(),
}))

const syncRegistryAndFetch = vi.hoisted(() => vi.fn(async () => ({})))

vi.mock('@/stores/plugin', () => ({
  usePluginStore: () => ({
    pluginsWithStatus: [
      {
        id: 'demo_plugin',
        name: 'Demo Plugin',
        description: '',
        version: '0.1.0',
        type: 'plugin',
      },
    ],
    syncRegistryAndFetch,
  }),
}))

vi.mock('@/utils/request', () => ({
  formatHttpError: (error: unknown) => String(error),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
  ElMessageBox: {
    confirm: vi.fn(),
  },
}))

const upgradePlan: PluginCliInstallPlanResponse = {
  action: 'upgrade',
  package_type: 'plugin',
  plugin_id: 'demo_plugin',
  directory_name: 'demo_plugin',
  current_version: '1.0.0',
  target_version: '2.0.0',
  confirmation_token: 'a'.repeat(64),
  reason: '',
  legacy_plugin_ids: [],
}

const installResponse: PluginCliInstallResponse = {
  package_path: 'demo.neko-plugin',
  package_type: 'plugin',
  package_id: 'demo_plugin',
  plugins_root: 'plugins',
  profiles_root: null,
  installed_plugins: [],
  profile_dir: null,
  metadata_found: true,
  payload_hash: 'hash',
  payload_hash_verified: true,
  conflict_strategy: 'fail',
  installed_plugin_count: 1,
  operation: 'install',
  restarted: false,
  rollback_status: 'not_needed',
}

beforeEach(() => {
  vi.clearAllMocks()
  syncRegistryAndFetch.mockResolvedValue({})
  vi.mocked(getPluginCliPlugins).mockResolvedValue({
    count: 1,
    plugins: [],
    plugin_refs: [pluginRef],
  })
})

describe('usePackageManager external plugin selection', () => {
  it('maps plugin list selections to package build targets', async () => {
    const selectedFromPluginList = ref(['demo_plugin'])
    const manager = usePackageManager({
      externalSelectedPluginIds: computed(() => selectedFromPluginList.value),
    })

    await manager.refreshPluginSources()

    expect(manager.selectedPluginIds.value).toEqual(['builtin:demo_plugin'])
    expect(manager.resolvedBuildTargets.value).toEqual(['builtin:demo_plugin'])
  })
})

describe('usePackageManager local package filtering', () => {
  it('recognizes an uppercase bundle suffix', async () => {
    vi.mocked(getPluginCliPackages).mockResolvedValue({
      packages: [
        {
          name: 'DEMO.NEKO-BUNDLE',
          path: '/packages/DEMO.NEKO-BUNDLE',
          suffix: '.NEKO-BUNDLE',
          size_bytes: 1024,
          modified_at: '2026-08-17T00:00:00+00:00',
        },
      ],
      count: 1,
      target_dir: '/packages',
    })
    const manager = usePackageManager()
    manager.packageFilterType.value = 'bundle'

    await manager.refreshPackageSources()

    expect(manager.filteredLocalPackages.value).toHaveLength(1)
    expect(manager.filteredLocalPackages.value[0]?.name).toBe('DEMO.NEKO-BUNDLE')
  })
})

describe('usePackageManager safe installation flow', () => {
  it('confirms a matching upgrade and forwards the confirmation token', async () => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'demo.neko-plugin'
    manager.installForm.value.profiles_root = 'profiles/custom'
    vi.mocked(planPluginInstall).mockResolvedValue(upgradePlan)
    vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as any)
    vi.mocked(installPluginPackage).mockResolvedValue({
      ...installResponse,
      operation: 'upgrade',
      restarted: true,
    })

    await manager.handleInstall()

    expect(planPluginInstall).toHaveBeenCalledWith(
      expect.objectContaining({
        profiles_root: 'profiles/custom',
      })
    )
    expect(installPluginPackage).toHaveBeenCalledWith(
      expect.objectContaining({
        profiles_root: 'profiles/custom',
        confirm_upgrade: true,
        confirmation_token: 'a'.repeat(64),
      })
    )
  })

  it.each([
    ['reinstall', 'package.install.reinstallSucceeded'],
    ['downgrade', 'package.install.downgradeSucceeded'],
  ] as const)('reports a distinct success message for %s', async (operation, messageKey) => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'demo.neko-plugin'
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...upgradePlan,
      action: operation,
    })
    vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as any)
    vi.mocked(installPluginPackage).mockResolvedValue({
      ...installResponse,
      operation,
    })

    await manager.handleInstall()

    expect(ElMessage.success).toHaveBeenCalledWith(
      `${messageKey}{"plugin":"demo_plugin"}`,
    )
  })

  it('installs a new plugin without upgrade credentials', async () => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'demo.neko-plugin'
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...upgradePlan,
      action: 'install',
      current_version: '',
      target_version: '1.0.0',
      confirmation_token: '',
    })
    vi.mocked(installPluginPackage).mockResolvedValue(installResponse)

    await manager.handleInstall()

    expect(installPluginPackage).toHaveBeenCalledWith(
      expect.not.objectContaining({ confirmation_token: expect.anything() }),
    )
  })

  it('blocks a local package from overriding a builtin outside Market', async () => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'study_companion.neko-plugin'
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...upgradePlan,
      action: 'override_builtin',
      plugin_id: 'study_companion',
      directory_name: 'study_companion',
      current_source: 'builtin',
      target_source: 'market',
      current_version: '0.1.5',
      target_version: '0.1.6',
    })
    await manager.handleInstall()

    expect(installPluginPackage).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalledWith('market.autoUpgradeBlocked')
  })

  it('does not duplicate interceptor errors when refreshing plugin sources fails', async () => {
    const manager = usePackageManager()
    vi.mocked(getPluginCliPlugins).mockRejectedValue(new Error('offline'))

    await manager.refreshPluginSources()

    expect(ElMessage.warning).not.toHaveBeenCalled()
  })

  it('uses a fallback warning for refresh status errors hidden by the interceptor', async () => {
    const manager = usePackageManager()
    vi.mocked(getPluginCliPlugins).mockRejectedValue({ response: { status: 404 } })

    await manager.refreshPluginSources()

    expect(ElMessage.warning).toHaveBeenCalledWith('messages.pluginListRefreshFailed')
  })

  it('does not duplicate warnings when registry and plugin source both return 404', async () => {
    const manager = usePackageManager()
    syncRegistryAndFetch.mockResolvedValue({
      registryRefreshed: false,
      warningMessage: 'messages.resourceNotFound',
    })
    vi.mocked(getPluginCliPlugins).mockRejectedValue({ response: { status: 404 } })

    await manager.refreshPluginSources()

    expect(ElMessage.warning).toHaveBeenCalledTimes(1)
    expect(ElMessage.warning).toHaveBeenCalledWith('messages.resourceNotFound')
  })

  it('still reports a plugin source failure after a partial registry refresh warning', async () => {
    const manager = usePackageManager()
    syncRegistryAndFetch.mockResolvedValue({
      registryRefreshed: true,
      warningMessage: 'messages.pluginListRefreshPartial',
    })
    vi.mocked(getPluginCliPlugins).mockRejectedValue({ response: { status: 404 } })

    await manager.refreshPluginSources()

    expect(ElMessage.warning).toHaveBeenCalledTimes(2)
    expect(ElMessage.warning).toHaveBeenNthCalledWith(1, 'messages.pluginListRefreshPartial')
    expect(ElMessage.warning).toHaveBeenNthCalledWith(2, 'messages.pluginListRefreshFailed')
  })

  it('reports install success before a registry refresh warning', async () => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'demo.neko-plugin'
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...upgradePlan,
      action: 'install',
      current_version: '',
      target_version: '1.0.0',
      confirmation_token: '',
    })
    vi.mocked(installPluginPackage).mockResolvedValue(installResponse)
    syncRegistryAndFetch.mockResolvedValue({
      warningMessage: '插件列表刷新存在失败项: broken_plugin',
    })

    await manager.handleInstall()

    expect(ElMessage.success).toHaveBeenCalledWith('安装完成，处理了 1 个插件')
    expect(ElMessage.warning).toHaveBeenCalledWith('插件列表刷新存在失败项: broken_plugin')
    expect(vi.mocked(ElMessage.success).mock.invocationCallOrder[0]!)
      .toBeLessThan(vi.mocked(ElMessage.warning).mock.invocationCallOrder[0]!)
  })

  it('does not install when the user cancels an upgrade', async () => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'demo.neko-plugin'
    vi.mocked(planPluginInstall).mockResolvedValue(upgradePlan)
    vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel')

    await manager.handleInstall()

    expect(installPluginPackage).not.toHaveBeenCalled()
    expect(ElMessage.info).toHaveBeenCalledWith('package.install.upgradeCancelled')
  })

  it('does not install a blocked bundle conflict', async () => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'demo.neko-bundle'
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...upgradePlan,
      action: 'blocked',
      package_type: 'bundle',
      plugin_id: '',
      directory_name: '',
      current_version: '',
      target_version: '1.0.0',
      confirmation_token: '',
      reason: 'bundle_conflict',
    })

    await manager.handleInstall()

    expect(installPluginPackage).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalledWith('package.install.blockedBundleConflict')
  })

  it('reports an incomplete rollback without claiming the old version was restored', async () => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'demo.neko-plugin'
    vi.mocked(planPluginInstall).mockResolvedValue(upgradePlan)
    vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as any)
    vi.mocked(installPluginPackage).mockRejectedValue({
      response: {
        data: {
          detail: {
            code: 'PLUGIN_UPGRADE_ROLLED_BACK',
            details: { rollback_status: 'incomplete' },
          },
        },
      },
    })

    await manager.handleInstall()

    expect(ElMessage.error).toHaveBeenCalledWith('package.install.rollbackIncomplete')
    expect(ElMessage.error).not.toHaveBeenCalledWith('package.install.rollbackCompleted')
  })
})
