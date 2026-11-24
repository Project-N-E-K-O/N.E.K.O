// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import words from '@/i18n/power/en-US'
import PowerHome from '@/views/PowerHome.vue'

// What the home page shows is tested where each part is; here only which of the two views it is.
const stub = vi.hoisted(() => (name: string) => async () => {
  const { defineComponent, h } = await import('vue')
  return {
    // Told to be a module, as a real one is: what loads a component when it is first needed looks for that.
    __esModule: true,
    default: defineComponent({
      name,
      setup: (_, { expose }) => { expose({ load: () => {} }); return () => h('div', { 'data-stub': name }) },
    }),
  }
})
vi.mock('@/components/power/colony/FamiliarColony.vue', stub('colony'))
vi.mock('@/components/power/home/FamiliarBoard.vue', stub('list'))
vi.mock('@/components/power/home/PowerBar.vue', stub('bar'))
vi.mock('@/components/power/home/NewFamiliarDialog.vue', stub('dialog'))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

let cleanup: (() => void) | undefined
async function flush() {
  for (let i = 0; i < 6; i++) { await Promise.resolve(); await nextTick() }
  await vi.dynamicImportSettled()
  for (let i = 0; i < 4; i++) { await Promise.resolve(); await nextTick() }
}
async function mount() {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp({ render: () => h(PowerHome) })
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { power: words } } }))
  app.mount(container)
  cleanup = () => { app.unmount(); container.remove() }
  await flush()
}
const shown = () => [...document.querySelectorAll('[data-stub]')].map(element => element.getAttribute('data-stub'))
const view = (name: string) => document.querySelector(`[data-view="${name}"]`) as HTMLElement

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = '' })

describe('the two views of the familiars on the home page', () => {
  it('is the colony until the user chooses the list, and the choice is kept', async () => {
    await mount()
    expect(shown()).toEqual(['bar', 'colony'])
    expect(view('colony').getAttribute('aria-pressed')).toBe('true')
    view('list').click()
    await flush()
    expect(shown()).toEqual(['bar', 'list'])
    expect(view('list').getAttribute('aria-pressed')).toBe('true')

    // Opened again, it is the list; and the colony again once that is chosen.
    cleanup?.()
    await mount()
    expect(shown()).toEqual(['bar', 'list'])
    view('colony').click()
    await flush()
    cleanup?.()
    await mount()
    expect(shown()).toEqual(['bar', 'colony'])
  })

  it('is the colony when what was kept is nothing it knows', async () => {
    localStorage.setItem('neko-power-home-view', 'grid')
    await mount()
    expect(shown()).toEqual(['bar', 'colony'])
  })
})
