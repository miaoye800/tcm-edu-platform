<template>
  <main class="story-preview story-scope">
    <StoryStageFrame :scene="scene" muted :paused="true" />
    <StoryChapterBar :chapter="scene.chapter" :meta="progressLabel" @exit="noop" />

    <div class="story-preview__layer">
      <!-- 追问轮和主问题用同一套排版：舞台不换，答案竖排在右，题干在底部对话框。
           预览里把这一步也拍出来，才能看出"换题"和"换界面"的区别。 -->
      <div class="story-preview__stage">
        <ExtraQuestionScene
          v-if="extraQuestion"
          :question="extraQuestion"
          :committing="extraAnswer"
          :bubble-open="extraBubbleOpen"
          @choose="extraAnswer = $event"
          @toggle-bubble="extraBubbleOpen = !extraBubbleOpen"
          @submit-note="extraAnswer = $event"
        />
        <IdentityScene
          v-else-if="screen === 'identity'"
          :cards="identityCards"
          :selected="identityCard"
          :bubble-open="bubbleOpen"
          @choose="noop"
          @toggle-bubble="bubbleOpen = !bubbleOpen"
          @submit-note="noop"
        />
        <PrescriptionScene
          v-else-if="screen === 'prescription'"
          :case-text="caseText"
          :prescription="prescriptionText"
          :can-submit="Boolean(caseText.trim() && prescriptionText.trim())"
          :open-field="openField"
          @update:case-text="caseText = $event"
          @update:prescription="prescriptionText = $event"
          @open-field="openField = openField === $event ? '' : $event"
          @close-field="openField = ''"
          @submit="noop"
        />
        <RuleScene
          v-else-if="screen === 'rule'"
          :daily-minutes="dailyMinutes"
          :target-days="targetDays"
          :can-submit="dailyMinutes !== null && targetDays !== null"
          :open-field="openField"
          @update:daily-minutes="dailyMinutes = $event"
          @update:target-days="targetDays = $event"
          @open-field="openField = openField === $event ? '' : $event"
          @close-field="openField = ''"
          @submit="noop"
        />
        <QuizScene
          v-else
          :question="question"
          :index="0"
          :total="5"
          image-url="/story/herbs/taizishen.jpg"
          :prompt="apprenticeQuizPrompt"
          :selected="selectedOption"
          :bubble-open="bubbleOpen"
          @choose="selectedOption = $event"
          @toggle-bubble="bubbleOpen = !bubbleOpen"
          @submit-note="noop"
        />
      </div>

      <StoryDialogueBox class="story-preview__dialog" :name="dialogueSpeaker" :tone="dialogueTone" :text="dialogue" />
    </div>

    <p class="story-preview__hint">视觉预览 · 数据为样例，不连后端</p>
  </main>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import StoryStageFrame from '../components/story/StoryStageFrame.vue'
import StoryChapterBar from '../components/story/StoryChapterBar.vue'
import StoryDialogueBox from '../components/story/StoryDialogueBox.vue'
import ExtraQuestionScene from './apprentice/ExtraQuestionScene.vue'
import IdentityScene from './apprentice/IdentityScene.vue'
import QuizScene from './apprentice/QuizScene.vue'
import PrescriptionScene from './apprentice/PrescriptionScene.vue'
import RuleScene from './apprentice/RuleScene.vue'
import { apprenticeQuizPrompt, apprenticeScenes } from '../config/apprenticeStory'
import { apprenticeStoryNarration } from '../config/apprenticeStoryCues'
import type { ProfileSessionQuestion, ProfileSessionStoryQuestion } from '../types/profile'

const route = useRoute()
const screen = computed(() => {
  const value = route.query.screen
  return value === 'quiz' || value === 'prescription' || value === 'rule' ? value : 'identity'
})

const identityCards = ['考前冲刺', '临床学徒', '草木好奇者', '研读派']
const identityCard = ref('临床学徒')
const bubbleOpen = ref(route.query.bubble === '1')
const selectedOption = ref('')
// The prescription preview opens empty, exactly like the real screen: both
// fields are written by the viewer, never prefilled.
const caseText = ref('')
const prescriptionText = ref('')
const dailyMinutes = ref<number | null>(null)
const targetDays = ref<number | null>(null)
const openField = ref(typeof route.query.field === 'string' ? route.query.field : '')

const question: ProfileSessionQuestion = {
  id: 'preview_taizishen',
  question: '太子参的代表功效之一是？',
  options: ['益气健脾', '清热解毒', '理气健脾', '补气升阳'],
  topic: '补虚药·太子参功效',
  skill: '功效识记',
  herb_id: 'herb_taizishen',
  herb_name: '太子参',
  herb_hint: '平 · 甘、微苦 · 益气健脾',
}

// 追问栏也进视觉预览：它是新加的"一次只问一道"的对话栏，只能靠实拍看位置对不对。
// `?extra=choice` 换成带选项的那一道，免得只验证了自述输入框。
const EXTRA_PREVIEW: Record<string, ProfileSessionStoryQuestion> = {
  text: {
    id: 'extra_goal_scene',
    kind: 'text',
    prompt: '这次学中医，你主要是为了什么场景？大概什么时候要用上？',
    options: [],
  },
  choice: {
    id: 'extra_goal_priority',
    kind: 'choice',
    prompt: '如果这一个月只能先攻一块，你最想先拿下哪块？',
    options: ['药性功效', '配伍剂量', '辨证思路', '背诵记忆'],
  },
}
const quizExtra: ProfileSessionStoryQuestion = {
  id: 'extra_weak_cross_context',
  kind: 'text',
  prompt: '刚认过的这几味药，换到病例里换个说法，你还认得出来吗？举个你会犹豫的例子。',
  options: [],
}
const extraQuestion = computed(() => {
  const kind = route.query.extra
  if (typeof kind !== 'string') return null
  // 识药一幕的追问是"换到病例里还认不认得出"，不是序幕那句。
  if (screen.value === 'quiz') return quizExtra
  return EXTRA_PREVIEW[kind] ?? EXTRA_PREVIEW.text
})
const extraAnswer = ref(typeof route.query.answer === 'string' ? route.query.answer : '')
// 没有预设选项的追问，输入框默认展开——和线上一致，预览不能比真实多一步点击。
const extraBubbleOpen = ref(!extraQuestion.value?.options.length && route.query.bubble !== '0')

const scene = computed(() => {
  const sceneId = screen.value === 'quiz' ? 'herb-dialogue' : screen.value === 'prescription' ? 'prescription-counter' : screen.value === 'rule' ? 'make-rule' : 'master-offer'
  return apprenticeScenes.find((item) => item.sceneId === sceneId) ?? apprenticeScenes[0]
})
const dialogue = computed(() => {
  // 追问轮里，对话框说的就是这一轮的问题——题干在下方，答案在右侧。
  if (extraQuestion.value) return extraQuestion.value.prompt
  if (screen.value === 'quiz') return apprenticeQuizPrompt
  if (screen.value === 'prescription') return apprenticeStoryNarration['prescription-counter']
  if (screen.value === 'rule') return apprenticeStoryNarration['make-rule']
  // 主问题由旁白报题，线上就是这样（线上没有"师父先念一遍题干"这一段）。
  return apprenticeStoryNarration[scene.value.sceneId] ?? scene.value.dialogue
})
const dialogueSpeaker = computed(() => {
  if (extraQuestion.value) return '老中医'
  return '旁白'
})
const dialogueTone = computed<'master' | 'narration'>(() => (dialogueSpeaker.value === '旁白' ? 'narration' : 'master'))
const progressLabel = computed(() => (screen.value === 'quiz' ? '药圃图鉴 01 / 05' : ''))

const noop = () => undefined
</script>

<style scoped lang="scss">
.story-preview {
  position: relative;
  display: grid;
  height: 100dvh;
  overflow: hidden;
  background: var(--story-stage);
}

.story-preview__layer {
  position: relative;
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: var(--qh-space-4);
  height: 100dvh;
  min-height: 0;
  padding: 64px var(--story-dialog-inset) var(--story-dialog-inset);
}

.story-preview__stage {
  display: flex;
  flex: 1;
  align-items: flex-end;
  justify-content: center;
  min-height: 0;
  overflow-y: auto;
  padding-bottom: var(--qh-space-2);
}

.story-preview__dialog {
  flex: none;
  width: 100%;
}

.story-preview__hint {
  position: absolute;
  top: var(--qh-space-4);
  left: 50%;
  z-index: 20;
  margin: 0;
  padding: var(--qh-space-1) var(--qh-space-3);
  border-radius: var(--qh-radius-pill);
  background: var(--story-pill);
  color: var(--story-pill-text);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  opacity: 0.62;
  transform: translateX(-50%);
}

@media (max-width: 767px) {
  .story-preview__layer {
    gap: var(--qh-space-3);
    padding: 56px var(--qh-space-4) var(--qh-space-4);
  }

  // The chapter bar already fills this row on phones; the badge would collide
  // with the progress meta.
  .story-preview__hint {
    display: none;
  }
}
</style>
