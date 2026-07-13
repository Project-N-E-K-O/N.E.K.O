<template>
  <span
    v-if="source && source !== 'unknown'"
    class="source-tag-group"
    :class="{ 'source-tag-group--compact': compact }"
  >
    <!-- Match the cadence of the surrounding tags (disabled
         / manual-start): ``size="small"`` + ``effect="plain"``, icon
         inline, single Chinese/English word. Intentionally no custom
         font-size or padding overrides. -->
    <el-tag
      :type="tagType"
      size="small"
      effect="plain"
      class="source-tag source-tag--channel"
      :title="sourceLabel"
    >
      <el-icon class="source-tag__icon"><component :is="icon" /></el-icon>
      <span class="source-tag__label">{{ sourceLabel }}</span>
    </el-tag>
    <el-tag
      v-if="hasUpdate"
      type="warning"
      size="small"
      effect="plain"
      class="source-tag source-tag--update"
      :title="updateLabel"
    >
      <el-icon class="source-tag__icon"><Top /></el-icon>
      <span class="source-tag__label">{{ updateLabel }}</span>
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
  compact?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  source: undefined,
  hasUpdate: false,
  compact: false,
})

const { t } = useI18n()

const sourceLabel = computed(() => (
  props.source ? t(`plugins.installSource.channel.${props.source}`) : ''
))
const updateLabel = computed(() => t('plugins.installSource.updateAvailable'))

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
  flex: 0 0 auto;
  width: 1em;
  height: 1em;
  margin-right: 3px;
}

.source-tag__label {
  display: inline-block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-tag {
  max-width: 100%;
  overflow: hidden;
}

.source-tag :deep(.el-tag__content) {
  display: inline-flex;
  align-items: center;
  flex-wrap: nowrap;
  min-width: 0;
  max-width: 100%;
  line-height: 1;
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

.source-tag-group--compact {
  flex: 0 1 auto;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  white-space: normal;
}

.source-tag-group--compact .source-tag {
  min-width: 0;
  max-width: 100%;
}

.source-tag-group--compact :deep(.el-tag) {
  flex: 0 1 auto;
}

.source-tag-group--compact .source-tag--channel {
  flex: 0 0 auto;
}
</style>
