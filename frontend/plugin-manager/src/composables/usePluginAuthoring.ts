import { computed, onScopeDispose, ref } from 'vue'
import { createAuthoringFixtures, createAuthoringProject } from '@/fixtures/authoring'
import type {
  AuthoringActivity,
  AuthoringOperation,
  AuthoringProject,
  AuthoringProjectDraft,
} from '@/types/authoring'

/**
 * Local interaction model for the authoring preview. No API or runtime store dependencies.
 * Its sample projects and simulated operations are development-only; production builds start empty.
 */
export function usePluginAuthoring() {
  const projects = ref(import.meta.env.DEV ? createAuthoringFixtures() : [])
  const pending = ref<{ projectId: string; operation: AuthoringOperation } | null>(null)
  let timer: ReturnType<typeof setTimeout> | undefined

  function cancelPending() {
    clearTimeout(timer)
    timer = undefined
    pending.value = null
  }
  onScopeDispose(cancelPending)

  function record(
    project: AuthoringProject,
    kind: AuthoringActivity['kind'],
    outcome?: AuthoringActivity['outcome']
  ) {
    project.activity.unshift({ kind, at: Date.now(), outcome })
    project.activity = project.activity.slice(0, 12)
  }

  function perform(
    id: string,
    operation: AuthoringOperation,
    complete: (project: AuthoringProject) => void
  ) {
    if (pending.value) return
    pending.value = { projectId: id, operation }
    timer = setTimeout(() => {
      const project = projects.value.find((item) => item.id === id)
      if (project) complete(project)
      pending.value = null
      timer = undefined
    }, 800)
  }

  function check(id: string) {
    perform(id, 'check', (project) => {
      project.findings = project.sourceFindings.map((finding) => ({ ...finding }))
      project.check = project.findings.length ? 'issues' : 'passed'
      project.checkedAt = Date.now()
      project.checkedRevision = project.revision
      record(project, 'checked')
    })
  }

  function runtime(id: string, action: 'start' | 'stop' | 'reload') {
    const project = projects.value.find((item) => item.id === id)
    if (!project || pending.value) return
    if (action === 'stop') {
      project.runtime = 'stopped'
      record(project, 'stopped')
      return
    }
    perform(id, action, (current) => {
      current.runtime = 'running'
      record(current, action === 'start' ? 'started' : 'reloaded')
    })
  }

  function run(id: string, input: string, fail = false) {
    const project = projects.value.find((item) => item.id === id)
    if (!project || project.runtime !== 'running') return
    perform(id, 'run', (current) => {
      current.run = { status: fail ? 'failed' : 'succeeded', input, at: Date.now() }
      record(current, 'executed', current.run.status === 'failed' ? 'failed' : 'succeeded')
    })
  }

  function markChanged(id: string, repaired = false) {
    const project = projects.value.find((item) => item.id === id)
    if (!project || pending.value) return
    project.revision += 1
    if (project.check !== 'unchecked') project.check = 'stale'
    if (repaired) project.sourceFindings = []
    // A previous release belongs to the previous source snapshot.
    project.release = { phase: 0, interrupted: false }
    record(project, 'changed')
  }

  function build(id: string) {
    perform(id, 'build', (project) => {
      project.artifact = {
        name: `${project.id}.neko-plugin`,
        revision: project.revision,
        at: Date.now(),
      }
      project.release = { phase: 0, interrupted: false }
      record(project, 'built')
    })
  }

  function publish(id: string) {
    const project = projects.value.find((item) => item.id === id)
    if (
      !project ||
      project.check !== 'passed' ||
      project.artifact?.revision !== project.revision ||
      project.release.phase >= 4
    )
      return
    perform(id, 'publish', (current) => {
      current.release.interrupted = false
      current.release.phase += 1
      if (current.release.phase === 4) record(current, 'published')
    })
  }

  function addProject(draft: AuthoringProjectDraft) {
    if (
      !import.meta.env.DEV ||
      pending.value ||
      !/^[a-z][a-z0-9_]*$/.test(draft.id) ||
      projects.value.some((project) => project.id === draft.id)
    )
      return null
    const project = createAuthoringProject(draft)
    projects.value.unshift(project)
    return project.id
  }

  return {
    projects,
    pending,
    busy: computed(() => pending.value !== null),
    check,
    runtime,
    run,
    markChanged,
    build,
    publish,
    addProject,
  }
}
