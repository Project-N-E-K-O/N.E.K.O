import { beforeEach, describe, expect, it, vi } from 'vitest'
import { deleteModelBinding, getModelBindings, getModelUsage, listModelSlots, setModelBinding, testModelSlot, updateModelSlot } from './models'

const request = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() }))
vi.mock('@/utils/request', () => ({ default: request }))

beforeEach(() => vi.clearAllMocks())

describe('plugin model management API', () => {
  it('keeps all requests in the independent model configuration namespace', async () => {
    await listModelSlots()
    await updateModelSlot('slot_saved', { name: 'renamed' })
    await testModelSlot('slot_saved')
    expect(request.get).toHaveBeenCalledWith('/api/model-config/slots', expect.objectContaining({ suppressErrorMessage: true }))
    expect(request.patch).toHaveBeenCalledWith('/api/model-config/slots/slot_saved', { name: 'renamed' }, expect.any(Object))
    expect(request.post).toHaveBeenCalledWith('/api/model-config/slots/slot_saved/test', undefined, expect.objectContaining({ timeout: 35000 }))
  })
  it('encodes plugin and usage identifiers and binds stable slot IDs', async () => {
    await getModelBindings('plugin/a')
    await setModelBinding('plugin/a', 'use/b', 'slot_id', 3)
    await deleteModelBinding('plugin/a', 'use/b', 4)
    expect(request.get).toHaveBeenCalledWith('/api/model-config/plugins/plugin%2Fa/bindings', expect.any(Object))
    expect(request.put).toHaveBeenCalledWith('/api/model-config/plugins/plugin%2Fa/bindings/use%2Fb', { slot_id: 'slot_id', expected_version: 3 }, expect.any(Object))
    expect(request.delete).toHaveBeenCalledWith('/api/model-config/plugins/plugin%2Fa/bindings/use%2Fb', expect.objectContaining({ params: { expected_version: 4 } }))
  })
  it('passes explicit usage filters without adding write operations', async () => {
    await getModelUsage({ plugin_id: 'example', slot_id: 'slot_id', limit: 100 })
    expect(request.get).toHaveBeenCalledWith('/api/model-config/usage', { suppressErrorMessage: true, params: { plugin_id: 'example', slot_id: 'slot_id', limit: 100 } })
    expect(request.post).not.toHaveBeenCalled()
  })
})

it('confirms a timed-out write before reading bindings', async () => {
  request.put.mockRejectedValueOnce({ code: 'ECONNABORTED' })
  request.post.mockResolvedValueOnce({ version: 1, slot_id: null })
  await expect(setModelBinding('timeout', 'chat', 'slot_id', 0)).rejects.toEqual({ code: 'ECONNABORTED' })
  expect(request.post).toHaveBeenCalledWith('/api/model-config/plugins/timeout/bindings/chat/confirm', { expected_version: 0 }, expect.any(Object))
  await getModelBindings('timeout')
  expect(request.post.mock.invocationCallOrder[0]).toBeLessThan(request.get.mock.invocationCallOrder[0]!)
})

it('keeps an uncertain deletion pending until confirmation succeeds, and blocks stale reads', async () => {
  request.delete.mockRejectedValueOnce({ code: 'ERR_NETWORK' })
  request.post.mockRejectedValue(new Error('offline'))
  await expect(deleteModelBinding('offline', 'chat', 4)).rejects.toThrow('MODEL_BINDING_RESULT_UNKNOWN')
  await expect(listModelSlots()).rejects.toThrow('MODEL_BINDING_RESULT_UNKNOWN')
  expect(request.get).not.toHaveBeenCalled()
  request.post.mockResolvedValue({ version: 5, slot_id: null })
  await listModelSlots()
  expect(request.post).toHaveBeenLastCalledWith('/api/model-config/plugins/offline/bindings/chat/confirm', { expected_version: 4 }, expect.any(Object))
  expect(request.get).toHaveBeenCalledWith('/api/model-config/slots', expect.any(Object))
})

it.each(['bind', 'unbind'])('returns the confirmed result when a successful %s response was lost', async (action) => {
  const plugin = `confirmed-${action}`
  request.post.mockResolvedValueOnce({ version: 8, slot_id: action === 'bind' ? 'target' : null })
  if (action === 'bind') {
    request.put.mockRejectedValueOnce({ code: 'ECONNABORTED' })
    await expect(setModelBinding(plugin, 'chat', 'target', 7)).resolves.toEqual({ plugin_id: plugin, usage_id: 'chat', slot_id: 'target', version: 8 })
  } else {
    request.delete.mockRejectedValueOnce({ code: 'ECONNABORTED' })
    await expect(deleteModelBinding(plugin, 'chat', 7)).resolves.toEqual({ success: true, version: 8 })
  }
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

it('does not downgrade a newer pending version when an old error arrives late', async () => {
  const old = deferred<unknown>()
  request.put.mockImplementationOnce(() => old.promise)
  const oldResult = setModelBinding('race', 'chat', 'A', 0).catch(error => error)
  await vi.waitFor(() => expect(request.put).toHaveBeenCalledTimes(1))
  request.put.mockRejectedValueOnce({ code: 'ERR_NETWORK' })
  request.post.mockRejectedValueOnce(new Error('offline'))
  await expect(setModelBinding('race', 'chat', 'B', 1)).rejects.toThrow('MODEL_BINDING_RESULT_UNKNOWN')
  request.post.mockResolvedValueOnce({ version: 2, slot_id: 'A' })
  old.reject({ code: 'ECONNABORTED' })
  await expect(oldResult).resolves.toMatchObject({ slot_id: 'A', version: 2 })
  expect(request.post).toHaveBeenLastCalledWith('/api/model-config/plugins/race/bindings/chat/confirm', { expected_version: 1 }, expect.any(Object))
  expect(request.post.mock.calls.map(call => call[1].expected_version)).toEqual([1, 1])
})

it('does not let an in-flight older confirmation clear a newer pending write', async () => {
  const oldWrite = deferred<unknown>(), newWrite = deferred<unknown>(), oldConfirm = deferred<unknown>()
  request.put.mockImplementationOnce(() => oldWrite.promise).mockImplementationOnce(() => newWrite.promise)
  const oldResult = setModelBinding('inflight', 'chat', 'A', 0).catch(error => error)
  const newResult = setModelBinding('inflight', 'chat', 'B', 1).catch(error => error)
  await vi.waitFor(() => expect(request.put).toHaveBeenCalledTimes(2))
  request.post.mockImplementationOnce(() => oldConfirm.promise).mockRejectedValueOnce(new Error('offline'))
  oldWrite.reject({ code: 'ERR_NETWORK' })
  await vi.waitFor(() => expect(request.post).toHaveBeenCalledTimes(1))
  newWrite.reject({ code: 'ERR_NETWORK' })
  await expect(newResult).resolves.toMatchObject({ message: 'MODEL_BINDING_RESULT_UNKNOWN' })
  request.post.mockResolvedValueOnce({ version: 2, slot_id: 'A' })
  oldConfirm.resolve({ version: 1, slot_id: 'A' })
  await expect(oldResult).resolves.toMatchObject({ slot_id: 'A', version: 2 })
  expect(request.post.mock.calls.map(call => call[1].expected_version)).toEqual([0, 1, 1])
})
