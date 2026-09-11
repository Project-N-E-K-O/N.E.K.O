import { describe, expect, it } from 'vitest'

import { resolvePluginInstallErrorKey } from './pluginInstallError'

describe('resolvePluginInstallErrorKey', () => {
  it.each([
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
  ])('maps %s to a localized key', (code, key) => {
    expect(resolvePluginInstallErrorKey(code)).toBe(key)
  })

  it('uses a localized generic message for unknown backend codes', () => {
    expect(resolvePluginInstallErrorKey('backend_english_detail')).toBe('market.installFailed')
  })
})
