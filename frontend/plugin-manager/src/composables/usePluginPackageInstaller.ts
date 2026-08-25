import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  discardUploadedPluginPackage,
  installPluginPackage,
  planPluginInstall,
  type PluginCliInstallRequest,
  type PluginCliInstallPlanResponse,
  type PluginCliInstallResponse,
} from '@/api/pluginCli'
import {
  readPluginPackageErrorCode,
  resolvePluginPackageErrorMessage,
} from '@/utils/pluginPackageError'

export type InstallPackagePathOptions = {
  pluginsRoot?: string
  profilesRoot?: string
  installSource?: 'imported'
  discardOnFailure?: boolean
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? value as Record<string, unknown>
    : null
}

function isConfirmedSafeInstallFailure(error: unknown): boolean {
  const response = asRecord(asRecord(error)?.response)
  if (!response) return false

  const data = asRecord(response.data)
  const detail = asRecord(data?.detail)
  const code = readPluginPackageErrorCode(error)
  const details = asRecord(data?.details) ?? asRecord(detail?.details)
  const rollbackStatus = details?.rollback_status

  if (code === 'PLUGIN_UPGRADE_ROLLED_BACK') {
    return rollbackStatus === 'completed'
  }
  if (rollbackStatus === 'incomplete') return false
  if (rollbackStatus === 'completed') return true

  const status = response.status
  const isDomainFailure = typeof code === 'string' && code.startsWith('PLUGIN_')
  const isAmbiguousTimeout = status === 408 || status === 425 || status === 429
  return isDomainFailure
    && typeof status === 'number'
    && status >= 400
    && status < 500
    && !isAmbiguousTimeout
}

export function usePluginPackageInstaller() {
  const { t } = useI18n()
  const installing = ref(false)
  const installPlan = ref<PluginCliInstallPlanResponse | null>(null)

  async function installPackagePath(
    packagePathInput: string,
    options: InstallPackagePathOptions = {},
  ): Promise<PluginCliInstallResponse | null> {
    const packagePath = packagePathInput.trim()
    if (!packagePath) {
      ElMessage.warning(t('package.install.packageRequired'))
      return null
    }

    const pluginsRoot = options.pluginsRoot?.trim() || undefined
    const profilesRoot = options.profilesRoot?.trim() || undefined
    let installRequested = false
    let installFailureConfirmed = false
    installing.value = true
    installPlan.value = null
    try {
      const plan = await planPluginInstall({
        package: packagePath,
        plugins_root: pluginsRoot,
        profiles_root: profilesRoot,
      })
      installPlan.value = plan

      if (plan.action === 'blocked') {
        const blockedKey = plan.reason === 'bundle_conflict'
          ? 'package.install.blockedBundleConflict'
          : plan.reason === 'legacy_plugin_present'
            ? 'package.install.blockedLegacyPlugin'
            : 'package.install.blockedDirectoryConflict'
        ElMessage.error(
          plan.reason === 'legacy_plugin_present'
            ? t(blockedKey, {
                plugin: plan.legacy_plugin_ids[0] || plan.plugin_id || plan.directory_name,
              })
            : t(blockedKey),
        )
        return null
      }

      const request: PluginCliInstallRequest = {
        package: packagePath,
        plugins_root: pluginsRoot,
        profiles_root: profilesRoot,
        on_conflict: 'fail',
        install_source: options.installSource,
      }
      if (plan.action === 'upgrade' || plan.action === 'reinstall' || plan.action === 'downgrade') {
        const messagePrefix = plan.action
        try {
          await ElMessageBox.confirm(
            t(`package.install.${messagePrefix}Body`, {
              current: plan.current_version || '-',
              target: plan.target_version || '-',
            }),
            t(`package.install.${messagePrefix}Title`, {
              plugin: plan.plugin_id || plan.directory_name,
            }),
            {
              type: 'warning',
              confirmButtonText: t(`package.install.${messagePrefix}Confirm`),
              cancelButtonText: t('common.cancel'),
            },
          )
        } catch {
          ElMessage.info(t(`package.install.${messagePrefix}Cancelled`))
          return null
        }
        request.confirm_upgrade = true
        request.confirmation_token = plan.confirmation_token
      }

      installRequested = true
      return await installPluginPackage(request)
    } catch (error) {
      installFailureConfirmed = installRequested && isConfirmedSafeInstallFailure(error)
      ElMessage.error(resolvePluginPackageErrorMessage(
        error,
        t,
        installPlan.value ? 'install' : 'plan',
      ))
      return null
    } finally {
      if (options.discardOnFailure && (!installRequested || installFailureConfirmed)) {
        try {
          await discardUploadedPluginPackage(packagePath)
        } catch (cleanupError) {
          console.warn('Failed to discard abandoned plugin package upload', cleanupError)
        }
      }
      installing.value = false
    }
  }

  return {
    installing,
    installPlan,
    installPackagePath,
  }
}
