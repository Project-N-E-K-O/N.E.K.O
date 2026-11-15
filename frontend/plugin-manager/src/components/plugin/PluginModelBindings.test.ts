// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import PluginModelBindings from './PluginModelBindings.vue'
import type { ModelBindings, ModelSlot } from '@/types/model-api'

const api = vi.hoisted(() => ({
  getModelBindings: vi.fn(),
  listModelSlots: vi.fn(),
  setModelBinding: vi.fn(),
  deleteModelBinding: vi.fn(),
}))

vi.mock('@/api/models', () => api)
vi.mock('@/utils/request', () => ({ formatHttpError: (error: Error) => error.message }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

const mounted: Array<() => void> = []
const capable: ModelSlot = {
  id: 'slot_saved_id', name: 'Vision', model: 'vision-model', protocol: 'openai_chat',
  base_url: 'https://example.test/v1', api_key: '__NEKO_SECRET_MASKED__',
  capabilities: ['text', 'image_input'], defaults: { temperature: null, max_output_tokens: null },
  timeout_seconds: 60, fallback_slot_id: null, bound_by: [],
}
const textOnly: ModelSlot = { ...capable, id: 'slot_text', name: 'Text', capabilities: ['text'] }

function bindings(pluginId = 'vision_plugin', slotId: string | null = null): ModelBindings {
  return {
    plugin_id: pluginId,
    requirements: {
      vision: {
        label: `${pluginId} vision`, description: 'Describe an image', required: true,
        capabilities: ['text', 'image_input'], slot_id: slotId, version: 0, status: slotId ? 'bound' : 'unbound',
      },
      optional: {
        label: 'Optional summary', description: '', required: false,
        capabilities: ['text'], slot_id: null, version: 0, status: 'unbound',
      },
    },
    bindings: slotId ? { vision: slotId } : {},
    ready: Boolean(slotId),
  }
}

async function flush() {
  for (let index = 0; index < 10; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

function mountBindings(initialPluginId = 'vision_plugin') {
  const pluginId = ref(initialPluginId)
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(defineComponent(() => () => h(PluginModelBindings, { pluginId: pluginId.value })))
  app.component('el-button', defineComponent({
    props: { disabled: Boolean, loading: Boolean },
    setup: (props, { slots }) => () => h('button', { disabled: props.disabled || props.loading }, slots.default?.()),
  }))
  app.component('el-tag', defineComponent((_, { slots }) => () => h('span', slots.default?.())))
  app.component('el-icon', defineComponent((_, { slots }) => () => h('i', slots.default?.())))
  app.component('el-tooltip', defineComponent((_, { slots }) => () => h('span', [slots.default?.(), slots.content?.()])))
  app.component('el-alert', defineComponent({
    props: { title: String, description: String },
    setup: (props) => () => h('div', { role: 'status' }, `${props.title} ${props.description ?? ''}`),
  }))
  app.component('router-link', defineComponent({
    props: { to: Object },
    setup: (props, { slots }) => () => h('a', { href: JSON.stringify(props.to) }, slots.default?.()),
  }))
  app.component('el-select', defineComponent({
    props: { modelValue: String, disabled: Boolean },
    emits: ['update:modelValue', 'change'],
    setup: (props, { slots, emit }) => () => h('select', {
      value: props.modelValue, disabled: props.disabled,
      onChange: (event: Event) => {
        const value = (event.target as HTMLSelectElement).value
        emit('update:modelValue', value)
        emit('change', value)
      },
    }, slots.default?.()),
  }))
  app.component('el-option', defineComponent({
    props: { value: String, label: String, disabled: Boolean },
    setup: (props) => () => h('option', { value: props.value, disabled: props.disabled }, props.label),
  }))
  app.mount(host)
  mounted.push(() => { app.unmount(); host.remove() })
  return { host, pluginId }
}

function selectFor(host: HTMLElement, usageId = 'vision'): HTMLSelectElement {
  const select = host.querySelector<HTMLSelectElement>(`[data-usage-id="${usageId}"] select`)
  if (!select) throw new Error(`No selector for ${usageId}`)
  return select
}

function change(select: HTMLSelectElement, value: string) {
  select.value = value
  select.dispatchEvent(new Event('change'))
}

function content(host: HTMLElement): HTMLElement {
  const element = host.querySelector<HTMLElement>('#model-bindings-content')
  if (!element) throw new Error('No binding content')
  return element
}

function toggle(host: HTMLElement): HTMLButtonElement | null {
  return host.querySelector<HTMLButtonElement>('button[aria-controls="model-bindings-content"]')
}

beforeEach(() => {
  vi.resetAllMocks()
  api.getModelBindings.mockResolvedValue(bindings())
  api.listModelSlots.mockResolvedValue({ schema_version: 1, slots: [capable, textOnly] })
  api.setModelBinding.mockResolvedValue({ plugin_id: 'vision_plugin', usage_id: 'vision', slot_id: capable.id, version: 1 })
  api.deleteModelBinding.mockResolvedValue({ success: true, version: 1 })
})

afterEach(() => {
  mounted.splice(0).forEach((cleanup) => cleanup())
  localStorage.clear()
})

describe('PluginModelBindings', () => {
  it('renders nothing and does not fetch slots for plugins with no declared model purposes', async () => {
    api.getModelBindings.mockResolvedValue({ plugin_id: 'plain', requirements: {}, bindings: {}, ready: true })
    const { host } = mountBindings('plain')
    await flush()
    expect(host.querySelector('section')).toBeNull()
    expect(api.listModelSlots).not.toHaveBeenCalled()
  })

  it('disables slots missing required capabilities and saves stable slot IDs, not model names', async () => {
    const { host } = mountBindings()
    await flush()
    const select = selectFor(host)
    const unsupported = select.querySelector<HTMLOptionElement>(`option[value="${textOnly.id}"]`)
    expect(unsupported?.disabled).toBe(true)
    expect(unsupported?.textContent).toContain('modelBindings.incompatible')
    expect(host.textContent).toContain('modelBindings.notReady')
    expect(host.textContent).toContain('modelBindings.required')
    expect(host.textContent).toContain('modelBindings.optional')
    change(select, capable.id)
    await flush()
    expect(api.setModelBinding).toHaveBeenCalledExactlyOnceWith('vision_plugin', 'vision', capable.id, 0)
    expect(selectFor(host).value).toBe(capable.id)
    expect(host.textContent).toContain('modelBindings.ready')
    expect(host.textContent).toContain('modelBindings.readinessHint')
    expect(host.querySelector('a')?.getAttribute('href')).toBe(JSON.stringify({ path: '/model-api', query: { plugin_id: 'vision_plugin' } }))
  })

  it('unbinds independently and marks only required purposes as needed', async () => {
    api.getModelBindings.mockResolvedValue(bindings('vision_plugin', capable.id))
    const { host } = mountBindings()
    await flush()
    change(selectFor(host), '')
    await flush()
    expect(api.deleteModelBinding).toHaveBeenCalledExactlyOnceWith('vision_plugin', 'vision', 0)
    expect(api.setModelBinding).not.toHaveBeenCalled()
    expect(selectFor(host).value).toBe('')
    expect(host.textContent).toContain('modelBindings.notReady')
  })

  it('restores the saved selection and shows an inline error after a rejected change', async () => {
    api.getModelBindings.mockResolvedValue(bindings('vision_plugin', capable.id))
    api.deleteModelBinding.mockRejectedValue(new Error('MODEL_BINDING_REJECTED'))
    const { host } = mountBindings()
    await flush()
    change(selectFor(host), '')
    await flush()
    expect(selectFor(host).value).toBe(capable.id)
    expect(host.textContent).toContain('modelBindings.bindingErrors.failed')
    expect(host.textContent).toContain('modelBindings.ready')
  })

  it('ignores a stale load after navigating to another plugin', async () => {
    let resolveOld!: (value: ModelBindings) => void
    api.getModelBindings.mockImplementation((pluginId: string) => pluginId === 'old'
      ? new Promise<ModelBindings>((resolve) => { resolveOld = resolve })
      : Promise.resolve(bindings(pluginId)))
    const { host, pluginId } = mountBindings('old')
    pluginId.value = 'new'
    await flush()
    resolveOld(bindings('old', capable.id))
    await flush()
    expect(host.textContent).toContain('new vision')
    expect(host.textContent).not.toContain('old vision')
    expect(selectFor(host).value).toBe('')
    expect(api.listModelSlots).toHaveBeenCalledTimes(1)
  })

  it('does not apply an old plugin save result to the new plugin', async () => {
    let resolveSave!: () => void
    api.getModelBindings.mockImplementation((pluginId: string) => Promise.resolve(bindings(pluginId)))
    api.setModelBinding.mockImplementation(() => new Promise<void>((resolve) => { resolveSave = resolve }))
    const { host, pluginId } = mountBindings('old')
    await flush()
    change(selectFor(host), capable.id)
    await flush()
    expect(selectFor(host).disabled).toBe(true)
    pluginId.value = 'new'
    await flush()
    resolveSave()
    await flush()
    expect(api.setModelBinding).toHaveBeenCalledExactlyOnceWith('old', 'vision', capable.id, 0)
    expect(host.textContent).toContain('new vision')
    expect(selectFor(host).value).toBe('')
    expect(selectFor(host).disabled).toBe(false)
  })

  it('starts collapsed when ready and remembers the choice per plugin', async () => {
    api.getModelBindings.mockResolvedValue(bindings('vision_plugin', capable.id))
    const first = mountBindings()
    await flush()
    expect(content(first.host).style.display).toBe('none')
    expect(toggle(first.host)?.getAttribute('aria-expanded')).toBe('false')
    toggle(first.host)!.click()
    await flush()
    expect(content(first.host).style.display).toBe('')
    expect(localStorage.getItem('neko.pluginModelBindings.collapsed.vision_plugin')).toBe('false')
    mounted.splice(0).forEach((cleanup) => cleanup())

    const second = mountBindings()
    await flush()
    expect(content(second.host).style.display).toBe('')
    expect(toggle(second.host)?.getAttribute('aria-expanded')).toBe('true')
  })

  it('keeps unfinished bindings open without a collapse control, even if collapsed before', async () => {
    localStorage.setItem('neko.pluginModelBindings.collapsed.vision_plugin', 'true')
    const { host } = mountBindings()
    await flush()
    expect(content(host).style.display).toBe('')
    expect(toggle(host)).toBeNull()
    expect(host.querySelector('[data-usage-id="vision"]')?.textContent).toContain('modelBindings.status.unbound')
    expect(host.querySelector('[data-usage-id="optional"]')?.textContent).not.toContain('modelBindings.status.unbound')
    change(selectFor(host), capable.id)
    await flush()
    expect(content(host).style.display).toBe('')
    expect(toggle(host)?.getAttribute('aria-expanded')).toBe('true')
  })

  it('allows retrying a failed declaration load', async () => {
    api.getModelBindings.mockRejectedValueOnce(new Error('Temporary load failure'))
    const { host } = mountBindings()
    await flush()
    expect(host.textContent).toContain('Temporary load failure')
    host.querySelector<HTMLButtonElement>('button')?.click()
    await flush()
    expect(host.textContent).not.toContain('Temporary load failure')
    expect(selectFor(host)).toBeDefined()
  })
})

it('uses the returned version for the next mutation', async () => {
  const { host } = mountBindings()
  await flush()
  change(selectFor(host), capable.id)
  await flush()
  change(selectFor(host), '')
  await flush()
  expect(api.setModelBinding).toHaveBeenCalledWith('vision_plugin', 'vision', capable.id, 0)
  expect(api.deleteModelBinding).toHaveBeenCalledWith('vision_plugin', 'vision', 1)
})

it('reloads authoritative state on conflict without replaying the rejected selection', async () => {
  const { host } = mountBindings()
  await flush()
  const authoritative = bindings('vision_plugin', capable.id)
  authoritative.requirements.vision!.version = 7
  api.getModelBindings.mockResolvedValue(authoritative)
  api.setModelBinding.mockRejectedValue(new Error('MODEL_BINDING_CONFLICT'))
  change(selectFor(host), capable.id)
  await flush()
  expect(api.setModelBinding).toHaveBeenCalledTimes(1)
  expect(api.getModelBindings).toHaveBeenCalledTimes(2)
  expect(selectFor(host).value).toBe(capable.id)
  expect(host.textContent).toContain('modelBindings.bindingErrors.conflict')
  change(selectFor(host), '')
  await flush()
  expect(api.deleteModelBinding).toHaveBeenCalledWith('vision_plugin', 'vision', 7)
})

it('keeps the new binding when an old save finishes after leaving and returning', async () => {
  let rejectOld!: (reason: Error) => void
  api.getModelBindings.mockImplementation((pluginId: string) => Promise.resolve(bindings(pluginId)))
  api.setModelBinding.mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject }))
  const { host, pluginId } = mountBindings('old')
  await flush()
  change(selectFor(host), capable.id)
  await flush()
  pluginId.value = 'other'; await flush()
  pluginId.value = 'old'; await flush()
  change(selectFor(host), capable.id); await flush()
  rejectOld(new Error('MODEL_BINDING_CONFLICT')); await flush()
  expect(api.setModelBinding).toHaveBeenCalledTimes(2)
  expect(api.setModelBinding).toHaveBeenLastCalledWith('old', 'vision', capable.id, 0)
  expect(selectFor(host).value).toBe(capable.id)
  expect(host.textContent).not.toContain('modelBindings.bindingErrors.conflict')
})

it('does not display stale readiness when confirmation and reload fail', async () => {
  const { host } = mountBindings()
  await flush()
  api.setModelBinding.mockRejectedValue(new Error('MODEL_BINDING_RESULT_UNKNOWN'))
  api.getModelBindings.mockRejectedValue(new Error('MODEL_BINDING_RESULT_UNKNOWN'))
  change(selectFor(host), capable.id)
  await flush()
  expect(host.textContent).toContain('modelBindings.bindingErrors.unknown')
  expect(host.textContent).not.toContain('modelBindings.notReady')
  expect(host.querySelector('select')).toBeNull()
  api.getModelBindings.mockResolvedValue(bindings('vision_plugin', capable.id))
  host.querySelector<HTMLButtonElement>('button')!.click()
  await flush()
  expect(selectFor(host).value).toBe(capable.id)
  expect(host.textContent).not.toContain('modelBindings.bindingErrors.unknown')
})

it('clears the unknown warning when reload confirms the requested binding', async () => {
  const { host } = mountBindings()
  await flush()
  api.setModelBinding.mockRejectedValue(new Error('MODEL_BINDING_RESULT_UNKNOWN'))
  api.getModelBindings.mockResolvedValue(bindings('vision_plugin', capable.id))
  change(selectFor(host), capable.id)
  await flush()
  expect(selectFor(host).value).toBe(capable.id)
  expect(host.textContent).not.toContain('modelBindings.bindingErrors.unknown')
  expect(host.textContent).not.toContain('modelBindings.bindingErrors.failed')
})

it('reports a confirmed different result without leaving an unknown warning', async () => {
  const { host } = mountBindings()
  await flush()
  api.setModelBinding.mockRejectedValue(new Error('MODEL_BINDING_RESULT_UNKNOWN'))
  api.getModelBindings.mockResolvedValue(bindings())
  change(selectFor(host), capable.id)
  await flush()
  expect(selectFor(host).value).toBe('')
  expect(host.textContent).toContain('modelBindings.bindingErrors.failed')
  expect(host.textContent).not.toContain('modelBindings.bindingErrors.unknown')
})
