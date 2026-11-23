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
  return collectBatchPluginDeleteWarnings([{ result, plugin, pluginId: plugin }], t)
}

/**
 * Same as ``collectPluginDeleteWarnings`` for several deletes. An unusable
 * permission store affects every plugin alike, so its warning is reported
 * once, naming all affected plugins. Plugins are told apart by id, since
 * two plugins can share a display name (e.g. a user copy and a built-in).
 */
export function collectBatchPluginDeleteWarnings(
  deletes: Array<{ result: DeleteResultLike; plugin: string; pluginId: string }>,
  t: Translate,
): string[] {
  const warnings: string[] = []
  const permissionPlugins = new Map<string, string>()
  for (const { result, plugin, pluginId } of deletes) {
    if (result?.restored_builtin_restart_error) {
      warnings.push(t('messages.pluginDeletedBuiltinRestartFailed', {
        plugin,
        error: result.restored_builtin_restart_error.message,
      }))
    }
    if (result?.permission_warning && !permissionPlugins.has(pluginId)) {
      permissionPlugins.set(pluginId, plugin)
    }
  }
  const names = [...permissionPlugins.values()]
  if (names.length === 1) {
    warnings.push(t('messages.pluginDeletedPermissionsNotCleared', { plugin: names[0] }))
  } else if (names.length > 1) {
    warnings.push(t('messages.pluginsDeletedPermissionsNotCleared', { plugins: names.join(', ') }))
  }
  return warnings
}
