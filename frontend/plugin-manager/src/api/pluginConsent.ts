import { del, get } from './index'
import type { PluginScanRecord } from './pluginScan'

export type ConsentState = 'analyzing' | 'confirm' | 'clear'
export type ConsentDecision = 'approved' | 'denied'
export interface PluginConsent extends PluginScanRecord {
  decision: ConsentDecision | null
  decided_at: string | null
  state: ConsentState
}
/** The plugin list's per-plugin summary (``consent``); only the scan marks are read. */
export interface PluginConsentSummary {
  state: ConsentState
  decision: ConsentDecision | null
  upgraded: boolean
  /** Blind spots of the cached analysis; older servers omit it. */
  blind_spots?: number
  /** The analysis was partial or failed, so some code was not checked. */
  analysis_incomplete?: boolean
}
export function getPluginConsent(id: string) {
  return get<PluginConsent>(`/plugin-consent/plugins/${encodeURIComponent(id)}`, {
    suppressErrorMessage: true,
  })
}
export function revokePluginConsent(id: string) {
  return del<PluginConsent>(`/plugin-consent/plugins/${encodeURIComponent(id)}`, {
    suppressErrorMessage: true,
  })
}
