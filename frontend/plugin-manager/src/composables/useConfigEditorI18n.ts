import { useI18n } from 'vue-i18n'
import { configEditorMessages } from '@/i18n/config-editor'

const registered = new WeakSet<object>()

// All editor components share the global composer and its live locale. Register
// these messages once when the lazy configuration view is first used, including
// when a field editor is mounted on its own.
export function useConfigEditorI18n() {
  const composer = useI18n({ useScope: 'global' })
  if (!registered.has(composer)) {
    for (const [locale, configUi] of Object.entries(configEditorMessages)) {
      composer.mergeLocaleMessage(locale, { plugins: { configUi } })
    }
    registered.add(composer)
  }
  return composer
}
