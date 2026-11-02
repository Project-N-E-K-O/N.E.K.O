import { ref } from 'vue'
import { useEventListener } from '@vueuse/core'
import type { PluginProjectResult } from '@/api/pluginProjects'
import type { CreatedAuthoringProject } from '@/types/authoring'

const STORAGE_KEY = 'neko.pluginAuthoring.projects.v1'

function isProject(value: unknown): value is CreatedAuthoringProject {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return (
    (item.kind === undefined || item.kind === 'created') &&
    (item.type === 'plugin' || item.type === 'adapter') &&
    typeof item.createdAt === 'number' &&
    ['key', 'id', 'name', 'description', 'version', 'author', 'source', 'entry'].every(
      (field) => typeof item[field] === 'string'
    )
  )
}

function readProjects(): CreatedAuthoringProject[] {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(stored) ? stored.filter(isProject) : []
  } catch {
    return []
  }
}

/** Persist metadata returned by successful project creation. */
export function useCreatedPluginProjects() {
  const projects = ref(readProjects())
  useEventListener(window, 'storage', (event) => {
    if (event.key === STORAGE_KEY || event.key === null) projects.value = readProjects()
  })

  function remember(result: PluginProjectResult, key: string) {
    if (!result.created) return
    const existing = projects.value.find((project) => project.key === key)
    const project: CreatedAuthoringProject = {
      key,
      id: result.plugin_id,
      name: result.name,
      description: result.description,
      author: result.author,
      type: result.plugin_type,
      version: result.version,
      entry: result.entry,
      source: result.project_dir,
      createdAt: existing?.createdAt ?? Date.now(),
    }
    projects.value = [project, ...projects.value.filter((item) => item.key !== key)]
    // Surface storage failures to the caller while retaining the visible record.
    // A successful disk write must not disappear from the current page.
    localStorage.setItem(STORAGE_KEY, JSON.stringify(projects.value))
  }

  return { projects, remember }
}
