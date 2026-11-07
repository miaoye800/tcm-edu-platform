<template>
  <article class="card" :style="style">
    <header class="card-head">
      <span class="badge">
        <svg viewBox="0 0 26 18" aria-hidden="true">
          <path d="M1 9h9M10 9V4M10 9v5M15 4h9M15 9h9M15 14h9" />
          <rect x="0.5" y="6.5" width="4" height="5" rx="1" />
          <rect x="22" y="2.5" width="3.5" height="3" rx="1" />
          <rect x="22" y="7.5" width="3.5" height="3" rx="1" />
          <rect x="22" y="12.5" width="3.5" height="3" rx="1" />
        </svg>
        {{ structureName }}
      </span>
      <span class="name">{{ title || '未命名导图' }}</span>
    </header>

    <dl class="metrics">
      <div>
        <dt>节点</dt>
        <dd>{{ nodes }}</dd>
      </div>
      <div>
        <dt>层数</dt>
        <dd>{{ depth }}</dd>
      </div>
      <div v-if="inferred">
        <dt>推断</dt>
        <dd class="warn">{{ inferred }}</dd>
      </div>
    </dl>

    <p v-if="note" class="note" :class="{ 'is-degraded': degraded }">{{ note }}</p>

    <footer class="card-foot">
      <div class="quick" role="group" aria-label="换结构">
        <button
          v-for="item in mindmapStructures"
          :key="item.id"
          type="button"
          class="chip"
          :class="{ active: item.id === structureId }"
          :title="item.name"
          :aria-label="item.name"
          @click="$emit('structure', item.id)"
        >{{ item.short }}</button>
      </div>
      <div class="quick" role="group" aria-label="换配色">
        <button
          v-for="item in mindmapThemes"
          :key="item.id"
          type="button"
          class="dot"
          :class="{ active: item.id === themeId }"
          :style="{ '--c': item.swatches[0] }"
          :title="item.name"
          :aria-label="item.name"
          @click="$emit('theme', item.id)"
        />
      </div>
    </footer>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { getThemePreset, mindmapThemes } from '../../config/mindmapThemes'
import { getStructure, mindmapStructures, type StructureId } from '../../config/mindmapStructures'

const props = defineProps<{
  title?: string
  nodes?: number
  depth?: number
  inferred?: number
  structureId?: string
  themeId?: string
  note?: string
  degraded?: boolean
}>()

defineEmits<{ structure: [id: StructureId]; theme: [id: string] }>()

const structureName = computed(() => getStructure(props.structureId).name)
/** 卡片跟着图纸配色走，换色时这张卡也变——它本来就是这个产物的缩略。 */
const style = computed(() => {
  const preset = getThemePreset(props.themeId)
  return {
    '--card-accent': preset.swatches[0],
    '--card-soft': preset.swatches[1],
    '--card-line': preset.swatches[2],
  }
})
</script>

<style scoped lang="scss">
.card {
  padding: 10px 11px 8px;
  border: 1px solid var(--qh-line);
  border-left: 2px solid var(--card-accent, var(--qh-accent));
  border-radius: 10px;
  background: var(--qh-surface-raised);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.badge {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
  padding: 2px 6px 2px 4px;
  border-radius: 4px;
  color: var(--card-accent, var(--qh-accent-strong));
  background: color-mix(in srgb, var(--card-accent, #2E6BE6) 10%, transparent);
  font-size: 10px;
  letter-spacing: 0.3px;
  white-space: nowrap;

  svg {
    width: 16px;
    height: 11px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1;
  }

  rect {
    fill: color-mix(in srgb, currentColor 18%, transparent);
  }
}

.name {
  overflow: hidden;
  color: var(--qh-fg);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metrics {
  display: flex;
  gap: 14px;
  margin: 8px 0 6px;

  div {
    display: flex;
    align-items: baseline;
    gap: 4px;
  }

  dt {
    color: var(--qh-fg-muted);
    font-size: 11px;
  }

  dd {
    margin: 0;
    color: var(--qh-fg);
    font-family: var(--qh-font-mono, monospace);
    font-size: 14px;
  }

  /* 推断量单独标色：它是这张图唯一需要核对的部分。 */
  .warn {
    color: #A9714B;
  }
}

/* 「按「对比鉴别」组织」：解释这张图为什么长这样，一行就够。 */
.note {
  margin: 0 0 7px;
  padding-left: 7px;
  border-left: 2px solid color-mix(in srgb, var(--card-accent, #2E6BE6) 30%, transparent);
  color: var(--qh-fg-muted);
  font-size: 11px;
  line-height: 1.5;
}

/* 域外主题要能一眼看出来：这句话是提醒，不是装饰。 */
.note.is-degraded {
  border-left-color: #A9714B;
  color: #8A5A36;
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 7px;
  border-top: 1px dashed var(--qh-line);
}

.quick {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.chip {
  padding: 2px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;

  &:hover { color: var(--qh-fg); background: var(--qh-surface-hover); }
  &.active { color: var(--card-accent, var(--qh-accent-strong)); border-color: currentColor; }
  &:focus-visible { outline: 2px solid var(--qh-accent-line); outline-offset: 1px; }
}

.dot {
  width: 13px;
  height: 13px;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--qh-fg) 16%, transparent);
  border-radius: 50%;
  background: var(--c);
  cursor: pointer;

  &.active { box-shadow: 0 0 0 2px var(--qh-surface-raised), 0 0 0 3px var(--c); }
  &:focus-visible { outline: 2px solid var(--qh-accent-line); outline-offset: 2px; }
}
</style>
