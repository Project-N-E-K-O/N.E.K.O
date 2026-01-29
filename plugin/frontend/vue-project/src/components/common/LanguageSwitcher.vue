<template>
  <el-dropdown @command="handleCommand" trigger="click">
    <el-button circle>
      <span class="language-icon">{{ currentLocale === 'zh-CN' ? '中' : 'EN' }}</span>
    </el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item command="zh-CN" :disabled="currentLocale === 'zh-CN'">
          <span>🇨🇳 中文</span>
        </el-dropdown-item>
        <el-dropdown-item command="en-US" :disabled="currentLocale === 'en-US'">
          <span>🇺🇸 English</span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { setLocale, getLocale } from '@/i18n'

const { locale } = useI18n()
const currentLocale = computed(() => getLocale())

function handleCommand(command: 'zh-CN' | 'en-US') {
  setLocale(command)
  locale.value = command
  
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

