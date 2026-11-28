// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import PluginPermissionDialog from './PluginPermissionDialog.vue'
import type { PendingPermissions, PermissionCatalog, PermissionPanel } from '@/api/pluginPermissions'
import type { DialogRequest } from '@/utils/permissionDialogs'
import { panel, row } from '@/utils/permissionTab.fixtures'
import zhCN from '@/i18n/plugin-consent/zh-CN'
import { allPluginConsentMessages } from '@/i18n/plugin-consent/testing'

const api = vi.hoisted(() => ({ decidePermission: vi.fn(), getPermissionPanel: vi.fn() }))
vi.mock('@/api/pluginPermissions', () => api)
vi.mock('@/i18n', () => ({
  i18n: { global: { t: (key: string) => key } },
  localeLoadState: { pending: null },
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key} ${JSON.stringify(params)}` : key,
    te: () => true,
  }),
}))

const NET = 'system.network:connect'
const ENV = 'system.env:read'
const MEMORY = 'plugin_sdk.main_agent.memory:read'
const PUSH = 'plugin_sdk.main_agent.messages:push'

const ask = (over = {}) => ({ level: 'ask' as const, reason: 'fetch', background: false, background_reason: null, hosts: [], ...over })
const firstPanel = () =>
  panel({
    rows: [
      row({ permission: NET, tier: 'high', state: 'undecided', condition: null, declared: ask({ level: 'require' }) }),
      row({ permission: ENV, tier: 'high', state: 'undecided', condition: null, declared: null, reconcile: 'undeclared', enforced: false, usage_tracked: false }),
      row({ permission: 'system.fs:read', tier: 'medium', state: 'granted', declared: ask() }),
      row({ permission: MEMORY, tier: 'high', state: 'undecided', condition: null, declared: ask() }),
    ],
  })

const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}
interface Mounted {
  root: HTMLElement
  closed: Array<{ start: boolean; changed: boolean }>
  /** The props, reactive: a change reaches the dialog as the tab's would. */
  props: Record<string, unknown>
}
async function mount(props: {
  panel: PermissionPanel
  request: DialogRequest
  pending?: PendingPermissions | null
  start?: boolean
  catalog?: PermissionCatalog | null
}): Promise<Mounted> {
  const root = document.createElement('div')
  document.body.append(root)
  const closed: Mounted['closed'] = []
  const state = reactive<Record<string, unknown>>({ pending: null, pluginName: 'Stickers', ...props })
  const app = createApp({
    render: () =>
      h(PluginPermissionDialog as never, {
        ...state,
        onResult: (result: { start: boolean; changed: boolean }) => closed.push(result),
      }),
  })
  app.component(
    'el-dialog',
    defineComponent({
      props: { title: String, showClose: Boolean },
      emits: ['close'],
      setup: (props, { slots, emit }) => () =>
        h('div', [
          h('h2', { 'data-test': 'title' }, props.title),
          props.showClose ? h('button', { 'data-test': 'x', onClick: () => emit('close') }) : null,
          slots.default?.(),
          h('footer', slots.footer?.()),
        ]),
    }),
  )
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
  app.mount(root)
  apps.push(app)
  await flush()
  return { root, closed, props: state }
}
const q = (root: HTMLElement, test: string) => root.querySelector(`[data-test="${test}"]`) as HTMLElement | null

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: () => {} }) as never)
})
afterEach(() => {
  for (const app of apps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('dialog ① before the first start', () => {
  it('lists required, system and undeclared items by tier, and the optional ones in one line', async () => {
    const { root } = await mount({ panel: firstPanel(), request: { kind: 'first', items: [NET, ENV] }, start: true })
    expect(q(root, 'title')!.textContent).toContain('pluginConsent.dialogs.first.title')
    expect([...root.querySelectorAll('[data-test^="dialog-tier-"]')].map((el) => el.getAttribute('data-test'))).toEqual([
      'dialog-tier-high',
      'dialog-tier-medium',
    ])
    expect(q(q(root, `dialog-item-${NET}`)!, 'required')).not.toBeNull()
    expect(q(q(root, `dialog-item-${ENV}`)!, 'undeclared')).not.toBeNull()
    expect(q(root, `dialog-item-${ENV}`)!.textContent).toContain('pluginConsent.tab.undeclaredWarning')
    expect(q(root, `dialog-item-${ENV}`)!.textContent).toContain('pluginConsent.tab.notEnforcedNotTracked')
    expect(q(root, `dialog-item-${NET}`)!.textContent).toContain('pluginConsent.tab.pluginSays')
    expect(q(root, `dialog-item-${MEMORY}`)).toBeNull()
    expect(q(root, 'dialog-later')!.textContent).toContain('pluginConsent.dialogs.first.later')
  })

  it('always lets the user start, saying how many are left, and never decides for them', async () => {
    const { root, closed } = await mount({ panel: firstPanel(), request: { kind: 'first', items: [NET, ENV] }, start: true })
    const start = q(root, 'dialog-start') as HTMLButtonElement
    expect(start.disabled).toBe(false)
    expect(start.textContent).toContain('pluginConsent.dialogs.startUndecided {"n":2}')
    start.click()
    await flush()
    expect(closed).toEqual([{ start: true, changed: false }])
    expect(api.decidePermission).not.toHaveBeenCalled()
  })

  it('does not start when the user says so', async () => {
    const { root, closed } = await mount({ panel: firstPanel(), request: { kind: 'first', items: [NET] }, start: true })
    expect(q(root, 'dialog-skip')!.textContent).toContain('pluginConsent.dialogs.first.notStart')
    q(root, 'dialog-skip')!.click()
    expect(closed).toEqual([{ start: false, changed: false }])
  })

  it('saves each answer as it is given and shows what the server stored', async () => {
    const p = firstPanel()
    const { root, closed } = await mount({ panel: p, request: { kind: 'first', items: [NET, ENV] }, start: true })
    api.decidePermission.mockResolvedValueOnce({
      ...p,
      rows: p.rows.map((r) => (r.permission === NET ? { ...r, state: 'granted' as const, condition: 'anytime' as const } : r)),
    })
    q(root, `dialog-allow-${NET}`)!.click()
    await flush()
    // A required item covers background use when the server allows it.
    expect(api.decidePermission).toHaveBeenCalledWith('demo', NET, 'granted', 'anytime')
    expect(q(root, `dialog-allow-${NET}`)!.dataset.type).toBe('primary')
    expect(q(root, 'dialog-start')!.textContent).toContain('{"n":1}')

    api.decidePermission.mockResolvedValueOnce({
      ...p,
      rows: p.rows.map((r) => ({ ...r, state: r.permission === MEMORY ? r.state : r.permission === ENV ? 'denied' as const : 'granted' as const })),
    })
    q(root, `dialog-refuse-${ENV}`)!.click()
    await flush()
    expect(api.decidePermission).toHaveBeenLastCalledWith('demo', ENV, 'denied', 'in_use')
    expect(q(root, 'dialog-start')!.textContent).toContain('pluginConsent.dialogs.start')
    expect(q(root, 'dialog-start')!.textContent).not.toContain('startUndecided')
    q(root, 'dialog-skip')!.click()
    expect(closed).toEqual([{ start: false, changed: true }])
  })

  it('says when a save fails, shows what the server holds, and has the tab read again', async () => {
    const p = firstPanel()
    const { root, closed } = await mount({ panel: p, request: { kind: 'first', items: [NET] }, start: true })
    api.decidePermission.mockRejectedValueOnce(new Error('x'))
    api.getPermissionPanel.mockRejectedValueOnce(new Error('offline'))
    q(root, `dialog-allow-${NET}`)!.click()
    await flush()
    expect(ElMessage.error).toHaveBeenCalledWith('pluginConsent.saveFailed')
    expect(q(root, `dialog-allow-${NET}`)!.dataset.type).toBeUndefined()

    // The answer was lost but the server stored it: the read after the failure shows it.
    api.decidePermission.mockRejectedValueOnce(new Error('lost'))
    api.getPermissionPanel.mockResolvedValueOnce({
      ...p,
      rows: p.rows.map((r) => (r.permission === NET ? { ...r, state: 'granted' as const } : r)),
    })
    q(root, `dialog-allow-${NET}`)!.click()
    await flush()
    expect(api.getPermissionPanel).toHaveBeenLastCalledWith('demo')
    expect(q(root, `dialog-allow-${NET}`)!.dataset.type).toBe('primary')
    q(root, 'x')!.click()
    expect(closed).toEqual([{ start: false, changed: true }])
  })

  const withState = (p: PermissionPanel, states: Record<string, 'granted' | 'denied' | 'undecided'>) => ({
    ...p,
    rows: p.rows.map((r) => (r.permission in states ? { ...r, state: states[r.permission]! } : r)),
  })

  it('takes a later save answer over an earlier answer given here', async () => {
    const p = firstPanel()
    const { root } = await mount({ panel: p, request: { kind: 'first', items: [NET, ENV] }, start: true })
    api.decidePermission.mockResolvedValueOnce(withState(p, { [NET]: 'granted' }))
    q(root, `dialog-allow-${NET}`)!.click()
    await flush()
    // Meanwhile the source changed and cleared NET; the next save's answer says so.
    api.decidePermission.mockResolvedValueOnce(withState(p, { [NET]: 'undecided', [ENV]: 'denied' }))
    q(root, `dialog-refuse-${ENV}`)!.click()
    await flush()
    expect(q(root, `dialog-allow-${NET}`)!.dataset.type).toBeUndefined()
    expect(q(root, 'dialog-start')!.textContent).toContain('{"n":1}')
  })

  it('drops the answers given here when a panel from the tab shows the source changed', async () => {
    const p = firstPanel()
    const { root, props } = await mount({ panel: p, request: { kind: 'first', items: [NET] }, start: true })
    api.decidePermission.mockResolvedValueOnce(withState(p, { [NET]: 'granted' }))
    q(root, `dialog-allow-${NET}`)!.click()
    await flush()
    props.panel = { ...p, grants_reset: { at: null, reason: 'source_changed' } }
    await flush()
    expect(q(root, `dialog-allow-${NET}`)!.dataset.type).toBeUndefined()
    expect(q(root, 'dialog-reset')).not.toBeNull()
  })

  it('keeps what was read after a failed save against an older panel from the tab', async () => {
    const p = firstPanel()
    const { root, props } = await mount({ panel: p, request: { kind: 'first', items: [NET] }, start: true })
    api.decidePermission.mockRejectedValueOnce(new Error('lost'))
    api.getPermissionPanel.mockResolvedValueOnce(withState(p, { [NET]: 'granted' }))
    q(root, `dialog-allow-${NET}`)!.click()
    await flush()
    expect(q(root, `dialog-allow-${NET}`)!.dataset.type).toBe('primary')
    props.panel = { ...p }
    await flush()
    expect(q(root, `dialog-allow-${NET}`)!.dataset.type).toBe('primary')
  })

  it('follows a newer panel from the tab, keeping the answers given here', async () => {
    const p = firstPanel()
    const { root, props } = await mount({ panel: p, request: { kind: 'first', items: [NET] }, start: true })
    api.decidePermission.mockResolvedValueOnce({
      ...p,
      rows: p.rows.map((r) => (r.permission === NET ? { ...r, state: 'granted' as const } : r)),
    })
    q(root, `dialog-allow-${NET}`)!.click()
    await flush()
    // A scan finished: one more undeclared item; this read predates the answer above.
    props.panel = {
      ...p,
      rows: [...p.rows, row({ permission: 'system.process:spawn', tier: 'high', state: 'undecided', declared: null, reconcile: 'undeclared' })],
    }
    await flush()
    expect(q(root, 'dialog-item-system.process:spawn')).not.toBeNull()
    expect(q(root, `dialog-allow-${NET}`)!.dataset.type).toBe('primary')
  })

  it('cannot be closed while an answer is being saved', async () => {
    const { root, closed } = await mount({ panel: firstPanel(), request: { kind: 'first', items: [NET] }, start: true })
    let finish: (value: PermissionPanel) => void = () => {}
    api.decidePermission.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    q(root, `dialog-allow-${NET}`)!.click()
    await flush()
    expect(q(root, 'x')).toBeNull()
    expect((q(root, 'dialog-start') as HTMLButtonElement).disabled).toBe(true)
    expect((q(root, `dialog-refuse-${ENV}`) as HTMLButtonElement | null)?.disabled ?? true).toBe(true)
    finish(firstPanel())
    await flush()
    q(root, 'dialog-start')!.click()
    expect(closed).toEqual([{ start: true, changed: true }])
  })

  it('offers no "Don\'t allow" for an item the server does not take a refusal for', async () => {
    const p = panel({ rows: [row({ permission: PUSH, tier: 'low', state: 'undecided', declared: ask({ level: 'require' }) })] })
    const { root } = await mount({ panel: p, request: { kind: 'first', items: [PUSH] } })
    expect(q(root, `dialog-allow-${PUSH}`)).not.toBeNull()
    expect(q(root, `dialog-refuse-${PUSH}`)).toBeNull()
  })

  it('opened from the tab: a review title and "Done", nothing about starting', async () => {
    const { root, closed } = await mount({ panel: firstPanel(), request: { kind: 'first', items: [NET] } })
    expect(q(root, 'title')!.textContent).toContain('pluginConsent.dialogs.first.reviewTitle')
    expect(q(root, 'dialog-start')).toBeNull()
    expect(q(root, 'dialog-skip')).toBeNull()
    q(root, 'dialog-done')!.click()
    expect(closed).toEqual([{ start: false, changed: false }])
  })

  it('says why earlier decisions are gone after a source change', async () => {
    const p = { ...firstPanel(), grants_reset: { at: '2026-11-20T10:00:00Z', reason: 'source_changed' } }
    const { root } = await mount({ panel: p, request: { kind: 'first', items: [NET] } })
    expect(q(root, 'dialog-reset')!.textContent).toContain('pluginConsent.tab.notices.grantsReset')
  })
})

describe('dialog ③ after an update', () => {
  it('shows only the added items, the version, and how many stay as they were', async () => {
    const { root } = await mount({ panel: firstPanel(), request: { kind: 'upgrade', items: [ENV] }, start: true })
    expect(q(root, 'title')!.textContent).toContain('pluginConsent.dialogs.upgrade.title')
    expect(q(root, 'title')!.textContent).toContain('"version":"1.0.0"')
    expect(q(root, `dialog-item-${ENV}`)).not.toBeNull()
    expect(q(root, `dialog-item-${NET}`)).toBeNull()
    expect(q(root, 'dialog-unchanged')!.textContent).toContain('{"n":3}')
    expect(q(root, 'dialog-later')).toBeNull()
    expect(q(root, 'dialog-skip')!.textContent).toContain('pluginConsent.dialogs.upgrade.notStart')
    expect((q(root, 'dialog-start') as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('dialog ② for an item used before it was decided', () => {
  const usedPending = (): PendingPermissions => ({
    first_use: [{ plugin_id: 'demo', permission: MEMORY, at: '2026-11-20T10:00:00Z', count: 3, count_is_lower_bound: true }],
    incomplete: [],
  })

  it('says what it used and how often, what the plugin says, and offers no "only this once"', async () => {
    const { root } = await mount({ panel: firstPanel(), request: { kind: 'firstUse', permission: MEMORY }, pending: usedPending() })
    expect(q(root, 'title')!.textContent).toContain('pluginConsent.dialogs.firstUse.title')
    expect(q(root, 'dialog-tier-high')).not.toBeNull()
    expect(q(root, 'dialog-cause')!.textContent).toContain('pluginConsent.tab.firstUseAtLeast {"n":3}')
    expect(q(root, 'dialog-cause')!.textContent).toContain('pluginConsent.tab.wouldBlock')
    expect(root.textContent).toContain('pluginConsent.tab.pluginSays')
    expect(q(root, 'self-reported')).not.toBeNull()
    expect([...root.querySelectorAll('footer button')].map((b) => b.getAttribute('data-test'))).toEqual([
      'dialog-refuse',
      'dialog-allow',
    ])
  })

  it('grants "while I use it" by default and "any time" when chosen', async () => {
    const p = firstPanel()
    const { root, closed } = await mount({ panel: p, request: { kind: 'firstUse', permission: MEMORY }, pending: usedPending() })
    api.decidePermission.mockResolvedValue(p)
    const anytime = q(root, 'dialog-when-anytime')!.querySelector('input')!
    anytime.checked = true
    anytime.dispatchEvent(new Event('change'))
    await flush()
    q(root, 'dialog-allow')!.click()
    await flush()
    expect(api.decidePermission).toHaveBeenCalledWith('demo', MEMORY, 'granted', 'anytime')
    expect(closed).toEqual([{ start: false, changed: true }])
  })

  it('refuses, and stays open when the save fails', async () => {
    const { root, closed } = await mount({ panel: firstPanel(), request: { kind: 'firstUse', permission: MEMORY }, pending: usedPending() })
    api.decidePermission.mockRejectedValueOnce(new Error('x'))
    q(root, 'dialog-refuse')!.click()
    await flush()
    expect(api.decidePermission).toHaveBeenCalledWith('demo', MEMORY, 'denied', 'in_use')
    expect(closed).toEqual([])
    expect(ElMessage.error).toHaveBeenCalled()
  })

  it('offers only "while I use it" when the plugin does not declare background use, and says why', async () => {
    const p = panel({ rows: [row({ permission: MEMORY, state: 'undecided', condition: null, anytime_allowed: false, declared: ask() })] })
    const { root } = await mount({ panel: p, request: { kind: 'firstUse', permission: MEMORY }, pending: usedPending() })
    expect(q(root, 'dialog-when-anytime')).toBeNull()
    expect(q(root, 'dialog-when-in_use')).not.toBeNull()
    expect(q(root, 'dialog-when-lock')!.textContent).toContain('pluginConsent.tab.condition.lock.notDeclared')
  })

  it('says some ways of reading cannot be blocked yet, for the data the catalog marks so', async () => {
    const catalog: PermissionCatalog = {
      tiers: [], permissions: {}, blind_spot_tiers: {},
      privacy_groups: [
        { group: 'memory', permissions: [MEMORY], tier: 'high', partial_block: true },
        { group: 'network', permissions: [NET], tier: 'high', partial_block: false },
      ],
    }
    const used = await mount({ panel: firstPanel(), request: { kind: 'firstUse', permission: MEMORY }, pending: usedPending(), catalog })
    expect(q(used.root, 'dialog-partial')!.textContent).toContain('pluginConsent.tab.partialBlock')
    const net = await mount({ panel: firstPanel(), request: { kind: 'firstUse', permission: NET }, catalog })
    expect(q(net.root, 'dialog-partial')).toBeNull()
    // Without the catalog nothing is claimed either way.
    const none = await mount({ panel: firstPanel(), request: { kind: 'firstUse', permission: MEMORY }, pending: usedPending() })
    expect(q(none.root, 'dialog-partial')).toBeNull()
  })

  it('has nothing to ask when the item is gone', async () => {
    const { root, closed } = await mount({ panel: panel(), request: { kind: 'firstUse', permission: MEMORY } })
    expect(q(root, 'dialog-gone')).not.toBeNull()
    q(root, 'dialog-done')!.click()
    expect(closed).toEqual([{ start: false, changed: false }])
  })
})

describe('dialog wording', () => {
  it('never says a decision is needed to start', () => {
    for (const messages of Object.values(allPluginConsentMessages)) {
      expect(Object.keys(messages.dialogs).length).toBe(Object.keys(zhCN.dialogs).length)
    }
    const text = JSON.stringify(zhCN.dialogs)
    for (const phrase of ['无法启动', '才能启动', '才会启动', '仅这一次', '同意并启动']) {
      expect(text).not.toContain(phrase)
    }
  })
})
