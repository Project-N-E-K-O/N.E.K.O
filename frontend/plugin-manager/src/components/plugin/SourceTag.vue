<template>
  <span v-if="source && source !== 'unknown'" class="source-tag-group">
    <!-- Match the cadence of the surrounding tags (extension / disabled
         / manual-start): ``size="small"`` + ``effect="plain"``, icon
         inline, single Chinese/English word. Intentionally no custom
         font-size or padding overrides. -->
    <el-tag :type="tagType" size="small" effect="plain">
      <el-icon class="source-tag__icon"><component :is="icon" /></el-icon>
      {{ t(`plugins.installSource.channel.${source}`) }}
    </el-tag>
    <el-tag v-if="hasUpdate" type="warning" size="small" effect="plain">
      <el-icon class="source-tag__icon"><Top /></el-icon>
      {{ t('plugins.installSource.updateAvailable') }}
    </el-tag>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Box, User, Upload, ShoppingCart, Top } from '@element-plus/icons-vue'
import type { PluginInstallSourceChannel } from '@/types/api'

interface Props {
  source?: PluginInstallSourceChannel
  hasUpdate?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  source: undefined,
  hasUpdate: false,
})

const { t } = useI18n()

/** Tag colour, aligned with semantic conventions elsewhere in the app:
 *   builtin  -> info   (neutral, "shipped with the product")
 *   manual   -> info   (same family — no emphasis needed)
 *   imported -> primary (user-driven action, worth noting)
 *   market   -> success (comes from an official channel)
 *  builtin and manual both land on info so the default card doesn't
 *  scream at the user — there are 10+ built-ins in a fresh install and
 *  they should sit in the background. */
const tagType = computed(() => {
  switch (props.source) {
    case 'builtin': return 'info'
    case 'manual': return 'info'
    case 'imported': return 'primary'
    case 'market': return 'success'
    default: return 'info'
  }
})

const icon = computed(() => {
  switch (props.source) {
    case 'builtin': return Box
    case 'manual': return User
    case 'imported': return Upload
    case 'market': return ShoppingCart
    default: return Box
  }
})
</script>

<style scoped>
.source-tag__icon {
  /* Match Element Plus' convention for icon-in-tag: sit inline with
   * text, slight trailing gap, vertical-center via flex on the parent. */
  margin-right: 3px;
  vertical-align: -1px;
}

.source-tag-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  white-space: nowrap;
}

.source-tag-group :deep(.el-tag) {
  flex: 0 0 auto;
}
</style>
