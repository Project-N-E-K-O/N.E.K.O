// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { AxiosError, AxiosHeaders } from 'axios'
import { ElMessage } from 'element-plus'
import PrivacyOverview from './PrivacyOverview.vue'
import type { PrivacyGroupDetail, PrivacyOverview as Overview } from '@/api/pluginPermissions'
import { detail, ENV, item, MEMORY, NET, overview, plugin, PUSH, SPAWN } from '@/utils/privacyPage.fixtures'

const api = vi.hoisted(() => ({
  getPrivacyOverview: vi.fn(),
  getPrivacyGroup: vi.fn(),
  getPermissionCatalog: vi.fn(),
  decidePermission: vi.fn(),
  clearPermission: vi.fn(),
}))
vi.mock('@/api/pluginPermissions', () => api)
const pluginStore = vi.hoisted(() => ({
  pluginSummariesWithStatus: [{ id: 'diary', name: 'Diary' }] as Array<{ id: string; name: string }>,
  fetchPluginSummaries: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => pluginStore }))
vi.mock('@/i18n', () => ({
  i18n: { global: { t: (key: string) => key } },
  localeLoadState: { pending: null },
}))
// Keys that exist: the two personal data names and the permission titles.
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key} ${JSON.stringify(params)}` : key,
    te: (key: string) =>
      /^pluginConsent\.privacy\.groups\.(memory|conversations)$/.test(key) || key.startsWith('pluginConsent.capabilities.'),
  }),
}))

const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}
const apps: Array<ReturnType<typeof createApp>> = []
async function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(PrivacyOverview)
  app.component(
    'el-button',
    defineComponent({
      emits: ['click'],
      setup: (_, { slots, emit, attrs }) => () => h('button', { ...attrs, onClick: () => emit('click') }, slots.default?.()),
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
  app.component(
    'router-link',
    defineComponent({
      props: { to: String },
      setup: (props, { slots, attrs }) => () => h('a', { ...attrs, href: props.to }, slots.default?.()),
    }),
  )
  app.mount(root)
  apps.push(app)
  await flush()
  return root
}
const q = (root: HTMLElement, test: string) => root.querySelector(`[data-test="${test}"]`) as HTMLElement | null
const qa = (root: HTMLElement, test: string) => Array.from(root.querySelectorAll(`[data-test="${test}"]`)) as HTMLElement[]

const catalog = {
  tiers: [],
  permissions: {
    [MEMORY]: { tier: 'high', enforced: true, lock: true, privacy_group: 'memory' },
    [NET]: { tier: 'high', enforced: true, lock: false, privacy_group: 'network' },
  },
  blind_spot_tiers: {},
  privacy_groups: [
    { group: 'memory', permissions: [MEMORY], tier: 'high', partial_block: true },
    { group: 'conversations', permissions: ['plugin_sdk.main_agent.conversations:read'], tier: 'high', partial_block: true },
    { group: 'network', permissions: [NET], tier: 'high', partial_block: false },
    { group: 'process_env', permissions: [SPAWN, ENV], tier: 'high', partial_block: false },
    { group: 'fs_read', permissions: ['system.fs:read'], tier: 'medium', partial_block: false },
    { group: 'listen', permissions: ['system.network:listen'], tier: 'low', partial_block: false },
    { group: 'models', permissions: ['plugin_sdk.models:invoke'], tier: 'low', partial_block: false },
    { group: 'messages', permissions: [PUSH], tier: 'medium', partial_block: false },
  ],
}

function serve(data: Overview = overview(), groups: Record<string, PrivacyGroupDetail> = {}) {
  api.getPrivacyOverview.mockResolvedValue(data)
  api.getPermissionCatalog.mockResolvedValue(catalog)
  api.getPrivacyGroup.mockImplementation(async (group: string) => groups[group] ?? detail({ group, plugins: [], recent: [] }))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: () => {} }) as never)
})
afterEach(() => {
  for (const app of apps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('PrivacyOverview: overview', () => {
  it('shows a retry when the overview cannot be read, or has another shape', async () => {
    api.getPrivacyOverview.mockRejectedValueOnce(new Error('down'))
    api.getPermissionCatalog.mockResolvedValue(catalog)
    const root = await mount()
    expect(q(root, 'overview-failed')).not.toBeNull()
    expect(q(root, 'detail')).toBeNull()
    serve()
    q(root, 'overview-failed')!.querySelector('button')!.click()
    await flush()
    expect(q(root, 'overview-failed')).toBeNull()
    expect(q(root, 'group-memory')).not.toBeNull()

    api.getPrivacyOverview.mockResolvedValueOnce({ groups: [] })
    const stub = await mount()
    expect(q(stub, 'overview-failed')).not.toBeNull()
  })

  it('rows by data: name, count, latest use and the partial block note', async () => {
    serve()
    const root = await mount()
    const memory = q(root, 'group-memory')!
    expect(memory.textContent).toContain('pluginConsent.privacy.groups.memory')
    expect(memory.textContent).toContain('pluginConsent.privacy.count.read {"n":3}')
    expect(memory.textContent).toContain('pluginConsent.privacy.latest')
    expect(memory.querySelector('[data-test="partial-block"]')).not.toBeNull()
    // Other data reads "N plugins"; none says so; no latest use claims nothing.
    expect(q(root, 'group-fs_read')!.textContent).toContain('pluginConsent.privacy.count.use {"n":2}')
    const network = q(root, 'group-network')!
    expect(network.textContent).toContain('pluginConsent.privacy.count.none')
    expect(network.textContent).toContain('pluginConsent.privacy.latestNone')
    expect(network.querySelector('[data-test="partial-block"]')).toBeNull()
    // A group without a name of its own is its permissions' names.
    expect(network.textContent).toContain('pluginConsent.capabilities.system_network_connect.title')
  })

  it('names the latest user from the plugin list when the detail does not', async () => {
    serve(overview({ groups: [{ group: 'network', tier: 'high', plugins: 1, latest: { plugin_id: 'diary', at: '2026-11-28T03:00:00Z' }, partial_block: false }] }))
    const root = await mount()
    expect(q(root, 'group-network')!.textContent).toContain('"plugin":"Diary"')
  })

  it('folds low risk until opened', async () => {
    serve()
    const root = await mount()
    expect(q(root, 'group-listen')).toBeNull()
    expect(q(root, 'tier-low')!.textContent).toContain('pluginConsent.tab.tierCount {"n":2}')
    q(root, 'tier-low')!.click()
    await flush()
    expect(q(root, 'group-listen')).not.toBeNull()
    expect(q(root, 'tier-low')!.getAttribute('aria-expanded')).toBe('true')
  })

  it('shows "needs you" with the non-empty parts only, and nothing when empty', async () => {
    serve()
    const empty = await mount()
    expect(q(empty, 'pending')).toBeNull()
    serve(overview({ pending: { to_confirm: 2, upgraded: 1, inferred: 0, first_use: 0 } }))
    const root = await mount()
    expect(q(root, 'pending-to_confirm')!.textContent).toContain('{"n":2}')
    expect(q(root, 'pending-upgraded')).not.toBeNull()
    expect(q(root, 'pending-inferred')).toBeNull()
    expect(q(root, 'pending-go')!.getAttribute('href')).toBe('/plugins')
  })

  it('says when checks are off and when declarations could not be read', async () => {
    serve()
    const plain = await mount()
    expect(q(plain, 'checks-disabled')).toBeNull()
    expect(q(plain, 'incomplete')).toBeNull()
    serve(overview({ checks_disabled: true, incomplete: ['a', 'b'] }))
    const root = await mount()
    expect(q(root, 'checks-disabled')).not.toBeNull()
    expect(q(root, 'incomplete')!.textContent).toContain('{"n":2}')
  })

  it('states each read-and-send combination as a fact', async () => {
    serve(overview({ combos: [{ plugin_id: 'diary', reads: [MEMORY], sends: [NET] }] }))
    const root = await mount()
    const combo = q(root, 'combo')!
    expect(combo.textContent).toContain('"plugin":"Diary"')
    expect(combo.textContent).toContain('"reads":"pluginConsent.privacy.groups.memory"')
    expect(combo.textContent).toContain('"sends":"pluginConsent.capabilities.system_network_connect.title"')
  })
})

describe('PrivacyOverview: detail', () => {
  it('opens the first group, and another one on click', async () => {
    serve()
    const root = await mount()
    expect(api.getPrivacyGroup).toHaveBeenCalledWith('memory')
    expect(q(root, 'group-memory')!.getAttribute('aria-pressed')).toBe('true')
    expect(q(root, 'personal-note')).not.toBeNull()
    expect(q(root, 'detail-partial-block')).not.toBeNull()
    q(root, 'group-fs_read')!.click()
    await flush()
    expect(api.getPrivacyGroup).toHaveBeenLastCalledWith('fs_read')
    expect(q(root, 'group-fs_read')!.getAttribute('aria-pressed')).toBe('true')
    expect(q(root, 'personal-note')).toBeNull()
    expect(q(root, 'detail-partial-block')).toBeNull()
    expect(q(root, 'detail-empty')).not.toBeNull()
  })

  it('keeps the later choice when an earlier answer comes back late', async () => {
    serve()
    let release: (value: PrivacyGroupDetail) => void = () => {}
    api.getPrivacyGroup.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const root = await mount()
    q(root, 'group-fs_read')!.click()
    await flush()
    release(detail({ group: 'memory', plugins: [plugin()] }))
    await flush()
    expect(q(root, 'plugin-weather-' + MEMORY)).toBeNull()
    expect(q(root, 'detail-empty')).not.toBeNull()
  })

  it('shows a retry when the detail cannot be read', async () => {
    serve()
    api.getPrivacyGroup.mockRejectedValueOnce(new Error('down'))
    const root = await mount()
    expect(q(root, 'detail-failed')).not.toBeNull()
    q(root, 'detail-failed')!.querySelector('button')!.click()
    await flush()
    expect(q(root, 'detail-failed')).toBeNull()
  })

  it('one line per plugin: when it may, last use, uses in 7 days, tags', async () => {
    const memory = detail({
      plugins: [
        plugin({ items: [item({ uses_7d: 12, last_used: { at: '2026-11-28T06:00:00Z', origin_kind: 'chat_tool', outcome: 'allowed' } })] }),
        plugin({ plugin_id: 'diary', name: 'Diary', items: [item({ legacy_any: true, condition: 'anytime' })] }),
        plugin({ plugin_id: 'old', name: 'Old', items: [item({ unused_30d: true })] }),
        plugin({ plugin_id: 'translate', name: 'Translate', has_permission: false, items: [item({ state: 'undecided', condition: null })] }),
        plugin({ plugin_id: 'gone', name: 'Gone', installed: false, has_permission: false, items: [item({ state: null, condition: null, last_used: { at: '2026-11-20T00:00:00Z', origin_kind: null, outcome: 'allowed' } })] }),
      ],
    })
    serve(overview(), { memory })
    const root = await mount()
    const line = (id: string) => q(root, `plugin-${id}-${MEMORY}`)!
    expect(q(root, 'condition-note')).not.toBeNull()
    expect(line('weather').querySelector('[data-test="condition"]')!.textContent).toContain('pluginConsent.tab.condition.in_use')
    expect(line('weather').querySelector('[data-test="usage"]')!.textContent).toContain('pluginConsent.tab.lastUse')
    expect(line('weather').querySelector('[data-test="usage"]')!.textContent).toContain('pluginConsent.privacy.detail.week {"n":12}')
    expect(line('diary').querySelector('[data-test="legacy"]')).not.toBeNull()
    expect(line('diary').querySelector('[data-test="condition"]')).toBeNull()
    expect(line('old').querySelector('[data-test="suggest-off"]')).not.toBeNull()
    expect(line('old').querySelector('[data-test="usage"]')!.textContent).toContain('pluginConsent.tab.unused30d')
    expect(line('weather').querySelector('[data-test="suggest-off"]')).toBeNull()
    expect(line('translate').querySelector('[data-test="no-permission"]')).not.toBeNull()
    expect(line('translate').querySelector('[data-test="undecided"]')).not.toBeNull()
    expect(line('gone').querySelector('[data-test="uninstalled"]')).not.toBeNull()
    expect(line('gone').querySelector('input')).toBeNull()
    expect((line('weather').querySelector('input') as HTMLInputElement).checked).toBe(true)
    expect((line('translate').querySelector('input') as HTMLInputElement).checked).toBe(false)
  })

  it('shows no use at all for items whose uses are not recorded', async () => {
    serve(overview(), {
      memory: detail({ plugins: [plugin({ items: [item({ usage_tracked: false, enforced: false, uses_7d: 9, unused_30d: null })] })] }),
    })
    const root = await mount()
    const line = q(root, `plugin-weather-${MEMORY}`)!
    expect(line.querySelector('[data-test="usage"]')).toBeNull()
    expect(line.querySelector('[data-test="enforcement"]')!.textContent).toContain('pluginConsent.tab.notEnforcedNotTracked')
  })

  it('says "at least" while the history is shorter than 7 days', async () => {
    const recentHistory = { since: new Date(Date.now() - 2 * 86400000).toISOString(), covers_30d: false, last_use_since: null, last_use_covers_30d: false }
    serve(overview(), { memory: detail({ history: recentHistory, plugins: [plugin({ items: [item({ uses_7d: 3 })] })] }) })
    const root = await mount()
    expect(q(root, `plugin-weather-${MEMORY}`)!.textContent).toContain('pluginConsent.privacy.detail.weekAtLeast {"n":3}')
  })

  it('names the permission when a group has more than one', async () => {
    serve(overview({ groups: [{ group: 'process_env', tier: 'high', plugins: 1, latest: null, partial_block: false }] }), {
      process_env: detail({ group: 'process_env', partial_block: false, plugins: [plugin({ items: [item({ permission: SPAWN })] })] }),
    })
    const root = await mount()
    expect(q(root, `plugin-weather-${SPAWN}`)!.textContent).toContain('pluginConsent.capabilities.system_process_spawn.title')
  })
})

describe('PrivacyOverview: switch', () => {
  it('refuses, grants with the kept condition, and reloads', async () => {
    serve(overview(), { memory: detail({ plugins: [plugin({ items: [item({ condition: 'anytime' })] })] }) })
    api.decidePermission.mockResolvedValue({})
    const root = await mount()
    const input = () => q(root, `switch-weather-${MEMORY}`) as HTMLInputElement
    input().dispatchEvent(new Event('change'))
    await flush()
    expect(api.decidePermission).toHaveBeenCalledWith('weather', MEMORY, 'denied', 'in_use')
    expect(api.getPrivacyOverview).toHaveBeenCalledTimes(2)

    serve(overview(), { memory: detail({ plugins: [plugin({ items: [item({ state: 'denied', condition: null })] })] }) })
    const denied = await mount()
    ;(q(denied, `switch-weather-${MEMORY}`) as HTMLInputElement).dispatchEvent(new Event('change'))
    await flush()
    expect(api.decidePermission).toHaveBeenLastCalledWith('weather', MEMORY, 'granted', 'in_use')
  })

  it('returns what cannot be refused to undecided', async () => {
    serve(overview({ groups: [{ group: 'messages', tier: 'medium', plugins: 1, latest: null, partial_block: false }] }), {
      messages: detail({ group: 'messages', plugins: [plugin({ items: [item({ permission: PUSH })] })] }),
    })
    api.clearPermission.mockResolvedValue({})
    const root = await mount()
    ;(q(root, `switch-weather-${PUSH}`) as HTMLInputElement).dispatchEvent(new Event('change'))
    await flush()
    expect(api.clearPermission).toHaveBeenCalledWith('weather', PUSH)
    expect(api.decidePermission).not.toHaveBeenCalled()
  })

  it('keeps what is shown when the reload after a change fails', async () => {
    serve(overview(), { memory: detail() })
    api.decidePermission.mockResolvedValue({})
    const root = await mount()
    api.getPrivacyOverview.mockRejectedValueOnce(new Error('down'))
    ;(q(root, `switch-weather-${MEMORY}`) as HTMLInputElement).dispatchEvent(new Event('change'))
    await flush()
    expect(q(root, 'overview-failed')).toBeNull()
    expect(q(root, 'group-memory')).not.toBeNull()
  })

  it('shows the reason a change failed', async () => {
    serve(overview(), { memory: detail() })
    const headers = new AxiosHeaders({ 'X-Error-Code': 'PLUGIN_OPERATION_BUSY' })
    api.decidePermission.mockRejectedValue(
      new AxiosError('x', 'ERR', undefined, undefined, { status: 409, statusText: '', headers, config: { headers }, data: { detail: { code: 'PLUGIN_OPERATION_BUSY' } } }),
    )
    const root = await mount()
    ;(q(root, `switch-weather-${MEMORY}`) as HTMLInputElement).dispatchEvent(new Event('change'))
    await flush()
    expect(ElMessage.error).toHaveBeenCalledWith('pluginConsent.tab.errors.busy')
  })
})

describe('PrivacyOverview: recent records', () => {
  const recent = [
    { at: '2026-11-28T06:02:00Z', plugin_id: 'weather', permission: MEMORY, outcome: 'allowed' as const, origin_kind: 'chat_tool', target: null },
    { at: '2026-11-28T03:00:00Z', plugin_id: 'diary', permission: MEMORY, outcome: 'allowed' as const, origin_kind: 'mystery', target: null },
    { at: '2026-11-27T10:00:00Z', plugin_id: 'translate', permission: MEMORY, outcome: 'would_block' as const, origin_kind: null, target: null },
  ]

  it('lists uses with who, what and why; would-be blocks say they were only recorded', async () => {
    serve(overview(), { memory: detail({ recent }) })
    const root = await mount()
    const uses = qa(root, 'recent-use')
    expect(uses).toHaveLength(3)
    expect(uses[0]!.textContent).toContain('"plugin":"Weather"')
    expect(uses[0]!.textContent).toContain('pluginConsent.privacy.recent.origin.chat_tool')
    expect(uses[0]!.classList.contains('privacy-would-block')).toBe(false)
    // Diary is not in this group's plugins: named from the plugin list. Unknown origins are left out.
    expect(uses[1]!.textContent).toContain('"plugin":"Diary"')
    expect(uses[1]!.textContent).not.toContain('origin.mystery')
    expect(uses[2]!.textContent).toContain('pluginConsent.tab.wouldBlock')
    expect(uses[2]!.classList.contains('privacy-would-block')).toBe(true)
  })

  it('filters to the ones that would be blocked', async () => {
    serve(overview(), { memory: detail({ recent }) })
    const root = await mount()
    const filter = q(root, 'only-would-block') as HTMLInputElement
    filter.checked = true
    filter.dispatchEvent(new Event('change'))
    await flush()
    expect(qa(root, 'recent-use')).toHaveLength(1)

    serve(overview(), { memory: detail({ recent: recent.slice(0, 1) }) })
    const other = await mount()
    const otherFilter = q(other, 'only-would-block') as HTMLInputElement
    otherFilter.checked = true
    otherFilter.dispatchEvent(new Event('change'))
    await flush()
    expect(q(other, 'recent-empty')!.textContent).toContain('pluginConsent.privacy.recent.emptyFiltered')
  })

  it('an empty list says since when records are complete', async () => {
    serve(overview(), { memory: detail({ recent: [] }) })
    const root = await mount()
    expect(q(root, 'recent-empty')!.textContent).toContain('pluginConsent.privacy.recent.emptySince')
    serve(overview(), { memory: detail({ recent: [], history: { since: null, covers_30d: false, last_use_since: null, last_use_covers_30d: false } }) })
    const none = await mount()
    expect(q(none, 'recent-empty')!.textContent).toContain('pluginConsent.privacy.recent.empty')
    expect(q(none, 'recent-empty')!.textContent).not.toContain('emptySince')
  })
})
