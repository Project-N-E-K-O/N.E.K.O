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
export type CatLook = 'working' | 'waiting_user' | 'idle' | 'asleep' | 'compacting' | 'resting' | 'queued'

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
        put(cats, id, 'idle')
        if (id) atWork.delete(id)
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
  // How it ended is part of what is said: done, failed, stopped, stuck.
  const outcome = typeof entry.detail?.outcome === 'string' ? entry.detail.outcome : ''
  if (outcome && te(`${key}_${outcome}`)) key = `${key}_${outcome}`
  if (!te(key)) return [who, entry.kind, text].filter(Boolean).join(' · ')
  return t(key, { who, peer, text }).replace(/[\s：:]+$/u, '')
}

/** Return a time of day for the list: hours and minutes, with seconds when `seconds` is set. */
export function clockTime(at: number, seconds = false): string {
  const date = new Date(at * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  const base = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  return seconds ? `${base}:${pad(date.getSeconds())}` : base
}
