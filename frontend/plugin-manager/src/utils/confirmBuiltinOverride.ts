import { ElMessageBox } from 'element-plus'

type TranslateFn = (key: string, params?: Record<string, unknown>) => string

interface BuiltinOverrideConfirmation {
  pluginName: string
  currentVersion: string
  targetVersion: string
}

export async function confirmBuiltinOverride(
  t: TranslateFn,
  confirmation: BuiltinOverrideConfirmation
): Promise<boolean> {
  try {
    await ElMessageBox.confirm(
      t('package.install.overrideBuiltinBody', {
        current: confirmation.currentVersion || '-',
        target: confirmation.targetVersion || '-',
      }),
      t('package.install.overrideBuiltinTitle', {
        plugin: confirmation.pluginName,
      }),
      {
        type: 'warning',
        confirmButtonText: t('package.install.overrideBuiltinConfirm'),
        cancelButtonText: t('common.cancel'),
      }
    )
    return true
  } catch {
    return false
  }
}
