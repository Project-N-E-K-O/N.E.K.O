import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  installPluginPackage,
  planPluginInstall,
  type PluginCliInstallRequest,
  type PluginCliInstallPlanResponse,
  type PluginCliInstallResponse,
} from '@/api/pluginCli'
import { formatHttpError } from '@/utils/request'

export type InstallPackagePathOptions = {
  pluginsRoot?: string
  profilesRoot?: string
  installSource?: 'imported'
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
      if (plan.action === 'upgrade') {
        try {
          await ElMessageBox.confirm(
            t('package.install.upgradeBody', {
              current: plan.current_version || '-',
              target: plan.target_version || '-',
            }),
            t('package.install.upgradeTitle', {
              plugin: plan.plugin_id || plan.directory_name,
            }),
            {
              type: 'warning',
              confirmButtonText: t('package.install.upgradeConfirm'),
              cancelButtonText: t('common.cancel'),
            },
          )
        } catch {
          ElMessage.info(t('package.install.upgradeCancelled'))
          return null
        }
        request.confirm_upgrade = true
        request.confirmation_token = plan.confirmation_token
      }

      return await installPluginPackage(request)
    } catch (error) {
      const errorCode = (error as any)?.response?.data?.detail?.code
        || (error as any)?.response?.data?.code
      if (errorCode === 'PLUGIN_UPGRADE_ROLLED_BACK') {
        const rollbackStatus = (error as any)?.response?.data?.detail?.details?.rollback_status
        ElMessage.error(t(
          rollbackStatus === 'completed'
            ? 'package.install.rollbackCompleted'
            : 'package.install.rollbackIncomplete',
        ))
      } else if (!installPlan.value) {
        ElMessage.error(t('package.install.planFailed'))
      } else {
        ElMessage.error(t('package.install.installFailed', { error: formatHttpError(error) }))
      }
      return null
    } finally {
      installing.value = false
    }
  }

  return {
    installing,
    installPlan,
    installPackagePath,
  }
}
