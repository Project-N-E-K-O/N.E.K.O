import { motionPolicy } from './policy'
import { cancelMotion, counterScaledRadius, motionSuppressed, playFrames } from './runtime'

type Box = Pick<DOMRect, 'left' | 'top' | 'right' | 'bottom' | 'width' | 'height'>
/** Values read from one element in one pass, copied out of live computed style. */
type Metrics = {
  box: Box
  boxSizing: string
  paddingLeft: number
  paddingRight: number
  borderX: number
  marginLeft: number
  marginRight: number
}
type Atom = { element: HTMLElement; metrics: Metrics; labels: Map<HTMLElement, Metrics> }
type Plan = { element: HTMLElement; frames: Keyframe[]; leave?: boolean }
type Run = {
  scope: HTMLElement
  started: HTMLElement[]
  leaving: Set<Element>
  running: number
  superseded: boolean
}

const motionAttribute = 'data-motion-running'
type LayoutHook = () => (() => void) | void
const hooks = new Set<LayoutHook>()
/** Leaving elements kept in the DOM until their slide-out ends, with the Vue done that removes them. */
const held = new Map<Element, () => void>()
let mutating = 0
let latest: Run | undefined

/** Registers a hook called right before each transition's mutate. A function it
 * returns is called once the end state is laid out, before the atoms animate,
 * so its own animations start in the same frame. */
export function onLayoutTransition(hook: LayoutHook) {
  hooks.add(hook)
  return () => { hooks.delete(hook) }
}

/** Keeps a leaving panel shown during the current transition's slide-out. */
export function holdLeave(element: Element, done: () => void) {
  if (mutating) held.set(element, done)
  else queueMicrotask(done)
}

/** A held panel shown again before its slide-out ended (Vue's leave-cancelled):
 * it is no longer leaving, so the next plan moves it back instead of out. */
export function cancelLeave(element: Element) {
  held.delete(element)
}

/** Runs `mutate` and animates every atom in `scope` from where it was drawn to its laid place. */
export async function transitionLayout(scope: HTMLElement, mutate: () => void | Promise<void>) {
  const laidOut = [...hooks].flatMap(hook => hook() ?? [])
  const first = motionSuppressed() ? undefined : snapshot(scope)
  const run: Run = { scope, started: [], leaving: new Set(), running: 0, superseded: false }
  supersede(run)
  // Lay the end state out with the scope's transition styles on (a drawer
  // panel slides outside its rail), so it is measured where it animates.
  if (first) scope.setAttribute(motionAttribute, '')
  mutating++
  try {
    await mutate()
  } finally {
    mutating--
  }
  if (run !== latest) return
  const plan = first ? planTransition(scope, first, snapshot(scope)) : []
  for (const after of laidOut) after()
  launch(run, plan)
  releaseUnanimated(run)
}

/** Makes `run` the newest transition. The previous run's animations are cancelled
 * without releasing its held leaves; the new run decides their fate. */
function supersede(run: Run) {
  const previous = latest
  latest = run
  if (!previous) return
  previous.superseded = true
  previous.scope.removeAttribute(motionAttribute)
  for (const element of previous.started) cancelMotion(element)
}

/** Starts every planned animation in one synchronous pass with the same timing. */
function launch(run: Run, plans: Plan[]) {
  run.running = plans.length
  if (!plans.length) run.scope.removeAttribute(motionAttribute)
  for (const plan of plans) {
    run.started.push(plan.element)
    if (plan.leave) run.leaving.add(plan.element)
    playFrames(plan.element, plan.frames, {
      duration: motionPolicy.reflowDuration,
      easing: motionPolicy.reflowEasing,
    }, () => settle(run, plan))
  }
}

function settle(run: Run, plan: Plan) {
  if (plan.leave && !run.superseded) release(plan.element)
  run.running--
  if (!run.running && latest === run) run.scope.removeAttribute(motionAttribute)
}

/** Releases held leaves that the newest run did not animate, since nothing else will end them. */
function releaseUnanimated(run: Run) {
  for (const element of [...held.keys()]) if (!run.leaving.has(element)) release(element)
}

function release(element: Element) {
  const done = held.get(element)
  held.delete(element)
  done?.()
}

/** Atoms of `scope` that are not nested inside another atom of it. */
function atomsIn(scope: HTMLElement) {
  return Array.from(scope.querySelectorAll<HTMLElement>('[data-motion]')).filter(element => {
    const owner = element.parentElement?.closest('[data-motion]')
    return !owner || owner === scope || !scope.contains(owner)
  })
}

function pixels(value: string) {
  return parseFloat(value) || 0
}

function measure(element: HTMLElement): Metrics {
  const style = getComputedStyle(element)
  const { left, top, right, bottom, width, height } = element.getBoundingClientRect()
  return {
    box: { left, top, right, bottom, width, height },
    boxSizing: style.boxSizing,
    paddingLeft: pixels(style.paddingLeft),
    paddingRight: pixels(style.paddingRight),
    borderX: pixels(style.borderLeftWidth) + pixels(style.borderRightWidth),
    marginLeft: pixels(style.marginLeft),
    marginRight: pixels(style.marginRight),
  }
}

function snapshot(scope: HTMLElement) {
  const atoms = new Map<HTMLElement, Atom>()
  for (const element of atomsIn(scope)) {
    const labels = new Map<HTMLElement, Metrics>()
    for (const child of Array.from(element.children)) {
      if (child instanceof HTMLElement && child.hasAttribute('data-motion-label')) labels.set(child, measure(child))
    }
    atoms.set(element, { element, metrics: measure(element), labels })
  }
  return atoms
}

const isEmpty = (box: Box) => box.width === 0 && box.height === 0
const near = (a: number, b: number) => Math.abs(a - b) <= 0.5
const offset = (dx: number, dy: number) => `translate(${dx}px, ${dy}px)`

function planTransition(scope: HTMLElement, first: Map<HTMLElement, Atom>, last: Map<HTMLElement, Atom>): Plan[] {
  const scopeBox = scope.getBoundingClientRect()
  return [...last.values()].flatMap(atom => planAtom(atom, first.get(atom.element), scopeBox))
}

function planAtom(last: Atom, first: Atom | undefined, scopeBox: Box): Plan[] {
  const { element } = last
  const box = last.metrics.box
  if (isEmpty(box)) return []
  if (!first || isEmpty(first.metrics.box)) return planEnter(element, box, scopeBox)
  if (held.has(element)) return planLeave(element, first.metrics.box, box, scopeBox)
  switch (element.dataset.motion) {
    case 'move':
    case 'panel':
      return planMove(element, first.metrics.box, box)
    case 'resize':
      return planResize(last, first)
    case 'skin':
      return planSkin(element, first.metrics.box, box)
    default:
      return []
  }
}

/** Horizontal offset that parks a drawer panel just outside its scope edge. */
function panelOffset(element: HTMLElement, box: Box, scopeBox: Box): number | undefined {
  if (element.dataset.motion !== 'panel') return undefined
  if (element.dataset.motionEdge === 'start') return -(box.right - scopeBox.left)
  if (element.dataset.motionEdge === 'end') return scopeBox.right - box.left
  return undefined
}

function planEnter(element: HTMLElement, box: Box, scopeBox: Box): Plan[] {
  const hidden = panelOffset(element, box, scopeBox)
  if (hidden === undefined || near(hidden, 0)) return []
  return [{ element, frames: [{ transform: `translateX(${hidden}px)` }, { transform: 'none' }] }]
}

function planLeave(element: HTMLElement, drawn: Box, box: Box, scopeBox: Box): Plan[] {
  const hidden = panelOffset(element, box, scopeBox)
  const from = drawn.left - box.left
  if (hidden === undefined || near(from, hidden)) return []
  return [{
    element,
    frames: [{ transform: `translateX(${from}px)` }, { transform: `translateX(${hidden}px)` }],
    leave: true,
  }]
}

function planMove(element: HTMLElement, drawn: Box, box: Box): Plan[] {
  const dx = drawn.left - box.left
  const dy = drawn.top - box.top
  if (near(dx, 0) && near(dy, 0)) return []
  return [{ element, frames: [{ transform: offset(dx, dy) }, { transform: 'none' }] }]
}

function planResize(last: Atom, first: Atom): Plan[] {
  const { element } = last
  const drawn = first.metrics.box
  const box = last.metrics.box
  const plans = near(drawn.width, box.width)
    ? planMove(element, drawn, box)
    : [{
      element,
      frames: [
        {
          transform: offset(drawn.left - box.left, drawn.top - box.top),
          ...sizeFrame(first.metrics, drawn.width, last.metrics.marginRight + box.width - drawn.width),
        },
        { transform: 'none', ...sizeFrame(last.metrics, box.width, last.metrics.marginRight) },
      ],
    }]
  for (const [label, labelMetrics] of last.labels) {
    const before = first.labels.get(label)
    const frames = before && labelFrames(before, labelMetrics)
    if (frames) plans.push({ element: label, frames })
  }
  return plans
}

/** Width keyframe for a border box `width` wide. Content-box elements get their
 * content width; `marginRight` keeps the flex footprint the laid one. */
function sizeFrame(metrics: Metrics, width: number, marginRight: number): Keyframe {
  const inner = metrics.boxSizing === 'content-box'
    ? width - metrics.paddingLeft - metrics.paddingRight - metrics.borderX
    : width
  return {
    minWidth: `${inner}px`,
    maxWidth: `${inner}px`,
    paddingLeft: `${metrics.paddingLeft}px`,
    paddingRight: `${metrics.paddingRight}px`,
    marginRight: `${marginRight}px`,
  }
}

/** A label collapses or expands its width; opacity fades out early on collapse and in late on expand. */
function labelFrames(first: Metrics, last: Metrics): Keyframe[] | undefined {
  const from = first.box.width
  const to = last.box.width
  if (near(from, to)) return undefined
  const start = { maxWidth: `${from}px`, marginLeft: `${first.marginLeft}px` }
  const end = { maxWidth: `${to}px`, marginLeft: `${last.marginLeft}px` }
  if (to < from) {
    return [
      { ...start, opacity: '1', offset: 0 },
      { opacity: '0', offset: 0.4 },
      { ...end, opacity: '0', offset: 1 },
    ]
  }
  return [
    { ...start, opacity: '0', offset: 0 },
    { opacity: '0', offset: 0.6 },
    { ...end, opacity: '1', offset: 1 },
  ]
}

/** A skin stays laid in its end box and is drawn from its old box by a
 * translate and scale from its top-left corner, so the compositor runs it
 * without a layout per frame. Borders and corners scale with it while it moves. */
function planSkin(element: HTMLElement, drawn: Box, box: Box): Plan[] {
  if (!box.width || !box.height) return []
  const dx = drawn.left - box.left
  const dy = drawn.top - box.top
  const sx = drawn.width / box.width
  const sy = drawn.height / box.height
  if (near(dx, 0) && near(dy, 0) && near(drawn.width, box.width) && near(drawn.height, box.height)) return []
  // Corners keep their drawn radius; animating it costs a repaint, not a layout.
  const radius = pixels(getComputedStyle(element).borderTopLeftRadius)
  const corners = (x: number, y: number): Keyframe => (radius ? { borderRadius: counterScaledRadius(radius, x, y) } : {})
  return [{
    element,
    frames: [
      { transformOrigin: '0 0', transform: `${offset(dx, dy)} scale(${sx}, ${sy})`, ...corners(sx, sy) },
      { transformOrigin: '0 0', transform: 'translate(0px, 0px) scale(1, 1)', ...corners(1, 1) },
    ],
  }]
}
