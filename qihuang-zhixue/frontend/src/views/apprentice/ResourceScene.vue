<template>
  <div class="scene-resource">
    <StoryChoiceStack
      :options="options"
      label="药宝回答"
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
    types: string[]
    selected?: string
    committing?: string
    disabled?: boolean
    bubbleOpen?: boolean
    placeholder?: string
  }>(),
  { selected: '', committing: '', disabled: false, bubbleOpen: false, placeholder: '我想要带着药材图片的短剧和病例拆解……' },
)
const emit = defineEmits<{ choose: [type: string]; 'toggle-bubble': []; 'submit-note': [text: string] }>()

const NOTE_ID = '__note__'
const SEALS = ['谱', '剧', '歌', '案']

const options = computed<StoryChoiceOption[]>(() => [
  ...props.types.map((type, index) => ({ id: type, label: type, index: SEALS[index] ?? '宝' })),
  { id: NOTE_ID, label: '自己想要别的', index: '', icon: 'brush' as const },
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
.scene-resource {
  display: flex;
  justify-content: flex-end;
  width: 100%;
}
</style>
