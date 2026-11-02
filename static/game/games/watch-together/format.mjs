// Pure display formatting shared by the scene and its presentation hooks.
export function clock(seconds) {
  const value = Number(seconds);
  if (seconds == null || seconds === '' || !Number.isFinite(value) || value < 0) return '';
  const total = Math.floor(value), hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60);
  const pad = number => String(number).padStart(2, '0');
  return hours ? `${hours}:${pad(minutes)}:${pad(total % 60)}` : `${minutes}:${pad(total % 60)}`;
}

export function when(value, language) {
  if (value == null || value === '') return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const options = {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'};
  try { return date.toLocaleString(language || undefined, options); }
  catch (_) { return date.toLocaleString(undefined, options); }
}
