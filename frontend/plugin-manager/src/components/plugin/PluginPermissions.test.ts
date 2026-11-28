// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { AxiosError, AxiosHeaders } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
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
const consentStore = vi.hoisted(() => ({
  details: {} as Record<string, unknown>,
  analyze: vi.fn().mockResolvedValue(undefined),
  load: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/stores/pluginConsentState', () => ({ usePluginConsentState: () => consentStore }))
// The earlier plugin-wide consent line has its own tests.
vi.mock('./PluginLegacyConsentBanner.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      emits: ['changed'],
      setup: (_, { emit }) => () => h('button', { 'data-test': 'banner-changed', onClick: () => emit('changed') }),
    }),
  }
})
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

/** Noon of a local calendar day, so the date reads the same in every time zone. */
const localDay = (year: number, month: number, day: number) => new Date(year, month - 1, day, 12).toISOString()
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
  consentStore.details = {}
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
    expect((select.querySelector('option[value="anytime"]') as HTMLOptionElement).disabled).toBe(true)
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
    const select = q(root, `condition-select-${NET}`) as HTMLSelectElement
    expect((select.querySelector('option[value="anytime"]') as HTMLOptionElement).disabled).toBe(true)
    expect((select.querySelector('option[value="in_use"]') as HTMLOptionElement).disabled).toBe(false)
    expect(q(root, `condition-lock-${NET}`)!.textContent).toBe('pluginConsent.tab.condition.lock.notDeclared')
  })

  it('still narrows an earlier "any time" to "while in use" once it is no longer allowed', async () => {
    serve(panel({ rows: [row({ condition: 'anytime', anytime_allowed: false })] }))
    api.decidePermission.mockResolvedValue(panel({ rows: [row({ anytime_allowed: false })] }))
    const root = await mount()
    const select = q(root, `condition-select-${NET}`) as HTMLSelectElement
    expect(select.disabled).toBe(false)
    select.value = 'in_use'
    select.dispatchEvent(new Event('change'))
    await flush()
    expect(api.decidePermission).toHaveBeenCalledWith('demo', NET, 'granted', 'in_use')
  })

  it('reads the declaration again while it is held, and stops once it is read', async () => {
    vi.useFakeTimers()
    try {
      const held = panel({ manifest: 'unavailable', rows: [row({ declared: null, reconcile: null, anytime_allowed: null })] })
      serve(held)
      api.getPermissionPanel.mockReset().mockResolvedValueOnce(held).mockResolvedValue(panel())
      const root = await mount()
      expect(q(root, 'notice-unavailable')).not.toBeNull()
      await vi.advanceTimersByTimeAsync(5000)
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
      expect(q(root, 'notice-unavailable')).toBeNull()
      await vi.advanceTimersByTimeAsync(60000)
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('reads the shared plugin-wide consent again after a decision, not after a failed one', async () => {
    serve(panel({ rows: [row({ state: 'undecided', condition: null })] }))
    api.decidePermission.mockRejectedValueOnce(new Error('x')).mockResolvedValue(panel())
    const root = await mount()
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    expect(consentStore.load).not.toHaveBeenCalled()
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    expect(consentStore.load).toHaveBeenCalledWith('demo')
  })

  it('drops the counts of uses while undecided when a refresh cannot read them', async () => {
    serve(panel({ rows: [row({ state: 'undecided', condition: null })] }), {
      pending: {
        incomplete: [],
        first_use: [{ plugin_id: 'demo', permission: NET, at: localDay(2026, 11, 27), count: 4, count_is_lower_bound: false }],
      },
    })
    const root = await mount()
    expect(q(root, `first-use-${NET}`)).not.toBeNull()
    api.getPendingPermissions.mockRejectedValue(new Error('offline'))
    q(root, 'banner-changed')!.click()
    await flush()
    expect(q(root, `first-use-${NET}`)).toBeNull()
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
      history: { since: null, covers_30d: false, last_use_since: localDay(2026, 11, 20), last_use_covers_30d: false },
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
    expect(q(root, 'notice-reset')!.textContent!.trim()).toBe('pluginConsent.tab.notices.grantsResetPending')
  })

  it('shows a source reset with its date', async () => {
    serve(panel({ grants_reset: { at: localDay(2026, 11, 25), reason: 'source_changed' } }))
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

  it('says there are no blind spot files when only unscanned files are counted', async () => {
    serve(panel({ blind_spots: { files: [], counted: 0, not_analyzed_files: 2 } }))
    const root = await mount()
    expect(q(root, 'blind-spots-none')!.textContent).toBe('pluginConsent.tab.blindSpots.none')
    expect(q(root, 'blind-spots')!.textContent).toContain('pluginConsent.tab.blindSpots.notAnalyzed {"n":2}')
    serve(panel({
      blind_spots: { files: [{ file: 'a.py', count: 1, types: { code_exec: 1 }, high_risk: false }], counted: 1, not_analyzed_files: 0 },
    }))
    expect(q(await mount(), 'blind-spots-none')).toBeNull()
  })

  it('grants a required item "any time", since it shows no condition control', async () => {
    const required = row({ state: 'undecided', condition: null, declared: { ...row().declared!, level: 'require' } })
    serve(panel({ rows: [required] }))
    api.decidePermission.mockResolvedValue(panel({ rows: [required] }))
    const root = await mount()
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    expect(api.decidePermission).toHaveBeenCalledWith('demo', NET, 'granted', 'anytime')
  })

  it('loads again after the plugin-wide consent changed', async () => {
    serve(panel())
    const root = await mount()
    expect(api.getPermissionPanel).toHaveBeenCalledTimes(1)
    q(root, 'banner-changed')!.click()
    await flush()
    expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
  })

  it('loads again while the scan is queued, and stops once it finished', async () => {
    vi.useFakeTimers()
    try {
      serve(panel({ analysis: { ...panel().analysis, status: 'queued' } }))
      api.getPermissionPanel
        .mockReset()
        .mockResolvedValueOnce(panel({ analysis: { ...panel().analysis, status: 'queued' } }))
        .mockResolvedValue(panel())
      const root = await mount()
      expect(q(root, 'notice-scan-pending')).not.toBeNull()
      await vi.advanceTimersByTimeAsync(5000)
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
      expect(q(root, 'notice-scan-pending')).toBeNull()
      await vi.advanceTimersByTimeAsync(20000)
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not let a load answered during a save overwrite it, and keeps polling after a failed save', async () => {
    vi.useFakeTimers()
    try {
      const queued = (state: 'granted' | 'undecided') =>
        panel({ analysis: { ...panel().analysis, status: 'running' }, rows: [row({ state, condition: null })] })
      serve(queued('undecided'))
      const root = await mount()
      let finishSave: (value: PermissionPanel) => void = () => {}
      api.decidePermission.mockImplementation(() => new Promise((resolve) => { finishSave = resolve }))
      q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
      await flush()
      // The poll timer was stopped by the save: nothing loads meanwhile.
      await vi.advanceTimersByTimeAsync(6000)
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(1)
      finishSave(queued('granted'))
      await flush()
      expect((q(root, `switch-${NET}`) as HTMLInputElement).checked).toBe(true)

      api.decidePermission.mockRejectedValue(new Error('x'))
      q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
      await flush()
      await vi.advanceTimersByTimeAsync(5000)
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not say "no blind spot files" after a partial scan', async () => {
    serve(panel({ analysis: { ...panel().analysis, status: 'partial' } }))
    const root = await mount()
    expect(q(root, 'notice-scan-partial')).not.toBeNull()
    expect(q(root, 'blind-spots-none')).toBeNull()
  })

  it('shows the unscanned files after a partial scan without saying there are no blind spots', async () => {
    serve(panel({
      analysis: { ...panel().analysis, status: 'partial' },
      blind_spots: { files: [], counted: 0, not_analyzed_files: 3 },
    }))
    const root = await mount()
    expect(q(root, 'blind-spots')!.textContent).toContain('pluginConsent.tab.blindSpots.notAnalyzed {"n":3}')
    expect(q(root, 'blind-spots-none')).toBeNull()
  })

  it('takes back an approval of the disclosure-only item found by the scan', async () => {
    const pushRow = (state: 'granted' | 'undecided') =>
      row({ permission: PUSH, tier: 'medium', enforced: false, declared: null, reconcile: 'undeclared', state })
    serve(panel({ rows: [pushRow('granted')] }))
    api.clearPermission.mockResolvedValue(panel({ rows: [pushRow('undecided')] }))
    const root = await mount()
    expect(q(root, `refuse-${PUSH}`)).toBeNull()
    q(root, `undecide-${PUSH}`)!.click()
    await flush()
    expect(api.clearPermission).toHaveBeenCalledWith('demo', PUSH)
    expect(q(root, `undecide-${PUSH}`)).toBeNull()
  })

  it('keeps the table when a refresh fails and polls again later, waiting longer', async () => {
    vi.useFakeTimers()
    try {
      const running = panel({ analysis: { ...panel().analysis, status: 'running' } })
      serve(running)
      const root = await mount()
      api.getPermissionPanel.mockRejectedValue(new Error('offline'))
      await vi.advanceTimersByTimeAsync(5000)
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
      expect(q(root, `row-${NET}`)).not.toBeNull()
      expect(root.textContent).not.toContain('pluginConsent.tab.loadFailed')
      // Backoff: the next try waits 10 s, not 5 s.
      await vi.advanceTimersByTimeAsync(5000)
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
      api.getPermissionPanel.mockResolvedValue(panel())
      await vi.advanceTimersByTimeAsync(5000)
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(3)
      expect(q(root, 'notice-scan-pending')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('asks before stopping the plugin, then reports it stopped', async () => {
    serve(panel())
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockRejectedValueOnce('cancel')
    const success = vi.spyOn(ElMessage, 'success').mockImplementation(() => ({ close: () => {} }) as never)
    const root = await mount()
    q(root, 'stop-plugin')!.click()
    await flush()
    expect(confirm).toHaveBeenCalledWith('messages.confirmStop', 'common.confirm', { type: 'warning' })
    expect(pluginStore.stop).not.toHaveBeenCalled()
    confirm.mockResolvedValueOnce('confirm' as never)
    q(root, 'stop-plugin')!.click()
    await flush()
    expect(pluginStore.stop).toHaveBeenCalledWith('demo')
    expect(success).toHaveBeenCalledWith('messages.pluginStopped')
  })

  it('offers to scan again after a failed scan and loads the result', async () => {
    serve(panel({ analysis: { ...panel().analysis, status: 'failed' } }))
    const root = await mount()
    api.getPermissionPanel.mockResolvedValue(panel())
    q(root, 'rescan')!.click()
    await flush()
    expect(consentStore.analyze).toHaveBeenCalledWith('demo')
    expect(q(root, 'notice-scan-failed')).toBeNull()
  })

  it('shows the warnings of the last scan in the developer details', async () => {
    consentStore.details = {
      demo: { analysis: { warnings: [{ code: 'budget_exceeded' }, { code: 'source_unreadable', file: 'a.py' }] } },
    }
    serve(panel())
    const root = await mount()
    const warnings = q(root, 'scan-warnings')!.textContent!
    expect(warnings).toContain('pluginConsent.warnings.budget_exceeded')
    expect(warnings).toContain('pluginConsent.warnings.source_unreadable (a.py)')
  })

  it('reads the last-use coverage again after a decision', async () => {
    const undecided = panel({ rows: [row({ state: 'undecided', condition: null })] })
    serve(undecided)
    api.decidePermission.mockResolvedValue(undecided)
    const root = await mount()
    expect(q(root, `usage-${NET}`)!.querySelector('[data-test="unused30d"]')).not.toBeNull()
    api.getPermissionAuditHistory.mockResolvedValue({
      history: { since: null, covers_30d: false, last_use_since: localDay(2026, 11, 20), last_use_covers_30d: false },
      lost_at: null,
    })
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    expect(api.getPermissionAuditHistory).toHaveBeenCalledTimes(2)
    expect(q(root, `usage-${NET}`)!.textContent).toContain('pluginConsent.tab.unusedSince')
  })

  it('loads again after a save when a consent change asked for a load during it', async () => {
    serve(panel({ rows: [row({ state: 'undecided', condition: null })] }))
    const root = await mount()
    let finishSave: (value: PermissionPanel) => void = () => {}
    api.decidePermission.mockImplementation(() => new Promise((resolve) => { finishSave = resolve }))
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    q(root, 'banner-changed')!.click()
    await flush()
    // Not sent during the save: its answer could arrive later and predate it.
    expect(api.getPermissionPanel).toHaveBeenCalledTimes(1)
    // The consent change granted it; the save answered with an older view.
    api.getPermissionPanel.mockResolvedValue(panel({ rows: [row({ state: 'granted', legacy_any: true })] }))
    finishSave(panel({ rows: [row({ state: 'undecided', condition: null })] }))
    await flush()
    expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
    expect(q(root, 'legacy')).not.toBeNull()
  })

  it('loads again after a save that started while a load was on its way', async () => {
    serve(panel({ rows: [row({ state: 'undecided', condition: null })] }))
    const root = await mount()
    let finishLoad: (value: PermissionPanel) => void = () => {}
    api.getPermissionPanel.mockImplementationOnce(() => new Promise((resolve) => { finishLoad = resolve }))
    q(root, 'banner-changed')!.click()
    await flush()
    api.decidePermission.mockResolvedValue(panel({ rows: [row({ state: 'granted' })] }))
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    finishLoad(panel({ rows: [row({ state: 'undecided', condition: null })] }))
    await flush()
    // The dropped load ran again after the save, and the old answer was not shown.
    expect(api.getPermissionPanel).toHaveBeenCalledTimes(3)
  })

  it('stops claiming unused time when the last-use history cannot be read again', async () => {
    serve(panel({ rows: [row({ state: 'undecided', condition: null })] }))
    const root = await mount()
    expect(q(root, `usage-${NET}`)!.querySelector('[data-test="unused30d"]')).not.toBeNull()
    api.getPermissionAuditHistory.mockRejectedValue(new Error('offline'))
    q(root, 'banner-changed')!.click()
    await flush()
    expect(q(root, `usage-${NET}`)).toBeNull()
  })

  it('retries a failed refresh after the scan finished', async () => {
    vi.useFakeTimers()
    try {
      serve(panel())
      const root = await mount()
      api.getPermissionPanel.mockRejectedValueOnce(new Error('offline'))
      q(root, 'banner-changed')!.click()
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
      expect(q(root, `row-${NET}`)).not.toBeNull()
      await vi.advanceTimersByTimeAsync(10000)
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(3)
      // Back to normal: no more polling for a finished scan.
      await vi.advanceTimersByTimeAsync(60000)
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('drops the count of uses while undecided when it cannot be read again after a decision', async () => {
    serve(panel({ rows: [row({ state: 'undecided', condition: null })] }), {
      pending: {
        incomplete: [],
        first_use: [{ plugin_id: 'demo', permission: NET, at: localDay(2026, 11, 27), count: 4, count_is_lower_bound: false }],
      },
    })
    const root = await mount()
    expect(q(root, `first-use-${NET}`)).not.toBeNull()
    api.getPendingPermissions.mockRejectedValue(new Error('offline'))
    // Allowed, then back to undecided, with the list unreadable in between.
    api.decidePermission.mockResolvedValue(panel({ rows: [row({ state: 'undecided', condition: null })] }))
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    expect(q(root, `first-use-${NET}`)).toBeNull()
  })

  it('resumes retrying a failed refresh after a failed save', async () => {
    vi.useFakeTimers()
    try {
      serve(panel({ rows: [row({ state: 'undecided', condition: null })] }))
      const root = await mount()
      api.getPermissionPanel.mockRejectedValueOnce(new Error('offline'))
      q(root, 'banner-changed')!.click()
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
      // A save before the retry pauses it, and fails.
      api.decidePermission.mockRejectedValueOnce(new Error('x'))
      q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
      await flush()
      // The retry comes back with its backoff (10 s), not the scan interval.
      await vi.advanceTimersByTimeAsync(5000)
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(5000)
      await flush()
      expect(api.getPermissionPanel).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('says when the scan failed and does not claim there are no blind spots', async () => {
    serve(panel({ analysis: { ...panel().analysis, status: 'failed' } }))
    const root = await mount()
    expect(q(root, 'notice-scan-failed')).not.toBeNull()
    expect(q(root, 'blind-spots')).toBeNull()
  })

  it('says there are no blind spot files after a finished scan with nothing to count', async () => {
    serve(panel())
    const root = await mount()
    expect(q(root, 'blind-spots-none')).not.toBeNull()
    expect(q(root, 'notice-scan-partial')).toBeNull()
  })

  it('reads the uses while undecided again after a decision', async () => {
    serve(panel({ rows: [row({ state: 'undecided', condition: null })] }), {
      pending: {
        incomplete: [],
        first_use: [{ plugin_id: 'demo', permission: NET, at: localDay(2026, 11, 27), count: 4, count_is_lower_bound: false }],
      },
    })
    const root = await mount()
    expect(q(root, `first-use-${NET}`)).not.toBeNull()
    api.getPendingPermissions.mockResolvedValue({ first_use: [], incomplete: [] })
    api.decidePermission.mockResolvedValue(panel({ rows: [row({ state: 'undecided', condition: null })] }))
    q(root, `switch-${NET}`)!.dispatchEvent(new Event('change'))
    await flush()
    expect(q(root, `first-use-${NET}`)).toBeNull()
  })

  it('opens a folded tier when everything is expanded and keeps it open when collapsed', async () => {
    serve(panel({
      rows: [row({ permission: 'plugin_sdk.models:invoke', tier: 'low' })],
      tiers: [
        { tier: 'high', count: 0, collapsed: false },
        { tier: 'medium', count: 0, collapsed: false },
        { tier: 'low', count: 1, collapsed: true },
      ],
    }))
    const root = await mount()
    q(root, 'expand-all')!.click()
    await flush()
    expect(q(root, 'row-plugin_sdk.models:invoke')).not.toBeNull()
    expect(q(root, 'scan-files')).not.toBeNull()
    q(root, 'expand-all')!.click()
    await flush()
    expect(q(root, 'scan-files')).toBeNull()
    expect(q(root, 'row-plugin_sdk.models:invoke')).not.toBeNull()
  })

  it('treats a response of another shape as not loaded', async () => {
    serve(panel())
    api.getPermissionPanel.mockResolvedValue({})
    api.getPermissionCatalog.mockResolvedValue({})
    api.getPermissionAuditHistory.mockResolvedValue({})
    api.getPendingPermissions.mockResolvedValue({})
    const root = await mount()
    expect(root.textContent).toContain('pluginConsent.tab.loadFailed')
  })

  it('stays usable when only the optional sources answer with another shape', async () => {
    serve(panel({ rows: [row({ permission: MEMORY, state: 'undecided' })] }))
    api.getPermissionCatalog.mockResolvedValue({})
    api.getPermissionAuditHistory.mockResolvedValue({})
    api.getPendingPermissions.mockResolvedValue({})
    const root = await mount()
    expect(q(root, `row-${MEMORY}`)).not.toBeNull()
  })

  it('shows the manifest table as TOML', async () => {
    serve(panel({
      developer: {
        findings: {},
        blind_spots: [],
        not_analyzed: [],
        manifest_permissions: { require: { [NET]: { reason: 'fetch', hosts: ['a.example'] } } },
      },
    }))
    const root = await mount()
    expect(root.querySelector('.perm-dev__toml')!.textContent).toBe(
      '[permissions.require]\n"system.network:connect" = { reason = "fetch", hosts = ["a.example"] }',
    )
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
