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
function crossWindowEvent(key: string, newValue: string | null = '1'): Event {
  const event = new Event('storage')
  Object.defineProperties(event, { key: { value: key }, newValue: { value: newValue } })
  return event
}

/** Another window set or cleared the pending-reload flag for this plugin. */
const crossWindowPending = (pluginId: string, pending = true) =>
  crossWindowEvent(`neko-plugin-config-pending-reload:${pluginId}`, pending ? '1' : null)

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

  it('follows a pending reload raised and cleared by another window', async () => {
    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.pendingApplication.value).toBe(false)

    // A flag belonging to another plugin is not this editor's business.
    window.dispatchEvent(crossWindowPending('beta'))
    await settle()
    expect(drafts.pendingApplication.value).toBe(false)

    // Another window saved this plugin's active profile, so the warning has to
    // appear here as well, and reloading there clears it again.
    window.dispatchEvent(crossWindowPending('alpha'))
    await settle()
    expect(drafts.pendingApplication.value).toBe(true)

    window.dispatchEvent(crossWindowPending('alpha', false))
    await settle()
    expect(drafts.pendingApplication.value).toBe(false)
  })

  it('drops a cached draft whose profile was deleted in another window', async () => {
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
    let stored: Record<string, unknown> = { cache: { ttl: 1 } }
    vi.mocked(getPluginProfileConfig).mockImplementation(async () => ({
      plugin_id: 'alpha',
      profile: { name: 'staging', path: 'staging.toml', resolved_path: null, exists: true },
      config: stored,
    }))

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    await drafts.selectProfile('staging')
    expect(drafts.current.value?.draft).toEqual({ cache: { ttl: 1 } })

    // Another window deleted it. The endpoint drops only the mapping, so the orphaned
    // file is still readable and a refresh would put the deleted content back.
    files = { prod: file('prod') }
    window.dispatchEvent(crossWindowProfileWrite('alpha'))
    await vi.waitFor(() => expect(drafts.records.has('staging')).toBe(false))

    // Creating that name again has to start from the new empty profile instead of the
    // cached draft that was deleted.
    files = { prod: file('prod'), staging: file('staging') }
    stored = {}
    await drafts.loadAll()
    await drafts.selectProfile('staging')
    expect(drafts.current.value?.draft).toEqual({})
  })

  it('broadcasts a delete that finishes after the user left the plugin', async () => {
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

    // The mapping is already gone on the server, so a window that still holds this
    // profile must be told even though this one moved on before the response arrived.
    expect(localStorage.getItem('neko-plugin-config-profile-revision:alpha')).not.toBeNull()
    expect(localStorage.getItem('neko-plugin-config-profile-revision:beta')).toBeNull()
  })

  it('broadcasts a save that finishes after the user left the plugin', async () => {
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

    // The server already holds this write, so the other windows have to refresh their
    // stale draft for it even though this window moved on mid-flight.
    expect(localStorage.getItem('neko-plugin-config-profile-revision:alpha')).not.toBeNull()
    expect(localStorage.getItem('neko-plugin-config-profile-revision:beta')).toBeNull()
  })

  it('keeps an unsaved virtual default draft when a profile appears elsewhere', async () => {
    const file = (name: string) => ({ path: `${name}.toml`, resolved_path: null, exists: true })
    // Nothing is persisted yet, so the editor works on the placeholder `default`.
    let files: Record<string, ReturnType<typeof file>> | null = null
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: files ? { active: 'prod', files } : null,
    }))

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.selected.value).toBe('default')
    drafts.updateDraft({ cache: { ttl: 9 } })
    expect(drafts.dirty.value).toBe(true)

    // Another window created the first persisted profile. `default` leaves the list, but
    // nothing deleted it, so the draft being typed into must not be dropped with it.
    files = { prod: file('prod') }
    window.dispatchEvent(crossWindowProfileWrite('alpha'))
    await vi.waitFor(() => expect(drafts.selected.value).toBe('prod'))

    expect(drafts.records.has('default')).toBe(true)
    expect(drafts.anyDirty.value).toBe(true)
  })

  it('loads a recreated profile instead of reusing its pruned in-flight read', async () => {
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

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))

    // The read for `staging` never answers.
    hangNext = true
    void drafts.selectProfile('staging')
    await settle()
    expect(drafts.records.has('staging')).toBe(true)

    // Another window deleted it while that read was still in flight.
    files = { prod: file('prod') }
    window.dispatchEvent(crossWindowProfileWrite('alpha'))
    await vi.waitFor(() => expect(drafts.records.has('staging')).toBe(false))

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

  it('reloads the placeholder after the last persisted profile is deleted elsewhere', async () => {
    const file = (name: string) => ({ path: `${name}.toml`, resolved_path: null, exists: true })
    let files: Record<string, ReturnType<typeof file>> = { default: file('default') }
    vi.mocked(getPluginProfilesState).mockImplementation(async (id: string) => ({
      plugin_id: id,
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: { active: 'default', files },
    }))
    vi.mocked(getPluginProfileConfig).mockResolvedValue({
      plugin_id: 'alpha',
      profile: { name: 'default', path: 'default.toml', resolved_path: null, exists: true },
      config: { cache: { ttl: 1 } },
    } as never)

    const pluginId = ref('alpha')
    scope = effectScope()
    const drafts = scope.run(() => usePluginConfigDrafts(pluginId))!
    await vi.waitFor(() => expect(drafts.current.value?.loaded).toBe(true))
    expect(drafts.current.value?.draft).toEqual({ cache: { ttl: 1 } })

    // Another window deleted the only persisted profile, so `default` is the placeholder
    // again and must not keep showing the content that was deleted.
    files = {}
    window.dispatchEvent(crossWindowProfileWrite('alpha'))
    await vi.waitFor(() => expect(drafts.current.value?.draft).toEqual({}))
  })
})
