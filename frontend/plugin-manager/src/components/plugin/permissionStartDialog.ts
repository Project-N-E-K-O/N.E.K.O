import { deferredPanel } from '@/components/common/deferredPanel'

/** The permission dialog, loaded on first use; the start gate preloads it. */
export const PermissionStartDialog = deferredPanel(() => import('./PluginPermissionDialog.vue'))
