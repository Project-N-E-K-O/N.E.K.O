import { post } from './index'

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
  openPath?: (options: { path: string }) => Promise<{ ok: boolean; error?: string }>
}
export const projectDirectoryHost = () =>
  (window as unknown as { nekoHost?: DirectoryHost }).nekoHost

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
        { start_path: startPath },
        { ...config, timeout: 130_000 }
      )
  return picked.cancelled ? null : picked.selected_root?.trim() || null
}

export async function openProjectDirectory(path: string): Promise<void> {
  const bridge = projectDirectoryHost()
  if (bridge?.openPath) {
    const result = await bridge.openPath({ path })
    if (!result.ok) throw new Error(result.error || 'Cannot open directory')
  } else {
    await post('/plugin-projects/open-directory', { path }, config)
  }
}
