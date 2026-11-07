<template>
  <div class="strip-wrap">
    <div ref="scroller" class="strip" role="tablist" aria-label="胶片条">
      <button
        v-for="(slide, index) in slides"
        :key="slide.id"
        class="thumb"
        :class="{ active: index === current }"
        type="button"
        role="tab"
        :aria-selected="index === current"
        :aria-label="`第 ${index + 1} 页：${slide.title}`"
        @click="$emit('select', index)"
      >
        <span class="thumb-page">
          <SlideSurface :slide="slide" :theme-id="themeId" :index="index" :total="slides.length" />
        </span>
        <span class="thumb-no">{{ String(index + 1).padStart(2, '0') }}</span>
      </button>

      <button v-if="slides.length < limit" class="add" type="button" aria-label="加一页" @click="$emit('add')">
        <span class="add-plus" aria-hidden="true">＋</span>
        <span class="add-text">加一页</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { SlideNode } from '../../types/ppt'
import SlideSurface from './SlideSurface.vue'

const props = defineProps<{
  slides: SlideNode[]
  current: number
  themeId: string
  limit: number
}>()

defineEmits<{ select: [index: number]; add: [] }>()

const scroller = ref<HTMLElement | null>(null)

/** 长稿翻到后面的页时，把当前缩略图滚进视野——否则用户以为卡住了。 */
watch(
  () => props.current,
  async (index) => {
    await nextTick()
    const strip = scroller.value
    const target = strip?.querySelectorAll<HTMLElement>('.thumb')[index]
    target?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' })
  },
)
</script>

<style scoped lang="scss">
.strip-wrap {
  padding: 10px 16px 12px;
  border-top: 1px solid var(--qh-line);
  background: var(--qh-surface);
}

.strip {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 4px;
  scrollbar-width: thin;
}

.thumb {
  position: relative;
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 5px;
  width: 148px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

/* 缩略图是一个独立的 cqw 容器：纸里的字号按 148px 等比缩，像真的缩印。 */
.thumb-page {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-xs);
  background: var(--qh-surface-sunken);
  container-type: inline-size;
  overflow: hidden;
  transition: border-color var(--qh-duration-fast) var(--qh-ease), box-shadow var(--qh-duration-fast) var(--qh-ease),
    transform var(--qh-duration-fast) var(--qh-ease);
}

.thumb:hover .thumb-page {
  transform: translateY(-1px);
  box-shadow: var(--qh-shadow-1);
}

.thumb.active .thumb-page {
  border-color: var(--qh-accent);
  box-shadow: 0 0 0 3px var(--qh-accent-soft);
}

.thumb:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 2px;
}

/*
 * 缩略图里不画真字：148px 宽下 2cqw 只有 3px，读不出但会糊成一片灰。
 * 把文字变成同宽的色条，反而更像真实幻灯缩略图。
 */
.thumb-page :deep(.surface) {
  padding: 4cqw 5cqw 3cqw;
}

.thumb-page :deep(.foot) {
  display: none;
}

.thumb-page :deep(.cover-kicker),
.thumb-page :deep(.cover-line),
.thumb-page :deep(.title-rule),
.thumb-page :deep(.kicker),
.thumb-page :deep(.num),
.thumb-page :deep(.dot) {
  display: none;
}

.thumb-page :deep(input),
.thumb-page :deep(.text),
.thumb-page :deep(.column-items li span),
.thumb-page :deep(.column-head span),
.thumb-page :deep(.cover-sub span),
.thumb-page :deep(.section-sub span) {
  color: transparent;
  background: color-mix(in srgb, var(--ppt-fg) 30%, transparent);
  border-radius: 1px;
}

.thumb-page :deep(.cover-title span),
.thumb-page :deep(.section-title span),
.thumb-page :deep(.title span) {
  color: transparent;
  background: color-mix(in srgb, var(--ppt-fg) 52%, transparent);
  border-radius: 1px;
}

.thumb-page :deep(.chapter-mark) {
  opacity: 0.5;
}

.thumb-no {
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.thumb.active .thumb-no {
  color: var(--qh-accent);
}

.add {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 148px;
  aspect-ratio: 16 / 9;
  border: 1px dashed var(--qh-line-strong);
  border-radius: var(--qh-radius-xs);
  color: var(--qh-fg-faint);
  background: transparent;
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease), border-color var(--qh-duration-fast) var(--qh-ease);
}

.add:hover {
  color: var(--qh-accent);
  border-color: var(--qh-accent-line);
}

.add-plus {
  font-size: 18px;
  line-height: 1;
}

.add-text {
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.5px;
}

@media (prefers-reduced-motion: reduce) {
  .thumb-page,
  .add {
    transition: none;
  }
}
</style>
