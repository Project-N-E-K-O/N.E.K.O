import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePackageManager } from './usePackageManager'
import {
  inspectPluginPackage,
  installPluginPackage,
  planPluginInstall,
  verifyPluginPackage,
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
    syncRegistryAndFetch: vi.fn(async () => ({})),
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
  target_ownership: 'managed',
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

  it('never reports success when the installed payload was not activated', async () => {
    const manager = usePackageManager()
    manager.installForm.value.package = 'demo.neko-plugin'
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...upgradePlan,
      action: 'install',
      current_version: '',
      confirmation_token: '',
    })
    vi.mocked(installPluginPackage).mockResolvedValue({
      ...installResponse,
      activation: {
        status: 'blocked',
        plugin_ids: ['demo_plugin'],
        reason: 'selected_installation_not_projected',
      },
    })

    await manager.handleInstall()

    expect(ElMessage.error).toHaveBeenCalledWith(
      'package.install.activationBlocked{"reason":"selected_installation_not_projected"}',
    )
    expect(ElMessage.success).not.toHaveBeenCalled()
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

describe('usePackageManager package inspection errors', () => {
  it.each([
    ['inspect', inspectPluginPackage, 'handleInspect', 'package.install.error.inspectFailed'],
    ['verify', verifyPluginPackage, 'handleVerify', 'package.install.error.verifyFailed'],
  ] as const)('shows one localized %s error without exposing a local path', async (
    _name,
    requestPackage,
    handler,
    expectedMessage,
  ) => {
    const manager = usePackageManager()
    manager.packageRef.value.package = 'demo.neko-plugin'
    vi.mocked(requestPackage).mockRejectedValue({
      response: {
        data: {
          detail: 'unexpected failure at C:\\Users\\name\\private.neko-plugin',
        },
      },
    })

    await manager[handler]()

    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledWith(expectedMessage)
    expect(ElMessage.error).not.toHaveBeenCalledWith(expect.stringContaining('C:\\Users'))
  })
})
