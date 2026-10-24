<template>
  <div class="wb-toolbar">
    <slot />
  </div>
</template>

<style scoped>
.wb-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 8px 14px;
  border-radius: var(--radius-panel);
  background: var(--surface-inset);
  border: 1px solid var(--surface-border);
  box-shadow: none;
}
</style>
