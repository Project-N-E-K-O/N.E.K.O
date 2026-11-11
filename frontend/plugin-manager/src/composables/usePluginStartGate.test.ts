// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usePluginStore } from '@/stores/plugin'
import { startPlugin, reloadPlugin } from '@/api/plugins'
import { getPluginConsent, approvePluginConsent } from '@/api/pluginConsent'
import { usePluginConsentState } from '@/stores/pluginConsentState'
import { usePluginStartGate } from './usePluginStartGate'
import { ElMessage } from 'element-plus'
import type { PluginConsent } from '@/api/pluginConsent'

vi.mock('@/i18n', () => ({
  getLocale: () => 'en-US',
  i18n: { global: { t: (key: string) => key } },
}))
vi.mock('element-plus', async (original) => ({
  ...(await original<object>()),
  ElMessage: { warning: vi.fn(), error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))
vi.mock('@/api/plugins', () => ({
  getPlugins: vi.fn(),
  getPlugin: vi.fn(),
  getPluginSummaries: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  reloadAllPlugins: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
  setPluginAutoStart: vi.fn(),
}))
vi.mock('@/api/pluginConsent', () => ({
  getPluginConsent: vi.fn(),
  approvePluginConsent: vi.fn(),
  rejectPluginConsent: vi.fn(),
  revokePluginConsent: vi.fn(),
  getPluginConsentNotices: vi.fn(),
  analyzePluginCapabilities: vi.fn(),
}))
vi.mock('@/api/config', () => ({
  getPluginConfigApplicationState: vi.fn().mockResolvedValue({ config_state: 'matched' }),
}))

function consent(state: PluginConsent['state'], extras: Partial<PluginConsent> = {}): PluginConsent {
  return {
    plugin_id: 'demo',
    version: '1',
    decision: null,
    decided_at: null,
    approved_capabilities: [],
    unconfirmed: state === 'confirm' ? ['memory.read'] : [],
    state,
    upgraded: false,
    analysis: { status: 'completed', capabilities: [], evidence: [], warnings: [] },
    ...extras,
  }
}

function fixture(consentState: PluginConsent['state'] | null) {
  const store = usePluginStore()
  store.pluginSummaries = [
    {
      id: 'demo',
      name: 'Demo',
      description: '',
      version: '1',
      consent:
        consentState === null
          ? undefined
          : { state: consentState, decision: null, upgraded: false },
    },
  ]
  return store
}

function mockConfirmation(approved: boolean) {
  return vi
    .spyOn(usePluginConsentState(), 'requestConfirmation')
    .mockImplementation(async (plugins) => {
      if (!approved) return false
      for (const plugin of plugins) await usePluginConsentState().approve(plugin.id)
      return true
    })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(startPlugin)
    .mockReset()
    .mockResolvedValue({ success: true, plugin_id: 'demo', message: '' })
  vi.mocked(reloadPlugin).mockReset().mockResolvedValue({ success: true, plugin_id: 'demo', message: '' })
  vi.mocked(getPluginConsent).mockImplementation(async (id: string) => ({
    ...consent('confirm'),
    plugin_id: id,
  }))
  vi.mocked(approvePluginConsent).mockImplementation(async (id: string) => ({
    ...consent('clear', { decision: 'approved', decided_at: 'now' }),
    plugin_id: id,
  }))
})
afterEach(() => {
  vi.useRealTimers()
})

describe('manual start consent gate', () => {
  it('a clear plugin starts without a consent request', async () => {
    vi.mocked(getPluginConsent).mockResolvedValue(consent('clear'))
    fixture('clear')
    const request = vi.spyOn(usePluginConsentState(), 'requestConfirmation')
    await expect(usePluginStartGate().startWithConsent('demo')).resolves.toBe(true)
    expect(startPlugin).toHaveBeenCalledOnce()
    expect(request).not.toHaveBeenCalled()
  })

  it('first click on an analyzing plugin warns and does not start; a second within 30s starts', async () => {
    vi.useFakeTimers()
    vi.mocked(getPluginConsent).mockResolvedValue(consent('analyzing', {
      analysis: { status: 'queued', capabilities: [], evidence: [], warnings: [] },
    }))
    fixture('analyzing')
    const gate = usePluginStartGate()
    await expect(gate.startWithConsent('demo')).resolves.toBe(false)
    expect(startPlugin).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledOnce()

    await expect(gate.startWithConsent('demo')).resolves.toBe(true)
    expect(startPlugin).toHaveBeenCalledOnce()
  })

  it('warns again once the 30 second bypass expires', async () => {
    vi.useFakeTimers()
    vi.mocked(getPluginConsent).mockResolvedValue(consent('analyzing'))
    fixture('analyzing')
    const gate = usePluginStartGate()
    await expect(gate.startWithConsent('demo')).resolves.toBe(false)
    vi.setSystemTime(Date.now() + 31_000)
    await expect(gate.startWithConsent('demo')).resolves.toBe(false)
    expect(startPlugin).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledTimes(2)
  })

  it('confirm plugins go through one dialog and start on approve', async () => {
    fixture('confirm')
    const request = mockConfirmation(true)
    const gate = usePluginStartGate()
    await expect(gate.startWithConsent('demo')).resolves.toBe(true)
    expect(request).toHaveBeenCalledWith([
      { id: 'demo', name: 'Demo', version: '1' },
    ])
    expect(approvePluginConsent).toHaveBeenCalledWith('demo')
    expect(startPlugin).toHaveBeenCalledOnce()
  })

  it('does not start when the dialog is cancelled', async () => {
    fixture('confirm')
    mockConfirmation(false)
    await expect(usePluginStartGate().startWithConsent('demo')).resolves.toBe(false)
    expect(startPlugin).not.toHaveBeenCalled()
  })

  it('batch preparation partitions clear, analyzing, and confirm plugins', async () => {
    const store = fixture('confirm')
    store.pluginSummaries.push(
      { id: 'analyzing', name: 'Analyzing', description: '', version: '1', consent: { state: 'analyzing', decision: null, upgraded: false } },
      { id: 'clear', name: 'Clear', description: '', version: '1', consent: { state: 'clear', decision: null, upgraded: false } },
    )
    vi.mocked(getPluginConsent).mockImplementation(async (id: string) => ({
      ...consent(id === 'analyzing' ? 'analyzing' : id === 'clear' ? 'clear' : 'confirm'),
      plugin_id: id,
    }))
    const request = mockConfirmation(true)
    const { allowed, dialogShown } = await usePluginStartGate().prepareBatchStart([
      'demo', 'analyzing', 'clear',
    ])
    expect(dialogShown).toBe(true)
    expect(allowed.sort()).toEqual(['clear', 'demo'])
    expect(request).toHaveBeenCalledWith([
      { id: 'demo', name: 'Demo', version: '1' },
    ])
    for (const id of allowed) await store.start(id, { refresh: false })
    expect(startPlugin).toHaveBeenCalledTimes(2)
  })

  it('a cancelled batch dialog still starts the clear plugins', async () => {
    const store = fixture('confirm')
    store.pluginSummaries.push({
      id: 'clear', name: 'Clear', description: '', version: '1',
      consent: { state: 'clear', decision: null, upgraded: false },
    })
    mockConfirmation(false)
    vi.mocked(getPluginConsent).mockImplementation(async (id: string) => ({
      ...consent(id === 'clear' ? 'clear' : 'confirm'),
      plugin_id: id,
    }))
    const { allowed } = await usePluginStartGate().prepareBatchStart(['demo', 'clear'])
    expect(allowed).toEqual(['clear'])
  })

  it('store.start and reload never consult consent', async () => {
    const store = fixture('confirm')
    await store.start('demo', { refresh: false })
    await store.reload('demo', { refresh: false })
    expect(startPlugin).toHaveBeenCalledOnce()
    expect(reloadPlugin).toHaveBeenCalledOnce()
    expect(getPluginConsent).not.toHaveBeenCalled()
  })
})
