<template>
  <div class="empty-state">
    <el-empty :description="description" :image-size="imageSize">
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
import type { Component } from 'vue'

interface Props {
  description?: string
  icon?: Component
  iconSize?: number
  imageSize?: number
}

withDefaults(defineProps<Props>(), {
  description: '暂无数据',
  iconSize: 64,
  imageSize: 120
})
</script>

<style scoped>
.empty-state {
  padding: 40px 20px;
}
</style>

