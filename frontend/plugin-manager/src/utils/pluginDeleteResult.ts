import type { DeletePluginResult } from '@/api/plugins'

type Translate = (key: string, params?: Record<string, unknown>) => string

type DeleteResultLike = Pick<
  DeletePluginResult,
  'restored_builtin_restart_error' | 'permission_warning'
> | null | undefined

/**
 * A successful delete can still carry follow-up problems: the restored
 * built-in failed to start, or the permission records could not be read so
 * the plugin's grants were not cleared. Returns one translated line each.
 */
export function collectPluginDeleteWarnings(
  result: DeleteResultLike,
  plugin: string,
  t: Translate,
): string[] {
  return collectBatchPluginDeleteWarnings([{ result, plugin }], t)
}

/**
 * Same as ``collectPluginDeleteWarnings`` for several deletes. An unusable
 * permission store affects every plugin alike, so its warning is reported
 * once, naming all affected plugins.
 */
export function collectBatchPluginDeleteWarnings(
  deletes: Array<{ result: DeleteResultLike; plugin: string }>,
  t: Translate,
): string[] {
  const warnings: string[] = []
  const permissionPlugins: string[] = []
  for (const { result, plugin } of deletes) {
    if (result?.restored_builtin_restart_error) {
      warnings.push(t('messages.pluginDeletedBuiltinRestartFailed', {
        plugin,
        error: result.restored_builtin_restart_error.message,
      }))
    }
    if (result?.permission_warning && !permissionPlugins.includes(plugin)) {
      permissionPlugins.push(plugin)
    }
  }
  if (permissionPlugins.length > 0) {
    warnings.push(t('messages.pluginDeletedPermissionsNotCleared', {
      plugin: permissionPlugins.join(', '),
    }))
  }
  return warnings
}
