// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent } from '@testing-library/dom'
import fc from 'fast-check'
import runtimeSource from './runtime.js?raw'

declare global {
  interface Window {
    NekoUiKit: any
    __NEKO_PAYLOAD: any
  }
}

function installRuntime() {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  Object.defineProperty(window, 'parent', {
    value: window,
    configurable: true,
  })
  window.__NEKO_PAYLOAD = {
    locale: 'en',
    i18n: {
      locale: 'en',
      default_locale: 'en',
      messages: {
        en: {
          greeting: 'Hello {name}',
        },
      },
    },
  }
  window.NekoUiKit = undefined
  window.eval(runtimeSource)
  return window.NekoUiKit
}

async function flushMicrotasks() {
  await Promise.resolve()
  await Promise.resolve()
}

describe('hosted ui runtime', () => {
  let ui: any
  let root: HTMLElement

  beforeEach(() => {
    ui = installRuntime()
    root = document.createElement('main')
    document.body.appendChild(root)
  })

  it('runs hooks inside function components', async () => {
    function Counter() {
      const [count, setCount] = ui.useState(0)
      return ui.h('button', { id: 'counter', onClick: () => setCount((value: number) => value + 1) }, String(count))
    }

    ui.render(ui.h(Counter, null), root)

    const button = root.querySelector<HTMLButtonElement>('#counter')!
    expect(button.textContent).toBe('0')
    fireEvent.click(button)
    await flushMicrotasks()
    expect(root.querySelector('#counter')?.textContent).toBe('1')
  })

  it('keeps input DOM and focus while useLocalState updates', async () => {
    function Form() {
      const [value, setValue] = ui.useLocalState('name', '')
      return ui.h('input', {
        id: 'name',
        value,
        onInput: (event: InputEvent) => setValue((event.target as HTMLInputElement).value),
      })
    }

    ui.render(ui.h(Form, null), root)

    const input = root.querySelector<HTMLInputElement>('#name')!
    input.focus()
    input.value = 'abc'
    fireEvent.input(input)
    await flushMicrotasks()

    const nextInput = root.querySelector<HTMLInputElement>('#name')!
    expect(nextInput).toBe(input)
    expect(document.activeElement).toBe(input)
    expect(nextInput.value).toBe('abc')
  })

  it('reorders keyed children without replacing nodes', async () => {
    let setItems!: (items: string[]) => string[]

    function List() {
      const [items, updateItems] = ui.useState(['a', 'b', 'c'])
      setItems = updateItems
      return ui.h('ul', null, items.map((item: string) => ui.h('li', { key: item, id: item }, item)))
    }

    ui.render(ui.h(List, null), root)
    const a = root.querySelector('#a')
    const c = root.querySelector('#c')

    setItems(['c', 'b', 'a'])
    await flushMicrotasks()

    expect(Array.from(root.querySelectorAll('li')).map((item) => item.textContent)).toEqual(['c', 'b', 'a'])
    expect(root.querySelector('#a')).toBe(a)
    expect(root.querySelector('#c')).toBe(c)
  })

  it('cleans up effects on unmount', async () => {
    const cleanup = vi.fn()
    let setVisible!: (visible: boolean) => boolean

    function Child() {
      ui.useEffect(() => cleanup, [])
      return ui.h('span', { id: 'child' }, 'child')
    }

    function App() {
      const [visible, updateVisible] = ui.useState(true)
      setVisible = updateVisible
      return ui.h('div', null, visible ? ui.h(Child, null) : null)
    }

    ui.render(ui.h(App, null), root)
    await flushMicrotasks()
    expect(root.querySelector('#child')).not.toBeNull()

    setVisible(false)
    await flushMicrotasks()

    expect(root.querySelector('#child')).toBeNull()
    expect(cleanup).toHaveBeenCalledTimes(1)
  })

  it('translates with plugin i18n messages', () => {
    expect(ui.t('greeting', { name: 'Neko' })).toBe('Hello Neko')
  })

  it('updates event listeners instead of stacking stale handlers', () => {
    const first = vi.fn()
    const second = vi.fn()

    ui.render(ui.h('button', { id: 'button', onClick: first }, 'Click'), root)
    ui.render(ui.h('button', { id: 'button', onClick: second }, 'Click'), root)

    fireEvent.click(root.querySelector('#button')!)

    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('patches className, style, boolean props, and removes stale attributes', () => {
    ui.render(ui.h('button', {
      id: 'target',
      className: 'first',
      style: { color: 'red', backgroundColor: 'blue' },
      disabled: true,
      title: 'old',
    }, 'Button'), root)

    ui.render(ui.h('button', {
      id: 'target',
      className: 'second',
      style: { color: 'green' },
      disabled: false,
    }, 'Button'), root)

    const button = root.querySelector<HTMLButtonElement>('#target')!
    expect(button.className).toBe('second')
    expect(button.style.color).toBe('green')
    expect(button.style.backgroundColor).toBe('')
    expect(button.disabled).toBe(false)
    expect(button.hasAttribute('title')).toBe(false)
  })

  it('supports refs and clears them on unmount', async () => {
    const ref = { current: null as HTMLInputElement | null }
    let setVisible!: (visible: boolean) => boolean

    function App() {
      const [visible, updateVisible] = ui.useState(true)
      setVisible = updateVisible
      return ui.h('div', null, visible ? ui.h('input', { id: 'ref-input', ref }) : null)
    }

    ui.render(ui.h(App, null), root)
    expect(ref.current).toBe(root.querySelector('#ref-input'))

    setVisible(false)
    await flushMicrotasks()

    expect(ref.current).toBeNull()
  })

  it('supports useReducer, useMemo, useCallback, and useRef', async () => {
    const memoFactory = vi.fn((count: number) => count * 2)
    let dispatch!: (action: { type: 'inc' }) => void
    let force!: (value: number) => number
    let firstCallback: unknown

    function App() {
      const [count, send] = ui.useReducer((state: number, action: { type: 'inc' }) => {
        return action.type === 'inc' ? state + 1 : state
      }, 1)
      const [tick, setTick] = ui.useState(0)
      const ref = ui.useRef('stable')
      const doubled = ui.useMemo(() => memoFactory(count), [count])
      const callback = ui.useCallback(() => count, [count])
      dispatch = send
      force = setTick
      if (!firstCallback) firstCallback = callback
      return ui.h('output', { id: 'value', 'data-ref': ref.current, 'data-tick': tick }, String(doubled))
    }

    ui.render(ui.h(App, null), root)
    expect(root.querySelector('#value')?.textContent).toBe('2')
    expect(root.querySelector('#value')?.getAttribute('data-ref')).toBe('stable')
    expect(memoFactory).toHaveBeenCalledTimes(1)

    force(1)
    await flushMicrotasks()
    expect(memoFactory).toHaveBeenCalledTimes(1)

    dispatch({ type: 'inc' })
    await flushMicrotasks()
    expect(root.querySelector('#value')?.textContent).toBe('4')
    expect(memoFactory).toHaveBeenCalledTimes(2)
    expect(firstCallback).not.toBeUndefined()
  })

  it('reruns effects only when deps change and cleans previous effect first', async () => {
    const events: string[] = []
    let setValue!: (value: number) => number
    let setNoise!: (value: number) => number

    function App() {
      const [value, updateValue] = ui.useState(1)
      const [noise, updateNoise] = ui.useState(0)
      setValue = updateValue
      setNoise = updateNoise
      ui.useEffect(() => {
        events.push(`effect:${value}`)
        return () => events.push(`cleanup:${value}`)
      }, [value])
      return ui.h('span', { id: 'effect-value', 'data-noise': noise }, String(value))
    }

    ui.render(ui.h(App, null), root)
    await flushMicrotasks()
    expect(events).toEqual(['effect:1'])

    setNoise(1)
    await flushMicrotasks()
    expect(events).toEqual(['effect:1'])

    setValue(2)
    await flushMicrotasks()
    expect(events).toEqual(['effect:1', 'cleanup:1', 'effect:2'])
  })

  it('supports fragments and removes fragment children on unmount', async () => {
    let setVisible!: (visible: boolean) => boolean

    function Pair() {
      return ui.h(ui.Fragment, null, ui.h('span', { id: 'one' }, 'one'), ui.h('span', { id: 'two' }, 'two'))
    }

    function App() {
      const [visible, updateVisible] = ui.useState(true)
      setVisible = updateVisible
      return ui.h('div', null, visible ? ui.h(Pair, null) : null)
    }

    ui.render(ui.h(App, null), root)
    expect(root.querySelector('#one')).not.toBeNull()
    expect(root.querySelector('#two')).not.toBeNull()

    setVisible(false)
    await flushMicrotasks()

    expect(root.querySelector('#one')).toBeNull()
    expect(root.querySelector('#two')).toBeNull()
  })

  it('keeps textarea, select, and checkbox state stable across updates', async () => {
    function Form() {
      const [text, setText] = ui.useLocalState('textarea', '')
      const [choice, setChoice] = ui.useLocalState('choice', 'a')
      const [checked, setChecked] = ui.useLocalState('checked', false)
      return ui.h('form', null,
        ui.h('textarea', { id: 'textarea', value: text, onInput: (event: InputEvent) => setText((event.target as HTMLTextAreaElement).value) }),
        ui.h('select', { id: 'select', value: choice, onChange: (event: Event) => setChoice((event.target as HTMLSelectElement).value) },
          ui.h('option', { value: 'a' }, 'A'),
          ui.h('option', { value: 'b' }, 'B'),
        ),
        ui.h('input', { id: 'checkbox', type: 'checkbox', checked, onChange: (event: Event) => setChecked((event.target as HTMLInputElement).checked) }),
      )
    }

    ui.render(ui.h(Form, null), root)

    const textarea = root.querySelector<HTMLTextAreaElement>('#textarea')!
    textarea.focus()
    textarea.value = 'hello\nworld'
    textarea.setSelectionRange(5, 5)
    fireEvent.input(textarea)
    await flushMicrotasks()
    expect(root.querySelector('#textarea')).toBe(textarea)

    const select = root.querySelector<HTMLSelectElement>('#select')!
    select.value = 'b'
    fireEvent.change(select)
    await flushMicrotasks()

    const checkbox = root.querySelector<HTMLInputElement>('#checkbox')!
    checkbox.checked = true
    fireEvent.change(checkbox)
    await flushMicrotasks()

    expect(root.querySelector('#textarea')).toBe(textarea)
    expect(textarea.value).toBe('hello\nworld')
    expect(root.querySelector<HTMLSelectElement>('#select')!.value).toBe('b')
    expect(root.querySelector<HTMLInputElement>('#checkbox')!.checked).toBe(true)
  })

  it('preserves keyed node identity across randomized reorders', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.uniqueArray(fc.string({ minLength: 1, maxLength: 6 }).filter((value) => !value.includes('"') && !value.includes("'")), {
          minLength: 1,
          maxLength: 8,
        }),
        async (items) => {
          document.body.innerHTML = ''
          root = document.createElement('main')
          document.body.appendChild(root)

          let setItems!: (items: string[]) => string[]
          function List() {
            const [current, update] = ui.useState(items)
            setItems = update
            return ui.h('ol', null, current.map((item: string) => ui.h('li', { key: item, 'data-key': item }, item)))
          }

          ui.render(ui.h(List, null), root)
          const before = new Map(Array.from(root.querySelectorAll('li')).map((node) => [node.getAttribute('data-key'), node]))
          const reordered = [...items].reverse()
          setItems(reordered)
          await flushMicrotasks()

          expect(Array.from(root.querySelectorAll('li')).map((node) => node.getAttribute('data-key'))).toEqual(reordered)
          const after = new Map(Array.from(root.querySelectorAll('li')).map((node) => [node.getAttribute('data-key'), node]))
          for (const key of reordered) {
            expect(after.get(key)).toBe(before.get(key))
          }
        },
      ),
      { numRuns: 40 },
    )
  })

  it('supports randomized keyed insertions and removals', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.uniqueArray(fc.string({ minLength: 1, maxLength: 5 }).filter((value) => /^[a-z0-9_-]+$/i.test(value)), {
          minLength: 1,
          maxLength: 8,
        }),
        fc.uniqueArray(fc.string({ minLength: 1, maxLength: 5 }).filter((value) => /^[a-z0-9_-]+$/i.test(value)), {
          minLength: 0,
          maxLength: 8,
        }),
        async (initial, nextRaw) => {
          document.body.innerHTML = ''
          root = document.createElement('main')
          document.body.appendChild(root)

          const next = Array.from(new Set(nextRaw))
          let setItems!: (items: string[]) => string[]
          function List() {
            const [current, update] = ui.useState(initial)
            setItems = update
            return ui.h('ul', null, current.map((item: string) => ui.h('li', { key: item, 'data-key': item }, item)))
          }

          ui.render(ui.h(List, null), root)
          const before = new Map(Array.from(root.querySelectorAll('li')).map((node) => [node.getAttribute('data-key'), node]))

          setItems(next)
          await flushMicrotasks()

          expect(Array.from(root.querySelectorAll('li')).map((node) => node.getAttribute('data-key'))).toEqual(next)
          const after = new Map(Array.from(root.querySelectorAll('li')).map((node) => [node.getAttribute('data-key'), node]))
          for (const key of next) {
            if (before.has(key)) expect(after.get(key)).toBe(before.get(key))
          }
        },
      ),
      { numRuns: 60 },
    )
  })

  it('moves keyed components that render fragments as a range', async () => {
    let setItems!: (items: string[]) => string[]

    function Pair(props: { id: string }) {
      return ui.h(ui.Fragment, null,
        ui.h('span', { 'data-part': `${props.id}:a` }, `${props.id}:a`),
        ui.h('span', { 'data-part': `${props.id}:b` }, `${props.id}:b`),
      )
    }

    function List() {
      const [items, update] = ui.useState(['one', 'two'])
      setItems = update
      return ui.h('div', null, items.map((item: string) => ui.h(Pair, { key: item, id: item })))
    }

    ui.render(ui.h(List, null), root)
    const oneA = root.querySelector('[data-part="one:a"]')
    const oneB = root.querySelector('[data-part="one:b"]')

    setItems(['two', 'one'])
    await flushMicrotasks()

    expect(Array.from(root.querySelectorAll('span')).map((node) => node.textContent)).toEqual(['two:a', 'two:b', 'one:a', 'one:b'])
    expect(root.querySelector('[data-part="one:a"]')).toBe(oneA)
    expect(root.querySelector('[data-part="one:b"]')).toBe(oneB)
  })
})
