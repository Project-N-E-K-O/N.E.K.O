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
  /** What the model is told by when its settings are saved: its name alone may be another service's. */
  id: string
  window: number
  default: number
  set: number | null
  compact_at: number
  compact_at_set: number | null
  idle_compact_after: number
  idle_compact_after_set: number | null
  /** False when the user switched summing up idle sessions off for the model. */
  idle_compact_on: boolean
  reminded?: number
  reminded_at?: number
  learned?: number
  learned_at?: number
  lapses_at?: number
}

export interface WindowChanges {
  /** The `id` of the window the settings were read from; another model being configured now is answered with `stale`. */
  model_id?: string
  context_window?: number | null
  compact_at?: number | null
  idle_compact_after?: number | null
  idle_compact_on?: boolean
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
// The folder dialog stays open for two minutes, a draft is given 45 seconds: longer than a request usually waits.
const PICK_TIMEOUT_MS = 140_000
const DRAFT_TIMEOUT_MS = 65_000
const send = <T>(
  method: 'post' | 'put' | 'patch' | 'delete', url: string, data?: unknown, params?: Record<string, string>, timeout?: number,
) => request.request<T, T>({ ...quiet, method, url, data, params, ...(timeout ? { timeout } : {}) }) as Promise<T>

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
  /** Whether the main page shows the task HUD. Missing from an older agent process. */
  task_hud_visible?: boolean
}

export type DeviceSwitch = 'computer_use_enabled' | 'browser_use_enabled'
/** What a console page may switch besides the one for all of it. */
export type ConsoleSwitch = DeviceSwitch | 'task_hud_visible'

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
export const setDeviceSwitch = (key: ConsoleSwitch, value: boolean) =>
  send<{ success: boolean }>('post', '/console/switch', { key, value })
export const getBrowser = () => get<BrowserView>('/console/browser')
export const setBrowser = (settings: Partial<BrowserSettings>) => send<BrowserView>('post', '/console/browser', settings)

// -- the familiars at a glance --------------------------------------------------

/** What a familiar asks while it waits for the user. */
export interface BoardAsk {
  id?: string
  kind?: string
  category?: string
  detail?: Record<string, unknown>
  options?: string[]
  question?: string
  questions?: unknown[]
}

export interface BoardCommission {
  id: string
  title: string
  /** `working`, `waiting_user`, `resting`, `queued`. */
  status: string
  started_at: number | string | null
  ask?: BoardAsk | null
}

export type FamiliarState = 'working' | 'waiting_user' | 'idle' | 'asleep' | 'compacting' | (string & {})

/** One familiar: a session of a task agent, as the board shows it. */
export interface BoardCard {
  id: string
  name: string
  kind: string
  lanlan_name: string | null
  runtime: string | null
  workdir: string | null
  preset: string
  /** The kind it still works as, where its kind was changed and it has not started work since; else null. */
  preset_was?: string | null
  avatar: string
  /** The name a template of the user's gave itself; empty for a built-in preset. */
  label: string
  description: string
  /** Empty until the first review: the preset's name is shown then. */
  title: string
  duty: string
  duty_by: string
  doing: string
  summary?: string
  state: FamiliarState
  leader: boolean
  parent: string | null
  model: string | null
  /** Why the model set for its preset could not be used and its default was; absent when it could. */
  model_fallback?: string | null
  usage?: { requests: number; tokens: number }
  context_tokens?: number
  last_active_at?: number | null
  commission: BoardCommission | null
  /** The same for a project's leader and those it started for the commission it has in hand: one team. */
  team?: string | null
  /** The git worktree of its own it works in, or left for the user to decide on; see `Worktree`. */
  tree?: BoardTree | null
}

/** Where a git worktree stands: `held`, `unlanded` and `kept` are the user's to decide on. */
export type TreeState = 'making' | 'active' | 'landing' | 'resolving' | 'held' | 'unlanded' | 'kept' | (string & {})

/** A git worktree a familiar works in, as its card has it. */
export interface BoardTree {
  id: string
  path: string
  /** The project's directory within it. */
  workdir: string
  state: TreeState
  /** How many times what clashed went back to the familiar to merge. */
  rounds: number
  /** How many times an inspector looked at it. */
  checks: number
  conflicts: string[]
  /** Why it was not landed: `conflict`, `failed`, `inspection`, `ask`, `stopped`, `restart`, `skipped`; empty while it is at work. */
  reason: string
  project_dir: string
  commission_id: string | null
}

/** A git worktree on record, as the list of them has it. */
export interface Worktree extends BoardTree {
  session_id: string | null
  lanlan_name: string
  project: string | null
  created_at: number
  updated_at: number
  /** What its commission is called. */
  title: string
  session_name: string
}

/** What came of landing a worktree as the user asked. */
export interface Landing {
  status: 'landed' | 'unchanged' | 'conflict' | 'failed' | (string & {})
  files: Array<{ path: string; status: string; added: number | null; deleted: number | null }>
  more: number
  conflicts: string[]
  /** Whether what clashed went back to its familiar to merge. */
  resolving: boolean
  detail: string
  kept_copies?: string[]
  /** Changed outside the project's directory, elsewhere in its repository: landed with the rest. */
  outside?: string[]
  /** Not carried over (too large, a repository of their own): still in the worktree, which stays. */
  skipped?: string[]
}

/**
 * What becomes of work that waits for a familiar busy with something else, in a project without a
 * leader: `ask` the user whether another is to do it in a worktree of its own, `open` another
 * without asking, `queue` it behind the busy one.
 */
export type BesideChoice = 'ask' | 'open' | 'queue'

export interface LandingSettings {
  /** `auto`: what a familiar changed is merged as soon as it is done; `ask`: it waits for the user. */
  mode: 'auto' | 'ask'
  /** Whether an inspector looks at what was changed before every landing. */
  inspect: boolean
  /** Missing where the service has no such setting. */
  beside?: BesideChoice
  /** How many worktrees a project may have, those the user sees to included; no setting of the user's. */
  worktrees_per_project?: number
}

export interface BoardProject {
  name: string
  description: string
  workdir: string | null
  archived: boolean
  done_today: number
  members: BoardCard[]
}

export interface Board {
  revision: string
  /** True when nothing changed since the revision that was sent along: nothing else is in the answer then. */
  unchanged?: boolean
  projects?: BoardProject[]
  loose?: BoardCard[]
}

export const getBoard = (knownRevision?: string) =>
  get<Board>('/console/board', knownRevision ? { known_revision: knownRevision } : undefined)

/** A kind of familiar to choose from: a built-in preset, or a template the user wrote. */
export interface FamiliarTemplate {
  name: string
  label: string
  description: string
  avatar: string
  source: string
  /** Whether a familiar of it can change files, with a tool or through a command. */
  changes_files?: boolean | null
  /** Whether a familiar of it is held to it for certain in the built-in runtime only: in dsh it runs too, held to it by what it is told. */
  needs_builtin?: boolean | null
  valid: boolean
  error: string
}

/** What came of changing what kind of familiar a session is. */
export interface DutyChange {
  session_id: string
  preset: string
  was: string
  seat: string
  changed: boolean
  widened: boolean
  /** A run of it is under way, which goes on as the kind it was. */
  at_work: boolean
}

/** The form of a new familiar, as the user fills it in or as the model drafted it. */
export interface FamiliarForm {
  name: string
  title: string
  preset: string
  duty: string
  description: string
  rules: string
}

export const getProjectBoard = (project: string | null, knownRevision?: string) => {
  const params: Record<string, string> = {}
  if (project) params.project = project
  if (knownRevision) params.known_revision = knownRevision
  return get<Board>('/console/board', params)
}
/** The kinds to choose from; of a project, with the templates only it has, as that project has them. */
export const getTemplates = (project?: string | null) =>
  get<{ templates: FamiliarTemplate[] }>('/console/templates', project ? { project } : undefined)
export const draftFamiliar = (text: string, workdir?: string) =>
  send<{ draft: FamiliarForm }>('post', '/console/familiars/draft', { text, workdir: workdir || undefined }, undefined, DRAFT_TIMEOUT_MS)
export const makeFamiliar = (form: FamiliarForm & { workdir?: string }) =>
  send<{ project: SpaceProject; session_id: string }>('post', '/console/familiars', { ...form, workdir: form.workdir || undefined })
export const writeCard = (sessionId: string, lines: { title?: string; duty?: string; summary?: string }) =>
  send<{ written: string[] }>('patch', `/console/familiars/${encodeURIComponent(sessionId)}/card`, lines)
/** Make a familiar another kind (its preset); it holds from the next time the familiar starts work. */
export const changeDuty = (sessionId: string, preset: string) =>
  send<DutyChange>('post', `/console/familiars/${encodeURIComponent(sessionId)}/duty`, { preset })
export const describeProject = (project: string, description: string) =>
  send<{ project: SpaceProject }>('post', '/console/space/projects/description', { project, description })
export const tellCommission = (commissionId: string, text: string) =>
  send<{ success: boolean }>('post', `/console/commissions/${encodeURIComponent(commissionId)}/tell`, { text })
/**
 * Answer what a familiar asks: `choice` for a request for approval, `text` for words, `selected`
 * for the options of its questions that were picked (by each question's id). `note` is a sentence
 * added to a choice, passed on to the familiar once the answer is in: `note_taken` says whether it was
 * (null: it took longer than the route waits for it, and is still being passed on).
 */
export interface CommissionAnswer {
  text?: string
  choice?: string
  question_id?: string
  selected?: Record<string, string[]>
  note?: string
}
export const answerCommission = (commissionId: string, answer: CommissionAnswer) =>
  send<{ success: boolean; note_taken?: boolean | null }>('post', `/console/commissions/${encodeURIComponent(commissionId)}/answer`, answer)
export const stopCommission = (commissionId: string) =>
  send<{ status: string }>('post', `/console/commissions/${encodeURIComponent(commissionId)}/stop`)

// -- more familiars for a project that is there -------------------------------------

/** Add a familiar to a project: whether it works in a worktree of its own (`seat` "tree") is the service's to tell. */
export const addMember = (project: string, form: { preset?: string; title?: string; duty?: string; lanlan_name?: string | null }) =>
  send<{ session_id: string; seat: string }>('post', '/console/familiars/member', {
    project, preset: form.preset || undefined, title: form.title || undefined, duty: form.duty || undefined,
    lanlan_name: form.lanlan_name || undefined,
  })
/** Give a project its leader; `made` false when it had one. */
export const giveLeader = (project: string, lanlanName?: string | null) =>
  send<{ session_id: string; made: boolean }>('post', '/console/space/projects/leader', { project, lanlan_name: lanlanName || undefined })

// -- the git worktrees familiars work in, and landing what they changed ------------------

const tree = (id: string, action: string) => `/console/worktrees/${encodeURIComponent(id)}/${action}`
export const listWorktrees = (project?: string | null) =>
  get<{ worktrees: Worktree[] }>('/console/worktrees', project ? { project } : undefined)
/** Merge what it holds into the project's directory: once more, or the first time where the user is asked first. */
export const landWorktree = (id: string) => send<Landing>('post', tree(id, 'land'))
/** Remove it with what it holds. */
export const discardWorktree = (id: string) => send<{ removed: boolean }>('post', tree(id, 'discard'))
/** Leave it to the user: nothing reminds of it any more. */
export const keepWorktree = (id: string) => send<{ worktree: Worktree }>('post', tree(id, 'keep'))
export const openWorktree = (id: string) => send<{ success: boolean }>('post', tree(id, 'open'))
export const getLanding = () => get<LandingSettings>('/console/landing')
export const setLanding = (changes: Partial<LandingSettings>) => send<LandingSettings>('post', '/console/landing', changes)

// -- what happened among the familiars, and who waits for whom ----------------------

/** A session of a familiar, the user, or a free place a commission waits for. */
export interface TimelineParty {
  type: 'session' | 'user' | 'place' | (string & {})
  session_id?: string | null
  commission_id?: string | null
}

/** One thing that happened. The page passes over a `kind` it does not know. */
export interface TimelineEntry {
  cursor: string
  at: number
  kind: string
  lanlan_name: string | null
  project: string | null
  session_id: string | null
  commission_id: string | null
  peer: TimelineParty | null
  text: string
  detail: Record<string, unknown>
}

export interface TimelinePage {
  entries: TimelineEntry[]
  /** What to pass as `after` for the page after this one. */
  next: string
  more: boolean
  /** Stands after everything on record now: from here on only what is new comes. */
  latest: string
}

/** Who waits for whom right now. */
export interface Wait {
  kind: 'queue' | 'place' | 'user' | 'notice' | 'child' | 'ask' | (string & {})
  waiter: TimelineParty
  target: TimelineParty
  since: number
  text: string
  lanlan_name: string | null
  projects: string[]
}

export const getTimeline = (params: { project?: string | null; after?: string; limit?: number } = {}) => {
  const query: Record<string, string | number> = {}
  if (params.project) query.project = params.project
  if (params.after) query.after = params.after
  if (params.limit) query.limit = params.limit
  return get<TimelinePage>('/console/timeline', query)
}
/** How a familiar stood where a page begins reading: it was at something already. */
export interface SceneStanding {
  session_id: string
  commission_id: string
  look: 'working' | 'waiting_user' | 'resting' | 'queued' | (string & {})
  since: number
}

/**
 * Where to begin reading for what happened lately: `after` is empty when that is the beginning of
 * the record. `before` is how many entries come before it, `scene` how the familiars stood there.
 * (Missing from a service from before they were added.)
 */
export const getTimelineStart = () =>
  get<{ after: string; latest: string; before?: number; scene?: SceneStanding[] }>('/console/timeline/start')
export const getWaits = (project?: string | null) =>
  get<{ waits: Wait[]; more: boolean }>('/console/waits', project ? { project } : undefined)

// -- one familiar: what was said in it, what it used ---------------------------------

export interface TranscriptCall {
  name: string
  /** Its arguments as JSON text, cut when long. */
  arguments: string
  cut: boolean
}

export interface TranscriptEntry {
  /** Its place in the record: what `before` and `at` are given. */
  at: number
  role: 'user' | 'assistant' | 'tool_result' | 'notice' | 'summary' | (string & {})
  text: string
  /** True when `text` was cut: the whole entry is read with `at`. */
  cut: boolean
  calls: TranscriptCall[]
  /** The tool a result is of, or the kind of a notice. */
  tool: string | null
  failed: boolean
  pictures: number
  time: number | null
}

export interface TranscriptPage {
  /** False for a session dsh holds (`reason` "dsh") or one nothing was said in (`reason` "empty"). */
  readable: boolean
  reason?: 'dsh' | 'empty' | (string & {})
  entries: TranscriptEntry[]
  /** What to pass as `before` for the page before this one; null at the beginning. */
  before: number | null
}

export const getTranscript = (sessionId: string, params: { before?: number | null; limit?: number; at?: number } = {}) => {
  const query: Record<string, string | number> = {}
  if (params.before !== undefined && params.before !== null) query.before = params.before
  if (params.limit) query.limit = params.limit
  if (params.at !== undefined) query.at = params.at
  return get<TranscriptPage>(`/console/familiars/${encodeURIComponent(sessionId)}/transcript`, query)
}

export interface UsageCount {
  requests: number
  input: number
  output: number
  cache_read: number
  cache_write: number
  tokens: number
}

export interface SessionUsage {
  session: UsageCount & { context_tokens: number }
  window: { window: number; compact_at?: number } | null
  /** Its latest commission: what the work it belongs to used, and what it may before the user is asked. */
  commission: { id: string; title: string; status: string; used: number; limit: number | null; own: UsageCount } | null
}

export interface CommissionUsage {
  commission: UsageCount
  used: number
  limit: number | null
  session: (UsageCount & { context_tokens: number }) | null
  window: { window: number; compact_at?: number } | null
}

/** One place in what was said where the words looked for occur. */
export interface TranscriptHit {
  /** Opened whole with `getTranscript(id, { at })`. */
  at: number
  role: TranscriptEntry['role']
  tool: string | null
  excerpt: string
  time: number | null
}

export interface TranscriptSearch {
  readable: boolean
  reason?: 'dsh' | 'empty' | (string & {})
  /** The latest first. */
  hits: TranscriptHit[]
  /** How many places there are in all. */
  total: number
  /** False when the record is so long that only its end was looked through. */
  whole: boolean
}

export const searchTranscript = (sessionId: string, query: string) =>
  get<TranscriptSearch>(`/console/familiars/${encodeURIComponent(sessionId)}/transcript/search`, { q: query })

export const getSessionUsage = (sessionId: string) =>
  get<SessionUsage>(`/console/familiars/${encodeURIComponent(sessionId)}/usage`)
export const getCommissionUsage = (commissionId: string) =>
  get<CommissionUsage>(`/console/commissions/${encodeURIComponent(commissionId)}/usage`)

// -- the commissions ------------------------------------------------------------------

export interface CommissionRow {
  id: string
  title: string
  /** As the ledger has it, or `resting`. */
  status: string
  reason: string
  result: string
  source: string
  route: string | null
  project: string | null
  lanlan_name: string | null
  parent_id: string | null
  session_id: string | null
  session: { name: string; kind: string; preset: string; title: string } | null
  runtime: string | null
  plugin_id: string | null
  entry_id: string | null
  created_at: number
  started_at: number | null
  ended_at: number | null
  updated_at: number
  usage: { requests: number; tokens: number }
  ask: BoardAsk | null
}

export const listCommissions = (
  params: { status?: string[]; project?: string | null; loose?: boolean; days?: number; limit?: number } = {},
) => {
  const query: Record<string, string | number> = {}
  if (params.status?.length) query.status = params.status.join(',')
  if (params.loose) query.loose = '1'
  else if (params.project) query.project = params.project
  if (params.days !== undefined) query.days = params.days
  if (params.limit) query.limit = params.limit
  return get<{ commissions: CommissionRow[]; more: boolean }>('/console/commissions', query)
}

// -- the model each kind of familiar is sent to ----------------------------------------

export interface RoleModelInEffect {
  /** "starter": the model of the session that starts it; then nothing else is said. */
  follows: 'starter' | null
  slot?: string | null
  model?: string | null
  /** Whether it is the task agent model of the main app. */
  agent?: boolean
  free?: boolean
  /** Why its setting could not be used and its default was; null when it could. */
  fallback: string | null
}

export interface RoleModelPreset {
  preset: string
  label: string
  source: string
  valid: boolean
  /** The id of a slot, "inherit", or null for its default. */
  setting: string | null
  default: 'agent' | 'advanced' | 'inherit' | (string & {})
  can_inherit: boolean
  in_effect: RoleModelInEffect
}

export interface RoleModelSlot {
  slot_id: string
  name: string
  source: string
  host_api: string | null
  model: string | null
  configured: boolean
  free: boolean
  error_code: string | null
}

export interface RoleModelsView {
  presets: RoleModelPreset[]
  /** The slots of the model panel that can call tools, in its order: read each time, none is written in the page. */
  slots: RoleModelSlot[]
  slots_error: string | null
}

export const getRoleModels = () => get<RoleModelsView>('/console/role-models')
export const setRoleModel = (preset: string, value: string | null) =>
  send<RoleModelsView>('post', '/console/role-models', { preset, value })

// -- the MCP tools ---------------------------------------------------------------------

export interface McpServer {
  server: string
  /** What was set for it; "auto": nothing of its own, it goes by the setting all servers share. */
  mode: PermissionMode
  effective: Exclude<PermissionMode, 'auto'>
  tools: Array<{ id: string; name: string; description: string }>
}

export interface McpView {
  version: number
  adapter: 'running' | 'stopped' | 'missing' | (string & {})
  plugins_on: boolean
  category: { mode: PermissionMode; auto: Exclude<PermissionMode, 'auto'> }
  servers: McpServer[]
  unreadable: boolean
}

export const getMcp = () => get<McpView>('/console/mcp')
/** Set one server, or with `server` null the setting all of them share. */
export const setMcpPermission = (server: string | null, mode: PermissionMode, expectedVersion: number) =>
  send<McpView>('post', '/console/mcp/permission', { server: server ?? undefined, mode, expected_version: expectedVersion })

// -- how much one commission may use ---------------------------------------------------

export interface UsageLimit {
  /** What holds now; null for no limit. */
  tokens: number | null
  /** What the user set; null while the default holds. */
  set: number | null
  default: number | null
  min: number
  max: number
}

export const getUsageLimit = () => get<UsageLimit>('/console/usage-limit')
export const setUsageLimit = (tokens: number | null) => send<UsageLimit>('post', '/console/usage-limit', { tokens })

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
export const setProjectWorkdir = (project: string, workdir: string) =>
  send<{ project: SpaceProject }>('post', '/console/space/projects/workdir', { project, workdir })
/** Put a project away into the archive: nothing is removed, and a commission that names it brings it back. */
export const archiveProject = (project: string) =>
  send<{ project: SpaceProject }>('post', '/console/space/projects/archive', { project })
/** Show the system's dialog for choosing a folder; `path` is absent when the user closed it. */
export const pickDirectory = (start?: string | null) =>
  send<{ path?: string; cancelled?: boolean }>('post', '/console/space/pick-directory', { start: start || undefined }, undefined, PICK_TIMEOUT_MS)
export const readRules = (scope?: SpaceScope) => get<{ text: string }>('/console/space/rules', scopeParams(scope))
export const writeRules = (text: string, scope: SpaceScope = {}) =>
  send<{ text: string }>('put', '/console/space/rules', { text, project: scope.project || undefined, archive_year: scope.archive_year || undefined })
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
export const listReviews = (project?: string | null, archiveYear?: string | null) =>
  get<{ reviews: SpaceReview[] }>(
    '/console/space/reviews',
    // Of an archived project by its year: one begun after it under the same name has reviews of its own.
    project ? { project, ...(archiveYear ? { archive_year: archiveYear } : {}) } : undefined,
  )
export const readReview = (commissionId: string) =>
  get<{ review: SpaceReview }>(`/console/space/reviews/${encodeURIComponent(commissionId)}`)
export const deleteReview = (commissionId: string) =>
  send<{ success: boolean }>('delete', `/console/space/reviews/${encodeURIComponent(commissionId)}`)

// -- how she likes tasks done ---------------------------------------------------

export const listPreferences = () => get<{ preferences: TaskPreference[] }>('/console/preferences')
export const editPreference = (id: number, changes: { text?: string; active?: boolean }) =>
  send<{ preference: TaskPreference }>('patch', `/console/preferences/${id}`, changes)
export const removePreference = (id: number) => send<{ success: boolean }>('delete', `/console/preferences/${id}`)
