import { effectScope, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { configEditorMessages } from '@/i18n/config-editor'

type EditorLocale = keyof typeof configEditorMessages
type Composer = ReturnType<typeof useI18n>

const FALLBACK_LOCALE = 'zh-CN'
// Any key of the bundle tells whether a dictionary still carries it.
const PROBE_KEY = 'plugins.configUi.saveProfile'
const watched = new WeakSet<object>()

function ensureMessages(composer: Composer, locale: string) {
  const configUi = configEditorMessages[locale as EditorLocale]
  if (configUi && !composer.te(PROBE_KEY, locale))
    composer.mergeLocaleMessage(locale, { plugins: { configUi } })
}

// All editor components share the global composer and its live locale. The editor's
// messages are merged in when the lazy configuration view is used, but locale bundles
// load asynchronously and replace a language's whole dictionary (setLocaleMessage), which
// drops anything merged earlier. So the messages are ensured on every use and again after
// a language switch or a dictionary replacement, for the active and the fallback language.
export function useConfigEditorI18n() {
  const composer = useI18n({ useScope: 'global' })
  const ensureActive = () => {
    ensureMessages(composer, composer.locale.value)
    ensureMessages(composer, FALLBACK_LOCALE)
  }
  ensureActive()
  if (!watched.has(composer)) {
    watched.add(composer)
    // Detached: the composer outlives the component that first used it.
    effectScope(true).run(() =>
      watch(
        () => [
          composer.locale.value,
          composer.getLocaleMessage(composer.locale.value),
          composer.getLocaleMessage(FALLBACK_LOCALE),
        ],
        ensureActive,
        { flush: 'sync' }
      )
    )
  }
  return composer
}
