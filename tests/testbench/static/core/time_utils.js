// Shared time helpers used by Virtual Clock UI (P06) and later by
// Chat composer (P09) / Scripted runner (P12). Kept deliberately small
// so there's no chance of divergent parsing between workspaces.
//
// Format conventions:
//   * Duration text — "1h30m", "45s", "2d 4h", "1w 2d 3h 4m 5s"; parser is
//     case-insensitive and whitespace-tolerant. Returns total SECONDS
//     (integer, may be negative if the string starts with '-').
//   * secondsToLabel — canonical pretty form using the same units, skipping
//     zero slots ("3661" → "1h 1m 1s"). Used for read-only display; the
//     input round-trips via parseDurationText.
//   * datetimeLocalValue / datetimeLocalToISO — bridge the <input
//     type="datetime-local"> quirk: the browser returns "YYYY-MM-DDTHH:MM"
//     with LOCAL timezone, while the backend stores naive isoformat
//     strings. We treat both sides as local wall-clock time (matching
//     upstream VirtualClock/datetime.now() behavior).

const UNIT_SECONDS = {
  w: 7 * 24 * 3600,
  d: 24 * 3600,
  h: 3600,
  m: 60,
  s: 1,
};

/** Parse "1h30m" / "45s" / "-2d 4h". Returns null on empty or parse failure. */
export function parseDurationText(text) {
  if (text == null) return null;
  const trimmed = String(text).trim();
  if (!trimmed) return null;

  const sign = trimmed.startsWith('-') ? -1 : 1;
  const body = trimmed.replace(/^[-+]/, '');
  const re = /(\d+)\s*([wdhms])/gi;
  let total = 0;
  let matched = 0;
  let m;
  while ((m = re.exec(body)) !== null) {
    const n = Number.parseInt(m[1], 10);
    const unit = m[2].toLowerCase();
    total += n * UNIT_SECONDS[unit];
    matched += m[0].length;
  }
  if (matched === 0) {
    // Allow plain "120" as seconds — common Power-user shorthand.
    const plain = body.replace(/\s+/g, '');
    if (/^\d+$/.test(plain)) return sign * Number.parseInt(plain, 10);
    return null;
  }
  // Reject trailing garbage ("1h junk") so tester gets a red border, not a
  // silently truncated value.
  const consumed = body.replace(/\s+/g, '').match(/(\d+[wdhms])+/i);
  if (!consumed || consumed[0].length !== body.replace(/\s+/g, '').length) {
    return null;
  }
  return sign * total;
}

/** Render a seconds count as the canonical "1h 30m" form. */
export function secondsToLabel(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return '—';
  const sign = seconds < 0 ? '-' : '';
  let remainder = Math.abs(Math.trunc(seconds));
  if (remainder === 0) return '0s';
  const parts = [];
  for (const [unit, size] of Object.entries(UNIT_SECONDS)) {
    const n = Math.floor(remainder / size);
    if (n > 0) {
      parts.push(`${n}${unit}`);
      remainder -= n * size;
    }
  }
  return sign + parts.join(' ');
}

/** Convert a Date / ISO string → "YYYY-MM-DDTHH:MM" (local) for <input>. */
export function datetimeLocalValue(input) {
  if (!input) return '';
  const d = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  // NOTE: Deliberately using the LOCAL getters (not UTC) — the backend's
  // datetime.fromisoformat() reads naive strings as local-wall-clock.
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

/**
 * Turn an `<input type="datetime-local">` value into an ISO string the
 * backend can parse via `datetime.fromisoformat`. Returns null when empty.
 *
 * The browser gives us `YYYY-MM-DDTHH:MM` with no timezone suffix; we
 * append `:00` seconds and leave it tz-naive on purpose.
 */
export function datetimeLocalToISO(value) {
  if (!value) return null;
  const trimmed = String(value).trim();
  if (!trimmed) return null;
  // Tolerate optional ":SS" (some browsers add seconds, some don't).
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(trimmed)) return `${trimmed}:00`;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(trimmed)) return trimmed;
  return null;
}

/** Human-friendly "Sat 2026-04-18 09:30:00" (no seconds when you pass ISO without them). */
export function formatIsoReadable(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n) => String(n).padStart(2, '0');
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}
