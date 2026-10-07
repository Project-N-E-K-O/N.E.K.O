// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { get } from './index'
import { downloadDevelopmentPackage } from './development'

vi.mock('./index', () => ({ get: vi.fn() }))
vi.mock('@/utils/request', () => ({ default: {} }))
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

it('downloads a protected blob with its filename and releases the object URL', async () => {
  vi.useFakeTimers()
  const blob = new Blob(['archive'])
  vi.mocked(get).mockResolvedValueOnce(blob)
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:development')
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.href).toBe('blob:development')
    expect(this.download).toBe('中文 package.neko-plugin')
    expect(this.isConnected).toBe(true)
  })
  await downloadDevelopmentPackage('C:\\packages-development\\中文 package.neko-plugin')
  expect(get).toHaveBeenCalledWith('/plugins/development/download', expect.objectContaining({
    headers: { 'X-Neko-Development': '1' }, responseType: 'blob',
    params: { package: 'C:\\packages-development\\中文 package.neko-plugin' },
  }))
  expect(create).toHaveBeenCalledWith(blob)
  expect(click).toHaveBeenCalledOnce()
  expect(document.querySelector('a')).toBeNull()
  expect(revoke).not.toHaveBeenCalled()
  vi.runAllTimers()
  expect(revoke).toHaveBeenCalledWith('blob:development')
})

it('propagates access denial without starting a download', async () => {
  vi.mocked(get).mockRejectedValueOnce(new Error('403 forbidden'))
  const create = vi.spyOn(URL, 'createObjectURL')
  await expect(downloadDevelopmentPackage('demo.neko-plugin')).rejects.toThrow('403 forbidden')
  expect(create).not.toHaveBeenCalled()
})
