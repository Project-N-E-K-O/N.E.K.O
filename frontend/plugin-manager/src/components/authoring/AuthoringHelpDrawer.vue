<template>
  <el-drawer
    :model-value="visible"
    :title="t('authoring.help')"
    size="min(430px, 94vw)"
    class="authoring-help-drawer"
    @update:model-value="emit('update:visible', $event)"
  >
    <p class="authoring-muted">{{ t('authoring.helpIntro') }}</p>
    <article class="authoring-help-article">
      <span class="authoring-feature-icon"
        ><el-icon :size="26"><Reading /></el-icon
      ></span>
      <h2>{{ t('authoring.helpStart') }}</h2>
      <p>{{ t('authoring.helpStartBody') }}</p>
      <pre>{{ commands.start }}</pre>
    </article>
    <article class="authoring-help-article">
      <span class="authoring-feature-icon"
        ><el-icon :size="26"><Reading /></el-icon
      ></span>
      <h2>{{ t('authoring.helpChecks') }}</h2>
      <p>{{ t('authoring.helpChecksBody') }}</p>
      <pre>{{ commands.checks }}</pre>
    </article>
    <article class="authoring-help-article">
      <span class="authoring-feature-icon"
        ><el-icon :size="26"><Reading /></el-icon
      ></span>
      <h2>{{ t('authoring.helpPublish') }}</h2>
      <p>{{ t('authoring.helpPublishBody') }}</p>
      <pre>{{ commands.publish }}</pre>
    </article>
  </el-drawer>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Reading } from '@element-plus/icons-vue'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ 'update:visible': [visible: boolean] }>()
const { t } = useI18n()
// Commands are documentation only. The workbench never executes them.
const commands = {
  start: 'uv run neko-plugin init <plugin_id> --type plugin --name "My Plugin" --output "<parent-directory>/<plugin_id>"',
  checks: 'uv run neko-plugin check "<parent-directory>/<plugin_id>"\nuv run neko-plugin check "<parent-directory>/<plugin_id>" --release',
  publish: 'uv run neko-plugin build "<parent-directory>/<plugin_id>"\nuv run neko-plugin publish "<parent-directory>/<plugin_id>"',
}
</script>
