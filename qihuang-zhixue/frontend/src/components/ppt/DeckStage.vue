<template>
  <div class="stage" :style="vars">
    <Transition name="page">
      <div v-if="slide" :key="slide.id" class="stage-page">
        <SlideRenderer
          :slide="slide"
          :theme-id="themeId"
          :index="current"
          :total="slides.length"
          :deck-title="deckTitle"
          :design="design"
          :font-pair-id="design?.type_pair"
          interactive
          @update="$emit('update', $event)"
          @update-column="$emit('update-column', $event)"
          @add-bullet="$emit('add-bullet')"
          @remove-bullet="$emit('remove-bullet', $event)"
        />
      </div>
    </Transition>

    <template v-if="slides.length > 1">
      <button class="nav prev" type="button" aria-label="上一页" :disabled="current === 0" @click="$emit('go', current - 1)">
        ‹
      </button>
      <button
        class="nav next"
        type="button"
        aria-label="下一页"
        :disabled="current >= slides.length - 1"
        @click="$emit('go', current + 1)"
      >
        ›
      </button>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { pptThemeVars } from '../../config/pptThemes'
import type { SlideNode } from '../../types/ppt'
import type { DeckDesign } from '../../types/design'
import SlideRenderer from './SlideRenderer.vue'

const props = defineProps<{
  slides: SlideNode[]
  current: number
  themeId: string
  deckTitle: string
  /** 设计单：纸面处方与角饰从它来。没有设计单时用默认纸、不画角饰。 */
  design?: DeckDesign | null
}>()

const emit = defineEmits<{
  update: [patch: Partial<SlideNode>]
  'update-column': [payload: { index: number; patch: { heading?: string; items?: string[] } }]
  'add-bullet': []
  'remove-bullet': [index: number]
  go: [index: number]
}>()

const vars = computed(() => pptThemeVars(props.themeId))
const slide = computed(() => props.slides[props.current] ?? null)

/**
 * 键盘翻页。
 *
 * 必须排除输入框：编辑标题时按空格是要打字的，不能被当成「下一页」。
 */
const onKeydown = (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
  if (event.key === 'ArrowRight' || event.key === 'PageDown') {
    event.preventDefault()
    emit('go', Math.min(props.current + 1, props.slides.length - 1))
  } else if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
    event.preventDefault()
    emit('go', Math.max(props.current - 1, 0))
  } else if (event.key === 'Home') {
    emit('go', 0)
  } else if (event.key === 'End') {
    emit('go', props.slides.length - 1)
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped lang="scss">
/*
 * 舞台：16:9，宽度吃满可用空间但有上限，居中。
 * container-type 在这里声明，SlideSurface 内部的 cqw 才有正确的参照物。
 */
.stage {
  position: relative;
  width: min(1080px, 100%);
  aspect-ratio: 16 / 9;
  margin-inline: auto;
  border-radius: var(--qh-radius-card-sm);
  background: var(--ppt-bg, var(--qh-surface-raised));
  box-shadow: var(--qh-shadow-2);
  container-type: inline-size;
  overflow: hidden;
}

.stage-page {
  position: absolute;
  inset: 0;
}

.nav {
  position: absolute;
  top: 50%;
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid color-mix(in srgb, var(--ppt-line) 80%, transparent);
  border-radius: 50%;
  color: color-mix(in srgb, var(--ppt-fg) 62%, transparent);
  background: color-mix(in srgb, var(--ppt-bg) 82%, transparent);
  font-size: 20px;
  line-height: 1;
  opacity: 0;
  cursor: pointer;
  transform: translateY(-50%);
  transition: opacity var(--qh-duration-fast) var(--qh-ease), color var(--qh-duration-fast) var(--qh-ease);
}

.stage:hover .nav {
  opacity: 1;
}

.nav:hover:not(:disabled) {
  color: var(--ppt-accent);
}

.nav:disabled {
  cursor: default;
  opacity: 0;
}

.prev {
  left: 12px;
}

.next {
  right: 12px;
}

/* 翻页：12px 横向位移 + 淡入，180ms。位移比缩放更接近「翻纸」的体感。 */
.page-enter-active,
.page-leave-active {
  transition: opacity 180ms var(--qh-ease), transform 180ms var(--qh-ease);
}

.page-enter-from {
  opacity: 0;
  transform: translateX(12px);
}

.page-leave-to {
  opacity: 0;
  transform: translateX(-12px);
}

@media (prefers-reduced-motion: reduce) {
  .page-enter-active,
  .page-leave-active {
    transition: none;
  }
}
</style>
