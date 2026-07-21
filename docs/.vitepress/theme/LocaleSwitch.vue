<script setup lang="ts">
import { computed } from 'vue'
import { useData } from 'vitepress'
import type { DefaultTheme } from 'vitepress'

type LocaleSwitchTheme = DefaultTheme.Config & {
  availablePageRoutes?: string[]
}

defineProps<{
  variant: 'desktop' | 'mobile'
}>()

const { localeIndex, page, site, theme } = useData<LocaleSwitchTheme>()

function localeHome(key: string, locale: { link?: string }): string {
  const configured = locale.link || (key === 'root' ? '/' : `/${key}/`)
  return configured.endsWith('/') ? configured : `${configured}/`
}

function routeForPage(home: string, sourcePath: string): string {
  const routePath = sourcePath
    .replace(/(^|\/)index\.md$/, '$1')
    .replace(/\.md$/, '')
  return routePath ? `${home}${routePath}`.replace(/\/{2,}/g, '/') : home
}

const currentLocale = computed(() => localeIndex.value || 'root')

const pageWithinLocale = computed(() => {
  const sourcePath = page.value.relativePath.replaceAll('\\', '/')
  const prefix = currentLocale.value === 'root' ? '' : `${currentLocale.value}/`
  return prefix && sourcePath.startsWith(prefix)
    ? sourcePath.slice(prefix.length)
    : sourcePath
})

const currentLabel = computed(() =>
  site.value.locales[currentLocale.value]?.label || currentLocale.value,
)

const localeLinks = computed(() => {
  const available = new Set(theme.value.availablePageRoutes || [])

  return Object.entries(site.value.locales).flatMap(([key, locale]) => {
    if (key === currentLocale.value) return []

    const home = localeHome(key, locale)
    const candidate = routeForPage(home, pageWithinLocale.value)
    return [{
      text: locale.label || key,
      link: available.has(candidate) ? candidate : home,
      isFallback: !available.has(candidate),
    }]
  })
})
</script>

<template>
  <details
    v-if="variant === 'desktop' && localeLinks.length"
    class="NekoLocaleSwitch NekoLocaleSwitch--desktop"
  >
    <summary :aria-label="theme.langMenuLabel || 'Change language'">
      <span class="vpi-languages" aria-hidden="true" />
    </summary>
    <div class="NekoLocaleSwitch-menu">
      <p>{{ currentLabel }}</p>
      <a
        v-for="locale in localeLinks"
        :key="locale.text"
        :href="locale.link"
        :data-locale-fallback="locale.isFallback || undefined"
      >
        {{ locale.text }}
      </a>
    </div>
  </details>

  <div
    v-else-if="variant === 'mobile' && localeLinks.length"
    class="NekoLocaleSwitch NekoLocaleSwitch--mobile"
  >
    <p><span class="vpi-languages" aria-hidden="true" /> {{ currentLabel }}</p>
    <a
      v-for="locale in localeLinks"
      :key="locale.text"
      :href="locale.link"
      :data-locale-fallback="locale.isFallback || undefined"
    >
      {{ locale.text }}
    </a>
  </div>
</template>
