<template>
  <div class="scene-identity">
    <StoryChoiceStack
      :options="options"
      label="身份回答"
      :selected-id="selected"
      :committing-id="committing"
      :disabled="disabled"
      @select="onSelect"
    >
      <StoryDiscipleBubble
        v-if="bubbleOpen"
        :placeholder="placeholder"
        :disabled="disabled"
        @submit="emit('submit-note', $event)"
        @cancel="emit('toggle-bubble')"
      />
    </StoryChoiceStack>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StoryChoiceStack, { type StoryChoiceOption } from '../../components/story/StoryChoiceStack.vue'
import StoryDiscipleBubble from '../../components/story/StoryDiscipleBubble.vue'

const props = withDefaults(
  defineProps<{
    cards: string[]
    selected?: string
    committing?: string
    disabled?: boolean
    bubbleOpen?: boolean
    placeholder?: string
  }>(),
  { selected: '', committing: '', disabled: false, bubbleOpen: false, placeholder: '我学过一些基础药理，但对中药性味还不熟……' },
)
const emit = defineEmits<{ choose: [card: string]; 'toggle-bubble': []; 'submit-note': [text: string] }>()

const NOTE_ID = '__note__'
const SEALS = ['考', '临', '草', '研']

const options = computed<StoryChoiceOption[]>(() => [
  ...props.cards.map((card, index) => ({ id: card, label: card, index: SEALS[index] ?? '答' })),
  { id: NOTE_ID, label: '自己说说', index: '', icon: 'brush' as const },
])

const onSelect = (id: string) => {
  if (id === NOTE_ID) {
    emit('toggle-bubble')
    return
  }
  emit('choose', id)
}
</script>

<style scoped lang="scss">
.scene-identity {
  display: flex;
  justify-content: flex-end;
  width: 100%;
}
</style>
