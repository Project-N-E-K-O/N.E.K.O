// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import ElementPlus from 'element-plus'
import ConfigValueEditor from './ConfigValueEditor.vue'
import type { ConfigEditorSchema } from '@/types/configSchema'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    mergeLocaleMessage: vi.fn(),
    te: () => true,
    getLocaleMessage: () => ({}),
    locale: ref('en-US'),
    t: (key: string) => key,
  }),
}))

const mounted: Array<{ unmount: () => void; host: HTMLElement }> = []

afterEach(() => {
  while (mounted.length) {
    const item = mounted.pop()
    item?.unmount()
    item?.host.remove()
  }
})

/**
 * 挂载编辑器根节点。`modelValue` 是 profile overlay，`baselineValue` 是
 * 「清单默认值 + 运行时配置」的合并基线。emitted 收集写回 overlay 的结果。
 */
function mountEditor(
  modelValue: any,
  baselineValue: any,
  compact = false,
  schema?: ConfigEditorSchema
) {
  const emitted: any[] = []
  const host = document.createElement('div')
  document.body.appendChild(host)
  const Wrapper = defineComponent(
    () => () =>
      h(ConfigValueEditor as any, {
        modelValue,
        baselineValue,
        compact,
        schema,
        path: '',
        'onUpdate:modelValue': (v: any) => emitted.push(v),
      })
  )
  const app = createApp(Wrapper)
  app.use(ElementPlus)
  app.mount(host)
  mounted.push({ unmount: () => app.unmount(), host })
  return { host, emitted }
}

function mountSchemaEditor(modelValue: any, baselineValue: any, schema?: ConfigEditorSchema) {
  return mountEditor(modelValue, baselineValue, false, schema)
}

function lastEmit(emitted: any[]) {
  expect(emitted.length).toBeGreaterThan(0)
  return emitted[emitted.length - 1]
}

function typeInto(input: HTMLInputElement, value: string) {
  input.value = value
  input.dispatchEvent(new Event('input'))
  input.dispatchEvent(new Event('change'))
}

function rowFor(host: HTMLElement, key: string): HTMLElement {
  const rows = Array.from(host.querySelectorAll('.row')) as HTMLElement[]
  // Rows are identified by their raw key: the key tag, or the compact label (which shows
  // a schema title, with the key beside it only when the two differ).
  const keyOf = (r: HTMLElement) =>
    (
      r.querySelector('.k .el-tag') ??
      r.querySelector('.k .field-key') ??
      r.querySelector('.k label')
    )?.textContent?.trim()
  const row = rows.find((r) => keyOf(r) === key)
  if (!row) throw new Error(`row for key "${key}" not found`)
  return row
}

function opsButtons(row: HTMLElement): string[] {
  return Array.from(row.querySelectorAll(':scope > .ops button')).map((b) =>
    (b.textContent || '').trim()
  )
}

describe('literal configuration keys', () => {
  it('edits existing dotted and reserved keys as own properties', async () => {
    // A persisted profile may contain quoted TOML spellings that the Add-field
    // dialog refuses to create; they still have to be editable.
    const baseline = JSON.parse('{"http.timeout":1,"__proto__":2}')
    const { host, emitted } = mountEditor({}, baseline, true)
    await nextTick()

    const dotted = host.querySelector<HTMLInputElement>('input[aria-label="http.timeout"]')!
    dotted.value = '7'
    dotted.dispatchEvent(new Event('input'))
    await nextTick()
    expect(lastEmit(emitted)['http.timeout']).toBe(7)

    const reserved = host.querySelector<HTMLInputElement>('input[aria-label="__proto__"]')!
    reserved.value = '9'
    reserved.dispatchEvent(new Event('input'))
    await nextTick()

    const written = lastEmit(emitted)
    expect(Object.prototype.hasOwnProperty.call(written, '__proto__')).toBe(true)
    expect(written['__proto__']).toBe(9)
    expect(Object.getPrototypeOf(written)).toBe(Object.prototype)
  })

  describe('__replace__ marker', () => {
    // The server replaces a nested table carrying the marker outright, so its base-only
    // fields are not in effect. On a top-level section or the root it is ordinary data.
    // One mount per case keeps each within the default timeout on slow runners.
    const baseline = { net: { cache: { size: 1, ttl: 2 } }, top: 1 }
    const rowPaths = async (overlay: any) => {
      const { host } = mountEditor(overlay, baseline, true)
      await nextTick()
      return Array.from(host.querySelectorAll('[data-config-path]')).map((r) =>
        r.getAttribute('data-config-path')
      )
    }

    it('hides base-only fields under a nested marked table', async () => {
      const paths = await rowPaths({ net: { cache: { __replace__: true, size: 5 } } })
      expect(paths).toContain('net.cache.size')
      expect(paths).not.toContain('net.cache.ttl')
    })

    it('keeps inherited fields under an unmarked nested table', async () => {
      expect(await rowPaths({ net: { cache: { size: 5 } } })).toContain('net.cache.ttl')
    })

    it('treats the marker on a top-level section as data', async () => {
      expect(await rowPaths({ net: { __replace__: true } })).toContain('net.cache.ttl')
    })

    it('hides base fields under a nested explicitly empty table', async () => {
      // deep_merge replaces a nested table with an explicit {} as well.
      expect(await rowPaths({ net: { cache: {} } })).not.toContain('net.cache.ttl')
    })

    const addField = async (overlay: any, name: string, table = 'net.cache') => {
      const { host, emitted } = mountEditor(overlay, baseline, true)
      await nextTick()
      const editor = host.querySelector(`[data-config-path="${table}"] .cve`)!
      // Work only in the dialog this call opens: a rejected name leaves a dialog open,
      // and a global query could then drive another editor's.
      const opened = document.querySelectorAll('.el-dialog').length
      editor.querySelector<HTMLButtonElement>(':scope > .obj > .add button')!.click()
      await vi.waitFor(() =>
        expect(document.querySelectorAll('.el-dialog').length).toBeGreaterThan(opened)
      )
      const dialog = [...document.querySelectorAll<HTMLElement>('.el-dialog')].at(-1)!
      const input = dialog.querySelector<HTMLInputElement>('input')!
      input.value = name
      input.dispatchEvent(new Event('input'))
      await nextTick()
      const confirm = [...dialog.querySelectorAll<HTMLButtonElement>('button')].find(
        (b) => b.textContent?.trim() === 'common.confirm'
      )!
      confirm.click()
      await nextTick()
      return emitted
    }

    it('keeps an explicitly empty nested table replaced when adding its first field', async () => {
      // Without the marker the new field would turn the table back into a merge and
      // bring the base `ttl` back into effect.
      expect(lastEmit(await addField({ net: { cache: {} } }, 'extra'))).toEqual({
        net: { cache: { __replace__: true, extra: '' } },
      })
    })

    it('refuses a new __replace__ field in a nested table', async () => {
      // There it is the merge marker and would replace the table instead of holding a value.
      expect(await addField({ net: { cache: {} } }, '__replace__')).toEqual([])
    })

    it('accepts a new __replace__ field in a top-level section', async () => {
      // A top-level section merges key by key, so the name is ordinary data there.
      expect(lastEmit(await addField({ net: {} }, '__replace__', 'net'))).toEqual({
        net: { __replace__: '' },
      })
    })

    it('adds no marker to a nested table that merges with its base', async () => {
      expect(lastEmit(await addField({ net: { cache: { size: 5 } } }, 'extra'))).toEqual({
        net: { cache: { size: 5, extra: '' } },
      })
    })

    it('treats the marker at the root as data', async () => {
      expect(await rowPaths({ __replace__: true })).toContain('top')
    })
  })

  it('offers delete beside restore for base-named fields of a replacement table', async () => {
    // Omitting a key is how a replacement table leaves a field out; restoring writes
    // the base value back instead. Both have to be reachable.
    const baseline = { net: { cache: { size: 1, ttl: 2 } } }
    const { host, emitted } = mountEditor(
      { net: { cache: { __replace__: true, size: 5 } } },
      baseline,
      true
    )
    await nextTick()

    const row = host.querySelector<HTMLElement>('[data-config-path="net.cache.size"]')!
    const buttons = Array.from(row.querySelectorAll('.field-actions > button'))
    const labels = buttons.map((b) => (b.textContent || '').trim())
    expect(labels).toContain('plugins.configUi.restoreInheritance')
    expect(labels).toContain('common.delete')
    ;(buttons[labels.indexOf('common.delete')] as HTMLButtonElement).click()
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ net: { cache: { __replace__: true } } })
  })

  it('keeps a quoted top-level "plugin.id" key editable', async () => {
    // It flattens to the same display path as [plugin].id, but only the top-level
    // plugin table is protected, and that table is not rendered at the root at all.
    const baseline = JSON.parse('{"plugin.id":"literal","plugin":{"id":"demo"}}')
    const { host, emitted } = mountEditor({}, baseline, true)
    await nextTick()

    const fieldInput = (segments: string[]) =>
      host.querySelector<HTMLInputElement>(
        `[id="${'config-field-' + encodeURIComponent(JSON.stringify(segments))}"]`
      )
    expect(fieldInput(['plugin', 'id'])).toBeNull()

    const literal = fieldInput(['plugin.id'])!
    expect(literal.disabled).toBe(false)
    typeInto(literal, 'changed')
    await nextTick()
    expect(lastEmit(emitted)['plugin.id']).toBe('changed')
    expect(rowFor(host, 'plugin.id').querySelector('.field-actions')).not.toBeNull()
  })
})

describe('compact numeric field', () => {
  it('accepts negative and decimal numbers typed one keystroke at a time', async () => {
    const emitted: any[] = []
    const host = document.createElement('div')
    document.body.appendChild(host)
    const baseline = { temp: 1 }
    const Wrapper = defineComponent(() => {
      const model = ref<any>({ temp: 1 })
      return () =>
        h(ConfigValueEditor as any, {
          modelValue: model.value,
          baselineValue: baseline,
          compact: true,
          path: '',
          'onUpdate:modelValue': (v: any) => {
            model.value = v
            emitted.push(v)
          },
        })
    })
    const app = createApp(Wrapper)
    app.use(ElementPlus)
    app.mount(host)
    mounted.push({ unmount: () => app.unmount(), host })
    await nextTick()

    const input = host.querySelector<HTMLInputElement>('input[aria-label="temp"]')!
    const type = async (char: string, text: string) => {
      input.value = text + char
      input.dispatchEvent(new Event('input'))
      await nextTick()
      return input.value
    }

    // Clearing then typing "-" must keep the minus sign: a native number control
    // reports it as an empty value and the field would drop it.
    input.value = ''
    input.dispatchEvent(new Event('input'))
    await nextTick()
    expect(await type('-', '')).toBe('-')
    expect(await type('5', '-')).toBe('-5')
    expect(lastEmit(emitted)).toEqual({ temp: -5 })

    // Decimals and exponents stay editable while incomplete.
    expect(await type('0', '-5')).toBe('-50')
    input.value = ''
    input.dispatchEvent(new Event('input'))
    for (const [char, text] of [
      ['1', ''],
      ['.', '1'],
      ['5', '1.'],
    ] as const) {
      expect(await type(char, text)).toBe(text + char)
    }
    expect(lastEmit(emitted)).toEqual({ temp: 1.5 })
  })

  it('keeps a typed spelling whose value prints differently', async () => {
    // "-0" and "1e2" parse to numbers that print as "0" and "100". The field's own
    // update must not rewrite the text, or the next keystroke builds the wrong number.
    const emitted: any[] = []
    const host = document.createElement('div')
    document.body.appendChild(host)
    const baseline = { temp: 7 }
    const Wrapper = defineComponent(() => {
      const model = ref<any>({ temp: 7 })
      return () =>
        h(ConfigValueEditor as any, {
          modelValue: model.value,
          baselineValue: baseline,
          compact: true,
          path: '',
          'onUpdate:modelValue': (v: any) => {
            model.value = v
            emitted.push(v)
          },
        })
    })
    const app = createApp(Wrapper)
    app.use(ElementPlus)
    app.mount(host)
    mounted.push({ unmount: () => app.unmount(), host })
    await nextTick()

    const input = host.querySelector<HTMLInputElement>('input[aria-label="temp"]')!
    const typeAll = async (text: string) => {
      input.value = ''
      input.dispatchEvent(new Event('input'))
      for (let i = 1; i <= text.length; i++) {
        input.value = input.value + text[i - 1]
        input.dispatchEvent(new Event('input'))
        await nextTick()
        expect(input.value).toBe(text.slice(0, i))
      }
    }

    await typeAll('-0.5')
    expect(lastEmit(emitted)).toEqual({ temp: -0.5 })
    await typeAll('1e20')
    expect(lastEmit(emitted)).toEqual({ temp: 1e20 })

    // Blur still normalises the spelling.
    input.dispatchEvent(new FocusEvent('blur'))
    await nextTick()
    expect(input.value).toBe(String(1e20))
  })
})

describe('ConfigValueEditor — profile overlay 保持稀疏', () => {
  it('编辑「基线独有段」里的一个叶子时，只写回被改的键（不固化整段默认值）', async () => {
    const baseline = { llm: { model: 'gpt-a', temperature: 1, top_p: 0.9 } }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const input = rowFor(host, 'model').querySelector('input') as HTMLInputElement
    typeInto(input, 'gpt-b')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ llm: { model: 'gpt-b' } })
  })

  it('该段已有稀疏覆盖时，编辑不会把清单新增的默认值一起拖进 profile', async () => {
    // 模拟插件升级：清单新增了 top_p，profile 里早就覆盖过 model
    const baseline = { llm: { model: 'gpt-a', temperature: 1, top_p: 0.9 } }
    const { host, emitted } = mountEditor({ llm: { model: 'mine' } }, baseline)
    await nextTick()

    const input = rowFor(host, 'model').querySelector('input') as HTMLInputElement
    typeInto(input, 'mine2')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ llm: { model: 'mine2' } })
  })

  it('编辑顶层继承叶子时也只写回该键', async () => {
    const baseline = { alpha: 'a', beta: 'b' }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const input = rowFor(host, 'alpha').querySelector('input') as HTMLInputElement
    typeInto(input, 'changed')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ alpha: 'changed' })
  })

  it('继承中的键渲染基线值，但不提供任何写入按钮', async () => {
    const baseline = { llm: { model: 'gpt-a' } }
    const { host } = mountEditor({}, baseline)
    await nextTick()

    const input = rowFor(host, 'model').querySelector('input') as HTMLInputElement
    expect(input.value).toBe('gpt-a')
    expect(opsButtons(rowFor(host, 'llm'))).toEqual([])
  })

  it('「重置」把被覆盖的键移出 overlay，而不是把基线值写进去', async () => {
    const baseline = { alpha: 'default', beta: 'b' }
    const { host, emitted } = mountEditor({ alpha: 'overridden' }, baseline)
    await nextTick()

    const row = rowFor(host, 'alpha')
    expect(opsButtons(row)).toEqual(['common.reset'])
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({})
  })

  it('基线里没有的自定义键提供「删除」', async () => {
    const baseline = { alpha: 'default' }
    const { host, emitted } = mountEditor({ custom: 'x' }, baseline)
    await nextTick()

    const row = rowFor(host, 'custom')
    expect(opsButtons(row)).toEqual(['common.delete'])
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({})
  })

  it('重置某段最后一个覆盖项时，摘掉空掉的父表而不是存空表', async () => {
    // 后端 deep_merge 把空 mapping 当「替换」处理，存下 { llm: {} } 会把整段
    // 基线抹掉，而预览的合并不实现这条语义，界面上还显示着继承内容。
    const baseline = { llm: { model: 'gpt-a', temperature: 1 } }
    const { host, emitted } = mountEditor({ llm: { model: 'mine' } }, baseline)
    await nextTick()

    const row = rowFor(host, 'model')
    expect(opsButtons(row)).toEqual(['common.reset'])
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({})
  })

  it('多层嵌套时剪枝一路向上冒泡', async () => {
    const baseline = { a: { b: { c: 0, d: 2 } } }
    const { host, emitted } = mountEditor({ a: { b: { c: 1 } } }, baseline)
    await nextTick()

    const row = rowFor(host, 'c')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({})
  })

  it('基线里没有的自定义空表是显式意图，不被剪掉', async () => {
    const baseline = { alpha: 'a' }
    const { host, emitted } = mountEditor({ custom: { x: 1 } }, baseline)
    await nextTick()

    const row = rowFor(host, 'x')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ custom: {} })
  })

  it('数组尚未被覆盖时，编辑其中一项落成完整数组', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const inputs = Array.from(host.querySelectorAll('input')) as HTMLInputElement[]
    expect(inputs).toHaveLength(3)
    typeInto(inputs[1]!, 'b2')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'b2', 'c'] })
  })

  it('数组尚未被覆盖时，添加项追加在基线全量之后', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const addBtn = Array.from(
      host.querySelectorAll('.arr > .add button')
    ).pop() as HTMLButtonElement
    addBtn.click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'b', 'c', ''] })
  })

  it('数组尚未被覆盖时，删除一项落成剩余的完整数组', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const row = rowFor(host, '0')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ hosts: ['b', 'c'] })
  })

  it('overlay 数组按自身长度渲染，不拿基线补尾巴', async () => {
    // 整体替换语义下尾部不会被继承，补出来就是删不掉的幻影项。
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host } = mountEditor({ hosts: ['a'] }, baseline)
    await nextTick()

    const inputs = Array.from(host.querySelectorAll('input')) as HTMLInputElement[]
    expect(inputs).toHaveLength(1)
    expect(inputs[0]!.value).toBe('a')
  })

  it('删掉末项后它不会被基线填回来', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({ hosts: ['a', 'b', 'c'] }, baseline)
    await nextTick()

    const row = rowFor(host, '2')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'b'] })

    // 用写回后的 overlay 重新渲染：末项不该复活
    const again = mountEditor({ hosts: ['a', 'b'] }, baseline)
    await nextTick()
    expect(Array.from(again.host.querySelectorAll('input'))).toHaveLength(2)
  })

  it('数组项 overlay 存在时，不显示基线独有的字段', async () => {
    const baseline = { servers: [{ host: 'h1', port: 80 }] }
    const { host } = mountEditor({ servers: [{ host: 'mine' }] }, baseline)
    await nextTick()

    const keys = Array.from(host.querySelectorAll('.row > .k')).map((n) =>
      (n.textContent || '').trim()
    )
    expect(keys).toContain('host')
    expect(keys).not.toContain('port')
  })

  it('数组项里能显式补回基线独有的字段（不被当成重名拒绝）', async () => {
    const baseline = { servers: [{ host: 'h1', port: 80 }] }
    const { host, emitted } = mountEditor({ servers: [{ host: 'mine' }] }, baseline)
    await nextTick()

    // 数组项那一层的「添加字段」按钮（文档序里最靠前的那个 .add 属于它）
    const addBtn = host.querySelector('.add button') as HTMLButtonElement
    addBtn.click()
    await nextTick()

    const dialogInput = document.querySelector('.el-dialog input') as HTMLInputElement
    dialogInput.value = 'port'
    dialogInput.dispatchEvent(new Event('input'))
    await nextTick()

    const confirm = Array.from(document.querySelectorAll('.el-dialog button')).find(
      (b) => (b.textContent || '').trim() === 'common.confirm'
    ) as HTMLButtonElement
    confirm.click()
    await nextTick()

    expect(emitted.length).toBeGreaterThan(0)
  })

  it('数组项内的字段「重置」写回基线值，而不是把字段删掉', async () => {
    // 数组整体替换，没有继承回填 —— 摘掉键就等于把 servers[0].host
    // 从生效配置里删了。
    const baseline = { servers: [{ host: 'h1', port: 80 }] }
    const { host, emitted } = mountEditor({ servers: [{ host: 'mine', port: 80 }] }, baseline)
    await nextTick()

    const row = rowFor(host, 'host')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ servers: [{ host: 'h1', port: 80 }] })
  })

  it('根节点隐藏 plugin 段（profile 不允许覆盖）', async () => {
    const baseline = { plugin: { id: 'demo', name: 'Demo' }, alpha: 'a' }
    const { host } = mountEditor({}, baseline)
    await nextTick()

    const keys = Array.from(host.querySelectorAll('.row > .k')).map((n) =>
      (n.textContent || '').trim()
    )
    expect(keys).toContain('alpha')
    expect(keys).not.toContain('plugin')
  })

  it('「添加字段」与基线中已有的键重名时被拒绝（否则会把整段默认值覆盖成空值）', async () => {
    const baseline = { llm: { model: 'gpt-a' } }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    // 根节点自己的「添加字段」按钮（嵌套编辑器也各有一个，必须限定层级）
    const addBtn = host.querySelector(':scope > .cve > .obj > .add button') as HTMLButtonElement
    addBtn.click()
    await nextTick()

    const dialogInput = document.querySelector('.el-dialog input') as HTMLInputElement
    dialogInput.value = 'llm'
    dialogInput.dispatchEvent(new Event('input'))
    await nextTick()

    const confirm = Array.from(document.querySelectorAll('.el-dialog button')).find(
      (b) => (b.textContent || '').trim() === 'common.confirm'
    ) as HTMLButtonElement
    confirm.click()
    await nextTick()

    expect(emitted).toEqual([])
  })

  it('数组整份写回（后端 deep_merge 对数组是替换语义，稀疏数组会产生空洞）', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const inputs = Array.from(host.querySelectorAll('input')) as HTMLInputElement[]
    expect(inputs).toHaveLength(3)
    typeInto(inputs[2]!, 'c2')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'b', 'c2'] })
  })
})

describe('compact configuration layout preserves editing semantics', () => {
  it('edits a nested inherited field without copying sibling defaults', async () => {
    const { host, emitted } = mountEditor(
      {},
      { network: { retry: { delay: 2, attempts: 3 } } },
      true
    )
    await nextTick()
    typeInto(rowFor(host, 'delay').querySelector('input')!, '4')
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ network: { retry: { delay: 4 } } })
  })

  it('edits an inherited array as a complete replacement', async () => {
    const { host, emitted } = mountEditor({}, { hosts: ['a', 'b', 'c'] }, true)
    await nextTick()
    typeInto(host.querySelectorAll('input')[1]!, 'updated')
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'updated', 'c'] })
  })
})

describe('ConfigValueEditor search/filter propagation', () => {
  it('array child editor receives search/filter props and hides non-matching fields within array items', async () => {
    const baseline = {
      servers: [
        { host: 'localhost', port: 8080, timeout: 30 },
        { host: 'example.com', port: 443, timeout: 60 },
      ],
    }
    const Wrapper = defineComponent({
      setup() {
        const search = ref('host')
        const filter = ref<'all' | 'configured' | 'dirty'>('all')
        return () =>
          h(ConfigValueEditor as any, {
            modelValue: {},
            baselineValue: baseline,
            compact: false,
            path: '',
            search: search.value,
            filter: filter.value,
          })
      },
    })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(Wrapper)
    app.use(ElementPlus)
    app.mount(host)
    await nextTick()

    // Array items should be visible (servers path matches query)
    // Within each array item, only 'host' field should be visible, 'port' and 'timeout' hidden
    const visibleKeys = Array.from(host.querySelectorAll('.row'))
      .map((row) => {
        const keyEl = row.querySelector('.k')
        const style = window.getComputedStyle(row as HTMLElement)
        if (style.display === 'none') return null
        return keyEl?.textContent?.trim()
      })
      .filter(Boolean)

    expect(visibleKeys).toContain('host')
    expect(visibleKeys).not.toContain('port')
    expect(visibleKeys).not.toContain('timeout')

    app.unmount()
    host.remove()
  })
})

describe('ConfigValueEditor — JSON Schema', () => {
  it('renders localized nested labels and descriptions while emitting only raw keys', async () => {
    const schema: ConfigEditorSchema = {
      type: 'object',
      properties: {
        search: {
          type: 'object',
          title: 'Search',
          properties: {
            query: {
              type: 'string',
              title: '查询',
              description: '说明',
              'x-title-i18n': { en: 'Search query', 'zh-CN': '查询' },
              'x-description-i18n': { en: '<b>Plain text</b>', 'zh-CN': '说明' },
            },
          },
        },
      },
    }
    const { host, emitted } = mountSchemaEditor(
      {},
      { search: { query: 'old', extra: 'keep' } },
      schema
    )
    await nextTick()
    expect(rowFor(host, 'query').textContent).toContain('Search query')
    expect(rowFor(host, 'query').textContent).toContain('<b>Plain text</b>')
    expect(rowFor(host, 'query').querySelector('b')).toBeNull()
    expect(emitted).toEqual([])
    typeInto(rowFor(host, 'query').querySelector('input')!, 'new')
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ search: { query: 'new' } })
  })

  it('uses declared types for absent fields without persisting schema defaults', async () => {
    const schema: ConfigEditorSchema = {
      type: 'object',
      properties: {
        retries: { type: 'integer', minimum: 1, maximum: 9, default: 4 },
        enabled: { type: 'boolean', default: true },
        name: { type: 'string', maxLength: 12, default: 'example' },
        nested: { type: 'object', properties: { text: { type: 'string' } } },
      },
    }
    const { host, emitted } = mountSchemaEditor({}, {}, schema)
    await nextTick()
    expect(rowFor(host, 'retries').querySelector('.el-input-number')).not.toBeNull()
    expect(rowFor(host, 'enabled').querySelector('.el-switch')).not.toBeNull()
    expect(rowFor(host, 'name').querySelector('input')?.maxLength).toBe(12)
    expect(rowFor(host, 'text')).toBeTruthy()
    expect(emitted).toEqual([])
    typeInto(rowFor(host, 'text').querySelector('input')!, 'new')
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ nested: { text: 'new' } })
  })

  it('enforces integer bounds through the number control', async () => {
    const { host, emitted } = mountSchemaEditor(
      {},
      { count: 3 },
      {
        type: 'object',
        properties: {
          count: { type: 'integer', minimum: 1, maximum: 5 },
        },
      }
    )
    await nextTick()
    typeInto(rowFor(host, 'count').querySelector('input')!, '9.5')
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ count: 5 })
  })

  it('keeps numeric and boolean enum values typed', async () => {
    for (const values of [
      [1, 2],
      [false, true],
    ]) {
      const { host, emitted } = mountSchemaEditor(
        {},
        { choice: values[0] },
        {
          type: 'object',
          properties: {
            choice: { enum: values },
          },
        }
      )
      await nextTick()
      ;(rowFor(host, 'choice').querySelector('.el-select__wrapper') as HTMLElement).click()
      await nextTick()
      const options = Array.from(document.querySelectorAll('.el-select-dropdown__item'))
      const option = options.find((o) => o.textContent?.trim() === String(values[1])) as HTMLElement
      option.click()
      await nextTick()
      expect(lastEmit(emitted)).toEqual({ choice: values[1] })
    }
  })

  it('propagates readOnly to nested controls and structural buttons', async () => {
    const { host, emitted } = mountSchemaEditor(
      { locked: { name: 'mine', list: ['a'] } },
      { locked: { name: 'old', list: ['b'] } },
      {
        type: 'object',
        properties: {
          locked: { type: 'object', readOnly: true },
        },
      }
    )
    await nextTick()
    const row = rowFor(host, 'locked')
    expect(Array.from(row.querySelectorAll('input')).every((input) => input.disabled)).toBe(true)
    expect(Array.from(row.querySelectorAll('button')).every((button) => button.disabled)).toBe(true)
    expect(emitted).toEqual([])
  })

  it('applies item schemas and preserves whole-array replacement when adding', async () => {
    const { host, emitted } = mountSchemaEditor(
      {},
      { servers: [{ port: 80 }] },
      {
        type: 'object',
        properties: {
          servers: {
            type: 'array',
            items: {
              type: 'object',
              default: { port: 443 },
              properties: {
                port: { type: 'integer', title: 'Port', minimum: 1 },
              },
            },
          },
        },
      }
    )
    await nextTick()
    expect(rowFor(host, 'port').textContent).toContain('Port')
    const add = Array.from(rowFor(host, 'servers').querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'plugins.addItem'
    )!
    add.click()
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ servers: [{ port: 80 }, { port: 443 }] })
  })

  it('ignores protected schema keys and keeps undeclared existing fields', async () => {
    const schema = JSON.parse(
      '{"type":"object","properties":{"plugin":{"type":"object"},"__proto__":{"type":"object"},"constructor":{"type":"string"},"a.b":{"type":"string"}}}'
    )
    const { host } = mountSchemaEditor({}, { legacy: 'value' }, schema)
    await nextTick()
    const keys = Array.from(host.querySelectorAll('.k .el-tag')).map((tag) =>
      tag.textContent?.trim()
    )
    expect(keys).toEqual(['legacy'])
  })
})

describe('ConfigValueEditor — schema review regressions', () => {
  it.each([false, true])(
    'rejects adding a missing readOnly field (array item: %s)',
    async (inArray) => {
      const locked: ConfigEditorSchema = {
        type: 'string',
        readOnly: true,
        default: 'locked default',
      }
      const object: ConfigEditorSchema = { type: 'object', properties: { locked } }
      const schema: ConfigEditorSchema = inArray
        ? { type: 'object', properties: { rows: { type: 'array', items: object } } }
        : object
      const { host, emitted } = mountSchemaEditor(
        inArray ? { rows: [{}] } : {},
        inArray ? { rows: [{ locked: 'baseline' }] } : {},
        schema
      )
      await nextTick()
      ;(host.querySelector('.add button') as HTMLButtonElement).click()
      await nextTick()
      typeInto(document.querySelector('.el-dialog input')!, 'locked')
      await nextTick()
      const confirm = Array.from(document.querySelectorAll('.el-dialog button')).find(
        (button) => button.textContent?.trim() === 'common.confirm'
      ) as HTMLButtonElement
      confirm.click()
      await nextTick()
      expect(emitted).toEqual([])
      expect(document.body.textContent).toContain('plugins.readOnlyField')
      // A rejected name must not prevent adding another writable field.
      typeInto(document.querySelector('.el-dialog input')!, 'custom')
      await nextTick()
      confirm.click()
      await nextTick()
      expect(lastEmit(emitted)).toEqual(inArray ? { rows: [{ custom: '' }] } : { custom: '' })
    }
  )

  it.each([
    { current: false, options: [true] },
    { current: true, options: [false] },
    { current: 0, options: [1, 2] },
    { current: 'legacy', options: ['new'] },
  ])(
    'shows an out-of-enum value $current without changing its type or writing on load',
    async ({ current, options }) => {
      for (const inherited of [true, false]) {
        const { host, emitted } = mountSchemaEditor(
          inherited ? {} : { choice: current },
          { choice: inherited ? current : options[0] },
          {
            type: 'object',
            properties: {
              choice: { enum: options },
            },
          }
        )
        await nextTick()
        const row = rowFor(host, 'choice')
        expect(
          row.querySelector('.el-select__selected-item.el-select__placeholder')?.textContent
        ).toBe(String(current))
        expect(emitted).toEqual([])
        ;(row.querySelector('.el-select__wrapper') as HTMLElement).click()
        await nextTick()
        const choices = Array.from(document.querySelectorAll('.el-select-dropdown__item'))
        const valid = choices.find(
          (item) => item.textContent?.trim() === String(options[0])
        ) as HTMLElement
        valid.click()
        await nextTick()
        expect(lastEmit(emitted)).toEqual({ choice: options[0] })
        // Remove teleported options before testing the other value source.
        const item = mounted.pop()!
        item.unmount()
        item.host.remove()
      }
    }
  )
})

describe('ConfigValueEditor — schema bounds in the compact field', () => {
  it('keeps an integer integral when its bounds are fractional', async () => {
    const { host, emitted } = mountEditor({}, { count: 3 }, true, {
      type: 'object',
      properties: { count: { type: 'integer', minimum: 0.5, maximum: 4.5 } },
    })
    await nextTick()
    const input = host.querySelector<HTMLInputElement>('input[aria-label="count"]')!
    input.value = '0'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new FocusEvent('blur'))
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ count: 1 })
    input.value = '9'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new FocusEvent('blur'))
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ count: 4 })
  })

  it('rounds a small negative integer to a positive zero in the model', async () => {
    // Math.round(-0.4) is -0, which serializes as 0 and would keep the draft dirty forever.
    const { host, emitted } = mountEditor({ count: 3 }, {}, true, {
      type: 'object',
      properties: { count: { type: 'integer' } },
    })
    await nextTick()
    const input = host.querySelector<HTMLInputElement>('input[aria-label="count"]')!
    input.value = '-0.4'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new FocusEvent('blur'))
    await nextTick()
    expect(input.value).toBe('0')
    expect(Object.is(lastEmit(emitted).count, 0)).toBe(true)
  })

  it('leaves a declared but absent number empty when blurred unchanged', async () => {
    // Nothing to fall back to: the field must not show the text "undefined".
    const { host, emitted } = mountEditor({}, {}, true, {
      type: 'object',
      properties: { count: { type: 'integer' } },
    })
    await nextTick()
    const input = host.querySelector<HTMLInputElement>('input[aria-label="count"]')!
    input.value = '-'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new FocusEvent('blur'))
    await nextTick()
    expect(input.value).toBe('')
    expect(emitted).toEqual([])
  })

  it('commits only in-range integers while typing and fits the value on blur', async () => {
    const { host, emitted } = mountEditor({}, { count: 3 }, true, {
      type: 'object',
      properties: { count: { type: 'integer', minimum: 1, maximum: 5 } },
    })
    await nextTick()
    const input = host.querySelector<HTMLInputElement>('input[aria-label="count"]')!
    input.value = '9.5'
    input.dispatchEvent(new Event('input'))
    await nextTick()
    // Out of range and not an integer: nothing is committed mid-edit.
    expect(emitted).toEqual([])
    input.dispatchEvent(new FocusEvent('blur'))
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ count: 5 })
    expect(input.value).toBe('5')
  })
})

describe('ConfigValueEditor confidential controls', () => {
  it('masks enum-backed secrets instead of exposing them in a dropdown', async () => {
    const { host, emitted } = mountSchemaEditor(undefined, 'fixture-secret', {
      type: 'string',
      writeOnly: true,
      enum: ['fixture-secret'],
    })
    await nextTick()
    const input = host.querySelector('input')!
    expect(input.type).toBe('password')
    expect(host.querySelector('.el-select')).toBeNull()
    expect(host.textContent).not.toContain('fixture-secret')
    typeInto(input, 'fixture-replacement')
    await nextTick()
    expect(lastEmit(emitted)).toBe('fixture-replacement')
  })

  it.each([
    { source: 'baseline', value: { token: 'fixture-secret' } },
    { source: 'baseline', value: ['fixture-secret'] },
    { source: 'overlay', value: { token: 'fixture-secret' } },
    { source: 'overlay', value: ['fixture-secret'] },
  ])(
    'allows replacing a hidden container from $source with a secret string',
    async ({ source, value }) => {
      const { host, emitted } = mountSchemaEditor(
        source === 'overlay' ? value : undefined,
        source === 'baseline' ? value : 'fixture-baseline',
        { type: 'string', writeOnly: true }
      )
      await nextTick()
      const input = host.querySelector('input')!
      expect(input.type).toBe('password')
      expect(input.disabled).toBe(false)
      expect(input.value).toBe('')
      expect(host.textContent).not.toContain('fixture-')
      expect(emitted).toEqual([])
      typeInto(input, 'fixture-replacement')
      await nextTick()
      expect(lastEmit(emitted)).toBe('fixture-replacement')
      expect(host.textContent).not.toContain('fixture-')
    }
  )

  it('offers a reveal toggle while keeping the secret masked by default', async () => {
    const { host } = mountSchemaEditor(undefined, 'fixture-secret', {
      type: 'string',
      writeOnly: true,
    })
    await nextTick()
    const input = host.querySelector('input')!
    expect(input.type).toBe('password')
    const toggle = host.querySelector<HTMLElement>('.el-input__password')
    expect(toggle).not.toBeNull()
    toggle!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(host.querySelector('input')!.type).toBe('text')
  })

  it('keeps malformed read-only secrets disabled', async () => {
    const { host, emitted } = mountSchemaEditor(
      undefined,
      { token: 'fixture-secret' },
      {
        type: 'string',
        writeOnly: true,
        readOnly: true,
      }
    )
    await nextTick()
    const input = host.querySelector('input')!
    expect(input.disabled).toBe(true)
    expect(input.value).toBe('')
    typeInto(input, 'fixture-replacement')
    await nextTick()
    expect(emitted).toEqual([])
  })

  it('clears a previous scalar value when a secret becomes a container', async () => {
    const baseline = ref<unknown>('fixture-previous')
    const emitted: unknown[] = []
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(
      defineComponent(
        () => () =>
          h(ConfigValueEditor, {
            modelValue: undefined,
            baselineValue: baseline.value,
            schema: { type: 'string', writeOnly: true },
            'onUpdate:modelValue': (value: unknown) => emitted.push(value),
          })
      )
    )
    app.use(ElementPlus)
    app.mount(host)
    mounted.push({ unmount: () => app.unmount(), host })
    await nextTick()
    expect(host.querySelector('input')!.value).toBe('fixture-previous')
    baseline.value = { token: 'fixture-current' }
    await nextTick()
    expect(host.querySelector('input')!.value).toBe('')
    expect(host.querySelector('input')!.disabled).toBe(false)
    expect(host.textContent).not.toContain('fixture-')
    expect(emitted).toEqual([])
  })
})

describe('ConfigValueEditor add field dialog', () => {
  async function openDialog(schema?: ConfigEditorSchema) {
    const { host, emitted } = mountSchemaEditor({}, {}, schema)
    await nextTick()
    ;(host.querySelector('.add button') as HTMLButtonElement).click()
    await nextTick()
    const dialog = [...document.querySelectorAll<HTMLElement>('.el-dialog')].at(-1)!
    return { dialog, emitted }
  }

  it('offers a type choice for undeclared keys', async () => {
    const { dialog } = await openDialog({ type: 'object', properties: {} })
    expect(dialog.querySelector('.el-select')).not.toBeNull()
  })

  it('hides the type choice when a dynamic-key schema decides the value', async () => {
    const { dialog, emitted } = await openDialog({
      type: 'object',
      properties: {},
      additionalProperties: { type: 'string', writeOnly: true },
    })
    expect(dialog.querySelector('.el-select')).toBeNull()
    typeInto(dialog.querySelector('input')!, 'token')
    await nextTick()
    const confirm = [...dialog.querySelectorAll('button')].find(
      (b) => (b.textContent || '').trim() === 'common.confirm'
    ) as HTMLButtonElement
    confirm.click()
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ token: '' })
  })

  it.each([{}, { title: 'Entry' }])(
    'keeps the type choice when the dynamic-key schema %j leaves the value open',
    async (additionalProperties) => {
      const { dialog, emitted } = await openDialog({
        type: 'object',
        properties: {},
        additionalProperties,
      })
      const select = dialog.querySelector<HTMLElement>('.el-select__wrapper')
      expect(select).not.toBeNull()
      typeInto(dialog.querySelector('input')!, 'entry')
      select!.click()
      await nextTick()
      const option = [...document.querySelectorAll<HTMLElement>('.el-select-dropdown__item')].find(
        (item) => item.textContent?.trim() === 'array'
      )!
      option.click()
      await nextTick()
      const confirm = [...dialog.querySelectorAll('button')].find(
        (b) => (b.textContent || '').trim() === 'common.confirm'
      ) as HTMLButtonElement
      confirm.click()
      await nextTick()
      expect(lastEmit(emitted)).toEqual({ entry: [] })
    }
  )
})
