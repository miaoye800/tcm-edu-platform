<template>
  <aside class="tick-point" aria-label="知识点刻度">
    <header class="point-head">
      <p class="point-kicker">L2 · 逐点刻度</p>
      <p class="point-title">{{ section?.section.title || '未选小节' }}</p>
      <p class="point-meta">
        {{ section?.completed ?? 0 }}/{{ section?.total ?? 0 }} 已点亮
        <span v-if="section?.section.virtual">· 该章不分节</span>
      </p>
    </header>
    <ul role="list">
      <li v-for="tick in ticks" :key="tick.id">
        <button
          type="button"
          class="tick"
          :class="`is-${tick.presence}`"
          :aria-label="`${tick.name}，${tick.stateLabel}`"
          :aria-current="tick.id === activeTick ? 'true' : undefined"
          @click="emit('open', tick.id)"
        >
          <span class="tick-mark" aria-hidden="true" />
          <span class="tick-name">{{ tick.name }}</span>
          <span class="tick-state">{{ tick.stateLabel }}</span>
        </button>
      </li>
    </ul>
    <p v-if="!ticks.length" class="point-empty">这一节还没有可点的刻度（正文待补）。</p>
  </aside>
</template>

<script setup lang="ts">
/**
 * L2（穴位特写）的逐点刻度。每个刻度就是一个知识点，点了进详情。
 *
 * 不上 3D 的理由同 TickBand：五十个刻度贴在曲面上只会得到一团没法看的乱码。
 */
import { computed } from 'vue'
import type { BackendPathState } from '../../types/path'
import { presenceLabel, presenceOfNode, type SectionProgress } from './meridianPresence'

const props = defineProps<{
  section: SectionProgress | null
  states: Map<string, BackendPathState>
  activeTick: string | null
}>()

const emit = defineEmits<{ (event: 'open', id: string): void }>()

const ticks = computed(() =>
  (props.section?.section.points ?? []).map((tick) => {
    const presence = presenceOfNode(props.states.get(tick.id))
    return { ...tick, presence, stateLabel: presenceLabel[presence] }
  }),
)
</script>

<style scoped lang="scss">
.tick-point {
  display: grid;
  width: 100%;
  max-width: 236px;
  max-height: 52vh;
  gap: 8px;
  padding: var(--qh-space-3);
  overflow: auto;
  border: 1px solid color-mix(in srgb, var(--qh-color-gold) 26%, transparent);
  border-radius: var(--qh-radius-stage);
  background: color-mix(in srgb, var(--qh-color-night-soft) 82%, transparent);
  box-shadow: var(--qh-inner-highlight), var(--qh-shadow-soft);
  backdrop-filter: blur(14px);

  ul {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
}

.point-kicker {
  margin: 0;
  color: var(--qh-color-gold);
  font-size: 9.5px;
  font-weight: 800;
  letter-spacing: 0.12em;
}

.point-title {
  margin: 2px 0 0;
  color: var(--qh-color-cream);
  font-family: var(--qh-font-display);
  font-size: 13px;
  line-height: 1.4;
}

.point-meta {
  margin: 2px 0 0;
  color: var(--qh-text-muted);
  font-size: 10px;
}

.tick {
  display: grid;
  width: 100%;
  align-items: center;
  padding: 5px 6px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  color: var(--qh-text);
  font-size: 11.5px;
  text-align: left;
  cursor: pointer;
  gap: 6px;
  grid-template-columns: 8px 1fr auto;

  &:hover,
  &:focus-visible {
    border-color: color-mix(in srgb, var(--qh-color-gold) 45%, transparent);
    background: color-mix(in srgb, var(--qh-color-gold) 10%, transparent);
    outline: none;
  }

  &[aria-current='true'] {
    border-color: var(--qh-color-gold);
    background: color-mix(in srgb, var(--qh-color-gold) 16%, transparent);
  }
}

.tick-mark {
  width: 8px;
  height: 2px;
  border-radius: 1px;
  background: color-mix(in srgb, var(--qh-text) 28%, transparent);
}

.is-lit .tick-mark {
  background: var(--qh-color-gold);
  box-shadow: 0 0 6px color-mix(in srgb, var(--qh-color-gold) 60%, transparent);
}

.is-current .tick-mark {
  background: var(--qh-color-jade);
  box-shadow: 0 0 8px var(--qh-color-jade);
}

.is-deferred {
  opacity: 0.55;
}

.tick-state {
  color: var(--qh-text-muted);
  font-size: 9.5px;
  white-space: nowrap;
}

.point-empty {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 11px;
  line-height: 1.6;
}
</style>
