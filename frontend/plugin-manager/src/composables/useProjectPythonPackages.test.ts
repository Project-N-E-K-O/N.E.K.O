// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, ref } from 'vue'
import { useProjectPythonPackages } from './useProjectPythonPackages'
import {
  pythonPackagesSnapshot as state,
  pythonPackagesTask as task,
} from '@/test/fixtures/pythonPackages'
import type { PythonPackagesSnapshot, PythonPackagesTask } from '@/api/projectPythonPackages'

const api = vi.hoisted(() => ({
  getProjectPythonPackages: vi.fn(),
  saveProjectPythonRequirements: vi.fn(),
  startProjectPythonTask: vi.fn(),
  getProjectPythonTask: vi.fn(),
  cancelProjectPythonTask: vi.fn(),
}))
vi.mock('@/api/projectPythonPackages', async (original) => ({
  ...(await original<typeof import('@/api/projectPythonPackages')>()),
  ...api,
}))
let app: ReturnType<typeof createApp> | null = null
async function flush() {
  for (let n = 0; n < 12; n++) {
    await Promise.resolve()
    await nextTick()
  }
}
async function mount() {
  const projectId = ref('p1')
  const editing = ref(false)
  const changed = vi.fn()
  let session!: ReturnType<typeof useProjectPythonPackages>
  app = createApp({
    setup() {
      session = useProjectPythonPackages(projectId, changed, editing)
      return () => null
    },
  })
  app.mount(document.createElement('div'))
  await flush()
  return { ...session, projectId, editing, changed }
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  api.getProjectPythonPackages.mockResolvedValue(state())
  api.startProjectPythonTask.mockResolvedValue(task())
  api.getProjectPythonTask.mockResolvedValue(task())
  api.cancelProjectPythonTask.mockResolvedValue(task({ status: 'canceled', phase: 'finished' }))
})
afterEach(() => {
  app?.unmount()
  app = null
  vi.useRealTimers()
})
describe('project Python package session', () => {
  it('notifies once when refresh observes a terminal task before polling', async () => {
    const status = 'succeeded'
    const finished = task({ status, phase: 'finished' })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task() }))
      .mockResolvedValue(state({ task: finished }))
    const session = await mount()
    await session.reload()
    expect(session.changed).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2500)
    expect(api.getProjectPythonTask).not.toHaveBeenCalled()
    await session.reload()
    expect(session.changed).toHaveBeenCalledTimes(1)
    api.getProjectPythonPackages.mockResolvedValue(
      state({ task: { ...finished, task_id: 'another-task' } })
    )
    await session.reload()
    expect(session.changed).toHaveBeenCalledTimes(2)
  })
  it.each(['poll', 'cancel'] as const)(
    'retries failed terminal snapshot reads after %s until controls are released',
    async (completion) => {
      const finished = task({
        status: completion === 'cancel' ? 'canceled' : 'failed',
        phase: 'finished',
      })
      api.getProjectPythonPackages
        .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
        .mockRejectedValueOnce(new Error('Read failed'))
        .mockRejectedValueOnce(new Error('Read failed again'))
        .mockResolvedValue(state({ task: finished }))
      api.getProjectPythonTask.mockResolvedValue(finished)
      api.cancelProjectPythonTask.mockResolvedValue(finished)
      const session = await mount()
      if (completion === 'cancel') await session.cancel()
      else await vi.advanceTimersByTimeAsync(1000)
      await flush()
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(2)
      expect(session.changed).toHaveBeenCalledTimes(1)
      expect(session.busy.value).toBe(true)
      await vi.advanceTimersByTimeAsync(1000)
      await flush()
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(3)
      await vi.advanceTimersByTimeAsync(1000)
      await flush()
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(4)
      expect(session.busy.value).toBe(false)
      expect(session.changed).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(2500)
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(4)
      expect(api.getProjectPythonTask).toHaveBeenCalledTimes(completion === 'cancel' ? 0 : 1)
    }
  )
  it('refreshes a terminal snapshot until its project operation has released', async () => {
    const finished = task({ status: 'succeeded', phase: 'finished' })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
      .mockResolvedValueOnce(state({ task: finished, operation: 'python-packages-sync' }))
      .mockResolvedValue(state({ task: finished }))
    api.getProjectPythonTask.mockResolvedValue(finished)
    const session = await mount()
    await vi.advanceTimersByTimeAsync(2000)
    await flush()
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(3)
    expect(session.busy.value).toBe(false)
    expect(session.changed).toHaveBeenCalledTimes(1)
    expect(api.getProjectPythonTask).toHaveBeenCalledTimes(1)
  })
  it.each(['initial', 'after-terminal'] as const)(
    'keeps refreshing a busy project without a task on %s snapshot',
    async (timing) => {
      const busyWithoutTask = state({ task: null, operation: 'python-packages-sync' })
      if (timing === 'after-terminal') {
        api.getProjectPythonPackages.mockResolvedValueOnce(
          state({ task: task(), operation: 'python-packages-sync' })
        )
        api.getProjectPythonTask.mockResolvedValue(task({ status: 'failed', phase: 'finished' }))
      }
      api.getProjectPythonPackages.mockResolvedValueOnce(busyWithoutTask).mockResolvedValue(state())
      const session = await mount()
      if (timing === 'after-terminal') {
        await vi.advanceTimersByTimeAsync(1000)
        await flush()
      }
      expect(session.busy.value).toBe(true)
      await vi.advanceTimersByTimeAsync(1000)
      await flush()
      expect(session.busy.value).toBe(false)
      const reads = timing === 'after-terminal' ? 3 : 2
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(reads)
      expect(session.changed).toHaveBeenCalledTimes(timing === 'after-terminal' ? 1 : 0)
      await vi.advanceTimersByTimeAsync(2500)
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(reads)
    }
  )
  it('retries a failed snapshot after the server forgets the task', async () => {
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
      .mockRejectedValueOnce(new Error('Read failed'))
      .mockResolvedValueOnce(state({ task: null, operation: 'python-packages-sync' }))
      .mockResolvedValue(state())
    api.getProjectPythonTask.mockRejectedValue({
      response: { data: { detail: { code: 'PYTHON_PACKAGES_TASK_NOT_FOUND' } } },
    })
    const session = await mount()
    await vi.advanceTimersByTimeAsync(3000)
    await flush()
    expect(session.busy.value).toBe(false)
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(4)
    expect(api.getProjectPythonTask).toHaveBeenCalledTimes(1)
    expect(session.changed).not.toHaveBeenCalled()
  })
  it('does not carry terminal snapshot retries into the next project', async () => {
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
      .mockRejectedValueOnce(new Error('Read failed'))
      .mockResolvedValue(state())
    api.getProjectPythonTask.mockResolvedValue(task({ status: 'failed', phase: 'finished' }))
    const session = await mount()
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(session.changed).toHaveBeenCalledTimes(1)
    session.projectId.value = 'p2'
    await flush()
    expect(api.getProjectPythonPackages).toHaveBeenLastCalledWith('p2')
    await vi.advanceTimersByTimeAsync(2500)
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(3)
    expect(session.changed).toHaveBeenCalledTimes(1)
  })
  it('cleans up a pending terminal snapshot retry when the session unmounts', async () => {
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
      .mockRejectedValue(new Error('Read failed'))
    api.getProjectPythonTask.mockResolvedValue(task({ status: 'failed', phase: 'finished' }))
    await mount()
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    app!.unmount()
    app = null
    await vi.advanceTimersByTimeAsync(2500)
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(2)
  })
  it('does not repeat the terminal notification when an earlier poll finishes after refresh', async () => {
    const finished = task({ status: 'failed', phase: 'finished' })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task() }))
      .mockResolvedValue(state({ task: finished }))
    let resolve!: (value: PythonPackagesTask) => void
    api.getProjectPythonTask.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    const session = await mount()
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(api.getProjectPythonTask).toHaveBeenCalledTimes(1)
    await session.reload()
    expect(session.changed).toHaveBeenCalledTimes(1)
    resolve(finished)
    await flush()
    expect(session.changed).toHaveBeenCalledTimes(1)
  })
  it('does not repeat the notification for a canceled task returned by refresh', async () => {
    const finished = task({ status: 'canceled', phase: 'finished' })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task() }))
      .mockResolvedValue(state({ task: finished }))
    const session = await mount()
    await session.cancel()
    expect(session.changed).toHaveBeenCalledTimes(1)
    await session.reload()
    expect(session.changed).toHaveBeenCalledTimes(1)
  })
  it('combines save and terminal snapshot changes into one notification', async () => {
    const finished = task({ status: 'failed', phase: 'finished' })
    const saved = state({ revision: 'r2', task: finished })
    api.saveProjectPythonRequirements.mockResolvedValue(saved)
    const session = await mount()
    session.draft.value.push('new-package')
    await session.save()
    expect(session.changed).toHaveBeenCalledTimes(1)
    api.getProjectPythonPackages.mockResolvedValue(saved)
    await session.reload()
    expect(session.changed).toHaveBeenCalledTimes(1)
  })
  it('ignores the previous project response after switching projects', async () => {
    let resolve!: (value: PythonPackagesSnapshot) => void
    api.getProjectPythonPackages.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    const session = await mount()
    session.projectId.value = 'p2'
    await flush()
    resolve(state({ vendor_path: '/old/vendor' }))
    await flush()
    expect(session.snapshot.value?.vendor_path).not.toBe('/old/vendor')
    expect(api.getProjectPythonPackages).toHaveBeenCalledWith('p2')
  })
})
