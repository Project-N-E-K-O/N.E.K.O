import type { PluginListAction } from '@/types/api'

const OPEN_UI_NAVIGATION_KINDS = new Set(['ui', 'url', 'route'])

export function isOpenUiNavigationAction(action: PluginListAction): boolean {
  return (
    action.id === 'open_ui' &&
    typeof action.kind === 'string' &&
    OPEN_UI_NAVIGATION_KINDS.has(action.kind)
  )
}
