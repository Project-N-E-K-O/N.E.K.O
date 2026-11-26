// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { useEnterKey } from './enterKey'

const key = (init: KeyboardEventInit) => new KeyboardEvent('keydown', init)
/** Enter as a browser tells of it while a word is being picked, in the two ways they do. */
const PICKING: Array<[string, KeyboardEventInit]> = [
  ['isComposing', { key: 'Enter', keyCode: 13, isComposing: true }],
  ['keyCode 229', { key: 'Enter', keyCode: 229 }],
  ['both, the key named Process', { key: 'Process', keyCode: 229, isComposing: true }],
]
const ENTER: KeyboardEventInit = { key: 'Enter', keyCode: 13 }

describe('Enter in a field that acts when the key is let go', () => {
  it('runs for a plain Enter', () => {
    const enterKey = useEnterKey()
    const run = vi.fn()
    enterKey.down(key(ENTER))
    enterKey.up(run)
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('runs for an Enter whose way down was not seen', () => {
    const run = vi.fn()
    useEnterKey().up(run)
    expect(run).toHaveBeenCalledTimes(1)
  })

  it.each(PICKING)('does not run for the Enter that picks a word (%s)', (_name, picking) => {
    const enterKey = useEnterKey()
    const run = vi.fn()
    enterKey.down(key(picking))
    enterKey.up(run)
    expect(run).not.toHaveBeenCalled()
  })

  it.each(PICKING)('runs for the plain Enter after the one that picked a word (%s)', (_name, picking) => {
    const enterKey = useEnterKey()
    const run = vi.fn()
    enterKey.down(key(picking))
    enterKey.up(run)
    enterKey.down(key(ENTER))
    enterKey.up(run)
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('runs for a plain Enter after letters typed through the input method', () => {
    const enterKey = useEnterKey()
    const run = vi.fn()
    // "ni", then the word picked with the space bar: no Enter was part of it.
    for (const letter of ['n', 'i', ' ']) enterKey.down(key({ key: 'Process', keyCode: 229, isComposing: true, code: letter }))
    enterKey.down(key(ENTER))
    enterKey.up(run)
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('does not run when another key goes down before the Enter that picked a word is let go', () => {
    const enterKey = useEnterKey()
    const run = vi.fn()
    enterKey.down(key({ key: 'Process', keyCode: 229, isComposing: true }))
    enterKey.down(key({ key: '1', keyCode: 49 }))
    enterKey.up(run)
    expect(run).not.toHaveBeenCalled()
  })

  it('holds back one Enter only: the next one let go runs even with no key seen going down', () => {
    const enterKey = useEnterKey()
    const run = vi.fn()
    enterKey.down(key({ key: 'Enter', keyCode: 229 }))
    enterKey.up(run)
    enterKey.up(run)
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('keeps what one field noted from another', () => {
    const one = useEnterKey()
    const other = useEnterKey()
    const run = vi.fn()
    one.down(key({ key: 'Enter', keyCode: 229 }))
    other.up(run)
    expect(run).toHaveBeenCalledTimes(1)
  })
})
