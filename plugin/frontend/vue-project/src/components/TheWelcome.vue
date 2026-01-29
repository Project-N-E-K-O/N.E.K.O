<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import WelcomeItem from './WelcomeItem.vue'
import DocumentationIcon from './icons/IconDocumentation.vue'
import ToolingIcon from './icons/IconTooling.vue'
import EcosystemIcon from './icons/IconEcosystem.vue'
import CommunityIcon from './icons/IconCommunity.vue'
import SupportIcon from './icons/IconSupport.vue'

const { t } = useI18n()

const docLinks = computed(() => t('welcome.documentation.links') as Array<{ text: string; url: string }>)
const docSeparator = computed(() => t('welcome.documentation.linkSeparator') as string)
const docLastSeparator = computed(() => t('welcome.documentation.linkLastSeparator') as string | undefined)

const communityLinks = computed(() => t('welcome.community.links') as Array<{ text: string; url: string }>)
const communitySeparator = computed(() => t('welcome.community.linkSeparator') as string)
const communityLastSeparator = computed(() => t('welcome.community.linkLastSeparator') as string | undefined)

const openReadmeInEditor = async () => {
  try {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 5000)
    
    const response = await fetch('/__open-in-editor?file=README.md', {
      signal: controller.signal
    })
    clearTimeout(timeoutId)
    
    if (!response.ok) {
      console.error('Failed to open README in editor:', response.status)
      ElMessage.warning(t('welcome.documentation.openFailed'))
    }
  } catch (error: any) {
    if (error.name === 'AbortError') {
      console.error('Request timeout opening README in editor')
      ElMessage.error(t('welcome.documentation.openTimeout'))
    } else {
      console.error('Error opening README in editor:', error)
      ElMessage.error(t('welcome.documentation.openError'))
    }
  }
}
</script>

<template>
  <WelcomeItem>
    <template #icon>
      <SupportIcon />
    </template>
    <template #heading>{{ t('welcome.about.title') }}</template>

    {{ t('welcome.about.description') }}
  </WelcomeItem>

  <WelcomeItem>
    <template #icon>
      <ToolingIcon />
    </template>
    <template #heading>{{ t('welcome.pluginManagement.title') }}</template>

    {{ t('welcome.pluginManagement.description') }}
  </WelcomeItem>

  <WelcomeItem>
    <template #icon>
      <EcosystemIcon />
    </template>
    <template #heading>{{ t('welcome.mcpServer.title') }}</template>

    {{ t('welcome.mcpServer.description') }}
  </WelcomeItem>

  <WelcomeItem>
    <template #icon>
      <DocumentationIcon />
    </template>
    <template #heading>{{ t('welcome.documentation.title') }}</template>

    {{ t('welcome.documentation.description') }}
    <template v-for="(link, index) in docLinks" :key="link.url">
      <a :href="link.url" target="_blank" rel="noopener">{{ link.text }}</a>
      <template v-if="index < docLinks.length - 1">
        <template v-if="index === docLinks.length - 2 && docLastSeparator">
          {{ docLastSeparator }}
        </template>
        <template v-else>
          {{ docSeparator }}
        </template>
      </template>
    </template>。

    <br />

    {{ t('welcome.documentation.readme') }}
    <a href="javascript:void(0)" @click="openReadmeInEditor"><code>README.md</code></a>
  </WelcomeItem>

  <WelcomeItem>
    <template #icon>
      <CommunityIcon />
    </template>
    <template #heading>{{ t('welcome.community.title') }}</template>

    {{ t('welcome.community.description') }}
    <template v-for="(link, index) in communityLinks" :key="link.url">
      <a :href="link.url" target="_blank" rel="noopener">{{ link.text }}</a>
      <template v-if="index < communityLinks.length - 1">
        <template v-if="index === communityLinks.length - 2 && communityLastSeparator">
          {{ communityLastSeparator }}
        </template>
        <template v-else>
          {{ communitySeparator }}
        </template>
      </template>
    </template>。
  </WelcomeItem>
</template>
