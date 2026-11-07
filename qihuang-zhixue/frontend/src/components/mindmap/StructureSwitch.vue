<template>
  <div class="structures" role="group" aria-label="导图结构">
    <button
      v-for="item in mindmapStructures"
      :key="item.id"
      class="chip"
      type="button"
      :class="{ active: item.id === structureId }"
      :title="`${item.name} — ${item.when}`"
      :aria-label="`${item.name}结构`"
      :aria-pressed="item.id === structureId"
      @click="$emit('change', item.id)"
    >
      <svg class="glyph" viewBox="0 0 26 18" aria-hidden="true">
        <!-- 向右：根在左，三支向右 -->
        <template v-if="item.id === 'logic-right'">
          <rect x="1" y="6.5" width="7" height="5" rx="1" />
          <path d="M8 9h5M13 3v12M13 3h5M13 9h5M13 15h5" />
          <rect x="18" y="1.5" width="7" height="3" rx="1" />
          <rect x="18" y="7.5" width="7" height="3" rx="1" />
          <rect x="18" y="13.5" width="7" height="3" rx="1" />
        </template>
        <!-- 四面发散：根居中，左右各两支 -->
        <template v-else-if="item.id === 'mind-both'">
          <rect x="10" y="7" width="6" height="4" rx="1" />
          <path d="M10 9H5M16 9h5M5 9V3M5 9v6M21 9V3M21 9v6" />
          <rect x="1.5" y="1.5" width="7" height="3" rx="1" />
          <rect x="1.5" y="13.5" width="7" height="3" rx="1" />
          <rect x="17.5" y="1.5" width="7" height="3" rx="1" />
          <rect x="17.5" y="13.5" width="7" height="3" rx="1" />
        </template>
        <!-- 向下：根在顶，三支向下 -->
        <template v-else-if="item.id === 'org-down'">
          <rect x="9.5" y="1" width="7" height="4" rx="1" />
          <path d="M13 5v4M4 9h18M4 9v3M13 9v3M22 9v3" />
          <rect x="1" y="12" width="6" height="4" rx="1" />
          <rect x="10" y="12" width="6" height="4" rx="1" />
          <rect x="19" y="12" width="6" height="4" rx="1" />
        </template>
        <!-- 鱼骨：水平主脊 + 上下斜刺 -->
        <template v-else>
          <path d="M1 9h22" />
          <rect x="20" y="6" width="5" height="6" rx="1" />
          <path d="M6 9l4-5M6 9l4 5M13 9l4-5M13 9l4 5" />
        </template>
      </svg>
      <span class="name">{{ item.short }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { mindmapStructures, type StructureId } from '../../config/mindmapStructures'

defineProps<{ structureId: string }>()
defineEmits<{ change: [id: StructureId] }>()
</script>

<style scoped lang="scss">
.structures {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 7px;
  border: 1px solid transparent;
  border-radius: var(--qh-radius-pill);
  color: var(--qh-fg-muted);
  background: transparent;
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
    color: var(--qh-accent-strong);
    border-color: var(--qh-accent-line);
    background: var(--qh-accent-soft);
  }
}

.glyph {
  width: 24px;
  height: 16px;
  overflow: visible;
  fill: none;
  stroke: currentColor;
  stroke-width: 1;
  stroke-linecap: round;
  stroke-linejoin: round;

  /* 实物块用淡填充，与「线框」区分：一眼看出哪是节点、哪是连线。 */
  rect {
    fill: color-mix(in srgb, currentColor 14%, transparent);
  }
}

.name {
  font-size: 11px;
  letter-spacing: 0.3px;
  /* 不加就会在 flex 里被压成竖排：「向/右」两行。 */
  white-space: nowrap;
}

@media (max-width: 1180px) {
  .name {
    display: none;
  }
}
</style>
