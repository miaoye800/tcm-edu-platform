<template>
  <div class="scene-rule" :aria-busy="submitting">
    <StoryChoiceStack
      :options="options"
      label="学规回答"
      :selected-id="openField"
      :committing-id="committing"
      :disabled="submitting"
      @select="onSelect"
    >
      <StoryDiscipleBubble
        v-if="openField"
        :key="openField"
        :value="openField === MINUTES_ID ? textOf(dailyMinutes) : textOf(targetDays)"
        :placeholder="openField === MINUTES_ID ? `如：${MINUTES_HINT}` : `如：${DAYS_HINT}`"
        :aria-label="openField === MINUTES_ID ? '写下每日时长' : '写下目标周期'"
        hint="回车记下"
        input-mode="numeric"
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
    dailyMinutes: number | null
    targetDays: number | null
    submitting?: boolean
    canSubmit?: boolean
    committing?: string
    openField?: string
  }>(),
  { submitting: false, canSubmit: false, committing: '', openField: '' },
)
const emit = defineEmits<{
  submit: []
  'update:dailyMinutes': [value: number]
  'update:targetDays': [value: number]
  'open-field': [field: string]
  'close-field': []
}>()

const MINUTES_ID = 'minutes'
const DAYS_ID = 'days'
const SUBMIT_ID = '__submit__'
// Same bounds the old form enforced, kept so a stray "9999" cannot reach the
// backend as a promise nobody can keep.
const MINUTES_RANGE = [5, 180] as const
const DAYS_RANGE = [3, 90] as const
const MINUTES_HINT = '30'
const DAYS_HINT = '14'

const textOf = (value: number | null) => (value === null ? '' : String(value))

const options = computed<StoryChoiceOption[]>(() => [
  { id: MINUTES_ID, label: props.dailyMinutes === null ? '定每日时长' : `每天 ${props.dailyMinutes} 分钟`, index: '时', icon: 'brush' },
  { id: DAYS_ID, label: props.targetDays === null ? '定目标周期' : `先走 ${props.targetDays} 天`, index: '期', icon: 'brush' },
  // The only pressable action, and only once both numbers are in.
  { id: SUBMIT_ID, label: '立下此约', index: '约', disabled: !props.canSubmit },
])

const onSelect = (id: string) => {
  if (id === SUBMIT_ID) {
    emit('submit')
    return
  }
  emit('open-field', id)
}

const clamp = (raw: string, [low, high]: readonly [number, number]) => {
  const digits = raw.replace(/[^\d]/g, '')
  if (!digits) return null
  return Math.min(high, Math.max(low, Number.parseInt(digits, 10)))
}

const onSubmitField = (text: string) => {
  if (props.openField === MINUTES_ID) {
    const value = clamp(text, MINUTES_RANGE)
    if (value === null) return
    emit('update:dailyMinutes', value)
  } else {
    const value = clamp(text, DAYS_RANGE)
    if (value === null) return
    emit('update:targetDays', value)
  }
  emit('close-field')
}
</script>

<style scoped lang="scss">
.scene-rule {
  display: flex;
  width: 100%;
  justify-content: flex-end;
}
</style>
