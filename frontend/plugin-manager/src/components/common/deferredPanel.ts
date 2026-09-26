import { defineComponent, h, onBeforeUnmount, onMounted, ref, shallowRef, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { OptionalModuleError, retryableModule } from '@/utils/retryableModule'

/** Local async boundary: v-show reopen preserves state, including an explicit
 * error/recovery UI instead of an invisible failed AsyncComponent instance. */
export function deferredPanel(loader: () => Promise<{ default: Component }>) {
  const load = retryableModule(loader)
  return defineComponent({
    inheritAttrs: false,
    emits: ['close'],
    setup(_, { attrs, slots, emit }) {
      const { t } = useI18n()
      const resolved = shallowRef<Component | null>(null)
      const error = ref('')
      const pending = ref(false)
      const reloadRequired = ref(false)
      let generation = 0
      let disposed = false
      async function start() {
        const seq = ++generation
        pending.value = true
        error.value = ''
        try {
          const module = await load()
          if (!disposed && seq === generation) resolved.value = module.default
        } catch (caught) {
          if (disposed || seq !== generation) return
          error.value = caught instanceof Error ? caught.message : String(caught)
          reloadRequired.value = caught instanceof OptionalModuleError && caught.reloadRequired
        } finally {
          if (!disposed && seq === generation) pending.value = false
        }
      }
      onMounted(start)
      onBeforeUnmount(() => { disposed = true; generation += 1 })
      return () => resolved.value
        ? h(resolved.value, { ...attrs, onClose: () => emit('close') }, slots)
        : h('div', { class: 'deferred-panel', style: 'padding:20px', 'aria-busy': pending.value }, [
          h('p', { role: error.value ? 'alert' : 'status', 'data-testid': error.value ? 'deferred-panel-error' : 'deferred-panel-loading' }, error.value ? `${t('plugins.ui.loadError')}: ${error.value}` : t('common.loading')),
          error.value && !reloadRequired.value ? h('button', { type: 'button', 'data-testid': 'deferred-panel-retry', onClick: start }, t('market.retry')) : null,
          error.value ? h('button', { type: 'button', 'data-testid': 'deferred-panel-reload', onClick: () => window.location.reload() }, t('common.languageReload')) : null,
          h('button', { type: 'button', onClick: () => emit('close') }, t('common.close')),
        ])
    },
  })
}
