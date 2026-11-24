/**
 * Error codes of the plugin permission store (plugin_permissions.py,
 * permission_service.py) that can reach the plugin manager: on install and
 * upgrade (Market tasks report them lower-cased), uninstall, source switch
 * and consent decisions. Keys are lower-case; look codes up through
 * `permissionErrorKey`, which accepts either case.
 */
export const PERMISSION_ERROR_KEYS: Record<string, string> = {
  plugin_permission_source_unknown: 'pluginConsent.errors.sourceUnknown',
  plugin_permissions_read_failed: 'pluginConsent.errors.readFailed',
  plugin_permissions_corrupt: 'pluginConsent.errors.corrupt',
  plugin_permissions_unsupported_version: 'pluginConsent.errors.unsupportedVersion',
  plugin_permissions_write_failed: 'pluginConsent.errors.writeFailed',
}

export function permissionErrorKey(code: unknown): string | null {
  const normalized = String(code || '').trim().toLowerCase()
  return Object.hasOwn(PERMISSION_ERROR_KEYS, normalized) ? PERMISSION_ERROR_KEYS[normalized] : null
}
