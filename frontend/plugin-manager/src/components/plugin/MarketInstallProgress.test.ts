// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, reactive } from 'vue'

import MarketInstallProgress from './MarketInstallProgress.vue'
import type { InstallStep, MarketInstallContext, MarketInstallTask } from '@/stores/marketInstallTask'

const mocks = vi.hoisted(() => ({ store: null as unknown as Record<string, unknown> }))

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/stores/marketInstallTask', () => ({
  useMarketInstallTaskStore: () => mocks.store,
  formatByteCount: (v: number) => String(v),
}))

function step(id: InstallStep['id'], state: InstallStep['state']): InstallStep {
  return { id, labelKey: `market.installStep.${id}`, state }
}

interface StoreOverrides {
  task?: MarketInstallTask | null
  context?: MarketInstallContext | null
  percent?: number
  barStatus?: 'success' | 'exception' | undefined
  stageLabelKey?: string
  errorKey?: string | null
  transferText?: string
  rollback?: MarketInstallTask['rollback']
  steps?: InstallStep[]
  overtime?: boolean
  done?: boolean
  running?: boolean
  detailsExpanded?: boolean
}

function makeStore(overrides: StoreOverrides = {}) {
  const store = reactive({
    task: { task_id: 't', status: 'downloading', stage: 'download', progress: 0.34 } as MarketInstallTask | null,
    context: {
      pluginId: 'neko_live',
      name: 'NEKO Live',
      mode: 'upgrade',
      channel: 'stable',
      fromVersion: '0.1.6',
      toVersion: '0.1.9',
    } as MarketInstallContext | null,
    percent: 34,
    barStatus: undefined,
    stageLabelKey: 'market.installStage.download',
    errorKey: null,
    transferText: '8.5 MB / 25.0 MB · 976.6 KB/s · 17s',
    rollback: null,
    steps: [step('download', 'active'), step('verify', 'pending'), step('replace', 'pending'), step('completed', 'pending')],
    overtime: false,
    detailsExpanded: false,
    done: false,
    running: true,
    toggleDetails: vi.fn(),
    ...overrides,
  })
  mocks.store = store as unknown as Record<string, unknown>
  return store
}

let cleanup = () => {}

function mount(props: Record<string, unknown> = {}) {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(MarketInstallProgress, props)
  app.component('ElIcon', defineComponent({ setup: (_, { slots }) => () => h('span', slots.default?.()) }))
  app.component('ElProgress', defineComponent({
    props: { percentage: Number, status: String, strokeWidth: Number, showText: { type: Boolean, default: true } },
    setup: (p) => () => h('div', {
      class: 'stub-progress',
      'data-percent': String(p.percentage),
      'data-status': p.status ?? '',
      'data-show-text': String(p.showText),
    }),
  }))
  app.component('ElAlert', defineComponent({
    props: { title: String, type: String },
    setup: (p) => () => h('div', { class: 'stub-alert', 'data-type': p.type }, p.title),
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

describe('market install progress', () => {
  it('renders nothing without a task', () => {
    makeStore({ task: null })
    const root = mount()
    expect(root.querySelector('[data-yui-guide-id="market-install-progress"]')).toBeNull()
  })

  it('shows the version transition, stage, transfer details and percentage', () => {
    const root = mount()
    const bar = root.querySelector('.stub-progress') as HTMLElement

    expect(text(root)).toContain('0.1.6 → 0.1.9')
    expect(text(root)).toContain('plugins.installSource.channelLabels.stable')
    expect(text(root)).toContain('market.installStage.download')
    expect(text(root)).toContain('8.5 MB / 25.0 MB · 976.6 KB/s · 17s')
    expect(bar.dataset.percent).toBe('34')
  })

  it('hides the version line and the bar label in compact mode', () => {
    const root = mount({ compact: true, showVersionTransition: false })
    expect(text(root)).not.toContain('0.1.6 → 0.1.9')
    expect((root.querySelector('.stub-progress') as HTMLElement).dataset.showText).toBe('false')
    // …but the percentage still has to be readable somewhere.
    expect(text(root)).toContain('34%')
  })

  it('keeps the checklist behind the details toggle', async () => {
    const store = makeStore()
    const root = mount()

    expect(root.querySelector('[data-yui-guide-id="market-install-details"]')).not.toBeNull()
    expect(root.querySelectorAll('.install-step')).toHaveLength(0)

    ;(root.querySelector('[data-yui-guide-id="market-install-details"]') as HTMLButtonElement).click()
    expect(store.toggleDetails).toHaveBeenCalled()
  })

  it('renders every step with its own state once expanded', () => {
    makeStore({
      detailsExpanded: true,
      steps: [
        step('download', 'done'),
        step('verify', 'done'),
        step('replace', 'failed'),
        step('completed', 'pending'),
      ],
    })
    const root = mount()

    const items = [...root.querySelectorAll('.install-step')]
    expect(items.map((item) => item.getAttribute('data-step'))).toEqual([
      'download', 'verify', 'replace', 'completed',
    ])
    expect(items.map((item) => item.getAttribute('data-state'))).toEqual([
      'done', 'done', 'failed', 'pending',
    ])
    // The download row repeats the byte counters so the pane is self-contained.
    expect(items[0]!.textContent).toContain('8.5 MB / 25.0 MB')
  })

  it('reports the terminal states with their own copy', () => {
    makeStore({
      task: { task_id: 't', status: 'completed', stage: 'completed', progress: 1 },
      done: true,
      running: false,
      percent: 100,
      barStatus: 'success',
      stageLabelKey: 'market.installStep.completed',
    })
    let root = mount()
    expect(text(root)).toContain('market.installCompleted')
    expect((root.querySelector('.stub-progress') as HTMLElement).dataset.status).toBe('success')

    cleanup()
    makeStore({
      task: { task_id: 't', status: 'failed', stage: 'replace', progress: 0.8, error_code: 'download_failed' },
      done: true,
      running: false,
      barStatus: 'exception',
      errorKey: 'market.downloadFailed',
    })
    root = mount()
    expect(text(root)).toContain('market.downloadFailed')
    // The failure copy appears once, not as a status line plus an alert.
    expect((root.querySelectorAll('.install-progress__status') as unknown as Element[]).length).toBe(1)
    expect(root.querySelector('.stub-alert')).toBeNull()

    cleanup()
    makeStore({
      task: { task_id: 't', status: 'canceled', stage: 'canceled', progress: 0.4 },
      done: true,
      running: false,
    })
    expect(text(mount())).toContain('market.installCancelled')
  })

  it('surfaces overtime and rollback outcomes', () => {
    makeStore({ overtime: true })
    let root = mount()
    expect(text(root)).toContain('market.installTakingLonger')

    cleanup()
    makeStore({ overtime: false, rollback: { prepared: true, restored: false, running: true } })
    root = mount()
    expect(text(root)).toContain('market.rollbackRunning')

    cleanup()
    makeStore({ rollback: { prepared: true, restored: true } })
    root = mount()
    expect(text(root)).toContain('market.rollbackCompleted')

    cleanup()
    makeStore({
      task: { task_id: 't', status: 'failed', stage: 'rollback', progress: 0.9, error_code: 'upgrade_rollback_incomplete' },
      done: true,
      running: false,
      rollback: { prepared: true, restored: false },
    })
    root = mount()
    expect(text(root)).toContain('market.rollbackIncomplete')
  })
})
