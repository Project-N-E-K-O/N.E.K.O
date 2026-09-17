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

/** The event another window receives for one of its storage keys. */
function crossWindowEvent(key: string): Event {
  const event = new Event('storage')
  Object.defineProperties(event, { key: { value: key }, newValue: { value: '1' } })
  return event
}

/** Another window changed the pending-reload flag for this plugin. */
const crossWindowPending = (pluginId: string) =>
  crossWindowEvent(`neko-plugin-config-pending-reload:${pluginId}`)

/** Another window persisted a profile for this plugin. */
const crossWindowProfileWrite = (pluginId: string) =>
  crossWindowEvent(`neko-plugin-config-profile-revision:${pluginId}`)

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
  it('rebases the local draft when another window saved the profile', async () => {
    // Window A added `search`; this window edited `cache`. Saving the stale draft
    // unchanged would drop A's addition, so the local edit is rebased onto it.
    let stored: Record<string, unknown> = { cache: { ttl: 1 } }
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'prod',
        files: { prod: { path: 'prod.toml', resolved_path: null, exists: true } },
      },
    }))
    vi.mocked(getPluginProfileConfig).mockImplementation(async () => ({
      plugin_id: 'alpha',
      profile: { name: 'prod', path: 'prod.toml', resolved_path: null, exists: true },
      config: stored,
    }))

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.selected.value).toBe('prod')

    drafts.updateDraft({ cache: { ttl: 9 } })
    expect(drafts.dirty.value).toBe(true)

    // Another window saved the profile and this window received the event.
    stored = { cache: { ttl: 1 }, search: { query: 'neko' } }
    window.dispatchEvent(crossWindowProfileWrite('alpha'))
    await vi.waitFor(() =>
      expect(drafts.current.value?.draft).toEqual({
        cache: { ttl: 9 },
        search: { query: 'neko' },
      })
    )
    expect(drafts.current.value?.original).toEqual({
      cache: { ttl: 1 },
      search: { query: 'neko' },
    })
    // Only the local edit is still unsaved.
    expect(drafts.changes.value.map((change) => change.path.join('.'))).toEqual(['cache.ttl'])
  })

  it('replaces an untouched cached draft with the saved content', async () => {
    let stored: Record<string, unknown> = { cache: { ttl: 1 } }
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'prod',
        files: { prod: { path: 'prod.toml', resolved_path: null, exists: true } },
      },
    }))
    vi.mocked(getPluginProfileConfig).mockImplementation(async () => ({
      plugin_id: 'alpha',
      profile: { name: 'prod', path: 'prod.toml', resolved_path: null, exists: true },
      config: stored,
    }))
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

    stored = { cache: { ttl: 5 } }
    window.dispatchEvent(crossWindowProfileWrite('alpha'))
    await vi.waitFor(() => expect(drafts.current.value?.draft).toEqual({ cache: { ttl: 5 } }))
    expect(drafts.dirty.value).toBe(false)
  })

  it('discards a superseded profile refresh', async () => {
    // Two external events start two refreshes; the earlier one returns last and must
    // not put back the content the newer one already replaced.
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'prod',
        files: { prod: { path: 'prod.toml', resolved_path: null, exists: true } },
      },
    }))
    let stored: Record<string, unknown> = { cache: { ttl: 1 } }
    let hangNext = false
    const stale = deferred<unknown>()
    vi.mocked(getPluginProfileConfig).mockImplementation(async () => {
      // Only the first refresh hangs, so the second one answers with newer content.
      const config = hangNext ? await ((hangNext = false), stale.promise) : stored
      return {
        plugin_id: 'alpha',
        profile: { name: 'prod', path: 'prod.toml', resolved_path: null, exists: true },
        config: config as Record<string, unknown>,
      } as never
    })

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

    stored = { cache: { ttl: 2 } }
    hangNext = true
    window.dispatchEvent(crossWindowProfileWrite('alpha'))
    await settle()
    window.dispatchEvent(crossWindowProfileWrite('alpha'))
    await vi.waitFor(() => expect(drafts.current.value?.draft).toEqual({ cache: { ttl: 2 } }))

    // The earlier response arrives last and carries older content.
    stale.resolve({ cache: { ttl: 100 } })
    await settle()
    await settle()

    expect(drafts.current.value?.draft).toEqual({ cache: { ttl: 2 } })
    expect(drafts.current.value?.original).toEqual({ cache: { ttl: 2 } })
  })

  it('broadcasts a deleted profile', async () => {
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
    vi.mocked(deletePluginProfileConfig).mockResolvedValue({
      plugin_id: 'alpha',
      profile: 'other',
      removed: true,
    } as never)

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

    await drafts.deleteProfile('other')

    // Another window holding this profile must drop it, not recreate it on save.
    expect(localStorage.getItem('neko-plugin-config-profile-revision:alpha')).not.toBeNull()
  })

  it('broadcasts a newly created profile', async () => {
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'prod',
        files: { prod: { path: 'prod.toml', resolved_path: null, exists: true } },
      },
    }))
    vi.mocked(getPluginProfileConfig).mockResolvedValue({
      plugin_id: 'alpha',
      profile: { name: 'extra', path: 'extra.toml', resolved_path: null, exists: true },
      config: {},
    } as never)
    vi.mocked(upsertPluginProfileConfig).mockResolvedValue({
      plugin_id: 'alpha',
      profile: { name: 'extra', path: 'extra.toml', resolved_path: null, exists: true },
      config: {},
    } as never)

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

    await drafts.createProfile('extra')

    // Other windows must refresh to see the new profile in their lists.
    expect(localStorage.getItem('neko-plugin-config-profile-revision:alpha')).not.toBeNull()
  })

  it('broadcasts a non-active profile save without a pending reload', async () => {
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
    // Other windows hold a stale draft for this profile and must be told to refresh…
    expect(localStorage.getItem('neko-plugin-config-profile-revision:alpha')).not.toBeNull()
    // …but the running host is unaffected, so no reload is pending.
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

  it('does not warn when another window activated a different profile', async () => {
    // The save started with no active profile, but by the time it finished another
    // window had activated a different one, so this host is not waiting on us.
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
})
