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
  new Function(runtimeSource).call(window)
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

  it('applies a controlled Select value after mounting its options', () => {
    ui.render(
      ui.h(ui.Select, {
        value: 'solo_stream',
        options: [
          { value: 'co_stream', label: 'Co-stream' },
          { value: 'solo_stream', label: 'Solo stream' },
        ],
      }),
      root,
    )

    expect(root.querySelector<HTMLSelectElement>('select')?.value).toBe('solo_stream')
  })

  it('forwards disabled state to hosted form controls', () => {
    ui.render(
      ui.h(
        'section',
        null,
        ui.h(ui.Input, { disabled: true, value: 'locked' }),
        ui.h(ui.Textarea, { disabled: true, value: 'locked' }),
        ui.h(ui.Select, {
          disabled: true,
          value: 'locked',
          options: [{ value: 'locked', label: 'Locked' }],
        }),
      ),
      root,
    )

    expect(root.querySelector<HTMLInputElement>('input')?.disabled).toBe(true)
    expect(root.querySelector<HTMLTextAreaElement>('textarea')?.disabled).toBe(true)
    expect(root.querySelector<HTMLSelectElement>('select')?.disabled).toBe(true)
  })

  it('renders high-value layout primitives with stable CSS variables', () => {
    ui.render(
      ui.h(
        ui.Container,
        { maxWidth: 720, padding: '8px' },
        ui.h(
          ui.Inline,
          { gap: 6, align: 'center', justify: 'space-between', wrap: false },
          ui.h('span', null, 'left'),
          ui.h('span', null, 'right'),
        ),
        ui.h(
          ui.Columns,
          { minWidth: 180, gap: '10px' },
          ui.h('span', null, 'a'),
          ui.h('span', null, 'b'),
        ),
        ui.h(
          ui.Split,
          { ratio: '2fr 1fr', gap: 14 },
          ui.h('span', null, 'input'),
          ui.h('span', null, 'output'),
        ),
        ui.h(ui.ScrollArea, { maxHeight: 120, axis: 'both', padding: 4 }, ui.h('span', null, 'scrollable')),
      ),
      root,
    )

    const container = root.querySelector<HTMLElement>('.neko-container')!
    const inline = root.querySelector<HTMLElement>('.neko-inline')!
    const columns = root.querySelector<HTMLElement>('.neko-columns')!
    const split = root.querySelector<HTMLElement>('.neko-split')!
    const scrollArea = root.querySelector<HTMLElement>('.neko-scroll-area')!

    expect(container.style.getPropertyValue('--container-max-width')).toBe('720px')
    expect(container.style.getPropertyValue('--container-padding')).toBe('8px')
    expect(inline.dataset.wrap).toBe('false')
    expect(inline.style.getPropertyValue('--inline-gap')).toBe('6px')
    expect(columns.classList.contains('is-fluid')).toBe(true)
    expect(columns.style.getPropertyValue('--columns-min')).toBe('180px')
    expect(split.dataset.direction).toBe('horizontal')
    expect(split.style.getPropertyValue('--split-template')).toBe('2fr 1fr')
    expect(scrollArea.dataset.axis).toBe('both')
    expect(scrollArea.style.getPropertyValue('--scroll-max-height')).toBe('120px')
  })

  it('supports controlled DOM helpers without exposing global DOM querying patterns', async () => {
    const scrollIntoView = vi.fn()
    const scrollTo = vi.fn()
    const writeText = vi.fn(async () => undefined)
    const previousScrollIntoView = Element.prototype.scrollIntoView
    const previousScrollTo = HTMLElement.prototype.scrollTo
    Element.prototype.scrollIntoView = scrollIntoView
    HTMLElement.prototype.scrollTo = scrollTo
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText, readText: vi.fn(async () => 'copied') },
    })

    function DomDemo() {
      const inputRef = ui.useRef(null)
      const targetRef = ui.useRef(null)
      const size = ui.useElementSize(inputRef)
      const scrollTarget = ui.useScrollIntoView(targetRef)
      const clipboard = ui.useClipboard()

      ui.useEffect(() => {
        if (inputRef.current) inputRef.current.setAttribute('data-ref-ready', 'yes')
      }, [])

      return ui.h('div', null,
        ui.h(ui.Input, { ref: inputRef, value: 'hello' }),
        ui.h('span', { id: 'size' }, `${size.width}x${size.height}`),
        ui.h('div', { ref: targetRef, id: 'target' }, 'target'),
        ui.h('button', { id: 'jump', onClick: () => scrollTarget({ block: 'end' }) }, 'jump'),
        ui.h('button', { id: 'copy', onClick: () => clipboard.write('copy me') }, 'copy'),
        ui.h(ui.ScrollArea, { maxHeight: 40, autoScroll: true }, ui.h('div', null, 'scroll body')),
      )
    }

    ui.render(ui.h(DomDemo, null), root)
    await flushMicrotasks()

    const input = root.querySelector<HTMLInputElement>('.neko-input')!
    const scrollArea = root.querySelector<HTMLElement>('.neko-scroll-area')!
    Object.defineProperty(scrollArea, 'scrollHeight', { configurable: true, value: 300 })

    expect(input.getAttribute('data-ref-ready')).toBe('yes')
    expect(root.querySelector('#size')?.textContent).toMatch(/^\d+x\d+$/)
    fireEvent.click(root.querySelector<HTMLButtonElement>('#jump')!)
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'end' })
    fireEvent.click(root.querySelector<HTMLButtonElement>('#copy')!)
    await flushMicrotasks()
    expect(writeText).toHaveBeenCalledWith('copy me')
    expect(scrollTo).toHaveBeenCalled()

    Element.prototype.scrollIntoView = previousScrollIntoView
    HTMLElement.prototype.scrollTo = previousScrollTo
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

  it('falls back from regional Chinese locales to zh-CN only for Chinese locales', () => {
    window.__NEKO_PAYLOAD = {
      locale: 'zh-TW',
      i18n: {
        default_locale: 'en',
        messages: {
          'zh-CN': { greeting: '你好 {name}' },
          en: {},
        },
      },
    }
    expect(ui.t('greeting', { name: 'Neko' })).toBe('你好 Neko')

    window.__NEKO_PAYLOAD.locale = 'ja'
    expect(ui.t('greeting', { name: 'Neko', defaultValue: 'Hello {name}' })).toBe('Hello Neko')
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

  it('loads async data and reloads with useAsync', async () => {
    let resolveLoad!: (value: string) => void
    let reload!: () => void
    const loader = vi.fn(() => new Promise<string>((resolve) => { resolveLoad = resolve }))

    function App() {
      const state = ui.useAsync(loader, [])
      reload = state.reload
      if (state.loading) return ui.h('span', { id: 'status' }, 'loading')
      if (state.error) return ui.h('span', { id: 'status' }, 'error')
      return ui.h('button', { id: 'status', onClick: reload }, state.data)
    }

    ui.render(ui.h(App, null), root)
    expect(root.querySelector('#status')?.textContent).toBe('loading')
    await flushMicrotasks()
    resolveLoad('ready')
    await flushMicrotasks()
    expect(root.querySelector('#status')?.textContent).toBe('ready')

    fireEvent.click(root.querySelector('#status')!)
    await flushMicrotasks()
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('renders AsyncBlock fallback, data, and error state', async () => {
    let resolveLoad!: (value: string) => void
    let shouldFail = false
    const load = vi.fn(() => new Promise<string>((resolve, reject) => {
      resolveLoad = resolve
      if (shouldFail) reject(new Error('failed'))
    }))

    function App() {
      return ui.h(ui.AsyncBlock, {
        load,
        fallback: ui.h('span', { id: 'fallback' }, 'loading'),
        error: (error: Error) => ui.h('span', { id: 'error' }, error.message),
      }, (data: string) => ui.h('span', { id: 'data' }, data))
    }

    ui.render(ui.h(App, null), root)
    expect(root.querySelector('#fallback')?.textContent).toBe('loading')
    await flushMicrotasks()
    resolveLoad('loaded')
    await flushMicrotasks()
    await flushMicrotasks()
    expect(root.querySelector('#data')?.textContent).toBe('loaded')

    shouldFail = true
    ui.render(ui.h(App, { key: 'fail' }), root)
    await flushMicrotasks()
    await flushMicrotasks()
    await flushMicrotasks()
    expect(root.querySelector('#error')?.textContent).toBe('failed')
  })

  it('preserves hosted bridge error metadata on api.call failures', async () => {
    let requestMessage: any
    Object.defineProperty(window, 'parent', {
      value: {
        postMessage(message: any) {
          requestMessage = message
        },
      },
      configurable: true,
    })

    const promise = ui.api.call('game_agent_status')
    expect(requestMessage?.type).toBe('neko-hosted-surface-request')

    window.dispatchEvent(new MessageEvent('message', {
      data: {
        type: 'neko-hosted-surface-response',
        requestId: requestMessage.requestId,
        ok: false,
        error: '插件未运行',
        code: 'PLUGIN_NOT_RUNNING',
        details: { plugin_id: 'game_agent_minecraft' },
        status: 409,
      },
    }))

    await expect(promise).rejects.toMatchObject({
      message: '插件未运行',
      code: 'PLUGIN_NOT_RUNNING',
      details: { plugin_id: 'game_agent_minecraft' },
      status: 409,
    })
  })

  it('honors per-call hosted bridge timeouts', async () => {
    vi.useFakeTimers()
    try {
      let requestMessage: any
      Object.defineProperty(window, 'parent', {
        value: {
          postMessage(message: any) {
            requestMessage = message
          },
        },
        configurable: true,
      })

      const promise = ui.api.call('study_export_notes', {}, { timeoutMs: 80000 })
      const settled = vi.fn()
      promise.then(settled, settled)
      expect(requestMessage?.type).toBe('neko-hosted-surface-request')
      expect(requestMessage?.timeoutMs).toBe(80000)

      await vi.advanceTimersByTimeAsync(30000)
      await flushMicrotasks()
      expect(settled).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(50000)
      await expect(promise).rejects.toThrow('Hosted surface request timed out')
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows toast notifications and removes them', () => {
    vi.useFakeTimers()
    const remove = ui.showToast ? ui.showToast('Saved', { tone: 'success', timeout: 100 }) : ui.useToast().success('Saved', { timeout: 100 })
    const toast = document.querySelector('.neko-toast')!
    expect(toast.textContent).toBe('Saved')
    expect(toast.getAttribute('data-tone')).toBe('success')
    vi.advanceTimersByTime(100)
    expect(document.querySelector('.neko-toast')).toBeNull()
    remove()
    vi.useRealTimers()
  })

  it('tracks toast promise states', async () => {
    vi.useFakeTimers()
    const promise = Promise.resolve('done')
    const tracked = ui.showToast.promise(promise, {
      loading: 'Saving',
      success: (value: string) => `Saved ${value}`,
    })

    expect(document.querySelector('.neko-toast')?.textContent).toBe('Saving')
    await expect(tracked).resolves.toBe('done')
    await flushMicrotasks()
    expect(Array.from(document.querySelectorAll('.neko-toast')).map((item) => item.textContent)).toContain('Saved done')
    vi.useRealTimers()
  })

  it('confirms through useConfirm', async () => {
    let confirm!: (options: any) => Promise<boolean>
    let setCount!: (value: number) => number

    function App() {
      confirm = ui.useConfirm()
      const [count, updateCount] = ui.useState(0)
      setCount = updateCount
      return ui.h('button', { id: 'open', onClick: () => confirm({ title: 'Delete', message: 'Really?', tone: 'danger' }) }, String(count))
    }

    ui.render(ui.h(App, null), root)
    const promise = confirm({ title: 'Delete', message: 'Really?', tone: 'danger' })
    expect(document.querySelector('.neko-modal')?.textContent).toContain('Really?')
    fireEvent.click(Array.from(document.querySelectorAll('button')).find((button) => button.textContent === 'Confirm')!)
    await expect(promise).resolves.toBe(true)
    setCount(1)
    await flushMicrotasks()
    expect(root.querySelector('#open')?.textContent).toBe('1')
  })

  it('manages form helpers, validation, and debounced state', async () => {
    vi.useFakeTimers()
    let formApi: any
    const submit = vi.fn()
    const invalid = vi.fn()
    let setSearch!: (value: string) => string

    function App() {
      formApi = ui.useForm({ name: '', enabled: false }, {
        validate: (values: any) => values.name ? {} : { name: 'Name required' },
      })
      const [search, updateSearch, debounced] = ui.useDebouncedState('', 50)
      setSearch = updateSearch
      return ui.h(ui.Form, { onSubmit: formApi.handleSubmit(submit, invalid) },
        ui.h(ui.FormSection, { title: 'Profile', description: 'Demo form section' },
          ui.h('input', { id: 'name', ...formApi.field('name') }),
          ui.h('input', { id: 'enabled', type: 'checkbox', ...formApi.checkbox('enabled') }),
        ),
        ui.h('output', { id: 'search', 'data-value': search }, debounced),
        ui.h(ui.FormActions, null, ui.h('button', { id: 'submit', type: 'submit' }, 'Save')),
      )
    }

    ui.render(ui.h(App, null), root)
    fireEvent.submit(root.querySelector('form')!)
    await flushMicrotasks()
    expect(invalid).toHaveBeenCalledWith({ name: 'Name required' }, { name: '', enabled: false }, expect.anything())
    expect(root.querySelector('.neko-form-section-title')?.textContent).toBe('Profile')

    formApi.setField('name', 'Neko')
    formApi.setField('enabled', true)
    setSearch('abc')
    await flushMicrotasks()

    expect(root.querySelector<HTMLInputElement>('#name')!.value).toBe('Neko')
    expect(root.querySelector<HTMLInputElement>('#enabled')!.checked).toBe(true)
    expect(formApi.dirty).toBe(true)
    expect(formApi.touched.name).toBe(true)
    expect(root.querySelector('#search')?.textContent).toBe('')

    fireEvent.submit(root.querySelector('form')!)
    await flushMicrotasks()
    expect(submit).toHaveBeenCalledWith({ name: 'Neko', enabled: true }, expect.anything())

    vi.advanceTimersByTime(50)
    await flushMicrotasks()
    expect(root.querySelector('#search')?.textContent).toBe('abc')
    vi.useRealTimers()
  })

  it('keeps value patch away during IME composition', async () => {
    let force!: (value: string) => string

    function App() {
      const [value, setValue] = ui.useState('初')
      force = setValue
      return ui.h('input', { id: 'ime', value, onChange: setValue })
    }

    ui.render(ui.h(App, null), root)
    const input = root.querySelector<HTMLInputElement>('#ime')!
    input.focus()
    fireEvent.compositionStart(input)
    input.value = '初稿'
    force('other')
    await flushMicrotasks()

    expect(input.value).toBe('初稿')
    fireEvent.compositionEnd(input)
    await flushMicrotasks()
    expect(input.value).toBe('初稿')
  })

  it('blocks dangerous html and javascript URLs', () => {
    ui.render(ui.h('a', {
      id: 'link',
      href: 'javascript:alert(1)',
      dangerouslySetInnerHTML: { __html: '<strong>bad</strong>' },
    }, 'safe'), root)

    const link = root.querySelector<HTMLAnchorElement>('#link')!
    expect(link.getAttribute('href')).toBeNull()
    expect(link.innerHTML).toBe('safe')
  })

  it('catches child render errors with ErrorBoundary', async () => {
    function Broken() {
      throw new Error('broken')
    }

    function App() {
      return ui.h(ui.ErrorBoundary, {
        fallback: (error: Error) => ui.h('span', { id: 'fallback' }, error.message),
      }, ui.h(Broken, null))
    }

    ui.render(ui.h(App, null), root)
    await flushMicrotasks()
    expect(root.querySelector('#fallback')?.textContent).toBe('broken')
  })

  it('enhances modal focus, size, scroll lock, and Escape close', async () => {
    let setOpen!: (value: boolean) => boolean
    const previousOverflow = document.body.style.overflow

    function App() {
      const [open, updateOpen] = ui.useState(true)
      setOpen = updateOpen
      return ui.h(ui.Modal, {
        open,
        title: 'Dialog',
        size: 'lg',
        onClose: () => setOpen(false),
        footer: ui.h('button', { id: 'close' }, 'Close'),
      }, ui.h('button', { id: 'first' }, 'First'))
    }

    ui.render(ui.h(App, null), root)
    await flushMicrotasks()
    const modal = document.querySelector<HTMLElement>('.neko-modal')!
    expect(modal).not.toBeNull()
    expect(modal.dataset.size).toBe('lg')
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.keyDown(window, { key: 'Escape' })
    await flushMicrotasks()
    expect(document.querySelector('.neko-modal')).toBeNull()
    expect(document.body.style.overflow).toBe(previousOverflow)
  })

  it('renders tooltip content for hover and focus help', () => {
    ui.render(ui.h(ui.Tooltip, { content: 'Helpful context', placement: 'right' }, ui.h('button', null, 'Info')), root)

    const tooltip = root.querySelector<HTMLElement>('.neko-tooltip')!
    expect(tooltip.dataset.placement).toBe('right')
    expect(tooltip.querySelector('.neko-tooltip-content')?.textContent).toBe('Helpful context')
  })

  it('supports Gradio-parity controls', async () => {
    let selectedLayer: any = null

    function App() {
      const [parts, setParts] = ui.useState(['Hair'])
      const [method, setMethod] = ui.useState('anime_face')
      const [feather, setFeather] = ui.useState(2)
      const [image, setImage] = ui.useState({ type: 'image', dataUrl: 'data:image/png;base64,ZmFrZQ==', label: 'Preview' })
      return ui.h('section', null,
        ui.h(ui.CheckboxGroup, {
          value: parts,
          options: [{ value: 'Hair', label: 'Hair' }, { value: 'Body', label: 'Body' }],
          onChange: setParts,
        }),
        ui.h(ui.RadioGroup, {
          value: method,
          options: [{ value: 'anime_face', label: 'AnimeFace' }, { value: 'color', label: 'Color' }],
          onChange: setMethod,
        }),
        ui.h(ui.SegmentedControl, {
          value: method,
          options: ['anime_face', 'color'],
          onChange: setMethod,
        }),
        ui.h(ui.Slider, { value: feather, min: 0, max: 8, onChange: setFeather }),
        ui.h(ui.PasswordInput, { value: 'secret', onChange: () => undefined }),
        ui.h(ui.ImageUpload, { value: image, onChange: setImage }),
        ui.h(ui.ImagePreview, { src: image, label: 'Preview' }),
        ui.h(ui.Gallery, {
          items: [{ name: 'Layer', preview_data_url: image }],
          onSelect: (item: any) => { selectedLayer = item },
        }),
        ui.h('output', { id: 'state', 'data-parts': parts.join(','), 'data-method': method, 'data-feather': feather }, image.dataUrl),
      )
    }

    ui.render(ui.h(App, null), root)

    fireEvent.click(Array.from(root.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).find((input) => input.value === 'Body')!)
    await flushMicrotasks()
    expect(root.querySelector('#state')?.getAttribute('data-parts')).toBe('Hair,Body')

    fireEvent.click(Array.from(root.querySelectorAll<HTMLInputElement>('input[type="radio"]')).find((input) => input.value === 'color')!)
    await flushMicrotasks()
    expect(root.querySelector('#state')?.getAttribute('data-method')).toBe('color')

    const slider = root.querySelector<HTMLInputElement>('input[type="range"]')!
    slider.value = '5'
    fireEvent.input(slider)
    await flushMicrotasks()
    expect(root.querySelector('#state')?.getAttribute('data-feather')).toBe('5')

    expect(root.querySelector('.neko-image-preview img')?.getAttribute('src')).toContain('data:image/png;base64')
    fireEvent.click(root.querySelector('.neko-gallery-item')!)
    expect(selectedLayer?.name).toBe('Layer')
  })

  it('routes hosted downloads through the parent window', () => {
    const messages: any[] = []
    Object.defineProperty(window, 'parent', {
      value: {
        postMessage(message: any) {
          messages.push(message)
        },
      },
      configurable: true,
    })
    window.__NEKO_PAYLOAD.host = { origin: 'http://127.0.0.1:48911' }

    ui.render(ui.h(ui.FileDownload, { href: '/plugin/demo/hosted-ui/artifact?path=x.zip', label: 'Download' }), root)
    fireEvent.click(root.querySelector('button')!)

    expect(messages).toEqual([{
      type: 'neko-hosted-surface-open-external',
      payload: { url: 'http://127.0.0.1:48911/plugin/demo/hosted-ui/artifact?path=x.zip' },
    }])
  })

  it('routes local hosted download paths through the parent window', () => {
    const messages: any[] = []
    Object.defineProperty(window, 'parent', {
      value: {
        postMessage(message: any) {
          messages.push(message)
        },
      },
      configurable: true,
    })
    window.__NEKO_PAYLOAD.host = { origin: 'http://127.0.0.1:48911' }

    ui.render(ui.h(ui.FileDownload, { path: '/tmp/neko/package', label: 'Open folder' }), root)
    fireEvent.click(root.querySelector('button')!)

    expect(messages).toEqual([{
      type: 'neko-hosted-surface-open-path',
      payload: { path: '/tmp/neko/package' },
    }])
  })

  it('normalizes artifact-like values into type and view', () => {
    expect(ui.normalizeArtifact({ type: 'table', rows: [{ a: 1 }] })).toMatchObject({
      type: 'json',
      view: 'table',
      data: [{ a: 1 }],
    })
    expect(ui.normalizeArtifact({ type: 'folder', path: '/tmp/out' })).toMatchObject({
      type: 'file',
      isDirectory: true,
      path: '/tmp/out',
    })
    expect(ui.normalizeArtifact({ markdown: '# Report' })).toMatchObject({
      type: 'text',
      view: 'markdown',
      text: '# Report',
    })
    expect(ui.normalizeArtifact({ mime: 'audio/webm', dataUrl: 'data:audio/webm;base64,ZmFrZQ==' })).toMatchObject({
      type: 'audio',
      dataUrl: 'data:audio/webm;base64,ZmFrZQ==',
    })
  })

  it('automatically renders mixed artifacts', () => {
    ui.render(ui.h(ui.ArtifactList, {
      items: [
        { type: 'image', dataUrl: 'data:image/png;base64,ZmFrZQ==', label: 'Image' },
        { type: 'audio', dataUrl: 'data:audio/webm;base64,ZmFrZQ==', label: 'Audio' },
        { type: 'video', dataUrl: 'data:video/webm;base64,ZmFrZQ==', label: 'Video' },
        { type: 'text', view: 'log', text: 'line 1\nline 2', label: 'Log' },
        { type: 'json', view: 'table', data: [{ name: 'Neko', score: 9 }], label: 'Rows' },
        { type: 'file', path: '/tmp/out.zip', label: 'Package' },
      ],
    }), root)

    expect(root.querySelectorAll('.neko-artifact-card')).toHaveLength(6)
    expect(root.querySelector('.neko-image-preview img')?.getAttribute('src')).toContain('data:image/png')
    expect(root.querySelector('audio')?.getAttribute('src')).toContain('data:audio')
    expect(root.querySelector('video')?.getAttribute('src')).toContain('data:video')
    expect(root.querySelector('.neko-log-viewer')?.textContent).toContain('line 2')
    expect(root.querySelector('.neko-table')?.textContent).toContain('Neko')
    expect(root.querySelector('.neko-download')?.textContent).toContain('Package')
  })

  it('emits artifact-like objects from audio and video uploads', async () => {
    const OriginalFileReader = window.FileReader
    class MockFileReader {
      result = ''
      onload: null | (() => void) = null
      onerror: null | (() => void) = null
      error: Error | null = null
      readAsDataURL(file: File) {
        this.result = `data:${file.type};base64,ZmFrZQ==`
        queueMicrotask(() => this.onload && this.onload())
      }
    }
    Object.defineProperty(window, 'FileReader', { value: MockFileReader, configurable: true })

    const audioChange = vi.fn()
    const videoChange = vi.fn()
    ui.render(ui.h('section', null,
      ui.h(ui.AudioUpload, { onChange: audioChange }),
      ui.h(ui.VideoUpload, { onChange: videoChange }),
    ), root)

    const [audioInput, videoInput] = Array.from(root.querySelectorAll<HTMLInputElement>('input[type="file"]')) as [HTMLInputElement, HTMLInputElement]
    fireEvent.change(audioInput, { target: { files: [new File(['audio'], 'voice.webm', { type: 'audio/webm' })] } })
    fireEvent.change(videoInput, { target: { files: [new File(['video'], 'clip.webm', { type: 'video/webm' })] } })
    await flushMicrotasks()

    expect(audioChange).toHaveBeenCalledWith(expect.objectContaining({
      type: 'audio',
      dataUrl: 'data:audio/webm;base64,ZmFrZQ==',
      name: 'voice.webm',
      mime: 'audio/webm',
      size: 5,
    }))
    expect(videoChange).toHaveBeenCalledWith(expect.objectContaining({
      type: 'video',
      dataUrl: 'data:video/webm;base64,ZmFrZQ==',
      name: 'clip.webm',
      mime: 'video/webm',
      size: 5,
    }))

    Object.defineProperty(window, 'FileReader', { value: OriginalFileReader, configurable: true })
  })
})
