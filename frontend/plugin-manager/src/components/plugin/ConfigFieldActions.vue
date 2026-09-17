<template>
  <div
    class="field-actions"
    :class="{ 'has-direct-actions': inline && (canUndo || canRestore || custom) }"
  >
    <template v-if="inline">
      <el-button
        v-if="canUndo"
        link
        size="small"
        :title="t('plugins.configUi.undoField')"
        :aria-label="t('plugins.configUi.undoField')"
        @click="emit('command', 'undo')"
        >{{ t('plugins.configUi.undoShort') }}</el-button
      >
      <el-button
        v-if="canRestore"
        link
        size="small"
        :title="restoreLabel"
        :aria-label="restoreLabel"
        @click="emit('command', 'reset')"
        >{{ t('plugins.configUi.restoreInheritance') }}</el-button
      >
      <el-button
        v-else-if="custom"
        link
        size="small"
        type="danger"
        @click="emit('command', 'delete')"
        >{{ t('common.delete') }}</el-button
      >
    </template>
    <el-dropdown
      class="more-actions"
      trigger="click"
      @command="(command: string) => emit('command', command)"
    >
      <el-button
        text
        :icon="MoreFilled"
        :aria-label="t('plugins.configUi.fieldActions', { path })"
      />
      <template #dropdown
        ><el-dropdown-menu>
          <el-dropdown-item disabled>{{ type }}</el-dropdown-item>
          <template v-if="!inline">
            <el-dropdown-item v-if="canUndo" command="undo">{{
              t('plugins.configUi.undoField')
            }}</el-dropdown-item>
            <el-dropdown-item v-if="canRestore" command="reset">{{
              restoreLabel
            }}</el-dropdown-item>
            <el-dropdown-item v-else-if="custom" command="delete">{{
              t('common.delete')
            }}</el-dropdown-item>
          </template>
          <el-dropdown-item command="copy">{{ t('plugins.configUi.copyPath') }}</el-dropdown-item>
        </el-dropdown-menu></template
      >
    </el-dropdown>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { MoreFilled } from '@element-plus/icons-vue'
import { configValueText } from '@/utils/configEditor'

const props = defineProps<{
  path: string
  type: string
  inline?: boolean
  canUndo?: boolean
  canRestore?: boolean
  custom?: boolean
  baseline?: any
}>()
const emit = defineEmits<{ (e: 'command', command: string): void }>()
const { t } = useI18n()
const restoreLabel = computed(() =>
  t('plugins.configUi.restoreValue', { value: configValueText(props.baseline) })
)
</script>

<style scoped>
.field-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  flex: 0 0 auto;
}
.field-actions .el-button + .el-button {
  margin-left: 0;
}
.field-actions .el-button {
  font-size: 12px;
}
.more-actions :deep(.el-button) {
  padding: 6px;
  width: 28px;
  min-height: 28px;
}
.more-actions {
  opacity: 0;
  transition: opacity 120ms;
}
.field-actions:hover .more-actions,
.field-actions:focus-within .more-actions {
  opacity: 1;
}
@media (hover: none) {
  .more-actions {
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .more-actions {
    transition: none;
  }
}
</style>
