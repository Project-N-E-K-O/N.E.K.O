<template>
  <div class="power-home">
    <PowerBar @changed="board?.load()" />
    <FamiliarBoard ref="board" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import FamiliarBoard from '@/components/power/home/FamiliarBoard.vue'
import PowerBar from '@/components/power/home/PowerBar.vue'

const board = ref<InstanceType<typeof FamiliarBoard> | null>(null)
</script>

<style scoped>
.power-home { max-width: 1400px; margin: 0 auto; padding: var(--spacing-lg, 24px); display: flex; flex-direction: column; gap: 16px; }
</style>
