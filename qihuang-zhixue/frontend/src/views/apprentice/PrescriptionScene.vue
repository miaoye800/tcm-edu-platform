<template>
  <div class="scene-prescription" :aria-busy="submitting">
    <StoryChoiceStack
      :options="options"
      label="处方回答"
      :selected-id="openField"
      :committing-id="committing"
      :disabled="submitting"
      @select="onSelect"
    >
      <StoryDiscipleBubble
        v-if="openField"
        :key="openField"
        :value="openField === CASE_ID ? caseText : prescription"
        :placeholder="openField === CASE_ID ? CASE_PLACEHOLDER : RX_PLACEHOLDER"
        :aria-label="openField === CASE_ID ? '写下病例' : '写下抓药方'"
        :hint="'回车记下　Shift + 回车换行'"
        :disabled="submitting"
        @submit="onSubmitField"
        @cancel="emit('close-field')"
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
    caseText: string
    prescription: string
    submitting?: boolean
    canSubmit?: boolean
    committing?: string
    openField?: string
  }>(),
  { submitting: false, canSubmit: false, committing: '', openField: '' },
)
const emit = defineEmits<{
  submit: []
  'update:caseText': [value: string]
  'update:prescription': [value: string]
  'open-field': [field: string]
  'close-field': []
}>()

const CASE_ID = 'case'
const RX_ID = 'rx'
const SUBMIT_ID = '__submit__'
const CASE_PLACEHOLDER = '如：热毒咽痛，咽喉肿痛，脉浮数。'
const RX_PLACEHOLDER = '如：金银花15g，连翘12g。'
// Long answers would stretch the whole pill column, so the pill shows a short
// reminder of what was written instead of the full line.
const PREVIEW_CHARS = 12

const preview = (text: string, fallback: string) => {
  const trimmed = text.trim()
  if (!trimmed) return fallback
  return trimmed.length > PREVIEW_CHARS ? `${trimmed.slice(0, PREVIEW_CHARS)}…` : trimmed
}

const options = computed<StoryChoiceOption[]>(() => [
  { id: CASE_ID, label: preview(props.caseText, '记下病例'), index: '病', icon: 'brush' },
  { id: RX_ID, label: preview(props.prescription, '落笔写方'), index: '方', icon: 'brush' },
  // The only pressable action, and only once both fields hold something.
  { id: SUBMIT_ID, label: '落笔呈上', index: '呈', disabled: !props.canSubmit },
])

const onSelect = (id: string) => {
  if (id === SUBMIT_ID) {
    emit('submit')
    return
  }
  emit('open-field', id)
}

const onSubmitField = (text: string) => {
  const value = text.trim()
  if (!value) return
  if (props.openField === CASE_ID) emit('update:caseText', value)
  else emit('update:prescription', value)
  emit('close-field')
}
</script>

<style scoped lang="scss">
.scene-prescription {
  display: flex;
  width: 100%;
  justify-content: flex-end;
}
</style>
