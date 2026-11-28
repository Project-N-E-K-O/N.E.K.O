import type {
  PrivacyGroupDetail,
  PrivacyGroupItem,
  PrivacyGroupPlugin,
  PrivacyOverview,
  UsageHistory,
} from '@/api/pluginPermissions'

export const MEMORY = 'plugin_sdk.main_agent.memory:read'
export const NET = 'system.network:connect'
export const SPAWN = 'system.process:spawn'
export const ENV = 'system.env:read'
export const PUSH = 'plugin_sdk.main_agent.messages:push'

export const covered: UsageHistory = {
  since: '2026-09-01T00:00:00Z',
  covers_30d: true,
  last_use_since: '2026-09-01T00:00:00Z',
  last_use_covers_30d: true,
}

export function overview(overrides: Partial<PrivacyOverview> = {}): PrivacyOverview {
  return {
    checks_disabled: false,
    history: covered,
    pending: { to_confirm: 0, upgraded: 0, inferred: 0, first_use: 0 },
    incomplete: [],
    groups: [
      { group: 'memory', tier: 'high', plugins: 3, latest: { plugin_id: 'weather', at: '2026-11-28T06:00:00Z' }, partial_block: true },
      { group: 'conversations', tier: 'high', plugins: 1, latest: null, partial_block: true },
      { group: 'network', tier: 'high', plugins: 0, latest: null, partial_block: false },
      { group: 'fs_read', tier: 'medium', plugins: 2, latest: null, partial_block: false },
      { group: 'listen', tier: 'low', plugins: 0, latest: null, partial_block: false },
      { group: 'models', tier: 'low', plugins: 1, latest: null, partial_block: false },
    ],
    combos: [],
    ...overrides,
  }
}

export function item(overrides: Partial<PrivacyGroupItem> = {}): PrivacyGroupItem {
  return {
    permission: MEMORY,
    state: 'granted',
    condition: 'in_use',
    condition_basis: 'self_reported',
    legacy_any: false,
    enforced: true,
    usage_tracked: true,
    last_used: null,
    unused_30d: false,
    uses_7d: 0,
    ...overrides,
  }
}

export function plugin(overrides: Partial<PrivacyGroupPlugin> = {}): PrivacyGroupPlugin {
  return {
    plugin_id: 'weather',
    name: 'Weather',
    installed: true,
    has_permission: true,
    items: [item()],
    ...overrides,
  }
}

export function detail(overrides: Partial<PrivacyGroupDetail> = {}): PrivacyGroupDetail {
  return {
    group: 'memory',
    tier: 'high',
    partial_block: true,
    checks_disabled: false,
    history: covered,
    incomplete: [],
    plugins: [plugin()],
    recent: [],
    ...overrides,
  }
}
