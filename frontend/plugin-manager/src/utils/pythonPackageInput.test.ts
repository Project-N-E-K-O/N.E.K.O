import { describe, expect, it } from 'vitest'
import { parsePythonPackageInput, pythonVersionPolicy } from './pythonPackageInput'

describe('package editor input', () => {
  it.each([
    'httpx',
    'pip install httpx',
    'python -m pip install httpx',
    'uv pip install httpx',
    'pip install --upgrade "httpx"',
  ])('reads a package name from %s', (value) => {
    expect(parsePythonPackageInput(value)).toEqual({
      name: 'httpx',
      specifier: '',
      extras: '',
      marker: '',
    })
  })
  it('keeps an existing range, optional features and environment condition', () => {
    expect(parsePythonPackageInput('httpx[socks]>=0.27,<1; sys_platform == "win32"')).toEqual({
      name: 'httpx',
      specifier: '>=0.27,<1',
      extras: 'socks',
      marker: 'sys_platform == "win32"',
    })
  })
  it.each(['pip install httpx requests', 'httpx @ https://example.test/httpx.whl', ''])(
    'does not silently reinterpret %s',
    (value) => {
      expect(parsePythonPackageInput(value)).toBeNull()
    }
  )
  it('distinguishes automatic, fixed and custom versions', () => {
    expect(pythonVersionPolicy('')).toBe('auto')
    expect(pythonVersionPolicy('==0.28.1')).toBe('exact')
    expect(pythonVersionPolicy('>=0.27,<1')).toBe('custom')
    expect(pythonVersionPolicy('==0.28.*')).toBe('custom')
  })
})
