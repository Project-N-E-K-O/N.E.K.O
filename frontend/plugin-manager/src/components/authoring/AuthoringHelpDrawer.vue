<template>
  <el-drawer
    :model-value="visible"
    :title="t('authoring.help')"
    size="min(430px, 94vw)"
    class="authoring-help-drawer"
    @update:model-value="emit('update:visible', $event)"
  >
    <p class="authoring-muted">{{ t('authoring.helpIntro') }}</p>
    <nav class="authoring-help-topics" :aria-label="t('authoring.help')">
      <button
        v-for="item in topics"
        :key="item"
        :class="{ active: topic === item }"
        :aria-current="topic === item ? 'page' : undefined"
        @click="emit('update:topic', item)"
      >
        {{ t(`authoring.${titles[item]}`) }}
      </button>
    </nav>
    <article class="authoring-help-article">
      <span class="authoring-feature-icon"
        ><el-icon :size="26"><Reading /></el-icon
      ></span>
      <h2>{{ t(`authoring.${titles[topic]}`) }}</h2>
      <p>{{ t(`authoring.${titles[topic]}Body`) }}</p>
      <pre>{{ commands[topic] }}</pre>
    </article>
  </el-drawer>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Reading } from '@element-plus/icons-vue'
import type { HelpTopic } from '@/types/authoring'

withDefaults(defineProps<{ visible: boolean; topic: HelpTopic; pluginId?: string }>(), {
  pluginId: 'hello_world',
})
const emit = defineEmits<{
  'update:visible': [visible: boolean]
  'update:topic': [topic: HelpTopic]
}>()
const { t } = useI18n()
const topics: HelpTopic[] = ['start', 'runtime', 'checks', 'publish']
const titles = {
  start: 'helpStart',
  runtime: 'helpRuntime',
  checks: 'helpChecks',
  publish: 'helpPublish',
} as const
// Commands are documentation only. The preview never executes them.
const commands = {
  start: 'uv run neko-plugin init <plugin_id>\n  --type plugin --name "My Plugin"',
  runtime:
    '@plugin_entry(id="hello")\nasync def hello(self, **_):\n    return Ok({"message": "Hello!"})',
  checks: 'uv run neko-plugin check <plugin_id>\nuv run neko-plugin check <plugin_id> --release',
  publish: 'uv run neko-plugin build <plugin_id>\nuv run neko-plugin publish <plugin_id>',
}
</script>
