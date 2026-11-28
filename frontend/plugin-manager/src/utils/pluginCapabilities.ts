import { isAxiosError } from 'axios'
import type { PluginConsentSummary } from '@/api/pluginConsent'
import { readErrorCode } from '@/utils/request'
import { permissionErrorKey } from '@/utils/pluginPermissionErrors'

/** Permission labels, and the list's mark for code the scan could not check. */

/** Earlier names (`memory.read`) and permission ids (`plugin_sdk.main_agent.memory:read`) alike. */
export function capabilityMessageKey(capability: string, field: 'title' | 'description') {
  return `pluginConsent.capabilities.${capability.replace(/[.:]/g, '_')}.${field}`
}

/** Message for a failed consent decision: a permission-store failure says what to do. */
export function consentSaveErrorKey(error: unknown): string {
  return (isAxiosError(error) && permissionErrorKey(readErrorCode(error))) || 'pluginConsent.saveFailed'
}

/**
 * The list's "partly unconfirmable" mark: the analysis has blind spots, or it
 * was partial or failed and so did not check all of the code.
 */
export function isPartlyUnconfirmed(consent: PluginConsentSummary | null | undefined): boolean {
  return (consent?.blind_spots ?? 0) > 0 || Boolean(consent?.analysis_incomplete)
}
