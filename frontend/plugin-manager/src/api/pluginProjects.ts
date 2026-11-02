import { post } from './index'
import { openLocalPath } from '@/utils/openExternal'

export interface PluginProjectRequest {
  parent_dir: string
  plugin_id: string
  name: string
  description: string
  author: string
  plugin_type: 'plugin' | 'adapter'
}

export type PluginProjectIdentity = Omit<PluginProjectRequest, 'parent_dir'>

export interface PluginProjectResult {
  plugin_id: string
  name: string
  description: string
  author: string
  plugin_type: 'plugin' | 'adapter'
  version: string
  entry: string
  project_dir: string
  files: string[]
  created: boolean
}

const config = {
  headers: { 'X-Neko-Development': '1' },
  suppressErrorMessage: true,
  timeout: 90_000,
}
export const previewPluginProject = (request: PluginProjectRequest): Promise<PluginProjectResult> =>
  post('/plugin-projects/preview', request, config)
export const createPluginProject = (request: PluginProjectRequest): Promise<PluginProjectResult> =>
  post('/plugin-projects/create', request, config)

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

/** Server error codes mapped to `authoring.*` message keys. */
export const PROJECT_ERROR_MESSAGES: Record<string, string> = {
  INVALID_PARENT: 'invalidParent',
  INVALID_PLUGIN_ID: 'invalidId',
  PROJECT_EXISTS: 'directoryExists',
  PROJECT_IO_FAILED: 'projectIoFailed',
  GIT_NOT_FOUND: 'gitMissing',
  GIT_INIT_FAILED: 'gitInitFailed',
  DIRECTORY_PICKER_UNAVAILABLE: 'pickerUnavailable',
  DIRECTORY_PICKER_FAILED: 'pickerUnavailable',
  DIRECTORY_UNAVAILABLE: 'directoryUnavailable',
  OPEN_DIRECTORY_UNAVAILABLE: 'openFolderFailed',
  OPEN_DIRECTORY_FAILED: 'openFolderFailed',
}

export function projectErrorCode(error: unknown): string | undefined {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  const code = (detail as { code?: unknown } | undefined)?.code
  return typeof code === 'string' ? code : undefined
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
