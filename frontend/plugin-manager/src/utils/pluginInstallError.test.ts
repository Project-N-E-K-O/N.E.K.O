import { describe, expect, it } from 'vitest'

import { pluginPackageSizeDetails, resolvePluginInstallErrorKey } from './pluginInstallError'
import { PERMISSION_ERROR_KEYS, permissionErrorKey } from './pluginPermissionErrors'
import zhCnConsent from '@/i18n/plugin-consent/zh-CN'
import zhTwConsent from '@/i18n/plugin-consent/zh-TW'
import enUsConsent from '@/i18n/plugin-consent/en-US'
import jaConsent from '@/i18n/plugin-consent/ja'
import koConsent from '@/i18n/plugin-consent/ko'
import ruConsent from '@/i18n/plugin-consent/ru'
import esConsent from '@/i18n/plugin-consent/es'
import ptConsent from '@/i18n/plugin-consent/pt'

// One module per language (see i18n/localeBundles.ts); the tests check all eight.
const pluginConsentMessages = {
  'zh-CN': zhCnConsent,
  'zh-TW': zhTwConsent,
  'en-US': enUsConsent,
  ja: jaConsent,
  ko: koConsent,
  ru: ruConsent,
  es: esConsent,
  pt: ptConsent,
}

describe('resolvePluginInstallErrorKey', () => {
  it.each([
    ['package_size_exceeded', 'market.packageSizeExceeded'],
    ['download_timeout', 'market.downloadTimeout'],
    ['download_network_error', 'market.downloadNetworkError'],
    ['download_http_error', 'market.downloadHttpError'],
    ['insufficient_disk_space', 'market.insufficientDiskSpace'],
    ['download_write_failed', 'market.downloadWriteFailed'],
    ['PLUGIN_EXEC_STATE_ROOT_COLLISION', 'market.execStateRootCollision'],
    ['override_rollback_completed', 'market.upgradeRollback'],
    ['override_rollback_incomplete', 'market.rollbackIncomplete'],
    ['override_source_changed', 'market.overrideSourceChanged'],
    ['override_confirmation_changed', 'market.confirmationChanged'],
    ['override_confirmation_required', 'market.confirmationRequired'],
    ['manual_takeover_confirmation_required', 'market.confirmationRequired'],
    ['manual_takeover_plan_changed', 'market.confirmationChanged'],
    ['manual_takeover_source_changed', 'market.confirmationChanged'],
    ['override_target_exists', 'market.autoUpgradeBlocked'],
    ['override_start_failed', 'market.overrideStartFailed'],
    ['INSTALL_SOURCE_READ_ONLY', 'market.lockWriteFailed'],
    ['PLUGIN_BUILTIN_OVERRIDE_MARKET_REQUIRED', 'market.autoUpgradeBlocked'],
    ['market_release_mismatch', 'market.releaseMismatch'],
    ['market_catalog_unavailable', 'market.marketListFetchFailed'],
    ['market_catalog_not_configured', 'market.catalogNotConfigured'],
  ])('maps %s to a localized key', (code, key) => {
    expect(resolvePluginInstallErrorKey(code)).toBe(key)
  })

  it('uses a localized generic message for unknown backend codes', () => {
    expect(resolvePluginInstallErrorKey('backend_english_detail')).toBe('market.installFailed')
  })
})

describe('pluginPackageSizeDetails', () => {
  it('promotes a rounded unit boundary instead of showing 1024 KiB', () => {
    expect(pluginPackageSizeDetails({
      actual_bytes: 1048575, maximum_bytes: 524288000, size_source: 'content_length',
    })?.actual).toBe('1 MiB')
  })

  it('keeps a one-byte overflow visible despite rounded binary units', () => {
    expect(pluginPackageSizeDetails({
      actual_bytes: 524288001, maximum_bytes: 524288000, size_source: 'content_length',
    })?.actual).toBe('500 MiB (524288001 B)')
  })

  it('formats the reported package and limit using binary units', () => {
    expect(pluginPackageSizeDetails({
      actual_bytes: 264366702, maximum_bytes: 209715200, size_source: 'content_length',
    })).toEqual({ actual: '252.12 MiB', limit: '200 MiB', source: 'content_length', actualBytes: 264366702, maximumBytes: 209715200 })
  })

  it.each([null, {}, { actual_bytes: '600', maximum_bytes: 500, size_source: 'content_length' },
    { actual_bytes: Infinity, maximum_bytes: 500, size_source: 'content_length' },
    { actual_bytes: 600, maximum_bytes: 0, size_source: 'content_length' },
    { actual_bytes: 600, maximum_bytes: 500, size_source: 'unknown' },
  ])('ignores missing or malformed details: %j', (details) => {
    expect(pluginPackageSizeDetails(details)).toBeNull()
  })
})

describe('permission error codes', () => {
  const codes = [
    ['PLUGIN_PERMISSION_SOURCE_UNKNOWN', 'pluginConsent.errors.sourceUnknown'],
    ['PLUGIN_PERMISSIONS_READ_FAILED', 'pluginConsent.errors.readFailed'],
    ['PLUGIN_PERMISSIONS_CORRUPT', 'pluginConsent.errors.corrupt'],
    ['PLUGIN_PERMISSIONS_UNSUPPORTED_VERSION', 'pluginConsent.errors.unsupportedVersion'],
    ['PLUGIN_PERMISSIONS_WRITE_FAILED', 'pluginConsent.errors.writeFailed'],
  ] as const

  it('covers exactly the listed codes', () => {
    expect(Object.keys(PERMISSION_ERROR_KEYS).sort()).toEqual(codes.map(([code]) => code.toLowerCase()).sort())
  })

  // Local installs report the server's code, Market tasks the lower-cased one.
  it.each(codes.flatMap(([code, key]) => [[code, key], [code.toLowerCase(), key]]))(
    'install errors map %s to %s',
    (code, key) => {
      expect(resolvePluginInstallErrorKey(code)).toBe(key)
      expect(permissionErrorKey(code)).toBe(key)
    },
  )

  it.each(Object.entries(pluginConsentMessages))('%s has a message for every code', (_, messages) => {
    for (const [, key] of codes) {
      const name = key.replace('pluginConsent.errors.', '') as keyof typeof messages.errors
      expect(messages.errors[name]).toEqual(expect.any(String))
    }
  })

  it('ignores other codes', () => {
    expect(permissionErrorKey('PLUGIN_CONSENT_CHANGED')).toBeNull()
    expect(permissionErrorKey('toString')).toBeNull()
    expect(permissionErrorKey(undefined)).toBeNull()
  })
})
