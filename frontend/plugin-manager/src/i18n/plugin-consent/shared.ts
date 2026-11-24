import { withPermissionLabels } from '../plugin-permissions/shared'

/** Attach the permission-id labels to a locale's consent messages. */
export function withPermissions<M extends { capabilities: Parameters<typeof withPermissionLabels>[0] }>(
  messages: M,
  labels: Parameters<typeof withPermissionLabels>[1],
) {
  return { ...messages, capabilities: withPermissionLabels(messages.capabilities, labels) }
}
