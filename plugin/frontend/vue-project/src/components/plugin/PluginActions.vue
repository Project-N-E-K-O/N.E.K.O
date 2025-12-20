<template>
  <div class="plugin-actions">
    <el-button-group>
      <el-button
        v-if="status !== 'running'"
        type="success"
        :icon="VideoPlay"
        @click="handleStart"
        :loading="loading"
      >
        启动
      </el-button>
      <el-button
        v-if="status === 'running'"
        type="warning"
        :icon="VideoPause"
        @click="handleStop"
        :loading="loading"
      >
        停止
      </el-button>
      <el-button
        type="primary"
        :icon="Refresh"
        @click="handleReload"
        :loading="loading"
      >
        重载
      </el-button>
    </el-button-group>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { VideoPlay, VideoPause, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const pluginStore = usePluginStore()

const loading = ref(false)

const status = computed(() => {
  const plugin = pluginStore.pluginsWithStatus.find(p => p.id === props.pluginId)
  return plugin?.status || 'stopped'
})

async function handleStart() {
  try {
    loading.value = true
    await pluginStore.start(props.pluginId)
    ElMessage.success('插件启动成功')
  } catch (error: any) {
    ElMessage.error(error.message || '启动失败')
  } finally {
    loading.value = false
  }
}

async function handleStop() {
  try {
    await ElMessageBox.confirm('确定要停止该插件吗？', '确认', {
      type: 'warning'
    })
    loading.value = true
    await pluginStore.stop(props.pluginId)
    ElMessage.success('插件已停止')
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || '停止失败')
    }
  } finally {
    loading.value = false
  }
}

async function handleReload() {
  try {
    await ElMessageBox.confirm('确定要重载该插件吗？', '确认', {
      type: 'warning'
    })
    loading.value = true
    await pluginStore.reload(props.pluginId)
    ElMessage.success('插件重载成功')
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || '重载失败')
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.plugin-actions {
  display: flex;
  gap: 8px;
}
</style>

