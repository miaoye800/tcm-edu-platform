<template>
  <div class="seal" :class="`is-${status}`">
    <i class="seal-mark" aria-hidden="true">藏</i>
    <span class="seal-text">{{ message }}</span>
  </div>
</template>

<script setup lang="ts">
import type { SaveStatus } from '../../composables/useMindmapStudio'

defineProps<{ status: SaveStatus; message: string }>()
</script>

<style scoped lang="scss">
.seal {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* 朱砂小印：项目里炼丹步骤条与文档页都用这一枚，尺寸与旋转角保持一致。 */
.seal-mark {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 1px solid var(--qh-accent);
  border-radius: var(--qh-radius-seal);
  color: var(--qh-accent);
  font-family: var(--qh-font-reading);
  font-size: 13px;
  line-height: 1;
  transform: rotate(-4deg);
  transition: opacity var(--qh-duration-fast) var(--qh-ease), border-color var(--qh-duration-fast) var(--qh-ease);
}

.seal-text {
  color: var(--qh-fg-muted);
  font-size: var(--qh-text-footnote);
  white-space: nowrap;
}

.seal.is-saving .seal-mark {
  animation: seal-breathe 900ms var(--qh-ease) infinite;
}

.seal.is-conflict .seal-mark,
.seal.is-error .seal-mark {
  border-color: var(--qh-accent-strong);
  color: var(--qh-accent-strong);
}

.seal.is-conflict .seal-text,
.seal.is-error .seal-text {
  color: var(--qh-accent-strong);
}

.seal.is-saved .seal-mark {
  opacity: 0.86;
}

@keyframes seal-breathe {
  50% {
    opacity: 0.35;
  }
}

@media (prefers-reduced-motion: reduce) {
  .seal.is-saving .seal-mark {
    animation: none;
    opacity: 0.6;
  }
}
</style>
