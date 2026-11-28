/** Panel data for permission tab tests. */
import type { PanelRow, PermissionPanel } from '@/api/pluginPermissions'

export function row(over: Partial<PanelRow> = {}): PanelRow {
  return {
    permission: 'system.network:connect',
    tier: 'high',
    reconcile: 'matched',
    declared: { level: 'ask', reason: 'fetch', background: false, background_reason: null, hosts: [] },
    scan: { files: 1, sites: 2, basis: 'direct', possible_only: false },
    state: 'granted',
    condition: 'in_use',
    condition_basis: 'plugin_reported',
    anytime_allowed: true,
    legacy_any: false,
    enforced: true,
    usage_tracked: true,
    last_used: null,
    unused_30d: null,
    ...over,
  }
}

export function panel(over: Partial<PermissionPanel> = {}): PermissionPanel {
  return {
    plugin_id: 'demo',
    version: '1.0.0',
    source: { channel: 'market', fingerprint8: 'abcdef12', confirmed: true },
    grants_reset: null,
    checks_disabled: false,
    manifest: 'read',
    analysis: { status: 'completed', analyzer_version: '3', analyzed_at: '2026-11-20T10:00:00Z', declarations: 'declared' },
    counts: { total: 1, granted: 1, denied: 0, undecided: 0, undeclared_found: 0 },
    rows: [row()],
    tiers: [
      { tier: 'high', count: 1, collapsed: false },
      { tier: 'medium', count: 0, collapsed: false },
      { tier: 'low', count: 0, collapsed: false },
    ],
    blind_spots: { files: [], counted: 0, not_analyzed_files: 0 },
    developer: { findings: {}, blind_spots: [], not_analyzed: [], manifest_permissions: null },
    ...over,
  }
}
