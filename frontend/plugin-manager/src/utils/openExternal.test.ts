// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { openLocalPath, prepareExternalWindow } from './openExternal'

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

describe('prepareExternalWindow', () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, 'electronShell')
    vi.restoreAllMocks()
  })

  it('opens a blank window inside the click and points it at the URL later', () => {
    const popup = { opener: {}, closed: false, location: { href: '' }, close: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)

    const target = prepareExternalWindow()
    expect(open).toHaveBeenCalledTimes(1)
    expect(popup.opener).toBeNull()

    target.open('https://market.example/#/knowledge?neko_pair=c')
    expect(popup.location.href).toBe('https://market.example/#/knowledge?neko_pair=c')
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('reports a blank window the browser refused', () => {
    vi.spyOn(window, 'open').mockReturnValue(null)
    expect(prepareExternalWindow().blocked).toBe(true)
  })

  it('closes the blank window on cancel or an unsafe URL', () => {
    const popup = { opener: {}, closed: false, location: { href: '' }, close: vi.fn() }
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)

    prepareExternalWindow().cancel()
    prepareExternalWindow().open('javascript:alert(1)')
    expect(popup.close).toHaveBeenCalledTimes(2)
    expect(popup.location.href).toBe('')
  })

  it('opens nothing up front in Electron and routes to the system browser', () => {
    const openExternal = vi.fn()
    Object.defineProperty(window, 'electronShell', { value: { openExternal }, configurable: true })
    const open = vi.spyOn(window, 'open')

    const target = prepareExternalWindow()
    expect(open).not.toHaveBeenCalled()
    target.open('https://market.example/')
    expect(openExternal).toHaveBeenCalledWith('https://market.example/')
  })
})
