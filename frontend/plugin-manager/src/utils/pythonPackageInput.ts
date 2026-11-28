/** Read existing declarations and common copied install commands for the editor.
 * The backend remains responsible for validating Python requirement syntax. */
export function parsePythonPackageInput(input: string) {
  let value = input.trim()
  value = value.replace(
    /^(?:(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?\s+-m\s+pip|(?:uv\s+)?pip(?:3)?(?:\.exe)?)\s+install\s+(?:(?:-U|--upgrade)\s+)?/i,
    ''
  )
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    value = value.slice(1, -1)
  }
  const match = /^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[([^\]]*)\])?\s*([^;]*?)(?:;\s*(.*))?$/.exec(
    value
  )
  if (!match || (match[3]?.trim() && !/^(?:===|==|~=|!=|<=|>=|<|>)/.test(match[3].trim())))
    return null
  return {
    name: match[1]!,
    extras: match[2] ?? '',
    specifier: match[3]?.trim() ?? '',
    marker: match[4] ?? '',
  }
}

export function pythonVersionPolicy(specifier: string): 'auto' | 'exact' | 'custom' {
  if (!specifier) return 'auto'
  return /^==[vV]?\d[\w.!+-]*$/.test(specifier) ? 'exact' : 'custom'
}
