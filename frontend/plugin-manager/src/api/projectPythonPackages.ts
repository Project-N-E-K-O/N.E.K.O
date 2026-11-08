import { get, post, put } from './index'

export interface PythonRequirement {
  requirement: string
  name: string
  specifier: string
  extras: string[]
  marker: string | null
  editable: boolean
  status:
    | 'satisfied'
    | 'missing'
    | 'version_mismatch'
    | 'not_applicable'
    | 'host_provided'
    | 'invalid_metadata'
  installed_version: string | null
}
export interface InstalledPythonPackage {
  name: string
  version: string
  direct: boolean
  requires_dist: string[]
  requires_python: string | null
  summary: string | null
}
export interface PythonPackageIssue {
  code: string
  message: string
  package?: string
  requirement?: string
  path?: string
}
export interface PythonDependencyEdge {
  id: string
  requirements: string[]
  status: 'satisfied' | 'missing' | 'version_mismatch'
}
export interface PythonDependencyNode {
  id: string
  name: string
  version: string | null
  direct: boolean
  declarations: string[]
  status: 'satisfied' | 'missing' | 'version_mismatch'
  dependencies: PythonDependencyEdge[]
  required_by: string[]
}
export interface PythonDependencyGraph {
  roots: string[]
  other_roots: string[]
  nodes: PythonDependencyNode[]
}
export interface PythonPackagesSnapshot {
  revision: string
  requirements: PythonRequirement[]
  installed: InstalledPythonPackage[]
  dependency_graph: PythonDependencyGraph
  issues: PythonPackageIssue[]
  vendor_path: string
  pyproject_path: string
  vendor_exists: boolean
  running: boolean
  environment: {
    python_version: string
    python_executable: string | null
    platform: string
    architecture: string
    installer: string | null
    installer_path: string | null
    packaged: boolean
    can_sync: boolean
    reason: string | null
    setup_required: boolean
  }
  task: PythonPackagesTask | null
  operation: string | null
}
export interface PythonPackagesTask {
  task_id: string
  project_id: string
  operation: 'sync' | 'check'
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'
  phase: 'preparing' | 'installing' | 'checking' | 'refreshing' | 'replacing' | 'finished'
  log: string[]
  message: string
  started_at: number | null
  finished_at: number | null
  updated_at: number
  result: {
    after: Pick<
      PythonPackagesSnapshot,
      'revision' | 'requirements' | 'installed' | 'issues'
    >
    changes?: { name: string; before: string | null; after: string | null }[]
  } | null
}
export const pythonTaskActive = (task: PythonPackagesTask | null) =>
  task?.status === 'queued' || task?.status === 'running'
const config = { suppressErrorMessage: true, timeout: 90_000 }
const path = (projectId: string) =>
  `/plugin-projects/${encodeURIComponent(projectId)}/python-packages`
export const getProjectPythonPackages = (projectId: string): Promise<PythonPackagesSnapshot> =>
  get(path(projectId), config)
export const saveProjectPythonRequirements = (
  projectId: string,
  requirements: string[],
  revision: string
): Promise<PythonPackagesSnapshot> =>
  put(`${path(projectId)}/requirements`, { requirements, revision }, config)
export const startProjectPythonTask = (
  projectId: string,
  operation: 'sync' | 'check',
  revision: string
): Promise<PythonPackagesTask> => post(`${path(projectId)}/tasks`, { operation, revision }, config)
export const getProjectPythonTask = (
  projectId: string,
  taskId: string
): Promise<PythonPackagesTask> =>
  get(`${path(projectId)}/tasks/${encodeURIComponent(taskId)}`, config)
export const cancelProjectPythonTask = (
  projectId: string,
  taskId: string
): Promise<PythonPackagesTask> =>
  post(`${path(projectId)}/tasks/${encodeURIComponent(taskId)}/cancel`, {}, config)
