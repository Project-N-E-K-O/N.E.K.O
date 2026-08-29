import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessageBox } from 'element-plus'

import { confirmBuiltinOverride } from './confirmBuiltinOverride'

vi.mock('element-plus', () => ({
  ElMessageBox: {
    confirm: vi.fn(),
  },
}))

const t = (key: string, params?: Record<string, unknown>) =>
  `${key}${params ? JSON.stringify(params) : ''}`

describe('confirmBuiltinOverride', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the builtin and Market versions before confirming the source switch', async () => {
    vi.mocked(ElMessageBox.confirm).mockResolvedValue({ action: 'confirm', value: '' } as never)

    await expect(
      confirmBuiltinOverride(t, {
        pluginName: 'Study Companion',
        currentVersion: '0.1.5',
        targetVersion: '0.1.6',
      })
    ).resolves.toBe(true)
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      'package.install.overrideBuiltinBody{"current":"0.1.5","target":"0.1.6"}',
      'package.install.overrideBuiltinTitle{"plugin":"Study Companion"}',
      {
        type: 'warning',
        confirmButtonText: 'package.install.overrideBuiltinConfirm',
        cancelButtonText: 'common.cancel',
      }
    )
  })

  it('cancels the source switch when the dialog is dismissed', async () => {
    vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel')

    await expect(
      confirmBuiltinOverride(t, {
        pluginName: 'Study Companion',
        currentVersion: '0.1.5',
        targetVersion: '0.1.6',
      })
    ).resolves.toBe(false)
  })
})
