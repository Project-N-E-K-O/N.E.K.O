<template>
  <div class="authoring-toolchain">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-skeleton v-if="page.state === 'unknown' && !error" :rows="4" animated />
    <ul v-else class="authoring-toolchain__list">
      <li
        v-for="tool in page.tools"
        :key="tool.id"
        class="authoring-toolchain__item"
        :class="`authoring-toolchain__item--${tool.state}`"
        :data-tool="tool.id"
      >
        <details :open="tool.state === 'missing'">
          <summary class="authoring-toolchain__summary">
            <el-icon class="authoring-toolchain__icon" aria-hidden="true">
              <CircleCheckFilled v-if="tool.state === 'ok'" />
              <WarningFilled v-else-if="tool.state === 'missing'" />
              <QuestionFilled v-else />
            </el-icon>
            <span class="authoring-toolchain__name">{{ TOOL_NAMES[tool.id] }}</span>
            <el-tag size="small" :type="tool.required ? 'danger' : 'info'" effect="plain">
              {{ t(tool.required ? 'authoring.toolRequired' : 'authoring.toolRecommended') }}
            </el-tag>
            <span class="authoring-toolchain__state authoring-small">
              {{ stateText(tool) }}
            </span>
          </summary>
          <div class="authoring-toolchain__help">
            <p>{{ guide.tools[tool.id].purpose }}</p>
            <h5>{{ guide.labels.howToInstall }}</h5>
            <div
              v-for="install in guide.tools[tool.id].install"
              :key="install.platform"
              class="authoring-toolchain__install"
            >
              <strong class="authoring-small">{{ install.platform }}</strong>
              <AuthoringGuideCommand
                v-for="command in install.commands"
                :key="command"
                :command="command"
              />
              <p v-if="install.note" class="authoring-small authoring-muted">{{ install.note }}</p>
            </div>
            <template v-if="guide.tools[tool.id].after?.length">
              <h5>{{ guide.labels.afterInstall }}</h5>
              <ol>
                <li v-for="text in guide.tools[tool.id].after" :key="text">{{ text }}</li>
              </ol>
            </template>
            <p v-if="guide.tools[tool.id].links?.length" class="authoring-toolchain__links">
              <a
                v-for="link in guide.tools[tool.id].links"
                :key="link.url"
                href="#"
                @click.prevent="openExternalUrl(link.url)"
                >{{ link.label }}</a
              >
            </p>
          </div>
        </details>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { CircleCheckFilled, QuestionFilled, WarningFilled } from '@element-plus/icons-vue'
import AuthoringGuideCommand from '@/components/authoring/AuthoringGuideCommand.vue'
import { publishGuide } from '@/components/authoring/publishGuide'
import { TOOL_NAMES, type ToolCheck, type ToolchainPage } from '@/components/authoring/publishFlow'
import { openExternalUrl } from '@/utils/openExternal'

/** Step 0: each tool with its state, and how to install it when it is missing. */
defineProps<{ page: ToolchainPage; error: string }>()
const { t, locale } = useI18n()
const guide = computed(() => publishGuide(locale.value))

function stateText(tool: ToolCheck) {
  if (tool.state === 'unknown') return t('authoring.publishStatusUnknown')
  if (tool.state === 'missing') {
    return t(tool.id === 'githubAccount' ? 'authoring.toolSignedOut' : 'authoring.toolMissing')
  }
  return tool.value ? t('authoring.toolReadyValue', { value: tool.value }) : t('authoring.toolReady')
}
</script>

<style scoped>
.authoring-toolchain__list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  margin: 0;
  padding: 0;
  list-style: none;
}
.authoring-toolchain__item {
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-control);
  background: var(--surface-card);
}
.authoring-toolchain__item--missing {
  border-color: color-mix(in srgb, var(--el-color-warning) 50%, var(--surface-border));
}
.authoring-toolchain__summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm);
  cursor: pointer;
}
.authoring-toolchain__icon {
  font-size: 18px;
}
.authoring-toolchain__item--ok .authoring-toolchain__icon {
  color: var(--el-color-success);
}
.authoring-toolchain__item--missing .authoring-toolchain__icon {
  color: var(--el-color-warning);
}
.authoring-toolchain__item--unknown .authoring-toolchain__icon {
  color: var(--el-text-color-secondary);
}
.authoring-toolchain__name {
  font-weight: 600;
}
.authoring-toolchain__state {
  margin-left: auto;
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
.authoring-toolchain__help {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  padding: 0 var(--spacing-md) var(--spacing-md);
  line-height: 1.7;
}
.authoring-toolchain__help p,
.authoring-toolchain__help ol {
  margin: 0;
}
.authoring-toolchain__help ol {
  padding-left: 1.4em;
}
.authoring-toolchain__help h5 {
  margin: var(--spacing-xs) 0 0;
  font-size: 13px;
}
.authoring-toolchain__install {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.authoring-toolchain__links {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-md);
}
</style>
