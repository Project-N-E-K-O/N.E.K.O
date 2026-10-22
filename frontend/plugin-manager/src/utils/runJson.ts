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
