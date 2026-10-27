<template>
  <div class="story-dialogue-box">
    <StoryNamePlate v-if="name" class="story-dialogue-box__plate" :name="name" :tone="tone" />
    <p class="story-dialogue-box__text" aria-hidden="true">{{ visible }}<span v-if="typing" class="story-dialogue-box__caret">▍</span></p>
    <span class="story-dialogue-box__next" aria-hidden="true" />
    <p class="story-sr" aria-live="polite">{{ text }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import StoryNamePlate from './StoryNamePlate.vue'

const props = withDefaults(
  defineProps<{
    text: string
    name?: string
    tone?: 'master' | 'disciple' | 'narration'
    speedMs?: number
  }>(),
  { name: '', tone: 'master', speedMs: 42 },
)

const visible = ref('')
const typing = computed(() => visible.value.length < props.text.length)
let timer: ReturnType<typeof setInterval> | undefined

const stop = () => {
  if (timer) clearInterval(timer)
  timer = undefined
}

// The box never waits for a click: the parent swaps `text` when the video
// reaches the next beat, and this simply re-types the new line.
const start = () => {
  stop()
  visible.value = ''
  if (!props.text) return
  if (props.speedMs <= 0) {
    visible.value = props.text
    return
  }
  timer = setInterval(() => {
    if (visible.value.length >= props.text.length) {
      stop()
      return
    }
    visible.value = props.text.slice(0, visible.value.length + 1)
  }, props.speedMs)
}

watch(() => props.text, start, { immediate: true })
onBeforeUnmount(stop)
</script>

<style scoped lang="scss">
.story-dialogue-box {
  position: relative;
  width: 100%;
  min-height: 128px;
  padding: var(--qh-space-6) var(--qh-space-8) var(--qh-space-5);
  border-radius: var(--qh-radius-dialog);
  background: var(--story-cream);
  box-shadow: var(--qh-shadow-panel);
}

.story-dialogue-box__plate {
  position: absolute;
  top: -17px;
  left: var(--qh-space-10);
}

.story-dialogue-box__text {
  margin: 0;
  padding-top: var(--qh-space-3);
  color: var(--story-ink);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-lead);
  line-height: 1.9;
  letter-spacing: 0.01em;
}

.story-dialogue-box__caret {
  color: var(--story-gold-deep);
}

.story-dialogue-box__next {
  position: absolute;
  right: var(--qh-space-8);
  bottom: var(--qh-space-5);
  width: 9px;
  height: 9px;
  background: var(--story-gold-deep);
  opacity: 0.55;
  transform: rotate(45deg);
}
</style>
