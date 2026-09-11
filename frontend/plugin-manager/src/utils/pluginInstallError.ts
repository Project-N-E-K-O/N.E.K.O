const INSTALL_ERROR_KEYS: Record<string, string> = {
  version_already_at_target: 'market.upgradeAlreadyAtTarget',
  upgrade_target_not_greater: 'market.upgradeTargetNotGreater',
  plugin_not_installed_for_upgrade: 'market.pluginNotInstalled',
  upgrade_rollback_completed: 'market.upgradeRollback',
  override_rollback_completed: 'market.upgradeRollback',
  plugin_upgrade_rolled_back: 'market.upgradeRollback',
  upgrade_rollback_incomplete: 'market.rollbackIncomplete',
  override_rollback_incomplete: 'market.rollbackIncomplete',
  override_source_changed: 'market.overrideSourceChanged',
  override_confirmation_changed: 'market.confirmationChanged',
  override_confirmation_required: 'market.confirmationRequired',
  override_target_exists: 'market.autoUpgradeBlocked',
  override_start_failed: 'market.overrideStartFailed',
  plugin_exec_state_root_collision: 'market.execStateRootCollision',
  directory_identity_conflict: 'market.directoryIdentityConflict',
  identity_conflict: 'market.directoryIdentityConflict',
  bundle_conflict: 'market.bundleConflict',
  legacy_plugin_present: 'market.legacyPluginConflict',
  package_hash_mismatch: 'market.packageHashMismatch',
  download_failed: 'market.downloadFailed',
  market_list_fetch_failed: 'market.marketListFetchFailed',
  lock_write_failed: 'market.lockWriteFailed',
  install_source_read_only: 'market.lockWriteFailed',
  profile_write_failed: 'market.profileWriteFailed',
  unsafe_profile_path: 'market.unsafeProfilePath',
  plugin_identity_mismatch: 'market.packageIdentityMismatch',
  identity_mismatch: 'market.packageIdentityMismatch',
  plugin_upgrade_plan_changed: 'market.confirmationChanged',
  plugin_upgrade_confirmation_required: 'market.confirmationRequired',
  manual_takeover_confirmation_required: 'market.confirmationRequired',
  manual_takeover_plan_changed: 'market.confirmationChanged',
  manual_takeover_source_changed: 'market.confirmationChanged',
  manual_takeover_confirmation_not_applicable: 'market.autoUpgradeBlocked',
  plugin_replacement_source_unsupported: 'market.autoUpgradeBlocked',
  install_cancelled: 'market.installCancelled',
  install_failed: 'market.installFailed',
  plugin_install_blocked: 'market.autoUpgradeBlocked',
  plugin_builtin_override_market_required: 'market.autoUpgradeBlocked',
  plugin_builtin_override_blocked: 'market.autoUpgradeBlocked',
}

export function normalizePluginInstallErrorCode(code: unknown): string {
  return String(code || '')
    .trim()
    .toLowerCase()
}

export function resolvePluginInstallErrorKey(code: unknown): string {
  return INSTALL_ERROR_KEYS[normalizePluginInstallErrorCode(code)] || 'market.installFailed'
}
