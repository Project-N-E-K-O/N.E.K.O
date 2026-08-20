// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  AUTO_MIRROR_MEASUREMENT_MAX_AGE_MS,
  isGithubReleaseDownloadUrl,
  useGithubMirrorSource,
} from './useGithubMirrorSource'

afterEach(() => {
  vi.useRealTimers()
})

describe('isGithubReleaseDownloadUrl', () => {
  it('accepts a credential-free GitHub Release asset', () => {
    expect(
      isGithubReleaseDownloadUrl(
        'https://github.com/example/plugin/releases/download/v1.0.0/plugin.neko-plugin',
      ),
    ).toBe(true)
  })

  it.each([
    'https://user:password@github.com/example/plugin/releases/download/v1.0.0/plugin.neko-plugin',
    'https://github.com/example/plugin/releases/tag/v1.0.0',
    'https://example.com/example/plugin/releases/download/v1.0.0/plugin.neko-plugin',
  ])('rejects unsafe or non-asset URLs: %s', (url) => {
    expect(isGithubReleaseDownloadUrl(url)).toBe(false)
  })
})

describe('automatic mirror measurement expiry', () => {
  it('invalidates the displayed source when its five-minute TTL expires', () => {
    vi.useFakeTimers()
    const mirror = useGithubMirrorSource()
    mirror.setMode('auto')
    mirror.setAutoSourceId('gh-proxy-com')

    expect(mirror.autoMeasurementFresh.value).toBe(true)
    expect(mirror.activeSource.value?.id).toBe('gh-proxy-com')

    vi.advanceTimersByTime(AUTO_MIRROR_MEASUREMENT_MAX_AGE_MS)

    expect(mirror.autoMeasurementFresh.value).toBe(false)
    expect(mirror.activeSource.value).toBeNull()
  })
})
