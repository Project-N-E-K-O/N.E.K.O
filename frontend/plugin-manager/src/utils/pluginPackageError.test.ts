// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import {
  resolvePluginPackageErrorCodeMessage,
  resolvePluginPackageErrorMessage,
} from './pluginPackageError'

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
  it('exposes the same stable code mapping to Market task errors', () => {
    expect(resolvePluginPackageErrorCodeMessage(
      'PLUGIN_PACKAGE_IDENTITY_MISMATCH',
      t,
    )).toBe('package.install.error.identityMismatch')
    expect(resolvePluginPackageErrorCodeMessage('UNKNOWN', t)).toBeNull()
  })
  it.each([
    ['PLUGIN_PACKAGE_INVALID_ARCHIVE', 'package.install.error.invalidArchive'],
    ['PLUGIN_PACKAGE_MANIFEST_MISSING', 'package.install.error.manifestMissing'],
    ['PLUGIN_PACKAGE_NESTED_ROOT', 'package.install.error.nestedRoot'],
    ['PLUGIN_PACKAGE_MANIFEST_INVALID', 'package.install.error.manifestInvalid'],
    ['PLUGIN_PACKAGE_PLUGIN_MANIFEST_INVALID', 'package.install.error.pluginManifestInvalid'],
    ['PLUGIN_PACKAGE_PLUGIN_MANIFEST_MISSING', 'package.install.error.pluginManifestMissing'],
    ['PLUGIN_PACKAGE_IDENTITY_MISMATCH', 'package.install.error.identityMismatch'],
    ['PLUGIN_PACKAGE_TYPE_MISMATCH', 'package.install.error.packageTypeMismatch'],
    ['PLUGIN_PACKAGE_HASH_MISMATCH', 'package.install.error.hashMismatch'],
    ['PLUGIN_PACKAGE_STATE_CONFLICT', 'package.install.error.packageStateConflict'],
  ])('maps %s to one localized message', (code, expected) => {
    expect(resolvePluginPackageErrorMessage(responseError(code), t, 'plan')).toBe(expected)
  })

  it('explains a hash mismatch without implying the old plugin was damaged', () => {
    expect(resolvePluginPackageErrorMessage(responseError('PLUGIN_UPGRADE_ROLLED_BACK', {
      rollback_status: 'completed',
      cause_code: 'PLUGIN_PACKAGE_HASH_MISMATCH',
    }), t, 'install')).toBe('package.install.error.hashMismatchRolledBack')
  })

  it('does not claim restoration when rollback was incomplete', () => {
    expect(resolvePluginPackageErrorMessage(responseError('PLUGIN_UPGRADE_ROLLED_BACK', {
      rollback_status: 'incomplete',
      cause_code: 'PLUGIN_PACKAGE_HASH_MISMATCH',
    }), t, 'install')).toBe('package.install.rollbackIncomplete')
  })

  it('classifies legacy raw errors without exposing their absolute path', () => {
    const error = {
      response: {
        data: {
          detail: "'payload/plugins/demo/plugin.toml' in 'C:\\Users\\name\\package.neko-plugin' contains invalid TOML: broken",
        },
      },
    }
    expect(resolvePluginPackageErrorMessage(error, t, 'plan')).toBe(
      'package.install.error.pluginManifestInvalid',
    )
  })

  it.each([
    ['File is not a zip file', 'package.install.error.invalidArchive'],
    [
      "required file 'manifest.toml' not found in package archive",
      'package.install.error.manifestMissing',
    ],
    [
      'manifest.toml is nested below an extra parent folder',
      'package.install.error.nestedRoot',
    ],
  ])('keeps legacy servers actionable without showing %s', (detail, expected) => {
    expect(resolvePluginPackageErrorMessage({
      response: { data: { detail } },
    }, t, 'plan')).toBe(expected)
  })

  it('uses a short generic message for unknown technical details', () => {
    expect(resolvePluginPackageErrorMessage({
      response: {
        data: {
          detail: "unexpected failure at C:\\Users\\name\\private.neko-plugin",
        },
      },
    }, t, 'upload')).toBe('plugins.importFailed')
  })

  it.each([
    ['inspect', 'package.install.error.inspectFailed'],
    ['verify', 'package.install.error.verifyFailed'],
  ] as const)('uses a localized %s fallback without exposing technical details', (phase, expected) => {
    expect(resolvePluginPackageErrorMessage({
      response: {
        data: {
          detail: 'unexpected failure at C:\\Users\\name\\private.neko-plugin',
        },
      },
    }, t, phase)).toBe(expected)
  })
})
