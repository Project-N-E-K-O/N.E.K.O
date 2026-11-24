/**
 * Labels for plugin permission ids (plugin/sdk/shared/permissions/catalog.py).
 *
 * Keys are derived by `capabilityMessageKey`: every `.` and `:` becomes `_`.
 * Ids that had an earlier capability name reuse that name's label.
 *
 * One label module per language next to this file, so that a language pack
 * carries its own words only (see `../localeBundles.ts`).
 */

import type { PermissionLabels } from './en-US'

export type Label = { title: string; description: string }

export const PERMISSION_LEGACY_LABEL_KEYS = {
  plugin_sdk_main_agent_memory_read: 'memory_read',
  plugin_sdk_main_agent_conversations_read: 'conversations_read',
  plugin_sdk_main_agent_messages_push: 'messages_push',
} as const

/** Add the permission-id labels next to a locale's earlier capability labels. */
export function withPermissionLabels<C extends Record<string, Label>>(capabilities: C, labels: PermissionLabels) {
  const aliased = Object.fromEntries(
    Object.entries(PERMISSION_LEGACY_LABEL_KEYS).map(([key, legacy]) => [key, capabilities[legacy]]),
  ) as Record<keyof typeof PERMISSION_LEGACY_LABEL_KEYS, Label>
  return { ...capabilities, ...aliased, ...labels }
}
