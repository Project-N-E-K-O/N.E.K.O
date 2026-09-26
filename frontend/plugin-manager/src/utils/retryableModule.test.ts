import { afterEach, describe, expect, it, vi } from 'vitest'

import { OptionalModuleError, retryableModule } from './retryableModule'

describe('retryableModule', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('shares one in-flight load between callers', async () => {
    const load = vi.fn().mockResolvedValue({ ready: true })
    const getModule = retryableModule(load)

    const [first, second] = await Promise.all([getModule(), getModule()])

    expect(first).toEqual({ ready: true })
    expect(second).toBe(first)
    expect(load).toHaveBeenCalledOnce()
  })

  it('releases the slot after a rejected load so a later call retries', async () => {
    const load = vi.fn()
      .mockRejectedValueOnce(new Error('first failure'))
      .mockResolvedValueOnce({ ready: true })
    const getModule = retryableModule(load)

    await expect(getModule()).rejects.toMatchObject({
      name: 'OptionalModuleError',
      reloadRequired: true,
      message: 'first failure',
    })
    await expect(getModule()).resolves.toEqual({ ready: true })
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('marks timeout failures as retryable without requiring a page reload', async () => {
    vi.useFakeTimers()
    const getModule = retryableModule(() => new Promise(() => {}), 100)

    const pending = getModule()
    vi.advanceTimersByTime(100)

    await expect(pending).rejects.toSatisfy((error: unknown) => {
      return error instanceof OptionalModuleError
        && error.reloadRequired === false
        && error.message === 'Optional module loading timed out'
    })
  })
})
