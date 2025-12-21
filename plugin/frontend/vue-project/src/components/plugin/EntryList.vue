<template>
  <div class="entry-list">
    <EmptyState v-if="entries.length === 0" :description="$t('plugins.noEntries')" />
    <el-table v-else :data="entries" stripe>
      <el-table-column prop="name" :label="$t('plugins.entryName')" width="200" />
      <el-table-column prop="description" :label="$t('plugins.entryDescription')" />
      <el-table-column :label="$t('plugins.actions')" width="120">
        <template #default="{ row }">
          <el-button type="primary" size="small" @click="handleExecute(row)">
            {{ $t('plugins.trigger') }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import EmptyState from '@/components/common/EmptyState.vue'
import type { PluginEntry } from '@/types/api'

interface Props {
  entries: PluginEntry[]
  pluginId: string
}

const props = defineProps<Props>()
const { t } = useI18n()

function handleExecute(entry: PluginEntry) {
  ElMessage.info(`${t('plugins.trigger')} ${t('plugins.entryPoint')}: ${entry.name}`)
  // TODO: 打开执行对话框
}
</script>

<style scoped>
.entry-list {
  padding: 20px 0;
}
</style>

