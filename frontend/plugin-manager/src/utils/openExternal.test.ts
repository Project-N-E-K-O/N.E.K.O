// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { NO_DESKTOP_BRIDGE, openLocalPath, openVerifiedLocalPath, prepareExternalWindow, resolvePathWithinRoots } from './openExternal'

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

  it('keeps literal POSIX backslashes encoded in the file URL fallback', () => {
    const openExternal = vi.fn()
    Object.defineProperty(window, 'electronShell', {
      value: { openExternal },
      configurable: true,
    })
    const checked = resolvePathWithinRoots('/tmp/plugin/x\\..\\..\\..\\etc/passwd', ['/tmp/plugin'])
    expect(checked).toBe('/tmp/plugin/x\\..\\..\\..\\etc/passwd')

    openLocalPath(checked!)

    expect(openExternal).toHaveBeenCalledWith('file:///tmp/plugin/x%5C..%5C..%5C..%5Cetc/passwd')
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

describe('resolvePathWithinRoots', () => {
  const posixRoots = ['/home/u/neko/plugins/plugin_a', '/home/u/.neko/plugins/plugin_a/data']
  const windowsRoots = ['C:\\Neko\\plugins\\plugin_a', 'D:/NekoData/plugins/plugin_a/data/']

  it.each([
    ['/home/u/neko/plugins/plugin_a', '/home/u/neko/plugins/plugin_a'],
    ['/home/u/neko/plugins/plugin_a/', '/home/u/neko/plugins/plugin_a'],
    ['/home/u/neko/plugins/plugin_a/out/report.txt', '/home/u/neko/plugins/plugin_a/out/report.txt'],
    ['/home/u/.neko/plugins/plugin_a/data//exports/./a.csv', '/home/u/.neko/plugins/plugin_a/data/exports/a.csv'],
    ['/home/u/neko/plugins/plugin_a/x/../y', '/home/u/neko/plugins/plugin_a/y'],
    ['file:///home/u/neko/plugins/plugin_a/a%20b.txt', '/home/u/neko/plugins/plugin_a/a b.txt'],
  ])('allows %s inside a POSIX root', (target, expected) => {
    expect(resolvePathWithinRoots(target, posixRoots)).toBe(expected)
  })

  it.each([
    ['c:\\neko\\PLUGINS\\Plugin_A\\out.txt', 'C:\\neko\\PLUGINS\\Plugin_A\\out.txt'],
    ['C:/Neko/plugins/plugin_a/sub\\file.txt', 'C:\\Neko\\plugins\\plugin_a\\sub\\file.txt'],
    ['D:\\NekoData\\plugins\\plugin_a\\data', 'D:\\NekoData\\plugins\\plugin_a\\data'],
    ['file:///C:/Neko/plugins/plugin_a/a.txt', 'C:\\Neko\\plugins\\plugin_a\\a.txt'],
  ])('allows %s inside a Windows root, ignoring case and separator style', (target, expected) => {
    expect(resolvePathWithinRoots(target, windowsRoots)).toBe(expected)
  })

  it.each([
    '/home/u/neko/plugins/plugin_a_evil',
    '/home/u/neko/plugins/plugin_a_evil/x',
    '/home/u/neko/plugins/plugin_a/../plugin_b/x',
    '/home/u/neko/plugins/plugin_a/../../../../../etc/passwd',
    '/home/u/neko/plugins',
    '/HOME/u/neko/plugins/plugin_a',
    '/etc/passwd',
    '~/neko/plugins/plugin_a',
    'neko/plugins/plugin_a',
    '/home/u/neko/plugins/plugin_a/\u0000x',
    'https://example.com/home/u/neko/plugins/plugin_a',
    '',
  ])('rejects %j for POSIX roots', (target) => {
    expect(resolvePathWithinRoots(target, posixRoots)).toBeNull()
  })

  it.each([
    'C:\\Neko\\plugins\\plugin_a_evil\\x',
    'C:\\Neko\\plugins\\plugin_a\\..\\plugin_b',
    'C:\\Neko\\plugins\\plugin_a\\..\\..\\..\\..\\Windows',
    'E:\\Neko\\plugins\\plugin_a',
    'C:\\Neko\\plugins\\plugin_a.\\x',
    'C:\\Neko\\plugins\\plugin_a\\file.txt:stream',
    '\\\\?\\C:\\Neko\\plugins\\plugin_a\\x',
    '\\\\server\\share\\Neko\\plugins\\plugin_a',
    '/Neko/plugins/plugin_a',
    'C:Neko\\plugins\\plugin_a',
  ])('rejects %j for Windows roots', (target) => {
    expect(resolvePathWithinRoots(target, windowsRoots)).toBeNull()
  })

  it('matches UNC roots by host and share', () => {
    const roots = ['\\\\nas\\share\\plugins\\plugin_a']
    expect(resolvePathWithinRoots('//NAS/Share/plugins/plugin_a/x.txt', roots)).toBe('\\\\NAS\\Share\\plugins\\plugin_a\\x.txt')
    expect(resolvePathWithinRoots('\\\\nas\\other\\plugins\\plugin_a', roots)).toBeNull()
  })

  it('keeps backslashes literal in POSIX paths', () => {
    const roots = ['/tmp/plugin\\a']
    expect(resolvePathWithinRoots('/tmp/plugin/a/report.txt', roots)).toBeNull()
    expect(resolvePathWithinRoots('/tmp/plugin\\a/report.txt', roots)).toBe('/tmp/plugin\\a/report.txt')
    expect(resolvePathWithinRoots('/tmp/plugin/sub/report.txt', ['/tmp/plugin'])).toBe('/tmp/plugin/sub/report.txt')
    expect(resolvePathWithinRoots('/tmp/plugin\\..\\..\\etc', ['/tmp'])).toBe('/tmp/plugin\\..\\..\\etc')
    expect(resolvePathWithinRoots('\\tmp\\plugin\\x', ['/tmp/plugin'])).toBeNull()
  })

  it('compares and returns paths without trimming spaces', () => {
    const roots = ['/tmp/plugin ']
    expect(resolvePathWithinRoots('/tmp/plugin/report.txt', roots)).toBeNull()
    expect(resolvePathWithinRoots('/tmp/plugin', roots)).toBeNull()
    expect(resolvePathWithinRoots('/tmp/plugin /report.txt ', roots)).toBe('/tmp/plugin /report.txt ')
    expect(resolvePathWithinRoots(' /tmp/plugin /x', roots)).toBeNull()
    expect(resolvePathWithinRoots('/tmp/plugin/report.txt', ['/tmp/plugin'])).toBe('/tmp/plugin/report.txt')
  })

  it.each([
    'C:\\Neko\\plugins\\plugin_a\\CON',
    'C:\\Neko\\plugins\\plugin_a\\out\\nul.txt',
    'C:\\Neko\\plugins\\plugin_a\\COM1.log',
    'C:\\Neko\\plugins\\plugin_a\\lpt9',
    'C:\\Neko\\plugins\\plugin_a\\conout$',
    'C:\\Neko\\plugins\\plugin_a\\nul .txt',
    'D:/NekoData/plugins/plugin_a/data/con  .log',
  ])('rejects the Windows device name in %j', (target) => {
    expect(resolvePathWithinRoots(target, windowsRoots)).toBeNull()
  })

  it('still allows ordinary Windows names that start like device names', () => {
    expect(resolvePathWithinRoots('C:\\Neko\\plugins\\plugin_a\\console.txt', windowsRoots)).toBe('C:\\Neko\\plugins\\plugin_a\\console.txt')
    expect(resolvePathWithinRoots('C:\\Neko\\plugins\\plugin_a\\com10', windowsRoots)).toBe('C:\\Neko\\plugins\\plugin_a\\com10')
  })

  it('ignores missing roots', () => {
    expect(resolvePathWithinRoots('/anything', [null, undefined, '', 'relative/root'])).toBeNull()
  })
})

describe('openVerifiedLocalPath', () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, 'nekoHost')
    Reflect.deleteProperty(window, 'electronShell')
  })

  it('passes the checked path to the bridge untrimmed', () => {
    const openPath = vi.fn()
    Object.defineProperty(window, 'electronShell', { value: { openPath }, configurable: true })

    openVerifiedLocalPath('/tmp/plugin ')
    openLocalPath('  /tmp/legacy  ')

    expect(openPath).toHaveBeenNthCalledWith(1, '/tmp/plugin ')
    expect(openPath).toHaveBeenNthCalledWith(2, '/tmp/legacy')
  })
})

describe('openLocalPath without a desktop bridge', () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, 'nekoHost')
    Reflect.deleteProperty(window, 'electronShell')
  })

  it('rejects with a recognizable code', async () => {
    await expect(openLocalPath('/tmp/x')).rejects.toMatchObject({ code: NO_DESKTOP_BRIDGE })
  })
})
