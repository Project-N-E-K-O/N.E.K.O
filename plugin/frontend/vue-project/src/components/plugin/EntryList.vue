<template>
  <div class="entry-list">
    <EmptyState v-if="entries.length === 0" description="暂无入口点" />
    <el-table v-else :data="entries" stripe>
      <el-table-column prop="name" label="名称" width="200" />
      <el-table-column prop="description" label="描述" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button type="primary" size="small" @click="handleExecute(row)">
            执行
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import EmptyState from '@/components/common/EmptyState.vue'
import type { PluginEntry } from '@/types/api'

interface Props {
  entries: PluginEntry[]
  pluginId: string
}

const props = defineProps<Props>()

function handleExecute(entry: PluginEntry) {
  ElMessage.info(`执行入口点: ${entry.name}`)
  // TODO: 打开执行对话框
}
</script>

<style scoped>
.entry-list {
  padding: 20px 0;
}
</style>

