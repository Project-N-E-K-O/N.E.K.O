/**
 * Where the familiars of a territory stand.
 *
 * A territory is a project, or the loose commissions that belong to none.
 * A spot is a place within it, both coordinates between 0 and 1. Nothing is
 * drawn between the familiars: who belongs to whom is told by where they
 * stand, so one that another started stands close to it, and a leader, where
 * there is one, in the middle. A territory need not have a leader, and may
 * hold any number of familiars of its own.
 *
 * Where the user dragged a familiar to is kept in the browser's own storage
 * for this computer; nothing of it goes to the host.
 */

export interface Spot { x: number; y: number }

/** What standing somewhere takes of a familiar. */
export interface Stander { id: string; leader: boolean; parent: string | null }

const CENTRE: Spot = { x: 0.5, y: 0.52 }
// The half axes of the oval familiars stay within, of the territory's box.
const REACH_X = 0.4
const REACH_Y = 0.34
// How far one that another started stands from it: a familiar is drawn about a quarter of a territory wide.
const CHILD_X = 0.27
const CHILD_Y = 0.3

/** Whether a spot lies within the oval of a territory. */
export function inTerritory(spot: Spot): boolean {
  return ((spot.x - CENTRE.x) / REACH_X) ** 2 + ((spot.y - CENTRE.y) / REACH_Y) ** 2 <= 1
}

/** Return the spot within the territory that is nearest to `spot` on the line to its middle. */
export function intoTerritory(spot: Spot): Spot {
  const dx = (spot.x - CENTRE.x) / REACH_X
  const dy = (spot.y - CENTRE.y) / REACH_Y
  const far = Math.hypot(dx, dy)
  if (far <= 1) return spot
  // A hair inside the edge, so that rounding never leaves it outside.
  const reach = 0.999 / far
  return { x: CENTRE.x + dx * reach * REACH_X, y: CENTRE.y + dy * reach * REACH_Y }
}

function around(centre: Spot, count: number, radiusX: number, radiusY: number, turn: number): Spot[] {
  return Array.from({ length: count }, (_, index) => {
    const angle = turn + (index / Math.max(count, 1)) * Math.PI * 2
    return { x: centre.x + Math.cos(angle) * radiusX, y: centre.y + Math.sin(angle) * radiusY }
  })
}

/**
 * Return where each familiar of a territory stands when the user put it nowhere.
 *
 * A single leader stands in the middle and the others of the territory's
 * own around it; with no leader, or several, they all stand around the
 * middle (one alone stands in it). One that another started stands beside
 * that one, on the side away from the middle.
 */
export function defaultSpots(members: Stander[]): Record<string, Spot> {
  const ids = new Set(members.map(member => member.id))
  const roots = members.filter(member => !member.parent || !ids.has(member.parent))
  const spots: Record<string, Spot> = {}
  const leaders = roots.filter(member => member.leader)
  const middle = leaders.length === 1 ? leaders[0] : roots.length === 1 ? roots[0] : null
  if (middle) spots[middle.id] = { ...CENTRE }
  const ring = roots.filter(member => member !== middle)
  // Several rings once one is full: eight stand on the first. Begun at the left, so that two stand side by side.
  ring.forEach((member, index) => {
    const lap = Math.floor(index / 8)
    const onLap = Math.min(8, ring.length - lap * 8)
    const grow = 1 + lap * 0.45
    const [spot] = around(CENTRE, onLap, 0.26 * grow, 0.22 * grow, Math.PI + lap * 0.4).slice(index % 8)
    spots[member.id] = intoTerritory(spot)
  })
  // Those another one started, beside it: breadth first, so each finds its starter placed.
  let placed = true
  while (placed) {
    placed = false
    for (const parent of members) {
      const base = spots[parent.id]
      if (!base) continue
      const children = members.filter(member => member.parent === parent.id && !spots[member.id])
      if (!children.length) continue
      placed = true
      const outward = Math.atan2(base.y - CENTRE.y, base.x - CENTRE.x)
      const atMiddle = Math.hypot(base.x - CENTRE.x, base.y - CENTRE.y) < 0.02
      children.forEach((child, index) => {
        let angle: number
        if (!atMiddle) {
          // Fanned out behind one that stands off the middle.
          angle = outward + (index - (children.length - 1) / 2) * 0.8
        } else if (children.length <= 4) {
          // Below the one in the middle, side by side: the room is wider than it is high.
          const step = Math.min(0.5, 0.9 / Math.max(children.length - 1, 1))
          angle = Math.PI * (0.5 + (index - (children.length - 1) / 2) * step)
        } else {
          angle = Math.PI / 2 + (index / children.length) * Math.PI * 2
        }
        const reach = atMiddle ? 1 : 0.7
        spots[child.id] = intoTerritory({
          x: base.x + Math.cos(angle) * CHILD_X * reach, y: base.y + Math.sin(angle) * CHILD_Y * reach,
        })
      })
    }
  }
  // Whoever is left (a starter that is not among them any more): around the edge.
  const left = members.filter(member => !spots[member.id])
  around(CENTRE, left.length, REACH_X * 0.9, REACH_Y * 0.9, Math.PI / 5).forEach((spot, index) => {
    spots[left[index].id] = spot
  })
  return spots
}

/** How large a territory is drawn, in pixels, for so many familiars. */
export function territorySize(count: number): { width: number; height: number } {
  return {
    width: Math.round(Math.min(760, 320 + Math.max(0, count - 1) * 70)),
    height: Math.round(Math.min(460, 250 + Math.max(0, count - 1) * 36)),
  }
}

const STORE_KEY = 'neko-power-colony-spots'

type Kept = Record<string, Record<string, Spot>>

function readKept(storage: Pick<Storage, 'getItem'>): Kept {
  try {
    const raw = JSON.parse(storage.getItem(STORE_KEY) || '{}') as unknown
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
    const kept: Kept = {}
    for (const [territory, spots] of Object.entries(raw as Record<string, unknown>)) {
      if (!spots || typeof spots !== 'object' || Array.isArray(spots)) continue
      const good: Record<string, Spot> = {}
      for (const [id, spot] of Object.entries(spots as Record<string, unknown>)) {
        const { x, y } = (spot ?? {}) as { x?: unknown; y?: unknown }
        if (typeof x === 'number' && typeof y === 'number' && Number.isFinite(x) && Number.isFinite(y)) {
          good[id] = intoTerritory({ x, y })
        }
      }
      kept[territory] = good
    }
    return kept
  } catch {
    // What is there is not what this page wrote: as if nothing were kept.
    return {}
  }
}

/** Return where the user put the familiars of a territory, by familiar. */
export function keptSpots(territory: string, storage: Pick<Storage, 'getItem'> = localStorage): Record<string, Spot> {
  return readKept(storage)[territory] ?? {}
}

/**
 * Keep where the user put the familiars of a territory; `present` are the
 * familiars it has now, and what is kept of any other is dropped. An empty
 * `spots` forgets the territory.
 */
export function keepSpots(
  territory: string, spots: Record<string, Spot>, present: Iterable<string>,
  storage: Pick<Storage, 'getItem' | 'setItem'> = localStorage,
): void {
  const kept = readKept(storage)
  const here = new Set(present)
  const good = Object.fromEntries(Object.entries(spots).filter(([id]) => here.has(id)))
  if (Object.keys(good).length) kept[territory] = good
  else delete kept[territory]
  try {
    storage.setItem(STORE_KEY, JSON.stringify(kept))
  } catch {
    // Storage that is full or switched off: they stand where they were put until the page is left.
  }
}
