import { effectScope, watch } from 'vue'
import { useI18n, type Composer } from 'vue-i18n'
import { pythonDependencyMessages } from '@/i18n/python-dependencies'

type DialogLocale = keyof typeof pythonDependencyMessages

const FALLBACK_LOCALE = 'zh-CN'
const PROBE_KEY = 'pythonDependencies.title'
const watched = new WeakSet<object>()

function ensureMessages(composer: Composer, locale: string) {
  const messages = pythonDependencyMessages[locale as DialogLocale]
  // Merged, not replaced: the locale file keeps pythonDependencies.startBlocked.
  if (messages && !composer.te(PROBE_KEY, locale)) composer.mergeLocaleMessage(locale, { pythonDependencies: messages })
}

// Same lifecycle as useConfigEditorI18n: the dialog's words load with the lazy
// dialog, and are merged again after a language switch or a locale bundle
// replacing a dictionary (setLocaleMessage drops what was merged earlier).
export function usePythonDependenciesI18n() {
  const composer = useI18n({ useScope: 'global' })
  const ensureActive = () => {
    ensureMessages(composer, composer.locale.value)
    ensureMessages(composer, FALLBACK_LOCALE)
  }
  ensureActive()
  if (!watched.has(composer)) {
    watched.add(composer)
    effectScope(true).run(() =>
      watch(
        () => [
          composer.locale.value,
          composer.getLocaleMessage(composer.locale.value),
          composer.getLocaleMessage(FALLBACK_LOCALE),
        ],
        ensureActive,
        { flush: 'sync' },
      ),
    )
  }
  return composer
}
