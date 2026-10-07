import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePluginPackageInstaller } from './usePluginPackageInstaller'
import {
  discardUploadedPluginPackage,
  installPluginPackage,
  planPluginInstall,
  type PluginCliInstallPlanResponse,
  type PluginCliInstallResponse,
} from '@/api/pluginCli'
import { ElMessage, ElMessageBox } from 'element-plus'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => `${key}${params ? JSON.stringify(params) : ''}`,
  }),
}))

vi.mock('@/api/pluginCli', () => ({
  discardUploadedPluginPackage: vi.fn(),
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
  current_version: '1.0.0',
  target_version: '2.0.0',
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
  it('discards an owned upload when the install plan is blocked', async () => {
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...replacePlan,
      action: 'blocked',
      reason: 'directory_identity_conflict',
    })
    vi.mocked(discardUploadedPluginPackage).mockResolvedValue({
      success: true,
      removed: true,
      name: 'demo.neko-plugin',
    })
    const installer = usePluginPackageInstaller()

    const response = await installer.installPackagePath('/packages/demo.neko-plugin', {
      discardOnFailure: true,
    })

    expect(response).toBeNull()
    expect(discardUploadedPluginPackage).toHaveBeenCalledWith('/packages/demo.neko-plugin')
  })

  it.each([
    ['install_source_ownership_unknown', 'package.install.blockedOwnershipUnknown'],
    ['install_source_read_only', 'package.install.blockedInstallSourceReadOnly'],
  ])('explains the %s blocked plan', async (reason, messageKey) => {
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...replacePlan,
      action: 'blocked',
      reason,
    })
    const installer = usePluginPackageInstaller()

    const response = await installer.installPackagePath('/packages/demo.neko-plugin')

    expect(response).toBeNull()
    expect(ElMessage.error).toHaveBeenCalledWith(messageKey)
  })

  it('keeps the upload when the server completed install but the response was lost', async () => {
    let serverCompleted = false
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...replacePlan,
      action: 'install',
    })
    vi.mocked(installPluginPackage).mockImplementation(async () => {
      serverCompleted = true
      throw new Error('response lost after commit')
    })
    const installer = usePluginPackageInstaller()

    const response = await installer.installPackagePath('/packages/demo.neko-plugin', {
      discardOnFailure: true,
    })

    expect(serverCompleted).toBe(true)
    expect(response).toBeNull()
    expect(discardUploadedPluginPackage).not.toHaveBeenCalled()
  })

  it('discards the upload after a confirmed HTTP install failure', async () => {
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...replacePlan,
      action: 'install',
    })
    vi.mocked(installPluginPackage).mockRejectedValue({
      response: {
        status: 409,
        data: { code: 'PLUGIN_PACKAGE_PROFILE_OWNERSHIP_CONFLICT' },
      },
    })
    vi.mocked(discardUploadedPluginPackage).mockResolvedValue({
      success: true,
      removed: true,
      name: 'demo.neko-plugin',
    })
    const installer = usePluginPackageInstaller()

    const response = await installer.installPackagePath('/packages/demo.neko-plugin', {
      discardOnFailure: true,
    })

    expect(response).toBeNull()
    expect(discardUploadedPluginPackage).toHaveBeenCalledWith('/packages/demo.neko-plugin')
  })

  it('discards the upload when the plugin domain code is carried by a response header', async () => {
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...replacePlan,
      action: 'install',
    })
    vi.mocked(installPluginPackage).mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'existing profile ownership does not match' },
        headers: { 'x-error-code': 'PLUGIN_PACKAGE_PROFILE_OWNERSHIP_CONFLICT' },
      },
    })
    const installer = usePluginPackageInstaller()

    await installer.installPackagePath('/packages/demo.neko-plugin', {
      discardOnFailure: true,
    })

    expect(discardUploadedPluginPackage).toHaveBeenCalledWith('/packages/demo.neko-plugin')
  })

  it.each([
    {
      name: 'an incomplete rollback response',
      error: {
        response: {
          status: 409,
          data: {
            code: 'PLUGIN_UPGRADE_ROLLED_BACK',
            details: { rollback_status: 'incomplete' },
          },
        },
      },
    },
    {
      name: 'an unknown server error',
      error: {
        response: {
          status: 500,
          data: { code: 'INTERNAL_SERVER_ERROR' },
        },
      },
    },
    {
      name: 'an ambiguous proxy timeout',
      error: {
        response: {
          status: 408,
          data: { code: 'PLUGIN_INSTALL_TIMEOUT' },
        },
      },
    },
    {
      name: 'a 4xx response without a plugin domain code',
      error: {
        response: {
          status: 409,
          data: { code: 'PROXY_CONFLICT' },
        },
      },
    },
  ])('keeps the upload after $name', async ({ error }) => {
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...replacePlan,
      action: 'install',
    })
    vi.mocked(installPluginPackage).mockRejectedValue(error)
    const installer = usePluginPackageInstaller()

    const response = await installer.installPackagePath('/packages/demo.neko-plugin', {
      discardOnFailure: true,
    })

    expect(response).toBeNull()
    expect(discardUploadedPluginPackage).not.toHaveBeenCalled()
  })

  it('discards the upload after a completed rollback response', async () => {
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...replacePlan,
      action: 'upgrade',
    })
    vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as any)
    vi.mocked(installPluginPackage).mockRejectedValue({
      response: {
        status: 409,
        data: {
          code: 'PLUGIN_UPGRADE_ROLLED_BACK',
          details: { rollback_status: 'completed' },
        },
      },
    })
    const installer = usePluginPackageInstaller()

    await installer.installPackagePath('/packages/demo.neko-plugin', {
      discardOnFailure: true,
    })

    expect(discardUploadedPluginPackage).toHaveBeenCalledWith('/packages/demo.neko-plugin')
  })

  it('plans and confirms an uploaded package path before replacing an installed plugin', async () => {
    vi.mocked(planPluginInstall).mockResolvedValue(replacePlan)
    vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as any)
    vi.mocked(installPluginPackage).mockResolvedValue(replaceResponse)
    const installer = usePluginPackageInstaller()

    const response = await installer.installPackagePath('/packages/demo.neko-plugin', {
      installSource: 'imported',
    })

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
      install_source: 'imported',
      confirm_upgrade: true,
      confirmation_token: 'a'.repeat(64),
    })
    expect(response).toEqual(replaceResponse)
  })

  it.each([
    ['upgrade', 'upgradeTitle', 'upgradeBody', 'upgradeConfirm'],
    ['reinstall', 'reinstallTitle', 'reinstallBody', 'reinstallConfirm'],
    ['downgrade', 'downgradeTitle', 'downgradeBody', 'downgradeConfirm'],
  ] as const)(
    'uses operation-specific confirmation copy for %s',
    async (action, titleKey, bodyKey, confirmKey) => {
      vi.mocked(planPluginInstall).mockResolvedValue({
        ...replacePlan,
        action,
      })
      vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as any)
      vi.mocked(installPluginPackage).mockResolvedValue({
        ...replaceResponse,
        operation: action,
      })
      const installer = usePluginPackageInstaller()

      await installer.installPackagePath('/packages/demo.neko-plugin')

      expect(ElMessageBox.confirm).toHaveBeenCalledWith(
        expect.stringContaining(`package.install.${bodyKey}`),
        expect.stringContaining(`package.install.${titleKey}`),
        expect.objectContaining({
          confirmButtonText: `package.install.${confirmKey}`,
        }),
      )
      expect(installPluginPackage).toHaveBeenCalledWith(
        expect.objectContaining({
          confirm_upgrade: true,
          confirmation_token: 'a'.repeat(64),
        }),
      )
    },
  )

  it('uses ownership-transfer copy for a manual takeover plan', async () => {
    vi.mocked(planPluginInstall).mockResolvedValue({
      ...replacePlan,
      reason: 'manual_takeover',
      current_source: 'manual',
      target_source: 'imported',
    })
    vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as never)
    vi.mocked(installPluginPackage).mockResolvedValue(replaceResponse)
    const installer = usePluginPackageInstaller()

    await installer.installPackagePath('/packages/demo.neko-plugin')

    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('package.install.manualTakeoverBody'),
      expect.stringContaining('package.install.manualTakeoverTitle'),
      expect.objectContaining({
        confirmButtonText: 'package.install.manualTakeoverConfirm',
      }),
    )
    expect(installPluginPackage).toHaveBeenCalledWith(
      expect.objectContaining({
        confirm_upgrade: true,
        confirmation_token: 'a'.repeat(64),
      }),
    )
  })
})
