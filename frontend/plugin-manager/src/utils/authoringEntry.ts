export function authoringEntry(id: string): string {
  return `plugin.plugins.${id}:${id
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('')}Plugin`
}
