<template>
  <aside class="tick-band" aria-label="本章刻度带">
    <p class="band-title">
      {{ chapterLabel }}
      <span>{{ total }} 个知识点 · 已点亮 {{ done }}</span>
    </p>
    <button
      v-for="item in sections"
      :key="item.section.id"
      type="button"
      class="band"
      :class="{ 'is-active': item.section.id === active, 'is-lit': item.presence === 'lit' }"
      :aria-label="`${item.section.title}，${item.total} 个知识点，已点亮 ${item.completed}`"
      @click="emit('select', item.section.id)"
    >
      <span class="band-head">
        <span class="band-title-text">{{ item.section.title }}</span>
        <span class="band-count">{{ item.completed }}/{{ item.total }}</span>
      </span>
      <span class="band-ticks" aria-hidden="true">
        <i v-for="index in item.total" :key="index" :class="{ 'is-done': index <= item.completed }" />
      </span>
      <span v-if="item.section.virtual" class="band-virtual">教材该章不分节</span>
    </button>
    <p v-if="!sections.length" class="band-empty">这个穴位在当前学科里还没挂章节。</p>
  </aside>
</template>

<script setup lang="ts">
/**
 * L1（章节特写）的 2D 刻度带。
 *
 * 刻度**不跟着 3D 转**：跟着相机转的标签会互相遮挡、会被透视压扁，这是 3D 产品最典型的
 * 失败。3D 里只留光点与经脉，文字一律回到固定屏幕位置的 HUD 上——键盘与读屏也只需要
 * 应付 HUD 这一层。
 */
import { computed } from 'vue'
import type { SectionProgress } from './meridianPresence'

const props = defineProps<{
  sections: SectionProgress[]
  active: string | null
  chapterLabel: string
}>()

const emit = defineEmits<{ (event: 'select', id: string): void }>()

const total = computed(() => props.sections.reduce((sum, item) => sum + item.total, 0))
const done = computed(() => props.sections.reduce((sum, item) => sum + item.completed, 0))
</script>

<style scoped lang="scss">
.tick-band {
  display: grid;
  width: 100%;
  max-width: 236px;
  max-height: 46vh;
  gap: 6px;
  padding: var(--qh-space-3);
  overflow: auto;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 22%, transparent);
  border-radius: var(--qh-radius-stage);
  background: color-mix(in srgb, var(--qh-color-night-soft) 80%, transparent);
  box-shadow: var(--qh-inner-highlight), var(--qh-shadow-soft);
  backdrop-filter: blur(14px);
}

.band-title {
  display: grid;
  margin: 0 0 2px;
  color: var(--qh-color-cream);
  font-family: var(--qh-font-display);
  font-size: 13px;
  letter-spacing: 0.02em;

  span {
    color: var(--qh-text-muted);
    font-family: var(--qh-font-body);
    font-size: 10px;
    letter-spacing: 0.04em;
  }
}

.band {
  display: grid;
  gap: 5px;
  padding: 7px 8px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 18%, transparent);
  border-radius: 8px;
  background: rgb(255 255 255 / 2%);
  color: var(--qh-text);
  text-align: left;
  cursor: pointer;

  &:hover,
  &:focus-visible {
    border-color: color-mix(in srgb, var(--qh-color-jade) 55%, transparent);
    background: color-mix(in srgb, var(--qh-color-jade) 10%, transparent);
    outline: none;
  }

  &.is-active {
    border-color: var(--qh-color-gold);
    background: color-mix(in srgb, var(--qh-color-gold) 12%, transparent);
  }

  &.is-lit .band-count {
    color: var(--qh-color-gold);
  }
}

.band-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.band-title-text {
  font-size: 11.5px;
  line-height: 1.35;
}

.band-count {
  flex: 0 0 auto;
  color: var(--qh-text-muted);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.band-ticks {
  display: flex;
  gap: 3px;

  i {
    width: 3px;
    height: 9px;
    border-radius: 1px;
    background: color-mix(in srgb, var(--qh-text) 18%, transparent);

    &.is-done {
      background: var(--qh-color-gold);
      box-shadow: 0 0 5px color-mix(in srgb, var(--qh-color-gold) 55%, transparent);
    }
  }
}

.band-virtual {
  color: var(--qh-text-muted);
  font-size: 9.5px;
}

.band-empty {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 11px;
  line-height: 1.6;
}
</style>
