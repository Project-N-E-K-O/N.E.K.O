<template>
  <div class="empty-state">
    <el-empty :description="computedDescription" :image-size="imageSize">
      <template v-if="icon" #image>
        <el-icon :size="iconSize">
          <component :is="icon" />
        </el-icon>
      </template>
      <template v-if="$slots.default" #default>
        <slot></slot>
      </template>
    </el-empty>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Component } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface Props {
  description?: string
  icon?: Component
  iconSize?: number
  imageSize?: number
}

const props = withDefaults(defineProps<Props>(), {
  iconSize: 64,
  imageSize: 120
})

const computedDescription = computed(() => props.description || t('common.noData'))
</script>

<style scoped>
.empty-state {
  padding: 40px 20px;
}
</style>

