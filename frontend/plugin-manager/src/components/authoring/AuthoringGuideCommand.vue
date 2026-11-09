<template>
  <figure class="authoring-guide-command">
    <figcaption v-if="label" class="authoring-small authoring-muted">{{ label }}</figcaption>
    <div class="authoring-guide-command__row">
      <code>{{ command }}</code>
      <el-button size="small" text :icon="copied ? Check : CopyDocument" @click="copy">
        {{ copied ? guide.labels.copied : guide.labels.copy }}
      </el-button>
    </div>
  </figure>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Check, CopyDocument } from '@element-plus/icons-vue'
import { publishGuide } from '@/components/authoring/publishGuide'

/** One command to run in a terminal, with a copy button. */
const props = defineProps<{ command: string; label?: string }>()
const { locale } = useI18n()
const guide = computed(() => publishGuide(locale.value))
const copied = ref(false)
let reset: ReturnType<typeof setTimeout> | undefined
onUnmounted(() => clearTimeout(reset))

async function copy() {
  try {
    await navigator.clipboard.writeText(props.command)
    copied.value = true
    clearTimeout(reset)
    reset = setTimeout(() => (copied.value = false), 1600)
  } catch {
    ElMessage.error(guide.value.labels.copyFailed)
  }
}
</script>

<style scoped>
.authoring-guide-command {
  margin: 0;
}
.authoring-guide-command__row {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-xs);
  padding: 6px 6px 6px var(--spacing-sm);
  border-radius: var(--radius-control);
  background: var(--surface-inset);
  border: 1px solid var(--surface-border);
}
.authoring-guide-command__row code {
  flex: 1;
  min-width: 0;
  padding-top: 3px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
