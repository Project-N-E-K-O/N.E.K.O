// Compact numeric fields use a text input with `inputmode="decimal"`. A native
// `type="number"` control reports intermediate edits as a sanitised value ("-" and
// "1e" become "", "1." becomes "1") and Element Plus writes the bound value back to
// the element after every input event, so the character the user just typed is
// erased and negative or decimal values cannot be entered one keystroke at a time.
// Keeping the raw text lets the field accept any in-progress edit and only commit
// values that parse to a finite number.

export function parseNumberText(text: string): number | undefined {
  const trimmed = text.trim()
  if (trimmed === '' || !Number.isFinite(Number(trimmed))) return undefined
  return Number(trimmed)
}

/** Blur normalises the text so the field never keeps an uncommittable or odd form. */
export function settleNumberText(raw: string, fallback: string): string {
  const parsed = parseNumberText(raw)
  return parsed === undefined ? fallback : String(parsed)
}
