// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive } from 'vue'

import PluginUpdateFloatWindow from './PluginUpdateFloatWindow.vue'
import type { MarketUpdateCandidate, usePluginUpdatesStore } from '@/stores/pluginUpdates'

type UpdatesStore = ReturnType<typeof usePluginUpdatesStore>

const mocks = vi.hoisted(() => ({
  store: null as unknown as Record<string, unknown>,
  messages: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('element-plus', () => ({ ElMessage: mocks.messages }))
vi.mock('@/stores/pluginUpdates', () => ({
  usePluginUpdatesStore: () => mocks.store as unknown as UpdatesStore,
}))

// The shared install-task store is exercised by its own spec; here it only has
// to look idle so the progress panel stays hidden.
// Reactive so the popup's close watcher sees slot / task changes like production.
const installTask = reactive({ task: null as unknown, owner: null as string | null, reservation: null as string | null, running: false, done: false, dismiss: vi.fn(), percent: 0 })
vi.mock('@/stores/marketInstallTask', () => ({
  useMarketInstallTaskStore: () => installTask,
}))

function candidate(overrides: Partial<MarketUpdateCandidate> = {}): MarketUpdateCandidate {
  return {
    pluginId: 'alpha',
    marketId: '15',
    name: 'Alpha',
    channel: 'stable' as const,
    currentVersion: '1.0.0',
    latestVersion: '1.1.0',
    status: 'idle' as const,
    errorKey: null,
    needsManualUpgrade: false,
    ...overrides,
  }
}

type FakeUpdatesStore = {
  popupOpen: boolean
  candidates: MarketUpdateCandidate[]
  checking: boolean
  checkFailed: boolean
  unresolved: number
  batchRunning: boolean
  batchDone: number
  batchTotal: number
  busy: boolean
  check: ReturnType<typeof vi.fn>
  updateOne: ReturnType<typeof vi.fn>
  updateAll: ReturnType<typeof vi.fn>
  closePopup: ReturnType<typeof vi.fn>
}

function makeStore(overrides: Partial<FakeUpdatesStore> = {}): FakeUpdatesStore {
  const store = reactive({
    popupOpen: true,
    candidates: [] as MarketUpdateCandidate[],
    checking: false,
    checkFailed: false,
    unresolved: 0,
    batchRunning: false,
    batchDone: 0,
    batchTotal: 0,
    check: vi.fn(),
    updateOne: vi.fn(),
    updateAll: vi.fn(),
    closePopup: vi.fn(),
    ...overrides,
  })
  // Mirrors the real store's computed, so a test can flip the inputs and watch
  // the lock follow exactly like production.
  Object.defineProperty(store, 'busy', {
    enumerable: true,
    get: () => Boolean(
      store.checking
      || store.batchRunning
      || store.candidates.some((entry) => entry.status === 'updating'),
    ),
  })
  mocks.store = store as unknown as Record<string, unknown>
  return store as FakeUpdatesStore
}

let cleanup = () => {}

/** happy-dom has no layout, so the drag maths is pinned to explicit geometry:
 *  a 380px-wide window anchored under a 92px-tall app header. */
const FLOAT_HEIGHT = 247
const PARENT_LEFT = 220
const PARENT_TOP = 38

function stubGeometry(
  el: HTMLElement,
  geometry: { left: number; top: number; width: number; headerBottom: number },
): void {
  el.getBoundingClientRect = () => ({
    x: geometry.left,
    y: geometry.top,
    left: geometry.left,
    top: geometry.top,
    right: geometry.left + geometry.width,
    bottom: geometry.top + FLOAT_HEIGHT,
    width: geometry.width,
    height: FLOAT_HEIGHT,
    toJSON: () => ({}),
  }) as DOMRect
  Object.defineProperty(el, 'offsetLeft', {
    value: geometry.left - PARENT_LEFT,
    configurable: true,
  })
  Object.defineProperty(el, 'offsetTop', {
    value: geometry.top - PARENT_TOP,
    configurable: true,
  })
  Object.defineProperty(el, 'offsetParent', {
    value: {
      getBoundingClientRect: () => ({ bottom: geometry.headerBottom }),
    },
    configurable: true,
  })
}

function floatElement(root: Element): HTMLElement {
  const el = root.querySelector('[data-yui-guide-id="plugin-update-float"]')
  expect(el).not.toBeNull()
  return el as HTMLElement
}

function pointer(type: string, init: PointerEventInit): PointerEvent {
  return new PointerEvent(type, { bubbles: true, cancelable: true, ...init })
}

function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(PluginUpdateFloatWindow)
  app.component('ElIcon', defineComponent({ setup: (_, { slots }) => () => h('span', slots.default?.()) }))
  app.component('ElTag', defineComponent({
    setup: (_, { slots }) => () => h('span', { class: 'stub-tag' }, slots.default?.()),
  }))
  app.component('ElButton', defineComponent({
    props: { disabled: Boolean, loading: Boolean, type: String, size: String },
    emits: ['click'],
    setup: (props, { emit, slots }) => () => h(
      'button',
      { disabled: props.disabled || props.loading, onClick: () => emit('click') },
      slots.default?.(),
    ),
  }))
  app.mount(root)
  cleanup = () => {
    app.unmount()
    root.remove()
  }
  return root
}

function text(root: Element): string {
  return root.textContent || ''
}

beforeEach(() => {
  vi.clearAllMocks()
  makeStore()
})

afterEach(() => {
  cleanup()
  cleanup = () => {}
})

describe('plugin update float window', () => {
  it('only shows progress for a task this popup owns', () => {
    installTask.task = { task_id: 't', status: 'downloading', stage: 'download' }
    installTask.owner = 'panel'
    let root = mount()
    expect(root.querySelector('[data-yui-guide-id="market-install-progress"]')).toBeNull()

    cleanup()
    installTask.owner = 'float'
    root = mount()
    // The progress component is stubbed out by the mocked store module, so
    // assert on the panel wrapper this component owns.
    expect(root.querySelector('.update-float__progress-panel')).not.toBeNull()

    installTask.task = null
    installTask.owner = null
  })

  it('renders nothing while the popup is closed', () => {
    makeStore({ popupOpen: false })
    const root = mount()
    expect(root.querySelector('[data-yui-guide-id="plugin-update-float"]')).toBeNull()
  })

  it('lists every outdated plugin with both versions and its channel', () => {
    makeStore({ candidates: [candidate(), candidate({ pluginId: 'beta', name: 'Beta', latestVersion: '2.0.0', channel: 'beta' })] })
    const root = mount()

    expect(root.querySelectorAll('.update-item')).toHaveLength(2)
    expect(text(root)).toContain('plugins.installSource.channelLabels.stable')
    expect(text(root)).toContain('plugins.installSource.channelLabels.beta')
    expect(text(root)).toContain('1.0.0')
    expect(text(root)).toContain('2.0.0')
    // Title carries the count.
    expect(text(root)).toContain('pluginUpdates.titleWithCount')
  })

  it('says everything is up to date only when nothing was left unchecked', () => {
    makeStore({ candidates: [], unresolved: 0 })
    expect(text(mount())).toContain('pluginUpdates.allUpToDate')

    cleanup()
    makeStore({ candidates: [], unresolved: 2 })
    expect(text(mount())).toContain('pluginUpdates.checkIncomplete')

    // A wholesale failure reports nothing and leaves the list empty, so the
    // "up to date" copy must not be reachable through that path either.
    cleanup()
    makeStore({ candidates: [], checkFailed: true })
    expect(text(mount())).toContain('pluginUpdates.checkIncomplete')
  })

  it('shows the progress hint while the first check runs', () => {
    makeStore({ checking: true, candidates: [] })
    expect(text(mount())).toContain('pluginUpdates.checking')
  })

  it('upgrades a single plugin and confirms it', async () => {
    const store = makeStore({ candidates: [candidate()] })
    store.updateOne.mockImplementation(async () => {
      store.candidates = []
      return true
    })
    const root = mount()

    ;(root.querySelector('.update-item__button') as HTMLButtonElement).click()
    await nextTick()

    expect(store.updateOne).toHaveBeenCalledWith('alpha')
    expect(mocks.messages.success).toHaveBeenCalledWith('pluginUpdates.updateSucceeded')
  })

  it('keeps a failed plugin listed and surfaces its error', async () => {
    const store = makeStore({ candidates: [candidate()] })
    store.updateOne.mockImplementation(async () => {
      store.candidates[0]!.status = 'failed'
      store.candidates[0]!.errorKey = 'market.upgradeRollback'
      return false
    })
    const root = mount()

    ;(root.querySelector('.update-item__button') as HTMLButtonElement).click()
    await nextTick()

    expect(text(root)).toContain('market.upgradeRollback')
    expect(mocks.messages.error).toHaveBeenCalledWith('market.upgradeRollback')
  })

  it('disables the action for plugins that need the Market page', async () => {
    const store = makeStore({ candidates: [candidate({ needsManualUpgrade: true })] })
    const root = mount()

    expect(text(root)).toContain('pluginUpdates.manualRequired')
    const button = root.querySelector('.update-item__button') as HTMLButtonElement
    expect(button.disabled).toBe(true)

    button.click()
    await nextTick()
    expect(store.updateOne).not.toHaveBeenCalled()
  })

  it('updates everything through the batch action and locks the per-item buttons', async () => {
    const store = makeStore({ candidates: [candidate()] })
    const root = mount()

    const buttons = [...root.querySelectorAll('button')]
    const updateAll = buttons.find((button) => button.textContent === 'pluginUpdates.updateAll')
    expect(updateAll).toBeDefined()
    updateAll!.click()
    await nextTick()
    expect(store.updateAll).toHaveBeenCalled()

    cleanup()
    makeStore({
      candidates: [candidate()],
      batchRunning: true,
      batchTotal: 3,
      batchDone: 1,
    })
    const busyRoot = mount()
    expect(text(busyRoot)).toContain('pluginUpdates.updateAllProgress')
    expect((busyRoot.querySelector('.update-item__button') as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps the slot a queued upgrade reserved when the popup closes', async () => {
    installTask.done = true
    installTask.reservation = 'float'
    try {
      const store = makeStore({ candidates: [candidate()] })
      mount()
      store.popupOpen = false
      await nextTick()
      // Regression guard: dismissing here released the next upgrade's slot
      // mid-preflight, letting the Market page start a concurrent worker.
      expect(installTask.dismiss).not.toHaveBeenCalled()

      // The queued upgrade then fails before creating its task and releases
      // the slot: the finished panel must still be cleared, or it reappears
      // next to the failed row when the popup is reopened.
      installTask.reservation = null
      await nextTick()
      expect(installTask.dismiss).toHaveBeenCalledWith('float')
    } finally {
      installTask.done = false
      installTask.reservation = null
    }
  })

  it('locks the per-item buttons while a refresh keeps the previous list', () => {
    // ``check()`` retains the old candidates while it runs, but ``updateOne``
    // refuses during a check — an enabled button would silently do nothing.
    makeStore({ candidates: [candidate()], checking: true })
    const root = mount()
    expect((root.querySelector('.update-item__button') as HTMLButtonElement).disabled).toBe(true)
  })

  it('locks the header refresh while a check or upgrade is running', async () => {
    const store = makeStore({ candidates: [candidate()] })
    const root = mount()
    const refresh = () => [...root.querySelectorAll('.update-float__icon-btn')]
      .find((button) => (button as HTMLElement).title === 'pluginUpdates.refresh') as HTMLButtonElement

    expect(refresh().disabled).toBe(false)

    store.checking = true
    await nextTick()
    expect(refresh().disabled).toBe(true)

    store.checking = false
    store.candidates = [candidate({ status: 'updating' })]
    await nextTick()
    expect(refresh().disabled).toBe(true)
  })

  it('closes through the header control', async () => {
    const store = makeStore({ candidates: [candidate()] })
    const root = mount()

    const close = [...root.querySelectorAll('.update-float__icon-btn')]
      .find((button) => (button as HTMLElement).title === 'pluginUpdates.close')
    expect(close).toBeDefined()
    ;(close as HTMLButtonElement).click()
    await nextTick()

    expect(store.closePopup).toHaveBeenCalled()
  })

  it('re-checks on demand from the header control', async () => {
    const store = makeStore({ candidates: [candidate()] })
    const root = mount()

    const refresh = [...root.querySelectorAll('.update-float__icon-btn')]
      .find((button) => (button as HTMLElement).title === 'pluginUpdates.refresh')
    expect(refresh).toBeDefined()
    ;(refresh as HTMLButtonElement).click()
    await nextTick()

    expect(store.check).toHaveBeenCalledWith({ force: true })
  })
})

describe('plugin update float window — drag', () => {
  const GEOMETRY = { left: 500, top: 101, width: 380, headerBottom: 92 }

  function mountedWithGeometry() {
    makeStore({ candidates: [candidate()] })
    const root = mount()
    const el = floatElement(root)
    stubGeometry(el, GEOMETRY)
    return { root, el, header: el.querySelector('.update-float__header') as HTMLElement }
  }

  function drag(header: HTMLElement, from: [number, number], to: [number, number]) {
    header.dispatchEvent(pointer('pointerdown', { pointerId: 7, button: 0, clientX: from[0], clientY: from[1] }))
    window.dispatchEvent(pointer('pointermove', { pointerId: 7, clientX: to[0], clientY: to[1] }))
  }

  it('starts anchored, without any inline position', () => {
    const { el } = mountedWithGeometry()
    expect(el.style.left).toBe('')
    expect(el.style.top).toBe('')
    expect(el.style.right).toBe('')
  })

  it('follows the pointer once the header is dragged', async () => {
    const { el, header } = mountedWithGeometry()

    drag(header, [520, 120], [440, 220]) // dx -80, dy +100
    await nextTick()

    // offsetLeft 280 → 280 + (420 - 500) = 200; offsetTop 63 → 63 + (201 - 101) = 163
    expect(el.style.left).toBe('200px')
    expect(el.style.top).toBe('163px')
    expect(el.style.right).toBe('auto')
  })

  it('returns to the anchored corner when the viewport is resized', async () => {
    const { el, header } = mountedWithGeometry()

    drag(header, [520, 120], [440, 220])
    window.dispatchEvent(pointer('pointerup', { pointerId: 7 }))
    await nextTick()
    expect(el.style.left).toBe('200px')

    // Regression guard: offsets were only clamped while dragging, so a shrunk
    // app window could leave the popup (and its close button) off-screen.
    window.dispatchEvent(new Event('resize'))
    await nextTick()
    expect(el.style.left).toBe('')
    expect(el.style.top).toBe('')
  })

  it('ends a drag in progress when the viewport is resized', async () => {
    const { el, header } = mountedWithGeometry()

    drag(header, [520, 120], [440, 220])
    await nextTick()
    expect(el.style.left).toBe('200px')

    // Bounds were measured against the old viewport: the drag must stop and
    // the window return to its anchor, not keep following stale limits.
    window.dispatchEvent(new Event('resize'))
    window.dispatchEvent(pointer('pointermove', { pointerId: 7, clientX: 300, clientY: 220 }))
    await nextTick()
    expect(el.style.left).toBe('')
    expect(el.style.top).toBe('')
  })

  it('never lets the window be dragged over the app header', async () => {
    const { el, header } = mountedWithGeometry()

    drag(header, [520, 120], [520, -400])
    await nextTick()

    // Clamped to the header's bottom edge (92) → 63 + (92 - 101) = 54, i.e. the
    // header's own height, so the titlebar controls stay reachable.
    expect(el.style.top).toBe('54px')
  })

  it('keeps a sliver of the window inside the viewport', async () => {
    const { el, header } = mountedWithGeometry()

    header.dispatchEvent(pointer('pointerdown', { pointerId: 7, button: 0, clientX: 520, clientY: 120 }))
    window.dispatchEvent(pointer('pointermove', { pointerId: 7, clientX: -9000, clientY: 120 }))
    await nextTick()

    // Left edge pinned to the 8px viewport inset: 280 + (8 - 500) = -212.
    expect(el.style.left).toBe('-212px')
  })

  it('ignores a drag started on the header buttons', async () => {
    const { el, header } = mountedWithGeometry()
    const refresh = header.querySelector('.update-float__icon-btn') as HTMLElement

    refresh.dispatchEvent(pointer('pointerdown', { pointerId: 9, button: 0, clientX: 520, clientY: 120 }))
    window.dispatchEvent(pointer('pointermove', { pointerId: 9, clientX: 300, clientY: 400 }))
    await nextTick()

    expect(el.style.left).toBe('')
    expect(el.style.top).toBe('')
  })

  it('ignores right-click and stops tracking once the pointer is released', async () => {
    const { el, header } = mountedWithGeometry()

    drag(header, [520, 120], [440, 220])
    await nextTick()
    expect(el.style.left).toBe('200px')

    window.dispatchEvent(pointer('pointerup', { pointerId: 7, clientX: 440, clientY: 220 }))
    await nextTick()

    // A second pointermove after release must not move the window any further.
    window.dispatchEvent(pointer('pointermove', { pointerId: 7, clientX: 900, clientY: 700 }))
    await nextTick()
    expect(el.style.left).toBe('200px')

    header.dispatchEvent(pointer('pointerdown', { pointerId: 11, button: 2, clientX: 400, clientY: 200 }))
    await nextTick()
    expect(el.style.left).toBe('200px')
  })

  it('returns to the anchored corner when the popup is reopened', async () => {
    const store = makeStore({ candidates: [candidate()] })
    const root = mount()
    const el = floatElement(root)
    stubGeometry(el, GEOMETRY)
    const header = el.querySelector('.update-float__header') as HTMLElement

    drag(header, [520, 120], [440, 220])
    await nextTick()
    expect(el.style.left).toBe('200px')

    store.popupOpen = false
    await nextTick()
    store.popupOpen = true
    await nextTick()

    const reopened = floatElement(root)
    expect(reopened.style.left).toBe('')
    expect(reopened.style.top).toBe('')
  })
})
