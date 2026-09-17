// Numeric text-field helpers. Chromium and Electron sanitise `type="number"`
// input: intermediate edits such as "-", "1." or "1e" are reported as an empty
// string while the control still shows the characters the user typed. Writing
// that reading back clears the field mid-edit, so empty readings are ignored and
// the value is settled on blur instead.

export function nextNumberText(current: string, raw: string): string {
  return raw === '' ? current : raw
}

export function settleNumberText(raw: string, fallback: string): string {
  if (!raw.trim() || !Number.isFinite(Number(raw))) return fallback
  return raw
}
