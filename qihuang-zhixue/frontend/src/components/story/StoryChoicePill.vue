<template>
  <button
    class="story-choice-pill"
    :class="[`is-${state}`, `icon-${icon}`]"
    type="button"
    :aria-pressed="state === 'selected'"
    :disabled="disabled"
    @click="emit('select')"
  >
    <span class="story-choice-pill__icon" aria-hidden="true">
      <svg v-if="icon === 'brush'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z" />
        <path d="M14 6l4 4" />
      </svg>
      <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.8-.8L3 21l1.9-4.6A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z" />
      </svg>
    </span>
    <span class="story-choice-pill__label">{{ label }}</span>
    <span v-if="index" class="story-choice-pill__index" aria-hidden="true">{{ index }}</span>
  </button>
</template>

<script setup lang="ts">
export type StoryChoiceState = 'default' | 'hover' | 'selected' | 'committing' | 'disabled'

withDefaults(
  defineProps<{
    label: string
    index?: string
    icon?: 'bubble' | 'brush'
    state?: StoryChoiceState
    disabled?: boolean
  }>(),
  { index: '', icon: 'bubble', state: 'default', disabled: false },
)
const emit = defineEmits<{ select: [] }>()
</script>

<style scoped lang="scss">
.story-choice-pill {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: var(--qh-space-3);
  padding: 7px var(--qh-space-5) 7px 7px;
  border: 0;
  border-radius: var(--qh-radius-pill);
  background: var(--story-pill);
  color: var(--story-pill-text);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-option);
  line-height: 1.4;
  text-align: left;
  cursor: pointer;
  transition: background var(--qh-duration-fast) var(--qh-ease), transform var(--qh-duration-fast) var(--qh-ease);
}

.story-choice-pill:hover:not(:disabled) {
  background: var(--story-pill-hover);
  transform: translateX(-2px);
}

.story-choice-pill:focus-visible {
  outline: 2px solid var(--story-gold-deep);
  outline-offset: 3px;
}

.story-choice-pill:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.story-choice-pill__icon {
  display: grid;
  flex: none;
  width: 22px;
  height: 22px;
  place-items: center;
  border-radius: 50%;
  background: var(--story-pill-icon);
  color: var(--story-cream-soft);
}

.story-choice-pill__icon svg {
  width: 13px;
  height: 13px;
}

.story-choice-pill__label {
  flex: 1;
}

.story-choice-pill__index {
  flex: none;
  color: var(--story-cream-soft);
  font-size: var(--qh-text-footnote);
  opacity: 0.7;
}

.story-choice-pill.is-selected {
  background: var(--story-gold);
  color: var(--story-ink);
}

.story-choice-pill.is-selected .story-choice-pill__icon {
  background: var(--story-ink);
  color: var(--story-gold);
}

.story-choice-pill.is-selected .story-choice-pill__index {
  color: var(--story-ink);
  opacity: 0.6;
}

.story-choice-pill.is-committing {
  background: var(--story-gold);
  color: var(--story-ink);
}

.story-choice-pill.icon-brush .story-choice-pill__icon {
  background: transparent;
  border: 1px solid var(--story-cream-soft);
  color: var(--story-cream-soft);
}
</style>
