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
  border-radius: 14px;
  background: color-mix(in srgb, var(--el-bg-color) 78%, transparent);
  backdrop-filter: blur(16px) saturate(1.4);
  -webkit-backdrop-filter: blur(16px) saturate(1.4);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 40%, transparent);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 24%, transparent),
    0 2px 8px color-mix(in srgb, var(--el-text-color-primary) 3%, transparent);
}
</style>
