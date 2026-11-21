/**
 * What the pages about the task agents read and change.
 *
 * Every path is `/console/...` on the plugin server, which passes the request
 * on to the agent process. A refusal carries `detail.code`; the pages show
 * their own words for it (see `consoleErrorCode`), so the generic toast of the
 * request interceptor is switched off here.
 */
import request from '@/utils/request'
import type { ErrorDisplayRequestConfig } from '@/utils/request'

const quiet: ErrorDisplayRequestConfig = { suppressErrorMessage: true }

export type PermissionMode = 'auto' | 'first_ask' | 'always_ask' | 'allow' | 'deny'

export interface DshStatus {
  version: string
  supported: boolean
  platform?: string | null
  installed: boolean
  installed_at?: number | string | null
  path?: string | null
  download_bytes?: number | null
  downloading: boolean
  downloaded_bytes?: number | null
  last_error: string | null
  last_error_code: string | null
  preference: 'auto' | 'builtin'
  in_use?: boolean
  sessions: number
  fallback_reason?: string | null
}

export interface DshPlugin {
  name: string
  version: string
  enabled: boolean
}

export interface RuntimeView {
  dsh: DshStatus
  plugins: DshPlugin[]
}

export interface PermissionRow {
  category: string
  mode: PermissionMode
  /** What "auto" comes to for this category. */
  auto: Exclude<PermissionMode, 'auto'>
}

export interface PermissionsView {
  version: number
  unreadable: boolean
  modes: PermissionMode[]
  categories: PermissionRow[]
}

export interface ModelWindow {
  model: string
  window: number
  default: number
  set: number | null
  compact_at: number
  compact_at_set: number | null
  idle_compact_after: number
  idle_compact_after_set: number | null
  reminded?: number
  reminded_at?: number
  learned?: number
  learned_at?: number
  lapses_at?: number
}

export interface WindowChanges {
  context_window?: number | null
  compact_at?: number | null
  idle_compact_after?: number | null
}

export interface Place {
  key: string
  path: string
  exists: boolean
  bytes: number | null
  /** False when adding up the directory was cut short: `bytes` is a lower bound then. */
  bytes_whole: boolean
}

export interface SpaceProject {
  name: string
  description: string
  workdir: string | null
  status: string
  created_at: number
  last_commission_at: number
  commissions: number
  archived_at: number | null
  archive_year: string | null
  directory: string | null
}

export interface SpaceEntry {
  id: string
  project: string | null
  title: string
  description: string
  type: string
  created: string
  updated: string
  content: string
  confidence: string
  evidence: string[]
  evidence_outside: string[]
  seen: number
  status: 'active' | 'pending' | 'expired'
  outdated: string
}

export interface SpaceReview {
  commission_id: string
  project: string
  archive_year: string | null
  title: string
  status: string
  ended: string
  entries: string[]
  content: string
}

export interface TaskPreference {
  id: number
  kind: string
  text: string
  origin: 'stated' | 'inferred' | 'console'
  observations: number
  active: boolean
  lanlan_name: string | null
  updated_at: number
}

/** Where an entry or the rules belong: a project, an archived one, or the space all share. */
export interface SpaceScope {
  project?: string | null
  archive_year?: string | null
}

function scopeParams(scope: SpaceScope = {}): Record<string, string> {
  const params: Record<string, string> = {}
  if (scope.project) params.project = scope.project
  if (scope.archive_year) params.archive_year = scope.archive_year
  return params
}

const get = <T>(url: string, params?: Record<string, string | number>) =>
  request.get<T, T>(url, { ...quiet, params }) as Promise<T>
const send = <T>(method: 'post' | 'put' | 'patch' | 'delete', url: string, data?: unknown, params?: Record<string, string>) =>
  request.request<T, T>({ ...quiet, method, url, data, params }) as Promise<T>

/** Return the code of a refusal of a console route, or null for another failure. */
export function consoleErrorCode(error: unknown): string | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string') {
    return (detail as { code: string }).code
  }
  return null
}

// -- the switches, and the browser the tools use ---------------------------------

export interface Capability {
  ready?: boolean
  reason?: string
}

export interface Switches {
  /** The one switch for all of it. */
  enabled: boolean
  flags: Record<string, boolean | undefined>
  gate: { ready?: boolean; reasons?: string[]; is_free_version?: boolean }
  capabilities: Record<string, Capability | undefined>
  model_images: { sees: boolean; source?: string; reason?: string; noted_at?: number; lapses_at?: number }
}

export type DeviceSwitch = 'computer_use_enabled' | 'browser_use_enabled'

export interface BrowserSettings {
  mode: string
  executable: string | null
  headless: boolean
}

export interface BrowserView {
  settings: BrowserSettings
  modes: string[]
  browsers: Array<{ kind: string; name: string; executable: string }>
}

export const getSwitches = () => get<{ switches: Switches }>('/console/switches')
export const setPower = (enabled: boolean) => send<{ success: boolean }>('post', '/console/switch', { enabled })
export const setDeviceSwitch = (key: DeviceSwitch, value: boolean) =>
  send<{ success: boolean }>('post', '/console/switch', { key, value })
export const getBrowser = () => get<BrowserView>('/console/browser')
export const setBrowser = (settings: Partial<BrowserSettings>) => send<BrowserView>('post', '/console/browser', settings)

// -- the runtime --------------------------------------------------------------

export const getRuntime = () => get<RuntimeView>('/console/runtime')
export const setRuntimePreference = (preference: 'auto' | 'builtin') =>
  send<RuntimeView>('post', '/console/runtime/preference', { preference })
export const installDsh = () => send<RuntimeView>('post', '/console/runtime/dsh/install')
export const uninstallDsh = (purge: boolean) => send<RuntimeView>('post', '/console/runtime/dsh/uninstall', { purge })

// -- permissions ----------------------------------------------------------------

export const getPermissions = () => get<PermissionsView>('/console/permissions')
export const setPermission = (category: string, mode: PermissionMode, expectedVersion: number) =>
  send<PermissionsView>('post', '/console/permissions', { category, mode, expected_version: expectedVersion })

// -- the model's window ---------------------------------------------------------

export const getWindow = () => get<{ window: ModelWindow | null }>('/console/window')
export const setWindow = (changes: WindowChanges) => send<{ window: ModelWindow }>('post', '/console/window', changes)

// -- where things are -----------------------------------------------------------

export const getPlaces = () => get<{ places: Place[] }>('/console/places')
export const openPlace = (key: string) => send<{ success: boolean }>('post', '/console/places/open', { key })

// -- the task agent space -------------------------------------------------------

export const listProjects = () => get<{ projects: SpaceProject[] }>('/console/space/projects')
export const readRules = (scope?: SpaceScope) => get<{ text: string }>('/console/space/rules', scopeParams(scope))
export const writeRules = (text: string, scope: SpaceScope = {}) =>
  send<{ text: string }>('put', '/console/space/rules', { text, project: scope.project || undefined })
export const readIndex = (scope?: SpaceScope) => get<{ text: string }>('/console/space/index', scopeParams(scope))
export const listEntries = (scope?: SpaceScope) => get<{ entries: SpaceEntry[] }>('/console/space/entries', scopeParams(scope))
export const readEntry = (id: string, scope?: SpaceScope) =>
  get<{ entry: SpaceEntry }>(`/console/space/entries/${encodeURIComponent(id)}`, scopeParams(scope))
export const fileEntry = (entry: { title: string; content: string; description?: string; kind?: string }, scope: SpaceScope = {}) =>
  send<{ entry: SpaceEntry }>('post', '/console/space/entries', { ...entry, project: scope.project || undefined })
export const editEntry = (
  id: string,
  changes: { title?: string; description?: string; kind?: string; content?: string },
  scope: SpaceScope = {},
) => send<{ entry: SpaceEntry }>('patch', `/console/space/entries/${encodeURIComponent(id)}`, {
  ...changes,
  project: scope.project || undefined,
  archive_year: scope.archive_year || undefined,
})
export const deleteEntry = (id: string, scope?: SpaceScope) =>
  send<{ success: boolean }>('delete', `/console/space/entries/${encodeURIComponent(id)}`, undefined, scopeParams(scope))
export const listRemoved = (scope?: SpaceScope) => get<{ removed: unknown[] }>('/console/space/removed', scopeParams(scope))
export const listReviews = (project?: string | null) =>
  get<{ reviews: SpaceReview[] }>('/console/space/reviews', project ? { project } : undefined)
export const readReview = (commissionId: string) =>
  get<{ review: SpaceReview }>(`/console/space/reviews/${encodeURIComponent(commissionId)}`)
export const deleteReview = (commissionId: string) =>
  send<{ success: boolean }>('delete', `/console/space/reviews/${encodeURIComponent(commissionId)}`)

// -- how she likes tasks done ---------------------------------------------------

export const listPreferences = () => get<{ preferences: TaskPreference[] }>('/console/preferences')
export const editPreference = (id: number, changes: { text?: string; active?: boolean }) =>
  send<{ preference: TaskPreference }>('patch', `/console/preferences/${id}`, changes)
export const removePreference = (id: number) => send<{ success: boolean }>('delete', `/console/preferences/${id}`)
