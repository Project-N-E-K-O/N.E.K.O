import { computed, onScopeDispose, reactive, ref, watch, type Ref } from 'vue'
import * as api from '@/api/config'
import {
  configChanges,
  configEqual,
  deepClone,
  restoreConfigPath,
  type ConfigObject,
} from '@/utils/configEditor'
import {
  hasPendingReload,
  pendingRevision,
  setPendingReload,
  subscribePendingReload,
} from '@/utils/pendingReload'

interface ProfileDraft {
  original: ConfigObject
  draft: ConfigObject
  loaded: boolean
  loading: boolean
  error: string | null
}

export function usePluginConfigDrafts(pluginId: Readonly<Ref<string>>) {
  const base = ref<ConfigObject>({})
  const effective = ref<ConfigObject>({})
  const profiles = ref<api.PluginProfilesState | null>(null)
  const configPath = ref<string>()
  const lastModified = ref<string>()
  const selected = ref<string | null>(null)
  const records = reactive(new Map<string, ProfileDraft>())
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)
  const ready = ref(false)
  // True while the running host may not match the persisted configuration.
  const pendingApplication = ref(false)
  let releasePendingSubscription: (() => void) | undefined
  let generation = 0
  let loadVersion = 0
  const requests = new Map<string, Promise<void>>()

  const persistedNames = computed(() =>
    Object.keys(profiles.value?.config_profiles?.files || {}).sort()
  )
  const names = computed(() =>
    profiles.value ? (persistedNames.value.length ? persistedNames.value : ['default']) : []
  )
  const active = computed(
    () =>
      profiles.value?.config_profiles?.active ||
      (profiles.value && !persistedNames.value.length ? 'default' : null)
  )
  const current = computed(() => (selected.value ? records.get(selected.value) : undefined))
  const changes = computed(() =>
    current.value?.loaded ? configChanges(current.value.original, current.value.draft) : []
  )
  const dirty = computed(
    () => !!current.value?.loaded && !configEqual(current.value.original, current.value.draft)
  )
  const anyDirty = computed(() =>
    [...records.values()].some((r) => r.loaded && !configEqual(r.original, r.draft))
  )
  const canSave = computed(
    () =>
      ready.value &&
      !!profiles.value &&
      !!current.value?.loaded &&
      !current.value.error &&
      !loading.value &&
      !saving.value
  )
  const virtualDefault = (name: string) =>
    !!profiles.value && name === 'default' && !persistedNames.value.length
  const dirtyCount = (name: string) => {
    const r = records.get(name)
    return r?.loaded ? configChanges(r.original, r.draft).length : 0
  }
  const valid = (id: string, epoch: number) => id === pluginId.value && epoch === generation
  const message = (err: unknown) => (err instanceof Error ? err.message : String(err))

  // Storage is written for the plugin that performed the operation, while the
  // in-memory flag only follows it while that plugin is still the current one.
  // The revision captured at the start of the operation keeps a late result from
  // overriding a reload or start that happened while it was in flight.
  function setPendingApplication(
    pending: boolean,
    forPluginId = pluginId.value,
    revision?: number
  ) {
    const applied = setPendingReload(forPluginId, pending, revision)
    if (applied && forPluginId === pluginId.value) pendingApplication.value = pending
  }
  async function loadProfile(name: string): Promise<void> {
    if (records.get(name)?.loaded) return
    if (requests.has(name)) return requests.get(name)!
    const id = pluginId.value,
      epoch = generation
    const record = reactive<ProfileDraft>({
      original: {},
      draft: {},
      loaded: false,
      loading: true,
      error: null,
    })
    records.set(name, record)
    const request = (async () => {
      try {
        const config = virtualDefault(name)
          ? {}
          : (await api.getPluginProfileConfig(id, name)).config || {}
        if (!valid(id, epoch) || records.get(name) !== record) return
        record.original = deepClone(config)
        record.draft = deepClone(config)
        record.loaded = true
      } catch (err) {
        if (valid(id, epoch) && records.get(name) === record) record.error = message(err)
      } finally {
        if (valid(id, epoch) && records.get(name) === record) {
          record.loading = false
          requests.delete(name)
        }
      }
    })()
    requests.set(name, request)
    await request
    if (requests.get(name) === request) requests.delete(name)
  }

  async function loadAll(discardDrafts = false): Promise<void> {
    const id = pluginId.value,
      epoch = generation,
      version = ++loadVersion
    if (!id) return
    loading.value = true
    ready.value = false
    error.value = null
    try {
      const [baseResult, effectiveResult, profileResult] = await Promise.all([
        api.getPluginEffectiveBaseConfig(id),
        api.getPluginConfig(id),
        api.getPluginProfilesState(id),
      ])
      if (!valid(id, epoch) || version !== loadVersion) return
      if (discardDrafts) {
        records.clear()
        requests.clear()
      }
      base.value = baseResult.config || {}
      effective.value = effectiveResult.config || {}
      profiles.value = profileResult
      ready.value = true
      configPath.value = baseResult.config_path || effectiveResult.config_path
      lastModified.value = baseResult.last_modified || effectiveResult.last_modified
      if (!selected.value || !names.value.includes(selected.value))
        selected.value =
          active.value && names.value.includes(active.value) ? active.value : names.value[0] || null
      if (selected.value) await loadProfile(selected.value)
    } catch (err) {
      if (valid(id, epoch) && version === loadVersion) error.value = message(err)
    } finally {
      if (valid(id, epoch) && version === loadVersion) loading.value = false
    }
  }

  async function selectProfile(name: string) {
    if (saving.value || !names.value.includes(name)) return
    selected.value = name
    await loadProfile(name)
  }
  function updateDraft(value: ConfigObject | null) {
    if (current.value?.loaded) current.value.draft = value || {}
  }
  function undoAll() {
    if (current.value?.loaded) current.value.draft = deepClone(current.value.original)
  }
  function undoField(path: string[]) {
    if (current.value?.loaded)
      current.value.draft = restoreConfigPath(current.value.draft, current.value.original, path)
  }

  async function saveProfile(): Promise<string | null> {
    if (!canSave.value || !selected.value || !current.value) return null
    const id = pluginId.value,
      epoch = generation,
      name = selected.value,
      record = current.value
    const snapshot = deepClone(record.draft)
    // Captured before the request so a later plugin switch cannot change the answer.
    const appliesToRunningHost = name === active.value
    const revision = pendingRevision(id)
    saving.value = true
    error.value = null
    try {
      const result = await api.upsertPluginProfileConfig(id, name, snapshot, virtualDefault(name))
      if (!valid(id, epoch)) {
        if (appliesToRunningHost) setPendingApplication(true, id, revision)
        return null
      }
      // Saving an earlier snapshot must not erase edits typed while it was in flight.
      record.original = deepClone(result.config || snapshot)
      await loadAll()
      // Only the active profile changes what the running host should be using.
      if (appliesToRunningHost) setPendingApplication(true, id, revision)
      return valid(id, epoch) ? name : null
    } catch (err) {
      if (valid(id, epoch)) error.value = message(err)
      return null
    } finally {
      if (valid(id, epoch)) saving.value = false
    }
  }

  async function createProfile(name: string) {
    const id = pluginId.value,
      epoch = generation
    saving.value = true
    try {
      // Keep the existing first-profile auto-activation behavior on the server.
      await api.upsertPluginProfileConfig(id, name, {}, false)
      if (!valid(id, epoch)) return
      await loadAll()
      if (!valid(id, epoch) || !names.value.includes(name)) return
      selected.value = name
      await loadProfile(name)
    } finally {
      if (valid(id, epoch)) saving.value = false
    }
  }
  async function deleteProfile(name: string) {
    const id = pluginId.value,
      epoch = generation
    const wasActive = name === active.value
    const revision = pendingRevision(id)
    saving.value = true
    try {
      await api.deletePluginProfileConfig(id, name)
      // Deleting the active profile leaves the host running its configuration,
      // so it still needs a reload; other deletions change nothing at runtime.
      if (wasActive) setPendingApplication(true, id, revision)
      if (!valid(id, epoch)) return
      records.delete(name)
      await loadAll()
    } finally {
      if (valid(id, epoch)) saving.value = false
    }
  }
  async function activateProfile(name: string) {
    const id = pluginId.value,
      epoch = generation
    const revision = pendingRevision(id)
    saving.value = true
    try {
      const result = await api.setPluginActiveProfile(id, name)
      // Activation always changes what the host should be running.
      setPendingApplication(true, id, revision)
      if (!valid(id, epoch)) return
      profiles.value = result
      await loadAll()
    } finally {
      if (valid(id, epoch)) saving.value = false
    }
  }

  watch(
    pluginId,
    () => {
      generation++
      loadVersion++
      records.clear()
      requests.clear()
      pendingApplication.value = hasPendingReload(pluginId.value)
      selected.value = null
      profiles.value = null
      base.value = {}
      effective.value = {}
      ready.value = false
      configPath.value = undefined
      lastModified.value = undefined
      saving.value = false
      void loadAll()
    },
    { immediate: true }
  )
  onScopeDispose(() => {
    generation++
    loadVersion++
    releasePendingSubscription?.()
  })

  // A reload or start performed elsewhere (detail header, list, context menu) must
  // clear the warning on an already mounted editor.
  releasePendingSubscription = subscribePendingReload((changedId, pending) => {
    if (changedId === pluginId.value) pendingApplication.value = pending
  })

  return {
    base,
    effective,
    profiles,
    configPath,
    lastModified,
    selected,
    current,
    records,
    names,
    active,
    loading,
    saving,
    error,
    changes,
    dirty,
    anyDirty,
    canSave,
    pendingApplication,
    setPendingApplication,
    virtualDefault,
    dirtyCount,
    loadAll,
    selectProfile,
    updateDraft,
    undoAll,
    undoField,
    saveProfile,
    createProfile,
    deleteProfile,
    activateProfile,
  }
}
