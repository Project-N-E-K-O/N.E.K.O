import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePluginPackageInstaller } from './usePluginPackageInstaller'
import {
  installPluginPackage,
  planPluginInstall,
  type PluginCliInstallPlanResponse,
  type PluginCliInstallResponse,
} from '@/api/pluginCli'
import { ElMessageBox } from 'element-plus'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => `${key}${params ? JSON.stringify(params) : ''}`,
  }),
}))

vi.mock('@/api/pluginCli', () => ({
  installPluginPackage: vi.fn(),
  planPluginInstall: vi.fn(),
}))

vi.mock('@/utils/request', () => ({
  formatHttpError: (error: unknown) => String(error),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    info: vi.fn(),
  },
  ElMessageBox: {
    confirm: vi.fn(),
  },
}))

const replacePlan: PluginCliInstallPlanResponse = {
  action: 'upgrade',
  package_type: 'plugin',
  plugin_id: 'demo',
  directory_name: 'demo',
  current_version: '2.0.0',
  target_version: '1.0.0',
  confirmation_token: 'a'.repeat(64),
  reason: '',
  legacy_plugin_ids: [],
}

const replaceResponse: PluginCliInstallResponse = {
  package_path: 'demo.neko-plugin',
  package_type: 'plugin',
  package_id: 'demo',
  plugins_root: 'plugins',
  profiles_root: null,
  installed_plugins: [],
  profile_dir: null,
  metadata_found: true,
  payload_hash: 'hash',
  payload_hash_verified: true,
  conflict_strategy: 'fail',
  installed_plugin_count: 1,
  operation: 'upgrade',
  restarted: false,
  rollback_status: 'not_needed',
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('usePluginPackageInstaller', () => {
  it('plans and confirms an uploaded package path before replacing an installed plugin', async () => {
    vi.mocked(planPluginInstall).mockResolvedValue(replacePlan)
    vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as any)
    vi.mocked(installPluginPackage).mockResolvedValue(replaceResponse)
    const installer = usePluginPackageInstaller()

    const response = await installer.installPackagePath('/packages/demo.neko-plugin')

    expect(planPluginInstall).toHaveBeenCalledWith({
      package: '/packages/demo.neko-plugin',
      plugins_root: undefined,
      profiles_root: undefined,
    })
    expect(installPluginPackage).toHaveBeenCalledWith({
      package: '/packages/demo.neko-plugin',
      plugins_root: undefined,
      profiles_root: undefined,
      on_conflict: 'fail',
      confirm_upgrade: true,
      confirmation_token: 'a'.repeat(64),
    })
    expect(response).toEqual(replaceResponse)
  })
})
