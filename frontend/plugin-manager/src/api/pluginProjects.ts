import type { AxiosError } from 'axios'
import { del, get, post, put } from './index'
import { openLocalPath } from '@/utils/openExternal'
import { readErrorCode } from '@/utils/request'

export interface PluginProjectRequest {
  parent_dir: string
  plugin_id: string
  name: string
  description: string
  author: string
  plugin_type: 'plugin' | 'adapter'
}

export type PluginProjectIdentity = Omit<PluginProjectRequest, 'parent_dir'>

export interface PluginProjectMetadata {
  name: string
  version: string
  author: string
  description: string
}

export interface PluginProjectPreview {
  plugin_id: string
  name: string
  description: string
  author: string
  plugin_type: 'plugin' | 'adapter'
  version: string
  entry: string
  project_dir: string
  files: string[]
}

export interface CreatedPluginProject extends PluginProjectPreview {
  project_id: string
}

/** A registered project; attributes are read live from its plugin.toml. */
export interface AuthoringProject {
  project_id: string
  source_dir: string
  added_at: string
  status: 'ok' | 'missing' | 'invalid'
  error: string | null
  plugin_id: string | null
  name: string | null
  description: string | null
  author: string | null
  plugin_type: string | null
  version: string | null
  entry: string | null
  /** Whether the runtime provides the plugin from this project. */
  loaded: boolean
  running: boolean
  /** What keeps a readable project from working; `error` carries the diagnostic when one is known. */
  problem:
    | 'id_taken'
    | 'source_rejected'
    | 'config_rejected'
    /** The plugin system is not running, so nothing is loaded (not a project fault). */
    | 'runtime_inactive'
    /** The plugin system runs but has not picked the project up; a registry refresh does. */
    | 'not_registered'
    | 'load_failed'
    | 'reload_failed'
    | 'startup_degraded'
    | null
}

const config = {
  suppressErrorMessage: true,
  timeout: 90_000,
}
export const listPluginProjects = (): Promise<AuthoringProject[]> => get('/plugin-projects', config)
export const previewPluginProject = (
  request: PluginProjectRequest
): Promise<PluginProjectPreview> => post('/plugin-projects/preview', request, config)
export const createPluginProject = (request: PluginProjectRequest): Promise<CreatedPluginProject> =>
  post('/plugin-projects/create', request, config)
export const registerPluginProject = (sourceDir: string): Promise<AuthoringProject> =>
  post('/plugin-projects/register', { source_dir: sourceDir }, config)
export const updatePluginProjectMetadata = (
  projectId: string,
  metadata: PluginProjectMetadata
): Promise<AuthoringProject> =>
  put(`/plugin-projects/${encodeURIComponent(projectId)}/metadata`, metadata, config)
/** Stops and unloads the project plugin, then forgets the project; its files are kept. */
export const removePluginProject = (projectId: string): Promise<unknown> =>
  del(`/plugin-projects/${encodeURIComponent(projectId)}`, config)

export interface PrecheckStage {
  name: 'lint' | 'sync' | 'release-check'
  passed: boolean
  output: string
}

/** Verbatim report of `neko-plugin precheck --json`, the same gates Market CI runs. */
export interface PrecheckReport {
  plugin_id: string | null
  version: string | null
  plugin_dir: string
  passed: boolean
  ruff_version: string
  environment_issues: [string, string][]
  stages: PrecheckStage[]
}

export const precheckPluginProject = (projectId: string): Promise<PrecheckReport> =>
  post(
    `/plugin-projects/${encodeURIComponent(projectId)}/precheck`,
    {},
    // The server budgets 600s for the CLI; the client must outlive it.
    { ...config, timeout: 660_000 }
  )

export interface PublishRepositoryState {
  git_available: boolean
  is_repository: boolean
  tag: string
  origin_url: string | null
  github_repository: string | null
  branch: string | null
  upstream: string | null
  head: string | null
  detached: boolean
  /** True only when the upstream is origin/<branch> and equals HEAD; null without HEAD, upstream or branch. */
  head_pushed: boolean | null
  /** Same definition as the CLI publish gate. */
  worktree_clean: boolean | null
  /** The first changed paths when the worktree is not clean. */
  dirty_paths: string[]
  tag_exists: boolean
  tag_at_head: boolean | null
}

export interface PublishRemoteState {
  rate_limited: boolean
  /** False when a GitHub read failed by network error, timeout or 5xx (not a rate limit). */
  reachable: boolean
  visibility: 'public' | 'private' | 'not_found' | 'unknown'
  verify: 'success' | 'failure' | 'running' | 'not_run' | 'missing' | 'unknown'
  release: {
    state: 'published' | 'missing' | 'unknown'
    missing_assets: string[]
    workflow: 'success' | 'failure' | 'running' | 'not_run' | 'missing' | 'unknown'
    /** Complete downloadable assets and a successful tag release workflow. */
    ready: boolean
  }
  /** Remote tag v<version> compared with the local HEAD. */
  tag: 'missing' | 'at_head' | 'elsewhere' | 'unknown'
}

/** The workflow file the publish gate requires to be CURRENT. */
export const RELEASE_WORKFLOW_PATH = '.github/workflows/release.yml'

/** Read-only: local git facts plus public GitHub reads; nothing is pushed or published. */
export interface PublishStatus {
  plugin_id: string
  version: string
  repository: PublishRepositoryState
  /** Standard GitHub Actions files keyed by path. */
  actions: Record<string, 'ADD' | 'CURRENT' | 'UPGRADE' | 'CONFLICT'>
  remote: PublishRemoteState | null
  /** Recent commits and uncommitted changes; null without the plugin's own repository. */
  workspace: WorkspaceLog | null
}

export type WorkspaceChangeKind =
  | 'modified'
  | 'added'
  | 'deleted'
  | 'renamed'
  | 'untracked'
  | 'conflicted'

export interface WorkspaceLog {
  /** The paths that keep the worktree from being clean (the first 50). */
  changes: { path: string; kind: WorkspaceChangeKind }[]
  changes_total: number
  /** The 10 latest commits of HEAD, newest first. */
  commits: {
    sha: string
    author: string
    /** Unix seconds. */
    timestamp: number
    subject: string
    /** Whether the upstream has it; null without an upstream. */
    pushed: boolean | null
  }[]
  /** Commits not on the upstream yet, and upstream commits not here; null without one. */
  ahead: number | null
  behind: number | null
}

/** The publishing tools of the machine running the server. Local reads only. */
export interface Toolchain {
  git: { installed: boolean; version: string | null; user_name: string | null; user_email: string | null }
  uv: { installed: boolean; version: string | null }
  gh: { installed: boolean; version: string | null; authenticated: boolean; login: string | null }
  /** Checks, repository creation and publishing need a N.E.K.O source checkout. */
  source_checkout: boolean
}

/** The publishing tools of the machine running the server, seen from the project folder. Local reads only. */
export const getPluginToolchain = (projectId: string): Promise<Toolchain> =>
  get(`/plugin-projects/${encodeURIComponent(projectId)}/toolchain`, config)

/** `fresh` bypasses the server's GitHub cache (an explicit refresh); rate limits still apply. */
export const getPluginProjectPublishStatus = (
  projectId: string,
  fresh = false
): Promise<PublishStatus> =>
  get(`/plugin-projects/${encodeURIComponent(projectId)}/publish-status`, {
    ...config,
    ...(fresh ? { params: { fresh: 1 } } : {}),
  })

/** Outcome of `neko-plugin create-repo` run from the workbench. */
export type RepoCreateStatus = 'created' | 'manual' | 'failed'

export interface RepoCreateResult {
  status: RepoCreateStatus
  /** The new repository's URL on success; empty otherwise. */
  repository_url: string
  /** Bounded tail of the CLI output; carries the manual steps when status is 'manual'. */
  log: string[]
  finished_at: number
}

export const createPluginProjectRepo = (
  projectId: string,
  options: { private?: boolean } = {}
): Promise<RepoCreateResult> =>
  post(
    `/plugin-projects/${encodeURIComponent(projectId)}/create-repo`,
    { private: options.private ?? false },
    // gh network + repo creation can take a little while; outlive the default.
    { ...config, timeout: 190_000 }
  )

export type PublishMode = 'github_and_market' | 'github' | 'market_notify'

/** Lifecycle of one `neko-plugin publish` background task. */
export type PublishTaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'

/** The Market's answer to a release notification, from the CLI's `[MARKET] status=` line. */
export type MarketPublicationStatus =
  | 'published'
  | 'already_published'
  | 'pending_review'
  | 'approved'
  | 'superseded'
  | 'rejected'

export interface PublishTask {
  task_id: string
  project_id: string
  mode: PublishMode
  status: PublishTaskStatus
  message: string
  release_url: string
  /** Null until the run notified the Market. */
  market_status: MarketPublicationStatus | null
  /** The Market's release review candidate, when it answered with one. */
  market_candidate_id: number | null
  /** Bounded tail of the CLI output. */
  log: string[]
  started_at: number | null
  updated_at: number
  finished_at: number | null
}

/** True while the task can still change; the frontend polls until it is false. */
export const publishTaskActive = (task: PublishTask): boolean =>
  task.status === 'queued' || task.status === 'running'

export const startPluginProjectPublish = (
  projectId: string,
  mode: PublishMode
): Promise<PublishTask> =>
  post(
    `/plugin-projects/${encodeURIComponent(projectId)}/publish`,
    { mode },
    { ...config, timeout: 60_000 }
  )

export const getPluginProjectPublishTask = (projectId: string): Promise<PublishTask> =>
  get(`/plugin-projects/${encodeURIComponent(projectId)}/publish-task`, config)

export const cancelPluginProjectPublishTask = (
  projectId: string,
  taskId: string
): Promise<PublishTask> =>
  post(
    `/plugin-projects/${encodeURIComponent(projectId)}/publish-task/${encodeURIComponent(taskId)}/cancel`,
    {},
    { ...config, timeout: 60_000 }
  )

export interface PickedDirectory {
  cancelled?: boolean
  selected_root?: string
}

interface DirectoryHost {
  pickDirectory?: (options: { title: string; startPath?: string }) => Promise<PickedDirectory>
  openPath?: unknown
}
const projectDirectoryHost = () => (window as unknown as { nekoHost?: DirectoryHost }).nekoHost

/** Mirrors PLUGIN_ID_PATTERN in plugin/neko_plugin_cli/core/project.py; the server is authoritative. */
export const PLUGIN_ID_PATTERN = /^[a-z][a-z0-9_]*$/

/** Reads the code from either `detail.code` (workbench) or the `X-Error-Code` header (plugin APIs). */
export function projectErrorCode(error: unknown): string | undefined {
  if (error == null) return undefined
  return readErrorCode(error as AxiosError) || undefined
}

/**
 * Choose a directory and return its absolute path, or null when cancelled.
 * Browsers never expose absolute paths from their own folder pickers, so without a
 * desktop host the local server shows the native dialog instead.
 */
export async function pickProjectDirectory(
  startPath: string,
  title: string
): Promise<string | null> {
  const bridge = projectDirectoryHost()
  // The native dialog stays open until the user answers, so it outlives the default timeout.
  const picked: PickedDirectory = bridge?.pickDirectory
    ? await bridge.pickDirectory({ title, startPath })
    : await post(
        '/plugin-projects/pick-directory',
        { start_path: startPath, title },
        { ...config, timeout: 130_000 }
      )
  return picked.cancelled ? null : picked.selected_root?.trim() || null
}

export async function openProjectDirectory(path: string): Promise<void> {
  if (typeof projectDirectoryHost()?.openPath === 'function') return openLocalPath(path)
  await post('/plugin-projects/open-directory', { path }, config)
}
