// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cancelMotion } from './runtime'
import { holdLeave, onLayoutTransition, transitionLayout } from './scheduler'
import { motionPolicy } from './policy'

const boxes = new Map<Element, DOMRect>()
const styles = new Map<Element, Partial<CSSStyleDeclaration>>()
const animated: HTMLElement[] = []

/** Fixes the drawn/laid box of an element; the scheduler reads it through getBoundingClientRect. */
function place(element: Element, left: number, top: number, width: number, height: number) {
  boxes.set(element, { left, top, width, height, right: left + width, bottom: top + height, x: left, y: top } as DOMRect)
  element.getBoundingClientRect = () => boxes.get(element)!
}

/** Gives each animate call its own fake Animation that the test ends by hand. */
function animatable(element: HTMLElement) {
  animated.push(element)
  const animations: { cancel: ReturnType<typeof vi.fn>; finished: Promise<void>; end: () => void }[] = []
  const animate = vi.fn((...args: [Keyframe[], KeyframeAnimationOptions?]) => {
    void args
    let end!: () => void
    const animation = { cancel: vi.fn(), finished: new Promise<void>(resolve => { end = resolve }), end }
    animations.push(animation)
    return animation
  })
  element.animate = animate as unknown as HTMLElement['animate']
  return { animate, animations }
}

function scopeOf() {
  const scope = document.createElement('div')
  document.body.append(scope)
  place(scope, 0, 0, 800, 600)
  return scope
}

/** Lets finished-promise callbacks run. */
async function flush() {
  for (let index = 0; index < 3; index++) await Promise.resolve()
}

beforeEach(() => {
  boxes.clear()
  styles.clear()
  vi.useFakeTimers()
  document.body.replaceChildren()
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => (styles.get(element) ?? {}) as CSSStyleDeclaration)
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
})

afterEach(() => {
  animated.splice(0).forEach(cancelMotion)
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('moves an atom with a translate from its drawn box to its laid box', async () => {
  const scope = scopeOf()
  const atom = document.createElement('div')
  atom.dataset.motion = 'move'
  scope.append(atom)
  place(atom, 100, 50, 80, 40)
  const { animate } = animatable(atom)

  await transitionLayout(scope, () => { place(atom, 20, 50, 80, 40) })

  const [frames, timing] = animate.mock.calls[0] as unknown as [Keyframe[], KeyframeAnimationOptions]
  expect(frames).toEqual([{ transform: 'translate(80px, 0px)' }, { transform: 'none' }])
  expect(timing).toMatchObject({ duration: motionPolicy.reflowDuration, easing: motionPolicy.reflowEasing, delay: 0 })
})

it('compensates resize keyframes and animates label width so the footprint stays laid', async () => {
  const scope = scopeOf()
  const button = document.createElement('button')
  button.dataset.motion = 'resize'
  const label = document.createElement('span')
  label.dataset.motionLabel = ''
  button.append(label)
  scope.append(button)
  styles.set(button, { boxSizing: 'content-box', paddingLeft: '4px', paddingRight: '4px', marginRight: '8px' })
  styles.set(label, { marginLeft: '8px' })
  place(button, 100, 0, 100, 40)
  place(label, 0, 0, 40, 20)
  const { animate: buttonAnimate } = animatable(button)
  const { animate: labelAnimate } = animatable(label)

  await transitionLayout(scope, () => {
    place(button, 60, 0, 60, 40)
    place(label, 0, 0, 0, 20)
  })

  expect(buttonAnimate.mock.calls[0]?.[0]).toEqual([
    { transform: 'translate(40px, 0px)', minWidth: '92px', maxWidth: '92px', paddingLeft: '4px', paddingRight: '4px', marginRight: '-32px' },
    { transform: 'none', minWidth: '52px', maxWidth: '52px', paddingLeft: '4px', paddingRight: '4px', marginRight: '8px' },
  ])
  expect(labelAnimate.mock.calls[0]?.[0]).toEqual([
    { maxWidth: '40px', marginLeft: '8px', opacity: '1', offset: 0 },
    { opacity: '0', offset: 0.4 },
    { maxWidth: '0px', marginLeft: '8px', opacity: '0', offset: 1 },
  ])
})

it('slides a start panel in from offscreen when it was display none', async () => {
  const scope = scopeOf()
  const panel = document.createElement('aside')
  panel.dataset.motion = 'panel'
  panel.dataset.motionEdge = 'start'
  scope.append(panel)
  place(panel, 0, 0, 0, 0)
  const { animate } = animatable(panel)

  await transitionLayout(scope, () => { place(panel, 0, 0, 300, 600) })

  expect(animate.mock.calls[0]?.[0]).toEqual([{ transform: 'translateX(-300px)' }, { transform: 'none' }])
})

it('slides an end panel in from the right edge of the scope', async () => {
  const scope = scopeOf()
  const panel = document.createElement('aside')
  panel.dataset.motion = 'panel'
  panel.dataset.motionEdge = 'end'
  scope.append(panel)
  place(panel, 800, 0, 0, 0)
  const { animate } = animatable(panel)

  await transitionLayout(scope, () => { place(panel, 500, 0, 300, 600) })

  expect(animate.mock.calls[0]?.[0]).toEqual([{ transform: 'translateX(300px)' }, { transform: 'none' }])
})

it('holds a leaving panel until its slide-out finishes', async () => {
  const scope = scopeOf()
  const panel = document.createElement('aside')
  panel.dataset.motion = 'panel'
  panel.dataset.motionEdge = 'start'
  scope.append(panel)
  place(panel, 0, 0, 300, 600)
  const { animate, animations } = animatable(panel)
  const done = vi.fn()

  await transitionLayout(scope, () => {
    holdLeave(panel, done)
    place(panel, -50, 0, 300, 600)
  })

  expect(animate.mock.calls[0]?.[0]).toEqual([{ transform: 'translateX(50px)' }, { transform: 'translateX(-250px)' }])
  expect(done).not.toHaveBeenCalled()
  animations[0]!.end()
  await flush()
  expect(done).toHaveBeenCalledOnce()
})

it('keeps a held leave held when a second transition supersedes its slide-out', async () => {
  const scope = scopeOf()
  const panel = document.createElement('aside')
  panel.dataset.motion = 'panel'
  panel.dataset.motionEdge = 'start'
  scope.append(panel)
  place(panel, 0, 0, 300, 600)
  const { animations } = animatable(panel)
  const done = vi.fn()
  await transitionLayout(scope, () => {
    holdLeave(panel, done)
    place(panel, -50, 0, 300, 600)
  })

  await transitionLayout(scope, () => {})

  expect(animations[0]!.cancel).toHaveBeenCalledOnce()
  expect(done).not.toHaveBeenCalled()
  expect(animations).toHaveLength(2)
  animations[1]!.end()
  await flush()
  expect(done).toHaveBeenCalledOnce()
})

it('draws a skin from its old box with a translate and scale, counter-scaling its corners', async () => {
  const scope = scopeOf()
  const skin = document.createElement('div')
  skin.dataset.motion = 'skin'
  scope.append(skin)
  styles.set(skin, { borderTopLeftRadius: '12px' })
  place(skin, 400, 20, 400, 100)
  const { animate } = animatable(skin)

  await transitionLayout(scope, () => { place(skin, 0, 20, 800, 50) })

  expect(animate.mock.calls[0]?.[0]).toEqual([
    { transformOrigin: '0 0', transform: 'translate(400px, 0px) scale(0.5, 2)', borderRadius: '24px / 6px' },
    { transformOrigin: '0 0', transform: 'translate(0px, 0px) scale(1, 1)', borderRadius: '12px / 12px' },
  ])
})

it('marks the scope while its animations run and clears the mark when they end', async () => {
  const scope = scopeOf()
  const atom = document.createElement('div')
  atom.dataset.motion = 'move'
  scope.append(atom)
  place(atom, 100, 0, 80, 40)
  const { animations } = animatable(atom)

  await transitionLayout(scope, () => { place(atom, 20, 0, 80, 40) })
  expect(scope.hasAttribute('data-motion-running')).toBe(true)

  animations[0]!.end()
  await flush()
  expect(scope.hasAttribute('data-motion-running')).toBe(false)
})

it('ignores atoms nested inside another atom of the scope', async () => {
  const scope = scopeOf()
  const outer = document.createElement('div')
  outer.dataset.motion = 'move'
  const inner = document.createElement('div')
  inner.dataset.motion = 'move'
  outer.append(inner)
  scope.append(outer)
  place(outer, 0, 0, 100, 100)
  place(inner, 10, 10, 20, 20)
  const { animate: outerAnimate } = animatable(outer)
  const { animate: innerAnimate } = animatable(inner)

  await transitionLayout(scope, () => {
    place(outer, 50, 0, 100, 100)
    place(inner, 60, 10, 20, 20)
  })

  expect(outerAnimate).toHaveBeenCalledOnce()
  expect(innerAnimate).not.toHaveBeenCalled()
})

it('runs layout hooks before mutate', async () => {
  const scope = scopeOf()
  const order: string[] = []
  const unregister = onLayoutTransition(() => { order.push('hook') })

  await transitionLayout(scope, () => { order.push('mutate') })
  unregister()

  expect(order).toEqual(['hook', 'mutate'])
})

it('calls what a hook returns after mutate, before the atoms animate', async () => {
  const scope = scopeOf()
  const atom = document.createElement('div')
  atom.dataset.motion = 'move'
  scope.append(atom)
  place(atom, 0, 0, 10, 10)
  const order: string[] = []
  const { animate } = animatable(atom)
  animate.mockImplementationOnce(() => {
    order.push('animate')
    return { cancel: vi.fn(), finished: new Promise<void>(() => {}), end: () => {} }
  })
  const unregister = onLayoutTransition(() => () => { order.push('laid out') })

  await transitionLayout(scope, () => {
    order.push('mutate')
    place(atom, 40, 0, 10, 10)
  })
  unregister()

  expect(order).toEqual(['mutate', 'laid out', 'animate'])
})

it('only mutates, and releases held leaves, when motion is suppressed', async () => {
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
  const scope = scopeOf()
  const panel = document.createElement('aside')
  panel.dataset.motion = 'panel'
  panel.dataset.motionEdge = 'start'
  scope.append(panel)
  place(panel, 0, 0, 300, 600)
  const { animate } = animatable(panel)
  const done = vi.fn()
  const mutate = vi.fn(() => { holdLeave(panel, done) })

  await transitionLayout(scope, mutate)

  expect(mutate).toHaveBeenCalledOnce()
  expect(animate).not.toHaveBeenCalled()
  expect(done).toHaveBeenCalledOnce()
  expect(scope.hasAttribute('data-motion-running')).toBe(false)
})
