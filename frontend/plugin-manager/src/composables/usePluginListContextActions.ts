import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { deletePlugin } from '@/api/plugins'
import { packPluginCli } from '@/api/pluginCli'
import { usePluginStore } from '@/stores/plugin'
import type { PluginListAction, PluginMeta } from '@/types/api'

type PluginListContextPlugin = PluginMeta & {
  status?: string
  enabled?: boolean
  autoStart?: boolean
}

export type ResolvedPluginListAction = PluginListAction & {
  label: string
  disabled: boolean
  source: 'builtin' | 'plugin'
  sectionKey: 'navigation' | 'runtime' | 'plugin'
  sectionLabel: string
  sectionTone: 'slate' | 'mint' | 'sky'
}

const BUILTIN_ACTION_IDS = [
  'open_detail',
  'open_config',
  'open_logs',
] as const

function replacePluginTokens(value: string, pluginId: string): string {
  return value.split('{plugin_id}').join(pluginId)
}

export function usePluginListContextActions() {
  const router = useRouter()
  const pluginStore = usePluginStore()
  const { t } = useI18n()

  function isRunning(plugin: PluginListContextPlugin): boolean {
    return plugin.status === 'running'
  }

  function isDisabled(plugin: PluginListContextPlugin): boolean {
    return plugin.status === 'disabled'
  }

  function resolveBuiltinActions(plugin: PluginListContextPlugin): PluginListAction[] {
    const actions: PluginListAction[] = BUILTIN_ACTION_IDS.map((id) => ({
      id,
      kind: 'builtin',
    }))

    if (plugin.type === 'extension') {
      actions.push({
        id: plugin.status === 'disabled' ? 'enable_extension' : 'disable_extension',
        kind: 'builtin',
        danger: plugin.status !== 'disabled',
      })
      actions.push(
        {
          id: 'pack',
          kind: 'builtin',
        },
        {
          id: 'delete',
          kind: 'builtin',
          danger: true,
          confirm_mode: 'hold',
        },
      )
      return actions
    }

    if (!isRunning(plugin) && !isDisabled(plugin)) {
      actions.push({
        id: 'start',
        kind: 'builtin',
      })
    }
    if (isRunning(plugin)) {
      actions.push({
        id: 'stop',
        kind: 'builtin',
        danger: true,
      })
    }
    actions.push({
      id: 'reload',
      kind: 'builtin',
      disabled: isDisabled(plugin),
    })
    actions.push(
      {
        id: 'pack',
        kind: 'builtin',
      },
      {
        id: 'delete',
        kind: 'builtin',
        danger: true,
        confirm_mode: 'hold',
      },
    )
    return actions
  }

  function resolveActionLabel(action: PluginListAction): string {
    if (action.label) {
      return action.label
    }
    switch (action.id) {
      case 'open_detail':
        return t('plugins.viewDetails')
      case 'open_config':
        return t('plugins.config')
      case 'open_logs':
        return t('plugins.logs')
      case 'open_panel':
        return t('plugins.ui.panel')
      case 'open_guide':
        return t('plugins.ui.guide')
      case 'start':
        return t('plugins.start')
      case 'stop':
        return t('plugins.stop')
      case 'reload':
        return t('plugins.reload')
      case 'pack':
        return t('plugins.pack')
      case 'delete':
        return t('plugins.delete')
      case 'enable_extension':
        return t('plugins.enableExtension')
      case 'disable_extension':
        return t('plugins.disableExtension')
      case 'open_ui':
        return t('plugins.ui.open')
      default:
        return action.id
    }
  }

  function resolveActionDisabled(action: PluginListAction, plugin: PluginListContextPlugin): boolean {
    if (action.disabled === true) {
      return true
    }
    if (action.requires_running && !isRunning(plugin)) {
      return true
    }
    if (action.id === 'reload' && isDisabled(plugin)) {
      return true
    }
    return false
  }

  function resolveActionSection(action: PluginListAction): {
    key: 'navigation' | 'runtime' | 'plugin'
    label: string
    tone: 'slate' | 'mint' | 'sky'
  } {
    switch (action.id) {
      case 'open_detail':
      case 'open_config':
      case 'open_logs':
      case 'open_panel':
      case 'open_guide':
        return {
          key: 'navigation',
          label: t('plugins.contextSections.navigation'),
          tone: 'slate',
        }
      case 'start':
      case 'stop':
      case 'reload':
      case 'enable_extension':
      case 'disable_extension':
        return {
          key: 'runtime',
          label: t('plugins.contextSections.runtime'),
          tone: 'mint',
        }
      default:
        return {
          key: 'plugin',
          label: t('plugins.contextSections.plugin'),
          tone: 'sky',
        }
    }
  }

  function buildActions(plugin: PluginListContextPlugin): ResolvedPluginListAction[] {
    const resolved: ResolvedPluginListAction[] = []
    const seenIds = new Set<string>()

    const append = (action: PluginListAction, source: 'builtin' | 'plugin') => {
      if (!action.id || seenIds.has(action.id)) {
        return
      }
      seenIds.add(action.id)
      const section = resolveActionSection(action)
      resolved.push({
        ...action,
        label: resolveActionLabel(action),
        disabled: resolveActionDisabled(action, plugin),
        source,
        sectionKey: section.key,
        sectionLabel: section.label,
        sectionTone: section.tone,
      })
    }

    for (const action of resolveBuiltinActions(plugin)) {
      append(action, 'builtin')
    }
    for (const action of plugin.list_actions || []) {
      append(action, 'plugin')
    }

    return resolved
  }

  async function confirmIfNeeded(action: ResolvedPluginListAction) {
    if (action.confirm_mode === 'hold') {
      return true
    }
    if (!action.confirm_message) {
      return true
    }
    try {
      await ElMessageBox.confirm(action.confirm_message, t('common.confirm'), {
        type: action.danger ? 'warning' : 'info',
      })
      return true
    } catch {
      return false
    }
  }

  function resolvePackageDisplayName(packagePath: string): string {
    const normalized = packagePath.replace(/\\/g, '/')
    const segments = normalized.split('/')
    return segments[segments.length - 1] || packagePath
  }

  function shouldUseHoldConfirm(action: ResolvedPluginListAction): boolean {
    return action.confirm_mode === 'hold'
  }

  async function executeBuiltinAction(action: ResolvedPluginListAction, plugin: PluginListContextPlugin) {
    const safeId = encodeURIComponent(plugin.id)
    switch (action.id) {
      case 'open_detail':
        await router.push(`/plugins/${safeId}`)
        return
      case 'open_config':
        await router.push({ path: `/plugins/${safeId}`, query: { tab: 'config' } })
        return
      case 'open_logs':
        await router.push({ path: `/plugins/${safeId}`, query: { tab: 'logs' } })
        return
      case 'open_panel':
        await router.push({ path: `/plugins/${safeId}`, query: { tab: 'panel' } })
        return
      case 'open_guide':
        await router.push({ path: `/plugins/${safeId}`, query: { tab: 'guide' } })
        return
      case 'start':
        await pluginStore.start(plugin.id)
        ElMessage.success(t('messages.pluginStarted'))
        return
      case 'stop':
        if (!(await confirmIfNeeded({
          ...action,
          confirm_message: action.confirm_message || t('messages.confirmStop'),
        }))) {
          return
        }
        await pluginStore.stop(plugin.id)
        ElMessage.success(t('messages.pluginStopped'))
        return
      case 'reload':
        if (!(await confirmIfNeeded({
          ...action,
          confirm_message: action.confirm_message || t('messages.confirmReload'),
        }))) {
          return
        }
        await pluginStore.reload(plugin.id)
        ElMessage.success(t('messages.pluginReloaded'))
        return
      case 'pack': {
        const result = await packPluginCli({
          mode: 'single',
          plugin: plugin.id,
        })
        const packedItem = result.packed.find(item => item.plugin_id === plugin.id) || result.packed[0]
        if (!packedItem) {
          throw new Error(result.failed[0]?.error || t('messages.packFailed'))
        }
        ElMessage.success(
          t('messages.pluginPacked', {
            packageName: resolvePackageDisplayName(packedItem.package_path),
          }),
        )
        return
      }
      case 'delete':
        await deletePlugin(plugin.id)
        await pluginStore.fetchPlugins(true)
        await pluginStore.fetchPluginStatus()
        ElMessage.success(t('messages.pluginDeleted'))
        return
      case 'enable_extension':
        await pluginStore.enableExt(plugin.id)
        ElMessage.success(t('messages.extensionEnabled'))
        return
      case 'disable_extension':
        if (!(await confirmIfNeeded({
          ...action,
          confirm_message: action.confirm_message || t('messages.confirmDisableExt'),
        }))) {
          return
        }
        await pluginStore.disableExt(plugin.id)
        ElMessage.success(t('messages.extensionDisabled'))
        return
      default:
        ElMessage.warning(action.label)
    }
  }

  async function executeAction(action: ResolvedPluginListAction, plugin: PluginListContextPlugin) {
    if (action.disabled) {
      return
    }

    if (action.kind === 'builtin') {
      await executeBuiltinAction(action, plugin)
      return
    }

    if (!(await confirmIfNeeded(action))) {
      return
    }

    const target = typeof action.target === 'string' ? replacePluginTokens(action.target, plugin.id) : ''
    if (!target) {
      ElMessage.warning(action.label)
      return
    }

    if (action.kind === 'route') {
      await router.push(target)
      return
    }

    if (action.kind === 'ui' || action.kind === 'url') {
      const nextTarget = action.open_in === 'same_tab' ? '_self' : '_blank'
      window.open(target, nextTarget, nextTarget === '_blank' ? 'noopener' : undefined)
    }
  }

  return {
    buildActions,
    executeAction,
    shouldUseHoldConfirm,
  }
}
