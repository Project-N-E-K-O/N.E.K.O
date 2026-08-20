// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

import { isGithubReleaseDownloadUrl } from './useGithubMirrorSource'

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
