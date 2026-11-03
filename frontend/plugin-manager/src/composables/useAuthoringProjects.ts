import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  listPluginProjects,
  projectErrorCode,
  registerPluginProject,
  removePluginProject,
  type AuthoringProject,
} from '@/api/pluginProjects'
import type { AuthoringMessageKey } from '@/i18n/authoring'
import { formatHttpError } from '@/utils/request'

/**
 * Every ProjectErrorCode in plugin/server/domain/plugin_project_errors.py mapped to an
 * `authoring.*` message key; a test keeps both sides identical.
 */
export const PROJECT_ERROR_MESSAGES = {
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
  INVALID_PROJECT_DIRECTORY: 'invalidProjectDirectory',
  PROJECT_ALREADY_REGISTERED: 'projectAlreadyRegistered',
  PROJECT_PLUGIN_ID_CONFLICT: 'projectPluginIdConflict',
  PROJECT_STOP_FAILED: 'projectStopFailed',
  PROJECT_NOT_FOUND: 'projectNotFound',
  PROJECT_REGISTER_FAILED: 'projectRegisterFailed',
  PROJECT_STORE_INVALID: 'projectStoreInvalid',
  PROJECT_STORE_UNREADABLE: 'projectStoreUnreadable',
  PROJECT_STORE_WRITE_FAILED: 'projectStoreWriteFailed',
  PLUGIN_OPERATION_BUSY: 'operationBusy',
} as const satisfies Record<string, AuthoringMessageKey>

const ownKey = (messages: Record<string, AuthoringMessageKey>, code: string) =>
  Object.prototype.hasOwnProperty.call(messages, code) ? messages[code] : undefined

/** Message key for a server error code; only declared codes match. */
export function errorMessageKey(code: string | undefined): AuthoringMessageKey | undefined {
  if (!code) return undefined
  return ownKey(PROJECT_ERROR_MESSAGES, code)
}

/** Display name; unreadable projects fall back to their folder name. */
export function projectTitle(project: AuthoringProject): string {
  return (
    project.name ||
    project.plugin_id ||
    project.source_dir.split(/[\\/]/).filter(Boolean).pop() ||
    project.source_dir
  )
}

/**
 * Localized message for a workbench API failure; unknown codes
 * fall back to the server diagnostic, then to `authoring.<fallbackKey>`.
 */
export function useProjectErrorMessage() {
  const { t } = useI18n()
  return (error: unknown, fallbackKey: AuthoringMessageKey) => {
    const key = errorMessageKey(projectErrorCode(error))
    if (key) return t(`authoring.${key}`)
    const detail = formatHttpError(error) || (error instanceof Error ? error.message : '')
    return detail || t(`authoring.${fallbackKey}`)
  }
}

/** The server-owned project list. Mutations reload it so the server stays authoritative. */
export function useAuthoringProjects() {
  const projects = ref<AuthoringProject[]>([])
  const loading = ref(false)
  /** True once the list has loaded successfully at least once. */
  const loaded = ref(false)
  const loadError = ref<unknown>(null)

  async function refresh() {
    loading.value = true
    try {
      projects.value = await listPluginProjects()
      loaded.value = true
      loadError.value = null
    } catch (error) {
      loadError.value = error
    } finally {
      loading.value = false
    }
  }

  async function register(sourceDir: string) {
    const project = await registerPluginProject(sourceDir)
    await refresh()
    return project
  }

  async function remove(projectId: string) {
    await removePluginProject(projectId)
    await refresh()
  }

  return { projects, loading, loaded, loadError, refresh, register, remove }
}
