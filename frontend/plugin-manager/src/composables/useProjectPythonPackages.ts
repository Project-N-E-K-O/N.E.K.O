import { computed, onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import { projectErrorCode } from '@/api/pluginProjects'
import {
  cancelProjectPythonTask,
  getProjectPythonPackages,
  getProjectPythonTask,
  pythonTaskActive,
  saveProjectPythonRequirements,
  startProjectPythonTask,
  type PythonPackagesSnapshot,
  type PythonPackagesTask,
} from '@/api/projectPythonPackages'

/** Project-owned draft and task session. A response from an old project never changes the new one. */
export function useProjectPythonPackages(projectId: Ref<string>, onChanged: () => void) {
  const snapshot = shallowRef<PythonPackagesSnapshot | null>(null)
  const task = shallowRef<PythonPackagesTask | null>(null)
  const draft = ref<string[]>([])
  const baseline = ref<string[]>([])
  const revision = ref('')
  const loading = ref(false)
  const saving = ref(false)
  const error = shallowRef<unknown>(null)
  const clock = ref(Date.now())
  const dirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(baseline.value))
  const active = computed(() => pythonTaskActive(task.value))
  const busy = computed(() => saving.value || active.value || !!snapshot.value?.operation)
  let alive = true
  let generation = 0
  let request = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const current = (id: string, seq: number) => alive && projectId.value === id && generation === seq
  const resetTimer = () => {
    if (timer) clearTimeout(timer)
    timer = undefined
  }

  function applySnapshot(next: PythonPackagesSnapshot, replaceDraft: boolean) {
    if (replaceDraft) {
      draft.value = next.requirements.map((item) => item.requirement)
      baseline.value = [...draft.value]
      revision.value = next.revision
    }
    snapshot.value = next
    task.value = next.task
    schedule()
  }
  async function reload(discardDraft = false) {
    const id = projectId.value
    const seq = generation
    const req = ++request
    loading.value = true
    error.value = null
    try {
      const next = await getProjectPythonPackages(id)
      if (current(id, seq) && req === request) applySnapshot(next, discardDraft || !dirty.value)
    } catch (cause) {
      if (current(id, seq) && req === request) error.value = cause
    } finally {
      if (current(id, seq) && req === request) loading.value = false
    }
  }
  function schedule() {
    resetTimer()
    if (active.value && alive) timer = setTimeout(() => void poll(), 1000)
  }
  async function poll() {
    const id = projectId.value
    const seq = generation
    const taskId = task.value?.task_id
    if (!taskId) return
    clock.value = Date.now()
    try {
      const next = await getProjectPythonTask(id, taskId)
      if (!current(id, seq) || task.value?.task_id !== taskId) return
      task.value = next
      error.value = null
      if (!pythonTaskActive(next)) {
        await reload()
        if (current(id, seq)) onChanged()
      }
    } catch (cause) {
      if (current(id, seq)) {
        if (projectErrorCode(cause) === 'PYTHON_PACKAGES_TASK_NOT_FOUND') {
          task.value = null
          resetTimer()
          await reload()
        } else error.value = cause
      }
    } finally {
      if (current(id, seq)) schedule()
    }
  }
  async function run(operation: 'sync' | 'check') {
    if (busy.value || !snapshot.value || dirty.value) return false
    const id = projectId.value
    const seq = generation
    saving.value = true
    error.value = null
    try {
      const next = await startProjectPythonTask(id, operation, snapshot.value.revision)
      if (current(id, seq)) {
        task.value = next
        schedule()
        return true
      }
    } catch (cause) {
      if (current(id, seq)) error.value = cause
    } finally {
      if (current(id, seq)) saving.value = false
    }
    return false
  }
  async function save(andSync = false) {
    if (busy.value || !snapshot.value) return false
    const id = projectId.value
    const seq = generation
    saving.value = true
    error.value = null
    try {
      const next = await saveProjectPythonRequirements(id, [...draft.value], revision.value)
      if (!current(id, seq)) return false
      applySnapshot(next, true)
      onChanged()
    } catch (cause) {
      if (current(id, seq)) error.value = cause
      return false
    } finally {
      if (current(id, seq)) saving.value = false
    }
    if (andSync && current(id, seq)) await run('sync')
    return current(id, seq)
  }
  async function cancel() {
    if (!task.value || !active.value || saving.value) return
    const id = projectId.value
    const seq = generation
    saving.value = true
    try {
      const next = await cancelProjectPythonTask(id, task.value.task_id)
      if (current(id, seq)) {
        task.value = next
        await reload()
        onChanged()
      }
    } catch (cause) {
      if (current(id, seq)) error.value = cause
    } finally {
      if (current(id, seq)) saving.value = false
    }
  }
  watch(
    projectId,
    () => {
      generation += 1
      request += 1
      resetTimer()
      snapshot.value = null
      task.value = null
      draft.value = []
      baseline.value = []
      revision.value = ''
      loading.value = false
      saving.value = false
      error.value = null
      void reload()
    },
    { immediate: true }
  )
  onBeforeUnmount(() => {
    alive = false
    resetTimer()
  })
  return {
    snapshot,
    task,
    draft,
    dirty,
    active,
    busy,
    loading,
    saving,
    error,
    clock,
    reload,
    save,
    run,
    cancel,
  }
}
