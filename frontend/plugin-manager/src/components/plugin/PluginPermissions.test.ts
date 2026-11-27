// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { AxiosError, AxiosHeaders } from 'axios'
import { ElMessage } from 'element-plus'
import PluginPermissions from './PluginPermissions.vue'
import type { PermissionPanel } from '@/api/pluginPermissions'
import { panel, row } from '@/utils/permissionTab.fixtures'

const api = vi.hoisted(() => ({
  getPermissionPanel: vi.fn(),
  getPermissionCatalog: vi.fn(),
  getPermissionAuditHistory: vi.fn(),
  getPendingPermissions: vi.fn(),
  decidePermission: vi.fn(),
  clearPermission: vi.fn(),
}))
const pluginStore = vi.hoisted(() => ({
  status: 'running',
  getPluginById: vi.fn(() => ({ id: 'demo', status: pluginStore.status })),
  stop: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/api/pluginPermissions', () => api)
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => pluginStore }))
vi.mock('@/i18n', () => ({
  i18n: { global: { t: (key: string) => key } },
  localeLoadState: { pending: null },
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key} ${JSON.stringify(params)}` : key,
    te: (key: string) => !key.includes('system_example_future'),
  }),
}))

const MEMORY = 'plugin_sdk.main_agent.memory:read'
const PUSH = 'plugin_sdk.main_agent.messages:push'
const NET = 'system.network:connect'

const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}
async function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(PluginPermissions, { pluginId: 'demo' })
  app.use(createPinia())
  app.component(
    'el-button',
    defineComponent({
      props: { disabled: Boolean, loading: Boolean, type: String },
      emits: ['click'],
      setup: (props, { slots, emit, attrs }) => () =>
        h(
          'button',
          { ...attrs, disabled: props.disabled || props.loading, 'data-type': props.type, onClick: () => emit('click') },
          slots.default?.(),
        ),
    }),
  )
  app.component(
    'el-switch',
    defineComponent({
      props: { modelValue: Boolean, disabled: Boolean, loading: Boolean },
      emits: ['update:modelValue'],
      setup: (props, { emit, attrs }) => () =>
        h('input', {
          ...attrs,
          type: 'checkbox',
          checked: props.modelValue,
          disabled: props.disabled,
          onChange: () => emit('update:modelValue', !props.modelValue),
        }),
    }),
  )
  app.mount(root)
  apps.push(app)
  await flush()
  return root
}
const q = (root: HTMLElement, test: string) => root.querySelector(`[data-test="${test}"]`) as HTMLElement | null

function serve(data: PermissionPanel, extras: { history?: object; lostAt?: string | null; pending?: object } = {}) {
  api.getPermissionPanel.mockResolvedValue(data)
  api.getPermissionCatalog.mockResolvedValue({
    tiers: [],
    permissions: {},
    blind_spot_tiers: {},
    privacy_groups: [
      { group: 'memory', permissions: [MEMORY], tier: 'high', partial_block: true },
      { group: 'network', permissions: [NET], tier: 'high', partial_block: false },
    ],
  })
  api.getPermissionAuditHistory.mockResolvedValue({
    history: extras.history ?? {
      since: '2026-10-01T00:00:00Z',
      covers_30d: true,
      last_use_since: '2026-10-01T00:00:00Z',
      last_use_covers_30d: true,
    },
    lost_at: extras.lostAt ?? null,
  })
  api.getPendingPermissions.mockResolvedValue(extras.pending ?? { first_use: [], incomplete: [] })
}
function failure(status: number, code: string) {
  const headers = new AxiosHeaders({ 'X-Error-Code': code })
  return new AxiosError('x', 'ERR', undefined, undefined, {
    status, statusText: '', headers, config: { headers }, data: { detail: { code } },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  pluginStore.status = 'running'
  vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: () => {} }) as never)
})
afterEach(() => {
  for (const app of apps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('PluginPermissions tab', () => {
  it('shows the counts and the three reconcile states, with empty cells on the missing side', async () => {
    serve(panel({
      counts: { total: 3, granted: 1, denied: 0, undecided: 2, undeclared_found: 1 },
      rows: [
        row(),
        row({ permission: 'system.env:read', declared: null, reconcile: 'undeclared', state: 'undecided', condition: null }),
        row({ permission: 'system.fs:read', tier: 'medium', reconcile: 'unscanned', scan: null }),
      ],
    }))
    const root = await mount()
    const summary = q(root, 'summary')!.textContent!
    expect(summary).toContain('1 / 3')
    expect(summary).toContain('pluginConsent.tab.summary.undecided {"n":2}')
    expect(summary).toContain('pluginConsent.tab.summary.undeclared {"n":1}')
    expect(q(root, `row-${NET}`)!.querySelector('.perm-mark')!.textContent).toBe('=')
    const env = q(root, 'row-system.env:read')!
    expect(env.querySelector('.perm-mark')!.textContent).toBe('≠')
    expect(env.querySelector('[data-test="left-empty"]')!.textContent).toContain('pluginConsent.tab.notDeclared')
    expect(env.textContent).toContain('pluginConsent.tab.undeclaredWarning')
    const fs = q(root, 'row-system.fs:read')!
    expect(fs.querySelector('.perm-mark')!.textContent).toBe('?')
    expect(fs.querySelector('[data-test="right-empty"]')!.textContent).toContain('pluginConsent.tab.scanNone')
  })

  it('says the declaration cannot be read and lists the tab as incomplete', async () => {
    serve(panel({
      manifest: 'unavailable',
      rows: [row({ declared: null, reconcile: null, anytime_allowed: null })],
    }))
    const root = await mount()
    expect(q(root, 'notice-unavailable')).not.toBeNull()
    expect(q(root, 'incomplete')).not.toBeNull()
    expect(q(root, 'left-empty')!.textContent).toContain('pluginConsent.tab.manifestUnavailableCell')
    // "Any time" cannot be chosen while the declaration is unknown.
    const select = q(root, `condition-select-${NET}`) as HTMLSelectElement
    expect(select.disabled).toBe(true)
    expect(q(root, `condition-lock-${NET}`)!.textContent).toBe('pluginConsent.tab.condition.lock.unavailable')
  })

  it('treats an unusable manifest as no declaration and says why', async () => {
    serve(panel({ manifest: 'unusable', rows: [row({ anytime_allowed: false })] }))
    const root = await mount()
    expect(q(root, 'notice-unusable')).not.toBeNull()
    expect(q(root, 'incomplete')).toBeNull()
    expect(q(root, 'left-empty')).not.toBeNull()
    expect(q(root, `switch-${NET}`)).toBeNull()
    expect(q(root, `allow-${NET}`)).not.toBeNull()
    expect(q(root, `condition-lock-${NET}`)!.textContent).toBe('pluginConsent.tab.condition.lock.unusable')
  })

  it('does not show "when it may" for a required item', async () => {
    serve(panel({ rows: [row({ declared: { ...row().declared!, level: 'require' } })] }))
    const root = await mount()
    expect(q(root, `condition-${NET}`)).toBeNull()
  })

  it('shows "when it may" with the self-reported tag and saves a change', async () => {
    serve(panel())
    api.decidePermission.mockResolvedValue(panel({ rows: [row({ condition: 'anytime' })] }))
    const root = await mount()
    const condition = q(root, `condition-${NET}`)!
    expect(condition.textContent).toContain('pluginConsent.tab.condition.selfReported')
    const select = q(root, `condition-select-${NET}`) as HTMLSelectElement
    expect(select.disabled).toBe(false)
    select.value = 'anytime'
    select.dispatchEvent(new Event('change'))
    await flush()
    expect(api.decidePermission).toHaveBeenCalledWith('demo', NET, 'granted', 'anytime')
  })

  it('does not let "any time" be chosen when anytime_allowed is false', async () => {
    serve(panel({ rows: [row({ anytime_allowed: false })] }))
    const root = await mount()
    expect((q(root, `condition-select-${NET}`) as HTMLSelectElement).disabled).toBe(true)
    expect(q(root, `condition-lock-${NET}`)!.textContent).toBe('pluginConsent.tab.condition.lock.notDeclared')
  })

  it.each([
    [503, 'PLUGIN_MANIFEST_UNAVAILABLE', 'pluginConsent.tab.errors.manifestUnavailable'],
    [422, 'PLUGIN_MANIFEST_UNUSABLE', 'pluginConsent.tab.errors.manifestUnusable'],
  ])('reports %s %s when changing "when it may"', async (status, code, message) => {
    serve(panel())
    api.decidePermission.mockRejectedValue(failure(status, code))
    const root = await mount()
    const select = q(root, `condition-select-${NET}`) as HTMLSelectElement
    select.value = 'anytime'
    select.dispatchEvent(new Event('change'))
    await flush()
    expect(ElMessage.error).toHaveBeenCalledWith(message)
  })

  it('allows, refuses, and returns a disclosure-only item to undecided', async () => {
    const first = panel({
      rows: [
        row({ state: 'undecided', condition: null }),
        row({ permission: 'system.env:read', declared: null, reconcile: 'undeclared', state: 'undecided' }),
      ],
    })
    serve(first)
    api.decidePermission.mockResolvedValue(first)
    api.clearPermission.mockResolvedValue(first)
    const root = await mount()
    expect(q(root, `row-${NET}`)!.querySelector('[data-test="undecided"]')).not.toBeNull()
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    expect(api.decidePermission).toHaveBeenLastCalledWith('demo', NET, 'granted', 'in_use')

    serve(panel({ rows: [row({ permission: PUSH, tier: 'medium', enforced: false, state: 'granted' })] }))
    const second = await mount()
    expect(q(second, `row-${PUSH}`)!.textContent).toContain('pluginConsent.tab.recordOnly')
    q(second, `switch-${PUSH}`)!.dispatchEvent(new Event('change'))
    await flush()
    expect(api.clearPermission).toHaveBeenCalledWith('demo', PUSH)
    expect(api.decidePermission).not.toHaveBeenCalledWith('demo', PUSH, 'denied', expect.anything())

    q(root, 'refuse-system.env:read')!.click()
    await flush()
    expect(api.decidePermission).toHaveBeenLastCalledWith('demo', 'system.env:read', 'denied', 'in_use')
    q(root, 'allow-system.env:read')!.click()
    await flush()
    expect(api.decidePermission).toHaveBeenLastCalledWith('demo', 'system.env:read', 'granted', 'in_use')
  })

  it('shows nothing about use when uses are not recorded', async () => {
    serve(panel({ rows: [row({ permission: 'system.env:read', enforced: false, usage_tracked: false, state: 'undecided' })] }))
    const root = await mount()
    expect(q(root, 'usage-system.env:read')).toBeNull()
    expect(root.textContent).toContain('pluginConsent.tab.notEnforcedNotTracked')
    expect(root.textContent).not.toContain('unused30d')
  })

  it('writes "unused since X" when the history covers less than 30 days', async () => {
    serve(panel({ rows: [row({ state: 'undecided' })] }), {
      history: { since: null, covers_30d: false, last_use_since: '2026-11-20T00:00:00Z', last_use_covers_30d: false },
    })
    const root = await mount()
    const line = q(root, `usage-${NET}`)!
    expect(line.textContent).toContain('pluginConsent.tab.unusedSince {"date":"2026-11-20"}')
    expect(line.querySelector('[data-test="unused30d"]')).toBeNull()
  })

  it('tags "unused for 30 days" and the kept earlier approval', async () => {
    serve(panel({ rows: [row({ unused_30d: true, legacy_any: true })] }))
    const root = await mount()
    expect(q(root, `usage-${NET}`)!.querySelector('[data-test="unused30d"]')).not.toBeNull()
    expect(q(root, 'legacy')).not.toBeNull()
  })

  it('marks a last use that would have been blocked as recorded only', async () => {
    serve(panel({ rows: [row({ last_used: { at: '2026-11-01T10:00:00Z', origin_kind: null, outcome: 'would_block' } })] }))
    const root = await mount()
    expect(q(root, `usage-${NET}`)!.textContent).toContain('pluginConsent.tab.wouldBlock')
  })

  it('shows "at least N times" for a lower-bound count of uses while undecided', async () => {
    serve(panel({ rows: [row({ state: 'undecided' })] }), {
      pending: {
        incomplete: [],
        first_use: [{ plugin_id: 'demo', permission: NET, at: '2026-11-27T10:00:00Z', count: 4, count_is_lower_bound: true }],
      },
    })
    const root = await mount()
    expect(q(root, `first-use-${NET}`)!.textContent).toContain('pluginConsent.tab.firstUseAtLeast {"n":4}')
  })

  it('notes that memory reads cannot all be blocked yet', async () => {
    serve(panel({ rows: [row({ permission: MEMORY }), row()] }))
    const root = await mount()
    expect(q(root, `partial-${MEMORY}`)).not.toBeNull()
    expect(q(root, `partial-${NET}`)).toBeNull()
  })

  it('shows a source reset whose time is not saved yet', async () => {
    serve(panel({ grants_reset: { at: null, reason: 'source_changed' } }))
    const root = await mount()
    expect(q(root, 'notice-reset')!.textContent).toBe('pluginConsent.tab.notices.grantsResetPending')
  })

  it('shows a source reset with its date', async () => {
    serve(panel({ grants_reset: { at: '2026-11-25T08:00:00Z', reason: 'source_changed' } }))
    const root = await mount()
    expect(q(root, 'notice-reset')!.textContent).toContain('"date":"2026-11-25"')
  })

  it('says the source is not confirmed in the developer details', async () => {
    serve(panel({ source: { channel: 'local', fingerprint8: null, confirmed: false } }))
    const root = await mount()
    expect(q(root, 'source-unconfirmed')).not.toBeNull()
    serve(panel())
    const confirmed = await mount()
    expect(q(confirmed, 'source-unconfirmed')).toBeNull()
  })

  it('expands the scan side per row and all at once, without line numbers', async () => {
    serve(panel({
      developer: {
        findings: { [NET]: [{ file: 'net.py', line: 42, method: 'httpx.get', basis: 'direct' }] },
        blind_spots: [],
        not_analyzed: [],
        manifest_permissions: null,
      },
    }))
    const root = await mount()
    expect(q(root, 'scan-files')).toBeNull()
    q(root, `expand-${NET}`)!.click()
    await flush()
    const files = q(root, 'scan-files')!.textContent!
    expect(files).toContain('net.py')
    expect(files).not.toContain('42')
    q(root, 'expand-all')!.click()
    await flush()
    expect(q(root, 'scan-files')).toBeNull()
  })

  it('sums blind spots per file and counts files left out of the scan', async () => {
    serve(panel({
      blind_spots: {
        files: [{ file: 'loader.py', count: 2, types: { dynamic_import: 2 }, high_risk: false }],
        counted: 2,
        not_analyzed_files: 6,
      },
    }))
    const root = await mount()
    const spots = q(root, 'blind-spots')!.textContent!
    expect(spots).toContain('loader.py')
    expect(spots).toContain('pluginConsent.tab.blindSpots.types.dynamic_import {"n":2}')
    expect(spots).toContain('pluginConsent.tab.blindSpots.notAnalyzed {"n":6}')
  })

  it('folds a tier the server marks collapsed until clicked', async () => {
    serve(panel({
      rows: [row({ permission: 'plugin_sdk.models:invoke', tier: 'low' })],
      tiers: [
        { tier: 'high', count: 0, collapsed: false },
        { tier: 'medium', count: 0, collapsed: false },
        { tier: 'low', count: 1, collapsed: true },
      ],
    }))
    const root = await mount()
    expect(q(root, 'row-plugin_sdk.models:invoke')).toBeNull()
    q(root, 'folded-low')!.click()
    await flush()
    expect(q(root, 'row-plugin_sdk.models:invoke')).not.toBeNull()
  })

  it('stays usable when the optional sources fail', async () => {
    serve(panel())
    api.getPermissionCatalog.mockRejectedValue(new Error('x'))
    api.getPermissionAuditHistory.mockRejectedValue(new Error('x'))
    api.getPendingPermissions.mockRejectedValue(new Error('x'))
    const root = await mount()
    expect(q(root, `row-${NET}`)).not.toBeNull()
    expect(q(root, `usage-${NET}`)).toBeNull()
  })

  it('offers a retry when the panel cannot be loaded', async () => {
    serve(panel())
    api.getPermissionPanel.mockRejectedValueOnce(new Error('x'))
    const root = await mount()
    expect(root.textContent).toContain('pluginConsent.tab.loadFailed')
    root.querySelector('button')!.click()
    await flush()
    expect(q(root, `row-${NET}`)).not.toBeNull()
  })
})
