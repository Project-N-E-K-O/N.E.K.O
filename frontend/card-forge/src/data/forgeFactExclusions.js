export const MAX_EXCLUDE_CARDS = 50
export const MAX_EXCLUDE_PARAM_ENCODED_CHARS = 2800

function forgedAtTimestamp(card) {
  const timestamp = Number(card?.forgedAt)
  return Number.isFinite(timestamp) ? timestamp : 0
}

function boundedUniqueValues(cards, field, queryParameterName) {
  const values = []
  const seen = new Set()

  for (const card of cards) {
    const value = String(card?.[field] ?? '').trim()
    if (!value || seen.has(value)) continue

    const candidate = [...values, value].join(',')
    const serializedLength = new URLSearchParams([
      [queryParameterName, candidate],
    ]).toString().length
    if (serializedLength > MAX_EXCLUDE_PARAM_ENCODED_CHARS) {
      continue
    }

    seen.add(value)
    values.push(value)
  }

  return values
}

export function selectRecentForgeFactExclusions(inventoryCards, activeCharacterName) {
  const recentCards = (Array.isArray(inventoryCards) ? inventoryCards : [])
    .filter(card => !card?.sourceCharacter || card.sourceCharacter === activeCharacterName)
    .sort((left, right) => forgedAtTimestamp(right) - forgedAtTimestamp(left))
    .slice(0, MAX_EXCLUDE_CARDS)

  return {
    factIds: boundedUniqueValues(recentCards, 'sourceFactId', 'exclude_fact_ids'),
    factHashes: boundedUniqueValues(recentCards, 'sourceFactHash', 'exclude_hashes'),
  }
}
