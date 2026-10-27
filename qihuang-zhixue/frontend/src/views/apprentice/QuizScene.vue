<template>
  <div class="scene-quiz">
    <StorySpecimenCard
      v-if="question"
      class="scene-quiz__specimen"
      :image="imageUrl"
      :index="index + 1"
      :total="total"
    />
    <StoryChoiceStack
      :options="options"
      :label="prompt"
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
import StorySpecimenCard from '../../components/story/StorySpecimenCard.vue'
import type { ProfileSessionQuestion } from '../../types/profile'

const props = withDefaults(
  defineProps<{
    question: ProfileSessionQuestion | null
    index: number
    total: number
    imageUrl?: string
    prompt?: string
    selected?: string
    committing?: string
    disabled?: boolean
    bubbleOpen?: boolean
    placeholder?: string
  }>(),
  { imageUrl: '', prompt: '', selected: '', committing: '', disabled: false, bubbleOpen: false, placeholder: '说说你的判断：这味药是哪一样功效……' },
)
const emit = defineEmits<{ choose: [option: string]; 'toggle-bubble': []; 'submit-note': [text: string] }>()

const NOTE_ID = '__note__'
const SEALS = ['甲', '乙', '丙', '丁']

const options = computed<StoryChoiceOption[]>(() => [
  ...(props.question?.options ?? []).map((option, index) => ({ id: option, label: option, index: SEALS[index] ?? String(index + 1) })),
  { id: NOTE_ID, label: '自己作答', index: '', icon: 'brush' as const },
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
.scene-quiz {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--qh-space-8);
  width: 100%;
}

.scene-quiz__specimen {
  flex: none;
}

@media (max-width: 767px) {
  .scene-quiz {
    flex-direction: column;
    align-items: stretch;
    gap: var(--qh-space-3);
  }

  .scene-quiz__specimen {
    width: 100%;
  }

  .scene-quiz__specimen :deep(.story-specimen-card__media) {
    aspect-ratio: 16 / 9;
  }
}
</style>
