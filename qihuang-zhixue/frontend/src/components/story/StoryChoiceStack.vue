<template>
  <div class="story-choice-stack" role="group" :aria-label="label">
    <div class="story-choice-stack__options">
      <StoryChoicePill
        v-for="(option, position) in options"
        :key="option.id"
        :label="option.label"
        :index="option.index ?? String(position + 1)"
        :icon="option.icon"
        :state="option.id === selectedId ? 'selected' : option.id === committingId ? 'committing' : 'default'"
        :disabled="disabled || option.disabled"
        @select="emit('select', option.id)"
      />
    </div>
    <slot />
  </div>
</template>

<script setup lang="ts">
import StoryChoicePill from './StoryChoicePill.vue'

// `disabled` on a single option lets one entry of the menu stay locked (the
// prescription's "落笔呈上" is only pressable once both fields are filled) while
// the rest of the group stays usable.
export type StoryChoiceOption = { id: string; label: string; index?: string; icon?: 'bubble' | 'brush'; disabled?: boolean }

withDefaults(
  defineProps<{
    options: StoryChoiceOption[]
    label: string
    selectedId?: string | null
    committingId?: string | null
    disabled?: boolean
  }>(),
  { selectedId: null, committingId: null, disabled: false },
)
const emit = defineEmits<{ select: [id: string] }>()
</script>

<style scoped lang="scss">
.story-choice-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--qh-space-2);
  max-width: min(420px, 44vw);
}

// One width for the whole group: the longest label sets it and every pill
// follows, so the column reads as a menu instead of a ragged staircase.
// `max-content` is measured from the pills only - the answer bubble below is
// wider on purpose and must not stretch them.
.story-choice-stack__options {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--qh-space-2);
  width: max-content;
  max-width: 100%;
}

@media (max-width: 1023px) {
  .story-choice-stack {
    max-width: min(420px, 60vw);
  }
}

@media (max-width: 767px) {
  .story-choice-stack {
    // Full width here, not shrink-to-fit: on phones the column should reach both
    // edges instead of hugging the longest label.
    width: 100%;
    align-items: stretch;
    max-width: none;
  }

  .story-choice-stack__options {
    width: 100%;
  }
}
</style>
