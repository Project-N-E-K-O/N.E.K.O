<template>
  <el-dropdown @command="handleCommand" trigger="click" :persistent="false">
    <el-button circle :aria-busy="localeLoadState.pending !== null" data-testid="language-switcher">
      <span class="language-icon">{{ displayLabel }}</span>
    </el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item command="auto" :disabled="isApplied('auto')">
          <span>🌐 {{ $t('common.languageAuto') }}</span>
        </el-dropdown-item>
        <el-dropdown-item divided command="zh-CN" :disabled="isApplied('zh-CN')">
          <span>🇨🇳 简体中文</span>
        </el-dropdown-item>
        <el-dropdown-item command="zh-TW" :disabled="isApplied('zh-TW')">
          <span>🇹🇼 繁體中文</span>
        </el-dropdown-item>
        <el-dropdown-item command="en-US" :disabled="isApplied('en-US')">
          <span>🇺🇸 English</span>
        </el-dropdown-item>
        <el-dropdown-item command="ja" :disabled="isApplied('ja')">
          <span>🇯🇵 日本語</span>
        </el-dropdown-item>
        <el-dropdown-item command="ko" :disabled="isApplied('ko')">
          <span>🇰🇷 한국어</span>
        </el-dropdown-item>
        <el-dropdown-item command="ru" :disabled="isApplied('ru')">
          <span>🇷🇺 Русский</span>
        </el-dropdown-item>
        <el-dropdown-item command="es" :disabled="isApplied('es')">
          <span>🇪🇸 Español</span>
        </el-dropdown-item>
        <el-dropdown-item command="pt" :disabled="isApplied('pt')">
          <span>🇵🇹 Português</span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { setLocale, getLocale, getLocaleSetting, localeLoadState } from '@/i18n'
import type { LocaleSetting, AppLocale } from '@/i18n'

const currentSetting = computed(() => getLocaleSetting())

const LOCALE_SHORT_LABELS: Record<AppLocale, string> = {
  'zh-CN': '简',
  'zh-TW': '繁',
  'en-US': 'EN',
  'ja': 'JP',
  'ko': 'KR',
  'ru': 'RU',
  'es': 'ES',
  'pt': 'PT'
}

const displayLabel = computed(() => LOCALE_SHORT_LABELS[getLocale()])

// The applied language stays selectable during a pending switch, allowing
// the user to cancel that selection instead of waiting for an unwanted pack.
function isApplied(setting: LocaleSetting) {
  return currentSetting.value === setting && localeLoadState.pending === null && !localeLoadState.error
}

function handleCommand(command: LocaleSetting) {
  void setLocale(command)
}
</script>

<style scoped>
.language-icon {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.el-dropdown-menu__item span {
  display: inline-block;
  margin-right: 8px;
}
</style>
