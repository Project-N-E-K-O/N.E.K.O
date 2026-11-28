import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface DependencyInstallRequest {
  pluginId: string
  name: string
}

/**
 * One "install the plugin's Python dependencies" dialog at a time, shown when
 * a plugin that ships uv.lock instead of vendor/ is started. request() resolves
 * true once the dependencies are installed (the caller then starts the plugin),
 * false when the dialog is closed first.
 */
export const usePluginDependencyInstall = defineStore('pluginDependencyInstall', () => {
  const active = ref<DependencyInstallRequest | null>(null)
  let resolveActive: ((installed: boolean) => void) | null = null
  let activePromise: Promise<boolean> | null = null

  function request(pluginId: string, name: string): Promise<boolean> {
    if (active.value && activePromise) {
      // Another plugin's dialog is open: do not stack a second one.
      return active.value.pluginId === pluginId ? activePromise : Promise.resolve(false)
    }
    active.value = { pluginId, name }
    activePromise = new Promise<boolean>((resolve) => {
      resolveActive = resolve
    })
    return activePromise
  }

  function finish(installed: boolean) {
    const resolve = resolveActive
    active.value = null
    resolveActive = null
    activePromise = null
    resolve?.(installed)
  }

  return { active, request, finish }
})
