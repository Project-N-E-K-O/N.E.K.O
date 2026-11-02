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

export interface PluginProjectBundle extends PluginProjectResult {
  directories: string[]
  contents: { path: string; content_base64: string }[]
}

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

export const previewBrowserProject = (
  request: PluginProjectIdentity
): Promise<PluginProjectResult> => post('/plugin-projects/browser/preview', request, config)
export const bundleBrowserProject = (
  request: PluginProjectIdentity
): Promise<PluginProjectBundle> => post('/plugin-projects/browser/bundle', request, config)

interface DirectoryHost {
  pickDirectory?: (options: {
    title: string
    startPath?: string
  }) => Promise<{ cancelled?: boolean; selected_root?: string }>
  openPath?: (options: { path: string }) => Promise<{ ok: boolean; error?: string }>
}
export const projectDirectoryHost = () =>
  (window as unknown as { nekoHost?: DirectoryHost }).nekoHost

export async function openProjectDirectory(path: string): Promise<void> {
  const bridge = projectDirectoryHost()
  if (bridge?.openPath) {
    const result = await bridge.openPath({ path })
    if (!result.ok) throw new Error(result.error || 'Cannot open directory')
  } else {
    await post('/plugin-projects/open-directory', { path }, config)
  }
}
