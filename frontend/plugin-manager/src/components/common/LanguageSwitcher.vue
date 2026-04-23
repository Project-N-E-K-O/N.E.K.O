<template>
  <el-dropdown @command="handleCommand" trigger="click">
    <el-button circle>
      <span class="language-icon">{{ displayLabel }}</span>
    </el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item command="auto" :disabled="currentSetting === 'auto'">
          <span>🌐 {{ $t('common.languageAuto') }}</span>
        </el-dropdown-item>
        <el-dropdown-item divided command="zh-CN" :disabled="currentSetting === 'zh-CN'">
          <span>🇨🇳 简体中文</span>
        </el-dropdown-item>
        <el-dropdown-item command="zh-TW" :disabled="currentSetting === 'zh-TW'">
          <span>🇹🇼 繁體中文</span>
        </el-dropdown-item>
        <el-dropdown-item command="en-US" :disabled="currentSetting === 'en-US'">
          <span>🇺🇸 English</span>
        </el-dropdown-item>
        <el-dropdown-item command="ja" :disabled="currentSetting === 'ja'">
          <span>🇯🇵 日本語</span>
        </el-dropdown-item>
        <el-dropdown-item command="ko" :disabled="currentSetting === 'ko'">
          <span>🇰🇷 한국어</span>
        </el-dropdown-item>
        <el-dropdown-item command="ru" :disabled="currentSetting === 'ru'">
          <span>🇷🇺 Русский</span>
        </el-dropdown-item>
        <el-dropdown-item command="es" :disabled="currentSetting === 'es'">
          <span>🇪🇸 Español</span>
        </el-dropdown-item>
        <el-dropdown-item command="pt" :disabled="currentSetting === 'pt'">
          <span>🇵🇹 Português</span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { setLocale, getLocale, getLocaleSetting } from '@/i18n'
import type { LocaleSetting, AppLocale } from '@/i18n'

const { locale } = useI18n()
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

function handleCommand(command: LocaleSetting) {
  setLocale(command)
  locale.value = getLocale()

  // 更新 Element Plus 的 locale
  // 由于 Element Plus 的 locale 在应用初始化时设置，切换语言时重新加载页面
  // 这样可以确保所有组件（包括 Element Plus）都使用新的语言
  location.reload()
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
