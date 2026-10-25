// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref, withDirectives } from 'vue'
import { cancelMotion, playMotion } from './runtime'
import { vMotion } from './directive'
import { useGridMotionController } from './grid'
import MotionTransition from './MotionTransition.vue'

const elements: HTMLElement[] = []
const nativeAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')
let reduced = false
let hidden = false
let preferenceChanged: () => void
let removeListener: ReturnType<typeof vi.fn>

function fixture() {
  const element = document.createElement('div')
  elements.push(element)
  let finish!: () => void
  const animation = {
    cancel: vi.fn(),
    finished: new Promise<void>(resolve => { finish = resolve }),
  }
  const animate = vi.fn(() => animation)
  element.animate = animate as unknown as HTMLElement['animate']
  return { element, animation, animate, finish }
}

beforeEach(() => {
  vi.useFakeTimers()
  reduced = false
  hidden = false
  removeListener = vi.fn()
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
  vi.stubGlobal('matchMedia', vi.fn(() => ({
    get matches() { return reduced },
    addEventListener: (_: string, listener: () => void) => { preferenceChanged = listener },
    removeEventListener: removeListener,
  })))
})

afterEach(() => {
  elements.splice(0).forEach(cancelMotion)
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  if (nativeAnimate) Object.defineProperty(HTMLElement.prototype, 'animate', nativeAnimate)
  else delete (HTMLElement.prototype as Partial<HTMLElement>).animate
})

function mountGridController() {
  let controller!: ReturnType<typeof useGridMotionController>
  const app = createApp(defineComponent({
    setup() {
      controller = useGridMotionController()
      return () => null
    },
  }))
  app.mount(document.createElement('div'))
  return { controller, app }
}

/** Grid children whose offsetTop reflows as earlier siblings leave the flow. */
function gridItems(count: number) {
  const grid = document.createElement('div')
  const items = Array.from({ length: count }, (_, index) => {
    const { element: item } = fixture()
    item.dataset.motionIndex = String(index)
    vi.spyOn(item, 'getBoundingClientRect').mockReturnValue({ top: 0, bottom: 100, left: 0, right: 100 } as DOMRect)
    Object.defineProperty(item, 'offsetTop', {
      get: () => 100 * items.slice(0, index).filter(sibling => sibling.style.position !== 'absolute').length,
    })
    grid.appendChild(item)
    return item
  })
  return items
}

it('releases the effect and callback once, leaving authored styles untouched', async () => {
  const { element, animate, animation, finish } = fixture()
  element.style.transform = 'scale(0.9)'
  const done = vi.fn()
  playMotion(element, { preset: 'card', done })
  expect(animate).toHaveBeenCalledOnce()
  finish()
  await Promise.resolve()
  vi.runAllTimers()
  cancelMotion(element)
  expect(done).toHaveBeenCalledOnce()
  expect(animation.cancel).toHaveBeenCalledOnce()
  expect(element.style.transform).toBe('scale(0.9)')
  expect(removeListener).toHaveBeenCalledOnce()
})

it('supersedes an in-flight animation without letting its finished promise cancel the replacement', async () => {
  const { element, animate, finish } = fixture()
  const oldDone = vi.fn()
  playMotion(element, { preset: 'item', done: oldDone })
  const replacement = { cancel: vi.fn(), finished: new Promise<void>(() => {}) }
  animate.mockReturnValueOnce(replacement)
  const done = vi.fn()
  playMotion(element, { preset: 'quiet', done })
  finish()
  await Promise.resolve()
  expect(oldDone).toHaveBeenCalledOnce()
  expect(replacement.cancel).not.toHaveBeenCalled()
  expect(done).not.toHaveBeenCalled()
  cancelMotion(element)
  expect(replacement.cancel).toHaveBeenCalledOnce()
  expect(done).toHaveBeenCalledOnce()
})

it('settles every active animation when motion preference changes', () => {
  const first = fixture(), second = fixture()
  const done = vi.fn()
  playMotion(first.element, { preset: 'item', done })
  playMotion(second.element, { preset: 'item', done })
  reduced = true
  preferenceChanged()
  expect(done).toHaveBeenCalledTimes(2)
  expect(removeListener).toHaveBeenCalledOnce()
  playMotion(first.element, { preset: 'item', done })
  expect(first.animate).toHaveBeenCalledOnce()
  expect(done).toHaveBeenCalledTimes(3)
})

it('finishes hidden-page effects and handles missing or failing WAAPI without hiding content', () => {
  const { element, animation } = fixture()
  const done = vi.fn()
  playMotion(element, { preset: 'section', done })
  hidden = true
  document.dispatchEvent(new Event('visibilitychange'))
  expect(animation.cancel).toHaveBeenCalledOnce()
  hidden = false
  element.animate = undefined as unknown as HTMLElement['animate']
  playMotion(element, { preset: 'section', done })
  element.animate = () => { throw new Error('Unsupported') }
  playMotion(element, { preset: 'section', done })
  expect(done).toHaveBeenCalledTimes(3)
  expect(element.style.opacity).toBe('')
})

it('does not replay on status updates and cancels on unmount', async () => {
  const animate = vi.fn(() => ({
    cancel: vi.fn(), finished: new Promise<void>(() => {}),
  }) as unknown as Animation)
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  const key = ref('/'), count = ref(0)
  const host = document.createElement('div')
  const app = createApp({ render: () => withDirectives(h('div', count.value), [[vMotion, { preset: 'page', key: key.value }]]) })
  app.mount(host)
  count.value++
  await nextTick()
  expect(animate).toHaveBeenCalledTimes(1)
  key.value = '/plugins'
  await nextTick()
  expect(animate).toHaveBeenCalledTimes(2)
  app.unmount()
  vi.runAllTimers()
  expect(animate).toHaveBeenCalledTimes(2)
})

it('crossfades route views: the next view enters while the previous one leaves pinned and inert', async () => {
  const animate = vi.fn(() => ({
    cancel: vi.fn(), finished: new Promise<void>(() => {}),
  }) as unknown as Animation)
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  const path = ref('/')
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({
    render: () => h(MotionTransition, null, () => h('section', { key: path.value, class: 'view' }, path.value)),
  })
  app.mount(host)
  expect(animate).not.toHaveBeenCalled()

  path.value = '/plugins'
  await nextTick()
  const [leaving, entering] = [...host.querySelectorAll<HTMLElement>('.view')] as [HTMLElement, HTMLElement]
  expect(leaving.textContent).toBe('/')
  expect(leaving.style.position).toBe('absolute')
  expect(leaving.inert).toBe(true)
  expect(entering.textContent).toBe('/plugins')
  expect(entering.style.position).toBe('')
  expect(animate).toHaveBeenCalledTimes(2)
  app.unmount()
  host.remove()
})

it('continues an interrupted entrance from its current frame instead of snapping to the end', async () => {
  vi.spyOn(window, 'getComputedStyle').mockImplementation(() => ({ opacity: '0.4', transform: 'none' }) as CSSStyleDeclaration)
  const animate = vi.fn(() => ({ cancel: vi.fn(), finished: new Promise<void>(() => {}) }))
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  const path = ref('/')
  const host = document.createElement('div')
  const app = createApp({
    render: () => h(MotionTransition, null, () => h('section', { key: path.value }, path.value)),
  })
  app.mount(host)
  path.value = '/a'
  await nextTick()
  const entering = animate.mock.contexts[1] as HTMLElement
  expect(entering.textContent).toBe('/a')
  path.value = '/b'
  await nextTick()
  const exitIndex = animate.mock.contexts.findIndex((context, index) => index > 1 && context === entering)
  expect((animate.mock.calls[exitIndex] as unknown as [Keyframe[]])[0][0]).toMatchObject({ opacity: '0.4' })
  app.unmount()
})

it('pins every item of a batch removal at the box it had before the first pin', () => {
  const { controller, app } = mountGridController()
  const items = gridItems(3)

  for (const item of items) controller.pinLeavingItem(item)

  expect(items.map(item => item.style.top)).toEqual(['0px', '100px', '200px'])
  app.unmount()
})

it('staggers grid entrances by their position in the list', () => {
  const { controller, app } = mountGridController()
  const items = gridItems(4)

  controller.enterItem(items[3]!, vi.fn())

  const [, timing] = vi.mocked(items[3]!.animate).mock.calls[0] as unknown as [Keyframe[], KeyframeAnimationOptions]
  expect(timing.delay).toBe(72)
  app.unmount()
})

it('still cancels a replacement grid animation when the controller unmounts', () => {
  const { controller, app } = mountGridController()
  const item = gridItems(1)[0]!
  const replacement = { cancel: vi.fn(), finished: new Promise<void>(() => {}) }
  vi.spyOn(window, 'getComputedStyle').mockImplementation(() => ({ opacity: '0.5', transform: 'none' }) as CSSStyleDeclaration)

  controller.enterItem(item, vi.fn())
  vi.mocked(item.animate).mockReturnValueOnce(replacement as unknown as Animation)
  controller.enterItem(item, vi.fn())
  app.unmount()

  expect(replacement.cancel).toHaveBeenCalledOnce()
})

it('skips layout reads and pinning for bulk grid removals beyond the animated window', () => {
  const { controller, app } = mountGridController()
  const node = document.createElement('div')
  node.dataset.motionIndex = '40'
  const rect = vi.spyOn(node, 'getBoundingClientRect')
  const done = vi.fn()
  controller.pinLeavingItem(node)
  controller.leaveItem(node, done)
  expect(rect).not.toHaveBeenCalled()
  expect(node.style.position).toBe('')
  expect(done).toHaveBeenCalledOnce()
  app.unmount()
})

/** One grid item with surface/content layers whose drawn box `place` moves. */
function reflowGrid(columns: () => number, width = () => 800) {
  const grid = document.createElement('div')
  Object.defineProperty(grid, 'clientWidth', { get: width })
  vi.spyOn(grid, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 800, 600))
  const { element: item, animation: itemAnimation } = fixture()
  const { element: surface, animate: surfaceAnimate } = fixture()
  const { element: content, animate: contentAnimate } = fixture()
  surface.dataset.reflow = 'surface'
  content.dataset.reflow = 'content'
  item.append(surface, content)
  grid.append(item)
  let box = new DOMRect(0, 0, 400, 200)
  vi.spyOn(surface, 'getBoundingClientRect').mockImplementation(() => box)
  vi.spyOn(item, 'getBoundingClientRect').mockImplementation(() => box)
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => ({
    gridTemplateColumns: element === grid ? Array(columns()).fill('100px').join(' ') : '',
    borderTopLeftRadius: '12px',
    borderRightWidth: '1px',
  }) as CSSStyleDeclaration)
  return {
    grid, item, itemAnimation, surfaceAnimate, contentAnimate,
    place: (next: DOMRect) => { box = next },
  }
}

it('glides grid items to their new slot when the column count changes', () => {
  const { controller, app } = mountGridController()
  let columns = 2
  const { grid, item, surfaceAnimate, contentAnimate, place } = reflowGrid(() => columns)

  controller.reflowColumns(grid)
  columns = 1
  place(new DOMRect(0, 216, 800, 200))
  controller.reflowColumns(grid)

  const frames = (animate: unknown) => (vi.mocked(animate as HTMLElement['animate']).mock.calls[0] as unknown as [Keyframe[]])[0]
  expect(frames(item.animate)[0]).toEqual({ transform: 'translate(0px, -216px)' })
  expect(frames(surfaceAnimate)[0]).toEqual({ transform: 'scale(0.5, 1)', borderRadius: '24px / 12px' })
  expect(frames(contentAnimate)[0]).toEqual({ clipPath: 'inset(0 401px 1px 0 round 12px)' })
  app.unmount()
})

it('glides a width step that keeps the column count, such as a drawer opening', () => {
  const { controller, app } = mountGridController()
  let width = 800
  const { grid, item, surfaceAnimate, place } = reflowGrid(() => 2, () => width)

  controller.reflowColumns(grid)
  width = 600
  place(new DOMRect(0, 0, 300, 200))
  controller.reflowColumns(grid)

  const [frames] = vi.mocked(surfaceAnimate).mock.calls[0] as unknown as [Keyframe[]]
  expect(item.animate).toHaveBeenCalledOnce()
  expect(frames[0]).toMatchObject({ transform: 'scale(1.3333333333333333, 1)' })
  app.unmount()
})

it('follows gradual widths that keep the column count, and never animates reduced motion', () => {
  const { controller, app } = mountGridController()
  let columns = 2
  let width = 800
  const { grid, item, place } = reflowGrid(() => columns, () => width)

  controller.reflowColumns(grid)
  for (const step of [760, 720, 680]) {
    width = step
    place(new DOMRect(0, 0, step / 2, 200))
    controller.reflowColumns(grid)
  }
  reduced = true
  columns = 1
  width = 400
  place(new DOMRect(0, 216, 400, 200))
  controller.reflowColumns(grid)

  expect(item.animate).not.toHaveBeenCalled()
  app.unmount()
})

it('reverses a glide from where the card is drawn, not from its last slot', () => {
  const { controller, app } = mountGridController()
  let columns = 2
  const { grid, item, itemAnimation, surfaceAnimate, place } = reflowGrid(() => columns)

  controller.reflowColumns(grid)
  columns = 1
  place(new DOMRect(0, 216, 800, 200))
  controller.reflowColumns(grid)
  // Halfway along that glide the layout reverts to the first slot, so the
  // drawn box is that slot shifted 108px up and scaled by the running glide.
  columns = 2
  place(new DOMRect(0, -108, 300, 200))
  // Settling the glide leaves the plain first slot.
  vi.mocked(itemAnimation.cancel).mockImplementationOnce(() => place(new DOMRect(0, 0, 400, 200)))
  controller.reflowColumns(grid)

  // It was drawn halfway at (0, 108) and 600px wide; the reversal starts there.
  const [frames] = vi.mocked(item.animate).mock.calls[1] as unknown as [Keyframe[]]
  const [surfaceFrames] = vi.mocked(surfaceAnimate).mock.calls[1] as unknown as [Keyframe[]]
  expect(frames[0]).toEqual({ transform: 'translate(0px, 108px)' })
  expect(surfaceFrames[0]).toMatchObject({ transform: 'scale(1.5, 1)' })
  app.unmount()
})


