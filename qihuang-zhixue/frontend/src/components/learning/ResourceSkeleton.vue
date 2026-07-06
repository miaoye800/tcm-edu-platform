<template>
  <div class="resource-skeleton">
    <span />
    <strong>{{ label }}</strong>
    <p>资源会按 SSE 完成顺序逐个出现。</p>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  label: string
}>()
</script>

<style scoped lang="scss">
.resource-skeleton {
  display: grid;
  min-height: 300px;
  place-items: center;
  align-content: center;
  gap: 10px;
  color: var(--qh-text-muted);
  text-align: center;
}

.resource-skeleton strong {
  color: var(--qh-text);
  font-family: var(--qh-font-display);
  font-size: 18px;
  font-weight: 900;
}

.resource-skeleton span {
  width: 42px;
  height: 42px;
  border: 3px solid rgba(143, 208, 173, 0.16);
  border-top-color: var(--qh-color-jade);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.resource-skeleton p {
  margin: 0;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
