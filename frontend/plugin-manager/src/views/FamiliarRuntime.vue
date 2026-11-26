<template>
  <div class="familiar-runtime">
    <header class="page-heading">
      <div>
        <h1>{{ t('power.runtime.title') }}</h1>
        <p>{{ t('power.runtime.subtitle') }}</p>
      </div>
    </header>

    <el-tabs v-model="tab" data-testid="runtime-tabs">
      <el-tab-pane :label="t('power.runtime.tabs.runtime')" name="runtime">
        <RuntimeTab @loaded="runtime = $event" />
      </el-tab-pane>
      <el-tab-pane :label="t('power.runtime.tabs.plugins')" name="plugins" lazy>
        <DshPluginsTab :plugins="runtime?.plugins ?? []" :installed="runtime?.dsh.installed ?? false" />
      </el-tab-pane>
      <el-tab-pane :label="t('power.runtime.tabs.builtin')" name="builtin" lazy>
        <BuiltinTab />
      </el-tab-pane>
      <el-tab-pane :label="t('power.runtime.tabs.permissions')" name="permissions" lazy>
        <PermissionsTab />
        <LandingCard />
      </el-tab-pane>
      <el-tab-pane :label="t('power.runtime.tabs.roles')" name="roles" lazy>
        <RoleModelsTab />
      </el-tab-pane>
      <el-tab-pane :label="t('power.runtime.tabs.window')" name="window" lazy>
        <WindowTab />
        <UsageLimitCard />
      </el-tab-pane>
      <el-tab-pane :label="t('power.runtime.tabs.places')" name="places" lazy>
        <PlacesTab />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import type { RuntimeView } from '@/api/console'
import BuiltinTab from '@/components/power/BuiltinTab.vue'
import DshPluginsTab from '@/components/power/DshPluginsTab.vue'
import LandingCard from '@/components/power/LandingCard.vue'
import PermissionsTab from '@/components/power/PermissionsTab.vue'
import PlacesTab from '@/components/power/PlacesTab.vue'
import RoleModelsTab from '@/components/power/RoleModelsTab.vue'
import RuntimeTab from '@/components/power/RuntimeTab.vue'
import UsageLimitCard from '@/components/power/UsageLimitCard.vue'
import WindowTab from '@/components/power/WindowTab.vue'

const TABS = ['runtime', 'plugins', 'builtin', 'permissions', 'roles', 'window', 'places']
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const runtime = ref<RuntimeView | null>(null)
// The open tab is part of the address, so a link can point at one of them.
const tab = computed({
  get: () => (TABS.includes(String(route.query.tab)) ? String(route.query.tab) : 'runtime'),
  set: (name: string) => void router.replace({ query: { ...route.query, tab: name === 'runtime' ? undefined : name } }),
})
</script>

<style scoped>
.familiar-runtime { max-width: 1100px; margin: 0 auto; padding: var(--spacing-lg, 24px); }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
h1 { margin: 0 0 8px; font-size: 26px; }
.page-heading p { margin: 0; color: var(--el-text-color-secondary); line-height: 1.6; }
</style>
