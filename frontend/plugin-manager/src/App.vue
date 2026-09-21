<template>
  <el-config-provider
    :locale="elementLocale"
    :z-index="ELEMENT_Z_INDEX"
    :message="ELEMENT_MESSAGE_CONFIG"
  >
    <router-view />
    <LocaleLoadNotice />
  </el-config-provider>
</template>

<script setup lang="ts">
import { elementLocale } from './i18n'
import LocaleLoadNotice from './components/common/LocaleLoadNotice.vue'

const ELEMENT_Z_INDEX = 12000
const ELEMENT_MESSAGE_CONFIG = { offset: 54 }
</script>
