<template>
  <div class="scene-extra">
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
import type { ProfileSessionStoryQuestion } from '../../types/profile'

// 追问和主问题用同一套排版：舞台不换、右下角竖排答案、题干在底部对话框。
// 之前把追问做成聊天气泡流水，等于把"一题一屏"改回了"一屏多问"的另一种形态，
// 用户要的是每道题都长得和"四张身份牌摆在案上"那一屏一样，只是题目在换。
const props = withDefaults(
  defineProps<{
    question: ProfileSessionStoryQuestion
    committing?: string
    disabled?: boolean
    bubbleOpen?: boolean
  }>(),
  { committing: '', disabled: false, bubbleOpen: false },
)
const emit = defineEmits<{
  choose: [option: string]
  'toggle-bubble': []
  'submit-note': [text: string]
}>()

const NOTE_ID = '__note__'
const SEALS = ['甲', '乙', '丙', '丁', '戊', '己']

const prompt = computed(() => props.question.prompt)
// 自述框的占位只给一句"怎么写"，不替他答；选项为空时它就是唯一的入口。
const placeholder = computed(() => '按你的真实情况说几句……')
const options = computed<StoryChoiceOption[]>(() => [
  ...(props.question.options ?? []).map((option, index) => ({
    id: option,
    label: option,
    index: SEALS[index] ?? String(index + 1),
  })),
  // 没有预设选项的题目（"为什么挑这件药宝"这类）只留这一项，
  // 由 view 直接把它展开，用户不用先点一下才能写字。
  { id: NOTE_ID, label: '自己说说', index: '', icon: 'brush' as const },
])
// 正在提交的那一项高亮成金色，和主问题里点选后的反馈是同一个。
// 自述提交的是一段文字而不是选项，所以按"不在选项里"判定成自己说说那一行，
// 否则写完一句屏幕上看不到任何反馈。
const selected = computed(() => {
  const value = props.committing
  if (!value) return ''
  if (value === NOTE_ID) return NOTE_ID
  return (props.question.options ?? []).includes(value) ? value : NOTE_ID
})

const onSelect = (id: string) => {
  if (id === NOTE_ID) {
    emit('toggle-bubble')
    return
  }
  emit('choose', id)
}
</script>

<style scoped lang="scss">
.scene-extra {
  display: flex;
  width: 100%;
  justify-content: flex-end;
}
</style>
