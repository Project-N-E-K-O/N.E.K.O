import { formatHttpError } from '@/utils/request'

type Translate = (key: string) => string
export type PluginPackageErrorPhase = 'plan' | 'install' | 'upload' | 'inspect' | 'verify'

const ERROR_KEYS = {
  PLUGIN_PACKAGE_INVALID_ARCHIVE: 'package.install.error.invalidArchive',
  PLUGIN_PACKAGE_MANIFEST_MISSING: 'package.install.error.manifestMissing',
  PLUGIN_PACKAGE_NESTED_ROOT: 'package.install.error.nestedRoot',
  PLUGIN_PACKAGE_PLUGIN_MANIFEST_MISSING: 'package.install.error.pluginManifestMissing',
  PLUGIN_PACKAGE_PLUGIN_MANIFEST_INVALID: 'package.install.error.pluginManifestInvalid',
  PLUGIN_PACKAGE_IDENTITY_MISMATCH: 'package.install.error.identityMismatch',
  PLUGIN_PACKAGE_HASH_MISMATCH: 'package.install.error.hashMismatch',
} as const

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? value as Record<string, unknown>
    : null
}

function readHeader(headers: unknown, name: string): string {
  if (!headers || typeof headers !== 'object') return ''
  const bag = headers as Record<string, unknown> & { get?: (key: string) => unknown }
  const value = typeof bag.get === 'function'
    ? bag.get(name)
    : bag[name] ?? bag[name.toLowerCase()]
  return value == null ? '' : String(value)
}

export function readPluginPackageErrorCode(error: unknown): string {
  const response = asRecord(asRecord(error)?.response)
  const data = asRecord(response?.data)
  const detail = asRecord(data?.detail)
  if (typeof detail?.code === 'string') {
    return detail.code
  }
  if (typeof data?.code === 'string') return data.code
  return readHeader(response?.headers, 'X-Error-Code')
}

function readRollbackDetails(error: unknown): Record<string, unknown> {
  const response = asRecord(asRecord(error)?.response)
  const data = asRecord(response?.data)
  const detail = asRecord(data?.detail)
  return asRecord(detail?.details) ?? {}
}

function legacyErrorKey(error: unknown): string {
  const detail = formatHttpError(error).toLowerCase()
  if (/not a zip file|not a readable zip archive/.test(detail)) {
    return ERROR_KEYS.PLUGIN_PACKAGE_INVALID_ARCHIVE
  }
  if (/manifest\.toml.*(extra parent folder|nested)|extra parent folder/.test(detail)) {
    return ERROR_KEYS.PLUGIN_PACKAGE_NESTED_ROOT
  }
  if (/required file ['"]manifest\.toml['"].*not found|package manifest\.toml is missing/.test(detail)) {
    return ERROR_KEYS.PLUGIN_PACKAGE_MANIFEST_MISSING
  }
  if (/missing the required ['"]plugin\.toml['"]/.test(detail)) {
    return ERROR_KEYS.PLUGIN_PACKAGE_PLUGIN_MANIFEST_MISSING
  }
  if (/plugin\.toml.*invalid toml|invalid toml.*plugin\.toml/.test(detail)) {
    return ERROR_KEYS.PLUGIN_PACKAGE_PLUGIN_MANIFEST_INVALID
  }
  if (/plugin identity mismatch|plugin folder .* does not match plugin\.toml id/.test(detail)) {
    return ERROR_KEYS.PLUGIN_PACKAGE_IDENTITY_MISMATCH
  }
  if (/payload hash mismatch|content.*verification hash/.test(detail)) {
    return ERROR_KEYS.PLUGIN_PACKAGE_HASH_MISMATCH
  }
  return ''
}

export function resolvePluginPackageErrorMessage(
  error: unknown,
  t: Translate,
  phase: PluginPackageErrorPhase,
): string {
  const code = readPluginPackageErrorCode(error)
  if (code === 'PLUGIN_UPGRADE_ROLLED_BACK') {
    const details = readRollbackDetails(error)
    if (details.rollback_status !== 'completed') {
      return t('package.install.rollbackIncomplete')
    }
    if (details.cause_code === 'PLUGIN_PACKAGE_HASH_MISMATCH') {
      return t('package.install.error.hashMismatch')
    }
    return t('package.install.rollbackCompleted')
  }

  const key = ERROR_KEYS[code as keyof typeof ERROR_KEYS] || legacyErrorKey(error)
  if (key) return t(key)

  if (phase === 'plan') return t('package.install.planFailed')
  if (phase === 'inspect') return t('package.install.error.inspectFailed')
  if (phase === 'verify') return t('package.install.error.verifyFailed')
  if (phase === 'upload') return t('plugins.importFailed')
  return t('package.install.error.installFailed')
}
