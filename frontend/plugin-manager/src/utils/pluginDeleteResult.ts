import type { DeletePluginResult } from '@/api/plugins'

type Translate = (key: string, params?: Record<string, unknown>) => string

/**
 * A successful delete can still carry follow-up problems: the restored
 * built-in failed to start, or the permission records could not be read so
 * the plugin's grants were not cleared. Returns one translated line each.
 */
export function collectPluginDeleteWarnings(
  result: Pick<DeletePluginResult, 'restored_builtin_restart_error' | 'permission_warning'> | null | undefined,
  plugin: string,
  t: Translate,
): string[] {
  const warnings: string[] = []
  if (result?.restored_builtin_restart_error) {
    warnings.push(t('messages.pluginDeletedBuiltinRestartFailed', {
      plugin,
      error: result.restored_builtin_restart_error.message,
    }))
  }
  if (result?.permission_warning) {
    warnings.push(t('messages.pluginDeletedPermissionsNotCleared', { plugin }))
  }
  return warnings
}
