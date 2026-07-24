// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { openLocalPath } from './openExternal'

describe('openLocalPath file URL conversion', () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, 'nekoHost')
    Reflect.deleteProperty(window, 'electronShell')
  })

  it('keeps Windows drive letters unescaped in file URLs', () => {
    const openExternal = vi.fn()
    Object.defineProperty(window, 'electronShell', {
      value: { openExternal },
      configurable: true,
    })

    openLocalPath('C:\\tmp\\neko package\\model.json')

    expect(openExternal).toHaveBeenCalledWith('file:///C:/tmp/neko%20package/model.json')
  })

  it('preserves UNC host and share for file URL fallback', () => {
    const openExternal = vi.fn()
    Object.defineProperty(window, 'electronShell', {
      value: { openExternal },
      configurable: true,
    })

    openLocalPath('\\\\server\\share dir\\model.json')

    expect(openExternal).toHaveBeenCalledWith('file://server/share%20dir/model.json')
  })

  it('normalizes UNC file URLs back to native paths for direct openPath', () => {
    const openPath = vi.fn()
    Object.defineProperty(window, 'electronShell', {
      value: { openPath },
      configurable: true,
    })

    openLocalPath('file://server/share%20dir/model.json')

    expect(openPath).toHaveBeenCalledWith('\\\\server\\share dir\\model.json')
  })

  it('normalizes Windows drive file URLs back to native paths for direct openPath', () => {
    const openPath = vi.fn()
    Object.defineProperty(window, 'electronShell', {
      value: { openPath },
      configurable: true,
    })

    openLocalPath('file:///C:/tmp/neko%20package/model.json')

    expect(openPath).toHaveBeenCalledWith('C:\\tmp\\neko package\\model.json')
  })
})
