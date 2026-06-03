<template>
  <div class="market-workbench">
    <section class="market-workbench__main">
      <MarketPanel :embedded="false" />
    </section>
  </div>
</template>

<script setup lang="ts">
import MarketPanel from '@/components/plugin/MarketPanel.vue'
</script>

<style scoped>
.market-workbench {
  width: 100%;
  padding: 16px 0;
}

.market-workbench__main {
  width: 100%;
}
</style>
