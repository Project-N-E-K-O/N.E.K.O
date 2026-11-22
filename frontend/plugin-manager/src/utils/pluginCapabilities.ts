import { isAxiosError } from 'axios'
import type { ConsentSnapshot, PluginConsent } from '@/api/pluginConsent'
import { readErrorCode } from '@/utils/request'

/** Shared capability/identity helpers mirroring server consent policy. */

export const ANALYZING_STATUSES = new Set(['queued', 'running', 'unavailable'])
export const INCOMPLETE_STATUSES = new Set(['partial', 'failed'])

/** Earlier names (`memory.read`) and permission ids (`plugin_sdk.main_agent.memory:read`) alike. */
export function capabilityMessageKey(capability: string, field: 'title' | 'description') {
  return `pluginConsent.capabilities.${capability.replace(/[.:]/g, '_')}.${field}`
}

export function consentSnapshot(consent: PluginConsent): ConsentSnapshot {
  return {
    version: consent.version,
    unconfirmed: consent.unconfirmed,
    analysis_status: consent.analysis.status,
  }
}

export function isConsentChanged(error: unknown): boolean {
  return isAxiosError(error) && readErrorCode(error) === 'PLUGIN_CONSENT_CHANGED'
}
