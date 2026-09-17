// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, ref, type EffectScope } from 'vue'
import {
  deletePluginProfileConfig,
  getPluginConfig,
  getPluginEffectiveBaseConfig,
  getPluginProfileConfig,
  getPluginProfilesState,
  upsertPluginProfileConfig,
} from '@/api/config'
import { usePluginConfigDrafts } from './usePluginConfigDrafts'
import { hasPendingReload, setPendingReload } from '@/utils/pendingReload'

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
  // The flags live in the module, so clear the ones these tests touch.
  for (const pluginId of ['alpha', 'beta']) setPendingReload(pluginId, false)
  localStorage.clear()
})

describe('config draft lifecycle', () => {
  it('does not flag a pending reload for a non-active profile save', async () => {
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'prod',
        files: {
          prod: { path: 'prod.toml', resolved_path: null, exists: true },
          other: { path: 'other.toml', resolved_path: null, exists: true },
        },
      },
    }))
    vi.mocked(getPluginProfileConfig).mockResolvedValue({
      plugin_id: 'alpha',
      profile: { name: 'other', path: 'other.toml', resolved_path: null, exists: true },
      config: { cache: { ttl: 1 } },
    } as never)
    const upsert = vi.mocked(upsertPluginProfileConfig).mockResolvedValue({
      plugin_id: 'alpha',
      profile: { name: 'other', path: 'other.toml', resolved_path: null, exists: true },
      config: { cache: { ttl: 9 } },
    } as never)

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    await drafts.selectProfile('other')
    drafts.updateDraft({ cache: { ttl: 9 } })
    await drafts.saveProfile()

    expect(upsert).toHaveBeenCalledWith('alpha', 'other', { cache: { ttl: 9 } }, false)
    // The host is not running this profile, so nothing is waiting to be applied.
    expect(hasPendingReload('alpha')).toBe(false)
  })

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

  it('marks a save that implicitly becomes the active profile', async () => {
    // After the active profile is deleted the plugin has none; saving another
    // profile makes it active server-side, so the host now needs a reload.
    let active: string | null = null
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active,
        files: { other: { path: 'other.toml', resolved_path: null, exists: true } },
      },
    }))
    vi.mocked(upsertPluginProfileConfig).mockImplementation(async () => {
      active = 'other'
      return {
        plugin_id: 'alpha',
        profile: { name: 'other', path: 'other.toml', resolved_path: null, exists: true },
        config: { cache: { ttl: 9 } },
      } as never
    })
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.active.value).toBeNull()

    drafts.updateDraft({ cache: { ttl: 9 } })
    await drafts.saveProfile()

    expect(drafts.active.value).toBe('other')
    expect(hasPendingReload('alpha')).toBe(true)
  })

  it('keeps the implicit activation when the refresh after saving fails', async () => {
    // The server activates the saved profile, but refreshing the profile state
    // fails, so the host still needs a reload and the snapshot is all we have.
    let active: string | null = null
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active,
        files: { other: { path: 'other.toml', resolved_path: null, exists: true } },
      },
    }))
    vi.mocked(upsertPluginProfileConfig).mockImplementation(async () => {
      active = 'other'
      return {
        plugin_id: 'alpha',
        profile: { name: 'other', path: 'other.toml', resolved_path: null, exists: true },
        config: { cache: { ttl: 9 } },
      } as never
    })
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.active.value).toBeNull()

    vi.mocked(getPluginEffectiveBaseConfig).mockRejectedValue(new Error('refresh failed'))
    drafts.updateDraft({ cache: { ttl: 9 } })
    await drafts.saveProfile()

    expect(drafts.active.value).toBeNull()
    expect(hasPendingReload('alpha')).toBe(true)
  })

  it('does not warn when a different profile became active during the save', async () => {
    // The save started with no active profile, but by the time it finished a different
    // one had been activated, so this host is not waiting on us.
    let active: string | null = null
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active,
        files: {
          mine: { path: 'mine.toml', resolved_path: null, exists: true },
          other: { path: 'other.toml', resolved_path: null, exists: true },
        },
      },
    }))
    vi.mocked(upsertPluginProfileConfig).mockImplementation(async () => {
      active = 'other'
      return {
        plugin_id: 'alpha',
        profile: { name: 'mine', path: 'mine.toml', resolved_path: null, exists: true },
        config: { cache: { ttl: 9 } },
      } as never
    })
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.selected.value).toBe('mine')

    drafts.updateDraft({ cache: { ttl: 9 } })
    await drafts.saveProfile()

    expect(drafts.active.value).toBe('other')
    expect(hasPendingReload('alpha')).toBe(false)
  })

  it('keeps the implicit activation when the save is invalidated', async () => {
    // No active profile: the server activates whatever is saved, so the original
    // plugin still needs a reload even if the user left during the request.
    let active: string | null = null
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active,
        files: { other: { path: 'other.toml', resolved_path: null, exists: true } },
      },
    }))
    const blocked = deferred<unknown>()
    vi.mocked(upsertPluginProfileConfig).mockImplementation(async () => {
      active = 'other'
      return {
        plugin_id: 'alpha',
        profile: { name: 'other', path: 'other.toml', resolved_path: null, exists: true },
        config: { cache: { ttl: 9 } },
      } as never
    })

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.active.value).toBeNull()

    let blockNext = false
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => {
      if (blockNext) {
        blockNext = false
        await blocked.promise
      }
      return {
        plugin_id: id,
        profiles_path: 'profiles',
        profiles_exists: true,
        config_profiles: {
          active,
          files: { other: { path: 'other.toml', resolved_path: null, exists: true } },
        },
      }
    })

    drafts.updateDraft({ cache: { ttl: 9 } })
    blockNext = true
    const saving = drafts.saveProfile()
    await settle()
    pluginId.value = 'beta'
    await settle()
    blocked.resolve(undefined)
    await saving
    await settle()

    expect(hasPendingReload('alpha')).toBe(true)
    expect(hasPendingReload('beta')).toBe(false)
  })

  it('still records a late save so the stale host keeps its warning', async () => {
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

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

    // A reload from the plugin list clears the flag while the save is in flight.
    setPendingReload('alpha', false)
    blocked.resolve(undefined)
    await saving
    await settle()

    // The reload may have read the pre-save configuration, so the warning stays
    // until the next reload or start clears it.
    expect(hasPendingReload('alpha')).toBe(true)
  })

  it('follows a pending flag raised and cleared by another entry point', async () => {
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.pendingApplication.value).toBe(false)

    // A flag belonging to another plugin is not this editor's business.
    setPendingReload('beta', true)
    expect(drafts.pendingApplication.value).toBe(false)

    // The plugin list saved this plugin's active profile, so the warning has to appear
    // here as well; reloading there clears it again.
    setPendingReload('alpha', true)
    expect(drafts.pendingApplication.value).toBe(true)
    setPendingReload('alpha', false)
    expect(drafts.pendingApplication.value).toBe(false)
  })

  it('records a delete that finishes after the user left the plugin', async () => {
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'other',
        files: {
          prod: { path: 'prod.toml', resolved_path: null, exists: true },
          other: { path: 'other.toml', resolved_path: null, exists: true },
        },
      },
    }))
    const blocked = deferred<unknown>()
    vi.mocked(deletePluginProfileConfig).mockImplementation(async () => {
      await blocked.promise
      return { plugin_id: 'alpha', profile: 'other', removed: true } as never
    })

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

    const deleting = drafts.deleteProfile('other')
    await settle()
    pluginId.value = 'beta'
    await settle()
    blocked.resolve(undefined)
    await deleting
    await settle()

    // The host still runs the configuration of the profile that was just deleted, so the
    // warning has to be recorded even though this window moved on before it arrived.
    expect(hasPendingReload('alpha')).toBe(true)
    expect(hasPendingReload('beta')).toBe(false)
  })

  it('records a save that finishes after the user left the plugin', async () => {
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

    const blocked = deferred<unknown>()
    vi.mocked(upsertPluginProfileConfig).mockImplementation(async () => {
      await blocked.promise
      return {
        plugin_id: 'alpha',
        profile: { name: 'default', path: 'default.toml', resolved_path: null, exists: true },
        config: { cache: { ttl: 9 } },
      } as never
    })

    drafts.updateDraft({ cache: { ttl: 9 } })
    const saving = drafts.saveProfile()
    await settle()
    pluginId.value = 'beta'
    await settle()
    blocked.resolve(undefined)
    await saving
    await settle()

    // The server holds this write and the host has not reloaded, so the warning must be
    // recorded for the plugin that saved, not the one now on screen.
    expect(hasPendingReload('alpha')).toBe(true)
    expect(hasPendingReload('beta')).toBe(false)
  })

  it('loads a recreated profile instead of reusing its in-flight read', async () => {
    const file = (name: string) => ({ path: `${name}.toml`, resolved_path: null, exists: true })
    let files: Record<string, ReturnType<typeof file>> = {
      prod: file('prod'),
      staging: file('staging'),
    }
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: { active: 'prod', files },
    }))
    const abandoned = deferred<unknown>()
    let hangNext = false
    vi.mocked(getPluginProfileConfig).mockImplementation(async () => {
      if (hangNext) {
        hangNext = false
        await abandoned.promise
      }
      return {
        plugin_id: 'alpha',
        profile: { name: 'staging', path: 'staging.toml', resolved_path: null, exists: true },
        config: { cache: { ttl: 1 } },
      } as never
    })
    vi.mocked(deletePluginProfileConfig).mockResolvedValue({
      plugin_id: 'alpha',
      profile: 'staging',
      removed: true,
    } as never)

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

    // The read for `staging` never answers.
    hangNext = true
    void drafts.selectProfile('staging')
    await settle()
    expect(drafts.records.has('staging')).toBe(true)

    // It is deleted while that read is still in flight.
    files = { prod: file('prod') }
    await drafts.deleteProfile('staging')
    expect(drafts.records.has('staging')).toBe(false)

    // Recreating it has to start a new read: reusing the abandoned promise would select
    // the profile with no record behind it at all.
    files = { prod: file('prod'), staging: file('staging') }
    await drafts.loadAll()
    void drafts.selectProfile('staging')
    await settle()
    expect(drafts.current.value?.loaded).toBe(true)

    // And the abandoned read must not overwrite the recreated record when it answers.
    abandoned.resolve({ config: { cache: { ttl: 99 } } })
    await settle()
    expect(drafts.current.value?.draft).toEqual({ cache: { ttl: 1 } })
  })
})
