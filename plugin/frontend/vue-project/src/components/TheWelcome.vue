<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import WelcomeItem from './WelcomeItem.vue'
import DocumentationIcon from './icons/IconDocumentation.vue'
import ToolingIcon from './icons/IconTooling.vue'
import EcosystemIcon from './icons/IconEcosystem.vue'
import CommunityIcon from './icons/IconCommunity.vue'
import SupportIcon from './icons/IconSupport.vue'

const { t } = useI18n()

const openReadmeInEditor = () => fetch('/__open-in-editor?file=README.md')
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
    <span v-html="t('welcome.documentation.links')"></span>

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
    <span v-html="t('welcome.community.links')"></span>
  </WelcomeItem>
</template>
