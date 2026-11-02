import type { AuthoringProject, AuthoringProjectDraft } from '@/types/authoring'
import { authoringEntry } from '@/utils/authoringEntry'

export function createAuthoringProject(draft: AuthoringProjectDraft): AuthoringProject {
  return {
    ...draft,
    kind: 'demo',
    key: draft.id,
    version: '0.1.0',
    source: `plugin/plugins/${draft.id}`,
    entry: authoringEntry(draft.id),
    runtime: 'stopped',
    check: 'unchecked',
    checkedAt: null,
    revision: 1,
    checkedRevision: null,
    findings: [],
    sourceFindings: [],
    run: { status: 'idle', input: '', at: null },
    artifact: null,
    release: { phase: 0, interrupted: false },
    activity: [{ kind: 'created', at: Date.now() }],
  }
}

/** Each view owns a fresh fixture graph. Nothing is written to plugin stores or disk. */
export function createAuthoringFixtures(): AuthoringProject[] {
  const now = Date.now()
  const hello = createAuthoringProject({
    id: 'hello_world',
    name: '',
    description: '',
    author: 'N.E.K.O Developer',
    type: 'plugin',
  })
  hello.sample = 'hello'
  const weather = createAuthoringProject({
    id: 'weather_helper',
    name: '',
    description: '',
    author: 'N.E.K.O Developer',
    type: 'plugin',
  })
  Object.assign(weather, {
    sample: 'weather',
    version: '0.2.0',
    check: 'issues',
    checkedAt: now - 240_000,
    checkedRevision: 1,
    findings: [
      { id: 'dependencies', severity: 'error', file: 'pyproject.toml', line: 8 },
      { id: 'shutdown', severity: 'warning', file: '__init__.py', line: 16 },
    ],
    activity: [{ kind: 'checked', at: now - 240_000 }, ...weather.activity],
  })
  weather.sourceFindings = weather.findings.map((finding) => ({ ...finding }))
  const memo = createAuthoringProject({
    id: 'memo_assistant',
    name: '',
    description: '',
    author: 'N.E.K.O Developer',
    type: 'plugin',
  })
  Object.assign(memo, {
    sample: 'memo',
    version: '1.0.0',
    check: 'passed',
    checkedAt: now - 600_000,
    checkedRevision: 1,
    artifact: { name: 'memo_assistant.neko-plugin', revision: 1, at: now - 300_000 },
    activity: [
      { kind: 'built', at: now - 300_000 },
      { kind: 'checked', at: now - 600_000 },
      ...memo.activity,
    ],
  })
  return [hello, weather, memo]
}
