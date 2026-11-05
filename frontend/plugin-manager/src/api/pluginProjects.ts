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
  head_pushed: boolean | null
  worktree_clean: boolean | null
  tag_exists: boolean
  tag_at_head: boolean | null
}

export interface PublishRemoteState {
  rate_limited: boolean
  visibility: 'public' | 'private' | 'not_found' | 'unknown'
  verify: 'success' | 'failure' | 'running' | 'not_run' | 'missing' | 'unknown'
  release: { state: 'published' | 'missing' | 'unknown'; missing_assets: string[] }
}

/** Read-only: local git facts plus public GitHub reads; nothing is pushed or published. */
export interface PublishStatus {
  plugin_id: string
  version: string
  repository: PublishRepositoryState
  /** Standard GitHub Actions files keyed by path. */
  actions: Record<string, 'ADD' | 'CURRENT' | 'UPGRADE' | 'CONFLICT'>
  remote: PublishRemoteState | null
}

export const getPluginProjectPublishStatus = (projectId: string): Promise<PublishStatus> =>
  get(`/plugin-projects/${encodeURIComponent(projectId)}/publish-status`, config)

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
