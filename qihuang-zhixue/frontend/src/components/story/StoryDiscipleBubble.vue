<template>
  <div class="story-disciple-bubble">
    <textarea
      ref="field"
      v-model="draft"
      class="story-disciple-bubble__field"
      :placeholder="placeholder"
      :disabled="disabled"
      :aria-label="ariaLabel"
      :inputmode="inputMode"
      rows="1"
      @keydown.enter.exact.prevent="submit"
      @keydown.esc.prevent="emit('cancel')"
      @input="resize"
    />
    <p class="story-disciple-bubble__hint">{{ hint }}</p>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    placeholder?: string
    ariaLabel?: string
    disabled?: boolean
    autofocus?: boolean
    // Re-opening a field that already holds an answer should show it, not a
    // blank line. Read once on mount - the bubble is keyed per field.
    value?: string
    hint?: string
    // Numbers get the numeric keypad on touch keyboards.
    inputMode?: 'text' | 'numeric'
  }>(),
  {
    placeholder: '',
    ariaLabel: '写下你的回答',
    disabled: false,
    autofocus: true,
    value: '',
    hint: '回车呈上　Shift + 回车换行',
    inputMode: 'text',
  },
)
const emit = defineEmits<{ submit: [text: string]; cancel: [] }>()

const draft = ref(props.value)
const field = ref<HTMLTextAreaElement | null>(null)
const MAX_ROWS = 3

const resize = () => {
  const element = field.value
  if (!element) return
  element.style.height = 'auto'
  const lineHeight = 24
  element.style.height = `${Math.min(element.scrollHeight, lineHeight * MAX_ROWS + 12)}px`
}

const submit = () => {
  const text = draft.value.trim()
  if (!text || props.disabled) return
  emit('submit', text)
  draft.value = ''
  void nextTick(resize)
}

onMounted(() => {
  if (!props.autofocus) return
  void nextTick(() => {
    field.value?.focus()
    // A prefilled draft needs its height measured, otherwise the box opens at
    // one row and hides the answer behind a scrollbar.
    resize()
    const end = field.value?.value.length ?? 0
    field.value?.setSelectionRange(end, end)
  })
})

defineExpose({ focus: () => field.value?.focus() })
</script>

<style scoped lang="scss">
.story-disciple-bubble {
  position: relative;
  align-self: flex-end;
  // A fixed width keeps the bubble from shrinking to the width of the option
  // pills above it; `max-width: 100%` still lets it follow a narrow viewport.
  width: 360px;
  max-width: 100%;
  margin-top: var(--qh-space-2);
  padding: var(--qh-space-3) var(--qh-space-5) var(--qh-space-2);
  border-radius: var(--qh-radius-bubble);
  background: var(--story-cream-soft);
}

// The tail is what makes this read as "the disciple speaking" instead of a form
// field: it points back down toward the dialogue box.
.story-disciple-bubble::after {
  content: '';
  position: absolute;
  right: var(--qh-space-6);
  bottom: -5px;
  width: 12px;
  height: 12px;
  border-radius: 2px;
  background: var(--story-cream-soft);
  transform: rotate(45deg);
}

.story-disciple-bubble__field {
  display: block;
  width: 100%;
  max-height: 84px;
  padding: 0;
  border: 0;
  resize: none;
  background: transparent;
  color: var(--story-ink);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-ui);
  line-height: 1.6;
  outline: none;
}

.story-disciple-bubble__field::placeholder {
  color: var(--story-ink-faint);
}

.story-disciple-bubble__hint {
  margin: var(--qh-space-1) 0 0;
  // Muted but still readable: the placeholder may be faint, the key hint may not.
  color: var(--story-ink-soft);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.02em;
}

@media (max-width: 767px) {
  .story-disciple-bubble {
    align-self: stretch;
    width: auto;
  }
}
</style>
