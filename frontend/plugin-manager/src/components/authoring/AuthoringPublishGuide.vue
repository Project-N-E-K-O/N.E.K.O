<template>
  <section class="authoring-guide" :aria-label="guide.labels.guide">
    <p class="authoring-guide__summary">{{ fill(page.summary) }}</p>
    <details class="authoring-guide__details" :open="open">
      <summary>
        <el-icon><Reading /></el-icon>
        <span>{{ guide.labels.guide }}</span>
      </summary>
      <div class="authoring-guide__body">
        <section v-for="section in page.sections" :key="section.title" class="authoring-guide__section">
          <h4>{{ section.title }}</h4>
          <p v-for="(text, index) in section.paragraphs ?? []" :key="`p${index}`">{{ fill(text) }}</p>
          <ol v-if="section.steps?.length">
            <li v-for="(text, index) in section.steps" :key="`s${index}`">{{ fill(text) }}</li>
          </ol>
          <AuthoringGuideCommand
            v-for="(item, index) in section.commands ?? []"
            :key="`c${index}`"
            :label="item.label ? fill(item.label) : undefined"
            :command="fill(item.command)"
          />
          <p v-if="section.links?.length" class="authoring-guide__links">
            <a
              v-for="link in section.links"
              :key="link.url"
              href="#"
              @click.prevent="openExternalUrl(link.url)"
              >{{ link.label }}</a
            >
          </p>
        </section>
      </div>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Reading } from '@element-plus/icons-vue'
import AuthoringGuideCommand from '@/components/authoring/AuthoringGuideCommand.vue'
import { fillGuide, publishGuide, type GuideParams } from '@/components/authoring/publishGuide'
import type { PublishPageId } from '@/components/authoring/publishFlow'
import { openExternalUrl } from '@/utils/openExternal'

/**
 * The beginner guide of one publish page: a summary that is always shown, and
 * the full explanation in a disclosure that starts open on the page to act on.
 */
const props = defineProps<{ pageId: PublishPageId; params: GuideParams; open: boolean }>()
const { locale } = useI18n()
const guide = computed(() => publishGuide(locale.value))
const page = computed(() => guide.value.pages[props.pageId])
const fill = (text: string) => fillGuide(text, props.params)
</script>

<style scoped>
.authoring-guide {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}
.authoring-guide__summary {
  margin: 0;
  color: var(--el-text-color-regular);
  line-height: 1.7;
}
.authoring-guide__details {
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-control);
  background: var(--surface-card);
}
.authoring-guide__details > summary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px var(--spacing-sm);
  color: var(--el-color-primary);
  font-weight: 600;
  cursor: pointer;
}
.authoring-guide__body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  padding: 0 var(--spacing-md) var(--spacing-md);
}
.authoring-guide__section {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}
.authoring-guide__section h4 {
  margin: var(--spacing-xs) 0 0;
  font-size: 14px;
}
.authoring-guide__section p,
.authoring-guide__section ol {
  margin: 0;
  line-height: 1.7;
}
.authoring-guide__section ol {
  padding-left: 1.4em;
}
.authoring-guide__links {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-md);
}
</style>
