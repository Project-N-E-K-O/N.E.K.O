/** A flat JSON outline avoids recursive Vue component trees. Collapsed ranges
 * can be skipped without losing searchable values or the original copy text. */
export interface JsonLine {
  id: number
  parent: number | null
  depth: number
  key: string | null
  indexKey: boolean
  kind: 'open' | 'close' | 'string' | 'number' | 'boolean' | 'null'
  text: string
  suffix: string
  end: number
  count: number
}

export function buildJsonLines(value: unknown): JsonLine[] {
  const lines: JsonLine[] = []
  type Entry = {
    value: unknown
    parent: number | null
    depth: number
    key: string | null
    indexKey: boolean
    suffix: string
  }
  type Work = Entry | { close: number }
  const work: Work[] = [{ value, parent: null, depth: 0, key: null, indexKey: false, suffix: '' }]
  while (work.length) {
    const entry = work.pop()!
    if ('close' in entry) {
      const open = lines[entry.close]!
      lines.push({
        ...open,
        id: lines.length,
        parent: open.id,
        key: null,
        kind: 'close',
        text: open.text === '[' ? ']' : '}',
        count: 0,
        end: lines.length + 1,
      })
      open.end = lines.length
      continue
    }
    const container = entry.value !== null && typeof entry.value === 'object'
    const entries = container ? Object.entries(entry.value as object) : []
    const array = Array.isArray(entry.value)
    const line: JsonLine = {
      ...entry,
      id: lines.length,
      kind:
        container && entries.length
          ? 'open'
          : entry.value === null || entry.value === undefined || container
            ? 'null'
            : (typeof entry.value as 'string' | 'number' | 'boolean'),
      text: container ? (array ? '[' : '{') : (JSON.stringify(entry.value) ?? 'null'),
      end: lines.length + 1,
      count: entries.length,
    }
    if (container && !entries.length) line.text = array ? '[]' : '{}'
    lines.push(line)
    if (container && entries.length) {
      work.push({ close: line.id })
      for (let i = entries.length - 1; i >= 0; i--) {
        const [key, value] = entries[i]!
        work.push({
          value,
          parent: line.id,
          depth: line.depth + 1,
          key,
          indexKey: array,
          suffix: i < entries.length - 1 ? ',' : '',
        })
      }
    }
  }
  return lines
}

export function jsonAncestorIds(lines: JsonLine[], id: number): Set<number> {
  const ancestors = new Set<number>()
  let parent = lines[id]?.parent
  while (parent !== null && parent !== undefined) {
    ancestors.add(parent)
    parent = lines[parent]?.parent
  }
  return ancestors
}

export function visibleJsonLines(
  lines: JsonLine[],
  collapsed: Set<number>,
  revealed = new Set<number>()
): JsonLine[] {
  const visible: JsonLine[] = []
  for (let i = 0; i < lines.length;) {
    const line = lines[i]!
    visible.push(line)
    i = line.kind === 'open' && collapsed.has(i) && !revealed.has(i) ? line.end : i + 1
  }
  return visible
}

/** Budget the automatic outline before allocating a line for every value.
 * Parsing has already happened at the transport; large outlines remain opt-in. */
export function isAutoPreviewJson(
  value: unknown,
  maxNodes = 10_000,
  maxCharacters = 500_000
): boolean {
  const pending = [value]
  let nodes = 0,
    characters = 0
  while (pending.length) {
    const current = pending.pop()
    if (++nodes > maxNodes) return false
    if (typeof current === 'string') characters += current.length
    else if (current && typeof current === 'object') {
      for (const key in current) {
        if (!Object.hasOwn(current, key)) continue
        characters += key.length
        if (pending.length + nodes >= maxNodes || characters > maxCharacters) return false
        pending.push((current as Record<string, unknown>)[key])
      }
    }
    if (characters > maxCharacters) return false
  }
  return true
}

/** JSON values arrive parsed; compare without allocating another serialized
 * copy. Iteration also supports deep values without recursive stack growth. */
export function sameJsonValue(left: unknown, right: unknown): boolean {
  const pending: [unknown, unknown][] = [[left, right]]
  while (pending.length) {
    const [a, b] = pending.pop()!
    if (a === b) continue
    if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false
    if (Array.isArray(a) !== Array.isArray(b)) return false
    const keys = Object.keys(a),
      other = Object.keys(b)
    if (keys.length !== other.length) return false
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i]!
      if (key !== other[i]) return false
      pending.push([(a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]])
    }
  }
  return true
}
