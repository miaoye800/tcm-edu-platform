<template>
  <div class="themes" role="group" aria-label="导图配色">
    <button
      v-for="item in mindmapThemes"
      :key="item.id"
      class="swatch"
      type="button"
      :class="{ active: item.id === themeId }"
      :style="{ '--a': item.swatches[0], '--b': item.swatches[1], '--c': item.swatches[2] }"
      :title="`${item.name} · ${item.vibe}`"
      :aria-label="`${item.name}配色`"
      :aria-pressed="item.id === themeId"
      @click="$emit('change', item.id)"
    >
      <i aria-hidden="true" />
      <span>{{ item.name }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { mindmapThemes } from '../../config/mindmapThemes'

defineProps<{ themeId: string }>()
defineEmits<{ change: [id: string] }>()
</script>

<style scoped lang="scss">
.themes {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.swatch {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 6px 3px 4px;
  border: 1px solid transparent;
  border-radius: var(--qh-radius-pill);
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: 11px;
  /* flex 里会被压成竖排，两字主题名必须锁成一行。 */
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease), background-color var(--qh-duration-fast) var(--qh-ease);

  &:hover {
    color: var(--qh-fg);
    background: var(--qh-surface-hover);
  }

  &:focus-visible {
    outline: 2px solid var(--qh-accent-line);
    outline-offset: 1px;
  }

  &.active {
    color: var(--qh-fg);
    border-color: var(--qh-line-strong);
    background: var(--qh-surface-hover);
  }
}

/* 三段小样：根色 / 一级色 / 线色。比双色更能说明这套配色长什么样。 */
i {
  display: block;
  width: 22px;
  height: 12px;
  border-radius: 2px;
  background:
    linear-gradient(90deg, var(--a) 0 34%, transparent 34%),
    linear-gradient(90deg, var(--b) 34% 72%, transparent 72%),
    linear-gradient(90deg, var(--c) 72% 100%, transparent 100%);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--qh-fg) 12%, transparent);
}

@media (max-width: 1330px) {
  .swatch span {
    display: none;
  }
}
</style>
