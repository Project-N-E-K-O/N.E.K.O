export type AuthoringTab = 'overview' | 'runtime' | 'checks' | 'delivery'
export type CheckState = 'unchecked' | 'issues' | 'passed' | 'stale'
export type HelpTopic = 'start' | 'runtime' | 'checks' | 'publish'
export type AuthoringOperation = 'check' | 'start' | 'reload' | 'run' | 'build' | 'publish'

export interface AuthoringFinding {
  id: 'dependencies' | 'shutdown'
  severity: 'error' | 'warning'
  file: string
  line: number
}

export interface AuthoringActivity {
  kind:
    | 'created'
    | 'checked'
    | 'started'
    | 'stopped'
    | 'reloaded'
    | 'executed'
    | 'built'
    | 'published'
    | 'changed'
  at: number
  outcome?: 'succeeded' | 'failed'
}

export interface AuthoringProjectMetadata {
  key: string
  id: string
  name: string
  description: string
  type: 'plugin' | 'adapter'
  version: string
  author: string
  source: string
  entry: string
}

export interface CreatedAuthoringProject extends AuthoringProjectMetadata {
  kind: 'created'
  createdAt: number
}

export type AuthoringProjectItem = AuthoringProject | CreatedAuthoringProject

export interface AuthoringProject extends AuthoringProjectMetadata {
  kind: 'demo'
  sample?: 'hello' | 'weather' | 'memo'
  runtime: 'stopped' | 'running'
  check: CheckState
  checkedAt: number | null
  revision: number
  checkedRevision: number | null
  findings: AuthoringFinding[]
  sourceFindings: AuthoringFinding[]
  run: { status: 'idle' | 'succeeded' | 'failed'; input: string; at: number | null }
  artifact: { name: string; revision: number; at: number } | null
  release: { phase: number; interrupted: boolean }
  activity: AuthoringActivity[]
}

export interface AuthoringProjectDraft {
  id: string
  name: string
  description: string
  type: 'plugin' | 'adapter'
  author: string
}
