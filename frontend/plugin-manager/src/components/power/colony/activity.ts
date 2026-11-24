/**
 * What the timeline of the task agents says, worked out for the pages.
 *
 * The host gives what happened, in order (`getTimeline`), and who waits for
 * whom now (`getWaits`). How the familiars stood at an earlier moment is
 * not kept anywhere: it is worked out here from the entries up to that
 * moment, for replaying them. An entry of a kind this page does not know
 * changes nothing and is listed by the name of its kind.
 */
import type { TimelineEntry } from '@/api/console'

/** How a familiar is drawn. */
export type CatLook =
  | 'working' | 'waiting_user' | 'idle' | 'asleep' | 'compacting' | 'resting' | 'queued'
  | 'landing' | 'resolving' | 'held' | 'unlanded'

export interface SceneCat {
  look: CatLook
  /** What it said last, or was asked; shown above it for a while. */
  said: string
  saidAt: number
}

/** How long what a familiar said stays above it, in seconds of the timeline. */
export const SAID_SECONDS = 30

const SAYS = new Set(['reported', 'message', 'asked_session', 'asked_user'])

function put(cats: Map<string, SceneCat>, id: string | null | undefined, look?: CatLook): SceneCat | null {
  if (!id) return null
  let cat = cats.get(id)
  if (!cat) {
    cat = { look: look ?? 'idle', said: '', saidAt: 0 }
    cats.set(id, cat)
  } else if (look) {
    cat.look = look
  }
  return cat
}

/**
 * Return how each session stood once the entries up to `index` had happened.
 *
 * A session that no entry up to there names is not in the answer: whoever
 * draws it decides whether it was there already. What was asked through a
 * tool is not recorded as answered; the commission going on (`started`)
 * says that it was. A session is at one commission at a time: another that
 * waits for its turn in it, or is called off there, leaves it as it stands.
 */
export function sceneAt(entries: TimelineEntry[], index: number): Map<string, SceneCat> {
  const cats = new Map<string, SceneCat>()
  // The commission each session is at, by session.
  const atWork = new Map<string, string | null>()
  const busyWithAnother = (entry: TimelineEntry) =>
    Boolean(entry.session_id) && atWork.has(entry.session_id as string) && atWork.get(entry.session_id as string) !== entry.commission_id
  const last = Math.min(index, entries.length - 1)
  for (let at = 0; at <= last; at++) {
    const entry = entries[at]
    const id = entry.session_id
    switch (entry.kind) {
      case 'filed': put(cats, id); break
      case 'queued': put(cats, id, busyWithAnother(entry) ? undefined : 'queued'); break
      case 'started':
        put(cats, id, 'working')
        if (id) atWork.set(id, entry.commission_id)
        break
      case 'asked_user': put(cats, id, 'waiting_user'); break
      case 'resting': put(cats, id, 'resting'); break
      case 'rest_over': put(cats, id, 'idle'); break
      case 'ended':
        if (busyWithAnother(entry)) break
        // Stuck over what it changed in its worktree, which waits there for the user: drawn so.
        if (entry.detail?.outcome !== 'blocked' || !['held', 'unlanded'].includes(cats.get(id ?? '')?.look ?? '')) {
          put(cats, id, 'idle')
        }
        if (id) atWork.delete(id)
        break
      // What a familiar changed in a worktree of its own: merged where it clashed, left for the user.
      case 'landing_conflict': put(cats, id, busyWithAnother(entry) ? undefined : 'resolving'); break
      case 'not_landed': put(cats, id, busyWithAnother(entry) ? undefined : 'unlanded'); break
      case 'landing_held': put(cats, id, busyWithAnother(entry) ? undefined : 'held'); break
      // The user decided on it, maybe long after: of one at another commission by then, that one says how it stands.
      case 'landed': case 'tree_discarded': case 'tree_kept':
        if (!busyWithAnother(entry)) {
          const cat = put(cats, id)
          if (cat && ['resolving', 'held', 'unlanded'].includes(cat.look)) cat.look = atWork.has(id ?? '') ? 'working' : 'idle'
        }
        break
      case 'child_returned': case 'ask_answered': put(cats, id, 'idle'); break
      case 'child_started':
        put(cats, id)
        // The one it started is there from now on.
        if (entry.peer?.type === 'session') put(cats, entry.peer.session_id)
        break
      // Any other kind, known or not, leaves everyone as they stood.
      default: break
    }
    if (SAYS.has(entry.kind) && entry.text) {
      const cat = put(cats, id)
      if (cat) {
        cat.said = entry.text
        cat.saidAt = entry.at
      }
    }
  }
  // What was said long before the moment shown is not above it any more.
  const now = last >= 0 ? entries[last].at : 0
  for (const cat of cats.values()) {
    if (cat.said && cat.look !== 'waiting_user' && now - cat.saidAt > SAID_SECONDS) cat.said = ''
  }
  return cats
}

/**
 * Return the sessions that were not there before the entries came to them, each with where
 * it came in.
 *
 * `whole` says the entries begin where the record begins: whoever they name was not there
 * before it was first named. Where they begin later, only one that another started among
 * them is known to be new; one that is merely named may have stood by all along.
 */
export function firstSeen(entries: TimelineEntry[], whole: boolean): Map<string, number> {
  const seen = new Map<string, number>()
  entries.forEach((entry, index) => {
    for (const id of [whole ? entry.session_id : null, entry.kind === 'child_started' ? entry.peer?.session_id : null]) {
      if (id && !seen.has(id)) seen.set(id, index)
    }
  })
  return seen
}

/** A familiar the timeline names that is not on the board any more: what is known of it. */
export interface Ghost {
  id: string
  name: string
  preset: string
  project: string | null
  parent: string | null
}

/** Return the sessions the entries name that `known` does not hold, with what the entries say of them. */
export function ghostsOf(entries: TimelineEntry[], known: Set<string>): Ghost[] {
  const found = new Map<string, Ghost>()
  const note = (id: string | null | undefined, project: string | null): Ghost | null => {
    if (!id || known.has(id)) return null
    let ghost = found.get(id)
    if (!ghost) {
      ghost = { id, name: '', preset: '', project, parent: null }
      found.set(id, ghost)
    }
    return ghost
  }
  for (const entry of entries) {
    const own = note(entry.session_id, entry.project)
    if (own && !own.name && entry.kind === 'filed') own.name = entry.text
    if (entry.kind === 'child_started' && entry.peer?.type === 'session') {
      const child = note(entry.peer.session_id, entry.project)
      if (child) {
        child.name = entry.text || child.name
        child.preset = typeof entry.detail?.preset === 'string' ? entry.detail.preset : child.preset
        child.parent = entry.session_id
      }
    }
  }
  return [...found.values()]
}

/**
 * Return whether the list of what happened has a line for an entry.
 *
 * Where each commission of hers goes is decided as it comes (`orchestrated`),
 * and the lines of the commission itself say where it went: that entry is kept
 * for replaying and looking into what was decided, and is told only when the
 * commission went elsewhere than was decided. The host says where only then
 * (`placed`), also where it went on as decided, but in another familiar.
 */
export function toldInFeed(entry: TimelineEntry): boolean {
  if (entry.kind !== 'orchestrated') return true
  const placed = entry.detail?.placed
  return typeof placed === 'string' && placed !== ''
}

type Translate = (key: string, named?: Record<string, unknown>) => string
type Exists = (key: string) => boolean

/**
 * Return one line for an entry: who did what to whom, in the words of the user's language.
 *
 * `nameOf` gives the name a session is shown by. An entry of a kind the
 * page has no words for is told by the name of its kind.
 */
export function describeEntry(
  entry: TimelineEntry, nameOf: (sessionId: string | null | undefined) => string, t: Translate, te: Exists,
): string {
  const who = nameOf(entry.session_id)
  const peer = entry.peer?.type === 'user' ? t('power.activity.you') : nameOf(entry.peer?.session_id)
  let text = entry.text
  if (entry.kind === 'asked_user' && entry.detail?.question_kind === 'permission') {
    // A request for approval is recorded by its kind (and, of an MCP server, which): said in words here.
    const [category, ...rest] = text.split(':')
    const named = [`power.runtime.permissions.categories.${category}`, `power.ask.categories.${category}`].find(key => te(key))
    if (named) text = [t(named), ...rest].join(': ')
  }
  let key = `power.activity.kinds.${entry.kind}`
  // How it ended is part of what is said: done, failed, stopped, stuck. Of a project that got its
  // leader, or of what a familiar changed that was not merged, why; of an inspection, what it found;
  // of a landing, whether the user had it done.
  const how = entry.detail?.[VARIANT_OF[entry.kind] ?? 'outcome']
  const outcome = how === true ? 'user' : typeof how === 'string' ? how : ''
  if (outcome && te(`${key}_${outcome}`)) key = `${key}_${outcome}`
  if (!te(key)) return [who, entry.kind, text].filter(Boolean).join(' · ')
  const line = t(key, { who, peer, text, ...figures(entry, t, te) }).replace(/[\s：:]+$/u, '')
  // Of a landing: what landed outside the project's directory was not asked about; what was not
  // carried over stays in the worktree. No other kind says either.
  const told = [line]
  for (const [field, words] of [['outside', 'power.activity.landedOutside'], ['skipped', 'power.activity.landedSkipped']] as const) {
    const files = entry.detail?.[field]
    if (Array.isArray(files) && files.length) told.push(t(words, { count: files.length }))
  }
  return told.join(' ')
}

// Which field of its `detail` tells the words of an entry apart; `outcome` for the others.
const VARIANT_OF: Record<string, string> = {
  leader_derived: 'reason', not_landed: 'reason', inspected: 'verdict', landed: 'by_user', landing_conflict: 'by_user',
}

/** Return what the words of an entry about worktrees and teams tell besides who: how many files, lines, members. */
function figures(entry: TimelineEntry, t: Translate, te: Exists): Record<string, unknown> {
  const detail = entry.detail ?? {}
  const number = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : 0)
  const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
  const members = list(detail.members).filter((item): item is { name?: unknown; preset?: unknown } => !!item && typeof item === 'object')
  return {
    files: list(detail.files).length + number(detail.more),
    added: number(detail.lines_added),
    deleted: number(detail.lines_deleted),
    conflicts: list(detail.conflicts).length,
    round: number(detail.round),
    // Of a team, how many and what kinds; of a reminder, how many it had.
    count: entry.kind === 'team_formed' ? members.length : number(detail.members),
    names: members
      .map(item => {
        const preset = typeof item.preset === 'string' ? item.preset : ''
        const named = `power.home.presets.${preset}`
        return te(named) ? t(named) : typeof item.name === 'string' ? item.name : preset
      })
      .filter(Boolean)
      .join(' · '),
  }
}

/** Return a time of day for the list: hours and minutes, with seconds when `seconds` is set. */
export function clockTime(at: number, seconds = false): string {
  const date = new Date(at * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  const base = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  return seconds ? `${base}:${pad(date.getSeconds())}` : base
}
