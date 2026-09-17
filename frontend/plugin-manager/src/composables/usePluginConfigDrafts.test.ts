// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, ref, type EffectScope } from 'vue'
import {
  getPluginConfig,
  getPluginEffectiveBaseConfig,
  getPluginProfileConfig,
  getPluginProfilesState,
  upsertPluginProfileConfig,
} from '@/api/config'
import { usePluginConfigDrafts } from './usePluginConfigDrafts'
import { hasPendingReload } from '@/utils/pendingReload'

vi.mock('@/api/config', () => ({
  getPluginEffectiveBaseConfig: vi.fn(),
  getPluginConfig: vi.fn(),
  getPluginProfilesState: vi.fn(),
  getPluginProfileConfig: vi.fn(),
  upsertPluginProfileConfig: vi.fn(),
  deletePluginProfileConfig: vi.fn(),
  setPluginActiveProfile: vi.fn(),
}))

// A plugin with no persisted profile list exposes the virtual `default` profile,
// which is also the active one.
const emptyState = (pluginId: string) => ({
  plugin_id: pluginId,
  profiles_path: 'profiles',
  profiles_exists: false,
  config_profiles: null,
})

let scope: EffectScope | undefined

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => (resolve = res))
  return { promise, resolve }
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(getPluginEffectiveBaseConfig).mockResolvedValue({
    plugin_id: 'x',
    config: { cache: { ttl: 1 } },
  } as never)
  vi.mocked(getPluginConfig).mockResolvedValue({ plugin_id: 'x', config: {} } as never)
  vi.mocked(getPluginProfilesState).mockImplementation(async (pluginId: string) =>
    emptyState(pluginId)
  )
  vi.mocked(getPluginProfileConfig).mockResolvedValue({
    plugin_id: 'x',
    profile: { name: 'default', path: 'default.toml', resolved_path: null, exists: true },
    config: {},
  } as never)
  vi.mocked(upsertPluginProfileConfig).mockResolvedValue({
    plugin_id: 'x',
    profile: { name: 'default', path: 'default.toml', resolved_path: null, exists: true },
    config: { cache: { ttl: 9 } },
  } as never)
})

afterEach(() => {
  scope?.stop()
  scope = undefined
  localStorage.clear()
})

describe('config draft lifecycle', () => {
  it('scopes a late save to the plugin that issued it', async () => {
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.selected.value).toBe('default')

    // Hold the profile-state request that the save triggers.
    const blocked = deferred<unknown>()
    let blockNext = false
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => {
      if (blockNext) {
        blockNext = false
        await blocked.promise
      }
      return emptyState(id)
    })

    drafts.updateDraft({ cache: { ttl: 9 } })
    blockNext = true
    const saving = drafts.saveProfile()
    await settle()

    // The user moves to another plugin while the save is still in flight.
    pluginId.value = 'beta'
    await settle()
    blocked.resolve(undefined)
    await saving
    await settle()

    // The pending flag belongs to the plugin that saved, not the one on screen.
    expect(hasPendingReload('alpha')).toBe(true)
    expect(hasPendingReload('beta')).toBe(false)
    expect(drafts.pendingApplication.value).toBe(false)
  })
})
