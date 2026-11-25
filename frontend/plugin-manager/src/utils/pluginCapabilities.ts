import { isAxiosError } from 'axios'
import type {
  AnalysisBlindSpot,
  CapabilityAnalysis,
  ConsentSnapshot,
  PluginConsent,
  PermissionFinding,
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

function findingLocations(findings: PermissionFinding[] | null | undefined): string[] {
  return (findings ?? []).map((row) => `${row.file}:${row.line}`)
}

export interface CapabilityOrigin {
  /** Declared by the author, found by the scan, or null when the report has no permission entry. */
  declared: boolean | null
  locations: string[]
}

/**
 * Origin of an entry in the capability list. An earlier name (`memory.read`)
 * also carries its permission entry's declaration and code locations, since
 * that entry is not listed again under "used permissions".
 */
export function capabilityOrigin(
  analysis: CapabilityAnalysis | null | undefined,
  capability: string,
): CapabilityOrigin {
  const evidence = (analysis?.evidence ?? [])
    .filter((entry) => entry.capability === capability)
    .map((entry) => `${entry.file}:${entry.line}`)
  const id = Object.keys(LEGACY_CAPABILITY_NAMES).find(
    (key) => LEGACY_CAPABILITY_NAMES[key] === capability,
  )
  const entry = id ? analysis?.permissions?.[id] : undefined
  const found = findingLocations(entry?.findings)
  const declared = Boolean(entry?.declared)
  return {
    declared: declared || found.length ? declared : null,
    locations: [...new Set([...evidence, ...found])],
  }
}

/**
 * Permissions the analysis reports as declared or found in code. Ids already
 * listed under their earlier capability name are left to that list, which
 * shows their origin through `capabilityOrigin`.
 */
export function usedPermissions(analysis: CapabilityAnalysis | null | undefined): UsedPermission[] {
  if (!analysis?.permissions) return []
  const listed = new Set(analysis.capabilities ?? [])
  return Object.entries(analysis.permissions)
    .filter(([id]) => !listed.has(LEGACY_CAPABILITY_NAMES[id] ?? ''))
    .map(([id, item]) => ({
      id,
      declared: Boolean(item?.declared),
      locations: [...new Set(findingLocations(item?.findings))],
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

/** The list's "pending confirmation" mark: something the user can confirm. */
export function needsPermissionReview(consent: PluginConsentSummary | null | undefined): boolean {
  return consent?.state === 'confirm'
}

/**
 * The list's "partly unconfirmable" mark: the analysis has blind spots, or it
 * was partial or failed and so did not check all of the code.
 */
export function isPartlyUnconfirmed(consent: PluginConsentSummary | null | undefined): boolean {
  return (consent?.blind_spots ?? 0) > 0 || Boolean(consent?.analysis_incomplete)
}
