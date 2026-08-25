// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import { resolvePluginPackageErrorMessage } from './pluginPackageError'

const t = (key: string) => key

function responseError(code: string, details?: Record<string, unknown>) {
  return {
    response: {
      data: {
        detail: details ? { code, details } : 'technical detail',
      },
      headers: { 'x-error-code': code },
    },
  }
}

describe('resolvePluginPackageErrorMessage', () => {
  it('maps a stable package code to one localized message', () => {
    expect(resolvePluginPackageErrorMessage(
      responseError('PLUGIN_PACKAGE_IDENTITY_MISMATCH'),
      t,
      'plan',
    )).toBe('package.install.error.identityMismatch')
  })

  it.each([
    ['File is not a zip file', 'package.install.error.invalidArchive'],
    [
      "required file 'manifest.toml' not found in package archive",
      'package.install.error.manifestMissing',
    ],
    [
      'package manifest.toml is inside an extra parent folder',
      'package.install.error.nestedRoot',
    ],
  ])('classifies a legacy error without displaying %s', (detail, expected) => {
    expect(resolvePluginPackageErrorMessage({ response: { data: { detail } } }, t, 'plan'))
      .toBe(expected)
  })

  it('explains a rolled-back hash mismatch as a package validation problem', () => {
    expect(resolvePluginPackageErrorMessage(responseError('PLUGIN_UPGRADE_ROLLED_BACK', {
      rollback_status: 'completed',
      cause_code: 'PLUGIN_PACKAGE_HASH_MISMATCH',
    }), t, 'install')).toBe('package.install.error.hashMismatch')
  })

  it('does not expose an absolute path from an invalid plugin manifest', () => {
    const error = {
      response: {
        data: {
          detail: "'payload/plugins/demo/plugin.toml' in 'C:\\Users\\name\\package.neko-plugin' contains invalid TOML: broken",
        },
      },
    }
    const message = resolvePluginPackageErrorMessage(error, t, 'plan')
    expect(message).toBe('package.install.error.pluginManifestInvalid')
    expect(message).not.toContain('C:\\Users')
  })

  it('uses a short localized fallback for unknown technical details', () => {
    const error = {
      response: { data: { detail: 'failure at C:\\Users\\name\\private.neko-plugin' } },
    }
    expect(resolvePluginPackageErrorMessage(error, t, 'upload')).toBe('plugins.importFailed')
    expect(resolvePluginPackageErrorMessage(error, t, 'inspect'))
      .toBe('package.install.error.inspectFailed')
    expect(resolvePluginPackageErrorMessage(error, t, 'verify'))
      .toBe('package.install.error.verifyFailed')
  })
})
