import { isAxiosError } from 'axios'
import type {
  AnalysisBlindSpot,
  CapabilityAnalysis,
  ConsentSnapshot,
  PluginConsent,
  PluginConsentSummary,
} from '@/api/pluginConsent'
import { readErrorCode } from '@/utils/request'
import { permissionErrorKey } from '@/utils/pluginPermissionErrors'

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

/** Message for a failed consent decision: a permission-store failure says what to do. */
export function consentSaveErrorKey(error: unknown): string {
  return (isAxiosError(error) && permissionErrorKey(readErrorCode(error))) || 'pluginConsent.saveFailed'
}

/** Permission ids that also have an earlier capability name (catalog.py `legacy_name`). */
const LEGACY_CAPABILITY_NAMES: Record<string, string> = {
  'plugin_sdk.main_agent.memory:read': 'memory.read',
  'plugin_sdk.main_agent.conversations:read': 'conversations.read',
  'plugin_sdk.main_agent.messages:push': 'messages.push',
}

export interface UsedPermission {
  id: string
  declared: boolean
  locations: string[]
}

/**
 * Permissions the analysis reports as declared or found in code. Ids already
 * listed under their earlier capability name are left to that list.
 */
export function usedPermissions(analysis: CapabilityAnalysis | null | undefined): UsedPermission[] {
  if (!analysis?.permissions) return []
  const listed = new Set(analysis.capabilities ?? [])
  return Object.entries(analysis.permissions)
    .filter(([id]) => !listed.has(LEGACY_CAPABILITY_NAMES[id] ?? ''))
    .map(([id, item]) => ({
      id,
      declared: Boolean(item?.declared),
      locations: [...new Set((item?.findings ?? []).map((row) => `${row.file}:${row.line}`))],
    }))
    .filter((item) => item.declared || item.locations.length)
    .sort((a, b) => a.id.localeCompare(b.id))
}

export function blindSpots(analysis: CapabilityAnalysis | null | undefined): AnalysisBlindSpot[] {
  return Array.isArray(analysis?.blind_spots) ? analysis.blind_spots : []
}

/** `file:line`, or null for a blind spot not tied to a file (e.g. a scan cut short). */
export function blindSpotLocation(spot: AnalysisBlindSpot): string | null {
  if (!spot.file) return null
  return spot.line != null ? `${spot.file}:${spot.line}` : spot.file
}

/**
 * "Nothing found" is only true for a finished analysis that saw every file
 * and found no capability, no permission and no blind spot.
 */
export function analysisFoundNothing(analysis: CapabilityAnalysis | null | undefined): boolean {
  return Boolean(
    analysis
      && analysis.status === 'completed'
      && analysis.permissions
      && !analysis.capabilities.length
      && !usedPermissions(analysis).length
      && !blindSpots(analysis).length,
  )
}

/** The list's "pending review" mark: something to confirm, or blind spots. */
export function needsPermissionReview(consent: PluginConsentSummary | null | undefined): boolean {
  if (!consent) return false
  return consent.state === 'confirm' || (consent.blind_spots ?? 0) > 0
}
