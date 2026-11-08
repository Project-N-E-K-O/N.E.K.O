// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import Editor from './PythonRequirementEditor.vue'
import { pythonPackageMessages } from '@/i18n/python-packages'

const en = pythonPackageMessages['en-US']
const submitted = vi.fn()
let app: ReturnType<typeof createApp> | undefined
async function flush() {
  for (let n = 0; n < 12; n++) {
    await Promise.resolve()
    await nextTick()
  }
}
async function mount(declaration?: string) {
  const props = reactive({
    modelValue: true,
    declaration,
    saving: false,
    busy: false,
    canInstall: true,
  })
  const container = document.createElement('div')
  document.body.append(container)
  app = createApp({
    render: () =>
      h(Editor, {
        ...props,
        'onUpdate:modelValue': (value: boolean) => {
          props.modelValue = value
        },
        onSubmit: submitted,
      }),
  })
    .use(ElementPlus)
    .use(
      createI18n({
        legacy: false,
        locale: 'en-US',
        messages: { 'en-US': { pythonPackages: en, common: { cancel: 'Cancel' } } },
      })
    )
  app.mount(container)
  await flush()
  return props
}
async function click(text: string) {
  const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
    (el) => el.textContent?.trim() === text
  )
  expect(button, text).toBeTruthy()
  button!.click()
  await flush()
}
async function fill(id: string, value: string) {
  const input = document.querySelector<HTMLInputElement>(`#${id}`)!
  input.value = value
  input.dispatchEvent(new Event('input'))
  await flush()
}
afterEach(() => {
  app?.unmount()
  app = undefined
  document.body.innerHTML = ''
  submitted.mockClear()
})
describe('Python requirement editor', () => {
  it('starts with the simple package form and automatic version selection', async () => {
    await mount()
    expect(document.querySelector<HTMLDetailsElement>('.el-dialog details')?.open).toBe(false)
    await fill('python-package-name', 'demo')
    await click(en.addInstall)
    expect(submitted).toHaveBeenCalledWith('demo', true)
  })
  it('accepts a copied install command without requiring declaration syntax', async () => {
    await mount()
    await fill('python-package-name', 'python -m pip install demo==2.0.1')
    await click(en.addInstall)
    expect(submitted).toHaveBeenCalledWith('demo==2.0.1', true)
  })
  it('uses a version number chosen through the version selector', async () => {
    await mount()
    await fill('python-package-name', 'demo')
    document.querySelector<HTMLElement>('#python-package-version-policy')!.click()
    await flush()
    const option = [...document.querySelectorAll<HTMLElement>('[role="option"]')].find(
      (item) => item.textContent?.trim() === en.versionExact
    )!
    expect(option).toBeTruthy()
    option.click()
    await flush()
    await fill('python-package-exact-version', '2.0.1')
    await click(en.saveOnly)
    expect(submitted).toHaveBeenCalledWith('demo==2.0.1', false)
  })
  it('can return from an empty manual declaration to the simple form', async () => {
    await mount()
    const details = document.querySelector<HTMLDetailsElement>('.el-dialog details')!
    details.open = true
    await flush()
    const checkbox = document.querySelector<HTMLInputElement>('.el-dialog input[type="checkbox"]')!
    checkbox.click()
    await flush()
    await fill('python-package-requirement', '   ')
    checkbox.click()
    await flush()
    expect(document.querySelector<HTMLInputElement>('#python-package-name')?.value).toBe('')
    await fill('python-package-name', 'demo')
    await click(en.saveOnly)
    expect(submitted).toHaveBeenCalledWith('demo', false)
  })
  it('preserves advanced settings when editing an existing requirement', async () => {
    await mount('httpx[socks]>=0.27; python_version >= "3.11"')
    await fill('python-package-version', '>=1,<2')
    await fill('python-package-extras', 'extra')
    await fill('python-package-marker', 'sys_platform == "win32"')
    await click(en.saveOnly)
    expect(submitted).toHaveBeenCalledWith('httpx[extra]>=1,<2; sys_platform == "win32"', false)
  })
  it('resets the form when opening another declaration', async () => {
    const props = await mount('demo==1.0')
    await fill('python-package-name', 'unsaved')
    await click('Cancel')
    props.declaration = 'other[socks]>=2'
    props.modelValue = true
    await flush()
    expect(document.querySelector<HTMLInputElement>('#python-package-name')?.value).toBe('other')
    await click(en.saveOnly)
    expect(submitted).toHaveBeenCalledWith('other[socks]>=2', false)
  })
})
