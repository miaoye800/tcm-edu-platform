<template>
  <main class="apprentice-story story-scope">
    <StoryStageFrame
      :scene="scene"
      :video-fixture="videoFixture"
      :muted="false"
      @video-ended="onVideoEnded"
      @playback-time="playbackSeconds = $event"
    />

    <StoryChapterBar :chapter="displayChapter" :meta="progressLabel" @exit="leaveStory" />

    <section v-if="error" class="apprentice-story__state">
      <StateError title="这一幕暂时无法继续" :description="error" action-text="重新连接" size="sm" @action="restartFromSafePoint" />
    </section>
    <section v-else-if="loading" class="apprentice-story__state">
      <StateLoading title="药灯正在亮起" description="正在接续本次收徒会话。" size="sm" />
    </section>

    <div v-else class="apprentice-story__layer">
      <!-- 每一题都占满这一屏，排版完全一样：舞台不换，答案竖排在右侧，
           题干写在底部对话框里。追问只是"换了一道题"，不是换个界面。 -->
      <div class="apprentice-story__stage">
        <template v-if="interactionReady">
          <ExtraQuestionScene
            v-if="activeExtraQuestion"
            :key="activeExtraQuestion.id"
            :question="activeExtraQuestion"
            :committing="extraCommit"
            :disabled="submitting"
            :bubble-open="extraBubbleOpen"
            @choose="chooseExtraAnswer"
            @toggle-bubble="extraBubbleOpen = !extraBubbleOpen"
            @submit-note="submitExtraNote"
          />

          <QuizScene
            v-else-if="showQuizChoices && currentQuestion"
            :question="currentQuestion"
            :index="activeQuestionIndex"
            :total="questions.length"
            :image-url="herbPhotoFor(currentQuestion)"
            :prompt="currentQuizPrompt"
            :selected="questionAnswers[currentQuestion.id] ?? ''"
            :committing="committingOption"
            :disabled="stageBlocked"
            :bubble-open="quizBubbleOpen"
            @choose="chooseQuizOption"
            @toggle-bubble="quizBubbleOpen = !quizBubbleOpen"
            @submit-note="submitQuizNote"
          />

          <IdentityScene
            v-else-if="scene.kind === 'identity'"
            :cards="identityCards"
            :selected="identityCard"
            :committing="committingOption"
            :disabled="stageBlocked || !canSubmit"
            :bubble-open="identityBubbleOpen"
            @choose="chooseIdentity"
            @toggle-bubble="identityBubbleOpen = !identityBubbleOpen"
            @submit-note="submitIdentityNote"
          />

          <ResourceScene
            v-else-if="scene.kind === 'resource'"
            :types="resourceTypes"
            :selected="resourceType"
            :committing="committingOption"
            :disabled="stageBlocked || !canSubmit"
            :bubble-open="resourceBubbleOpen"
            @choose="chooseResource"
            @toggle-bubble="resourceBubbleOpen = !resourceBubbleOpen"
            @submit-note="submitResourceNote"
          />

          <PrescriptionScene
            v-else-if="scene.kind === 'prescription'"
            :case-text="caseText"
            :prescription="prescription"
            :submitting="stageBlocked"
            :can-submit="canSubmit"
            :committing="committingOption"
            :open-field="prescriptionField"
            @update:case-text="caseText = $event"
            @update:prescription="prescription = $event"
            @open-field="togglePrescriptionField"
            @close-field="prescriptionField = ''"
            @submit="onSceneSubmit"
          />

          <RuleScene
            v-else-if="scene.kind === 'rule'"
            :daily-minutes="dailyMinutes"
            :target-days="targetDays"
            :submitting="stageBlocked"
            :can-submit="canSubmit"
            :committing="committingOption"
            :open-field="ruleField"
            @update:daily-minutes="dailyMinutes = $event"
            @update:target-days="targetDays = $event"
            @open-field="toggleRuleField"
            @close-field="ruleField = ''"
            @submit="onSceneSubmit"
          />
        </template>
      </div>

      <StoryDialogueBox
        v-if="showDialogue"
        class="apprentice-story__dialog"
        :name="speakerName"
        :tone="speakerTone"
        :text="dialogueText"
      />
    </div>

    <StoryToast :message="toastMessage" />
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StoryStageFrame from '../components/story/StoryStageFrame.vue'
import StoryChapterBar from '../components/story/StoryChapterBar.vue'
import StoryDialogueBox from '../components/story/StoryDialogueBox.vue'
import StoryToast from '../components/story/StoryToast.vue'
import StateError from '../components/state/StateError.vue'
import StateLoading from '../components/state/StateLoading.vue'
import ExtraQuestionScene from './apprentice/ExtraQuestionScene.vue'
import IdentityScene from './apprentice/IdentityScene.vue'
import QuizScene from './apprentice/QuizScene.vue'
import ResourceScene from './apprentice/ResourceScene.vue'
import PrescriptionScene from './apprentice/PrescriptionScene.vue'
import RuleScene from './apprentice/RuleScene.vue'
import { apprenticeQuizPrompt, apprenticeVisual, sceneForStep } from '../config/apprenticeStory'
import { apprenticeStoryNarration, dialogueAt } from '../config/apprenticeStoryCues'
import { storyAssetForStage, type StoryVideoFixtureMode } from '../config/apprenticeStoryAssets'
import {
  advancePerformance,
  applySession,
  beginStepSubmission,
  canSubmitStep,
  initialApprenticeStoryState,
  restoreApprenticeStoryState,
  sceneForState,
  type ApprenticeStoryState,
} from '../composables/apprenticeStoryMachine'
import { answerProfileSession, finishProfileSession, startProfileSession } from '../services/profileApi'
import { saveOnboardingState } from '../services/onboardingStorage'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import type { ProfileSessionQuestion, ProfileSessionResponse } from '../types/profile'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const profileStore = useProfileStore()
const state = ref<ApprenticeStoryState>(initialApprenticeStoryState())
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const playbackSeconds = ref(0)
const toastMessage = ref('')
let choiceTimer: ReturnType<typeof setTimeout> | undefined
let toastTimer: ReturnType<typeof setTimeout> | undefined

const identityCard = ref('临床学徒')
const utterance = ref('')
const questionAnswers = reactive<Record<string, string>>({})
const questionNotes = reactive<Record<string, string>>({})
// Per-question reaction time in ms: from the photograph appearing to the answer
// being given. `quizShownAt` is a plain variable on purpose - it never renders.
const questionResponseTimes = reactive<Record<string, number>>({})
let quizShownAt: number | null = null
// Wall clock for the current question, started when the film ends - i.e. the
// moment the disciple can actually answer - and read once at submission. The
// backend turns it into a per-question-type speed bucket and never stores ms.
let stepShownAt: number | null = null
// Both fields start empty on purpose: the examples belong in the placeholders,
// so whatever reaches the backend was actually written by the disciple.
const caseText = ref('')
const prescription = ref('')
const prescriptionField = ref('')
const resourceType = ref('知识图谱')
const resourceCustom = ref('')
// Both numbers start unset: the pact is something the disciple writes, not a
// default they can nod through.
const dailyMinutes = ref<number | null>(null)
const targetDays = ref<number | null>(null)
const ruleField = ref('')
const activeQuestionIndex = ref(0)
const quizBubbleOpen = ref(false)
const identityBubbleOpen = ref(false)
const resourceBubbleOpen = ref(false)
const committingOption = ref('')
// The film always plays to its last frame; only then may the question appear.
const videoEnded = ref(false)
// The prologue film is long; the handshake hides behind it instead of in front.
let prefetchPromise: Promise<ProfileSessionResponse | null> | null = null
let prefetchError = ''
let performanceTimer: ReturnType<typeof setTimeout> | undefined

const storageKey = computed(() => `${apprenticeVisual.storagePrefix}${auth.user?.user_id ?? 'anonymous'}`)
const scene = computed(() => sceneForState(state.value))
const step = computed(() => state.value.session?.step ?? null)
const identityCards = computed(() => step.value?.payload.identity_cards ?? [])
const questions = computed<ProfileSessionQuestion[]>(() => step.value?.payload.questions ?? [])
// The extra questions belong to whichever act is on screen; the backend decides
// what each one is evidence for, so nothing but the prompt reaches the client.
const storyQuestions = computed(() => step.value?.payload.story_questions ?? [])
const storyAnswers = reactive<Record<string, string>>({})

// 追问和主问题是同一件事的两种排列：都是"一屏一题"。
//
// 顺序是「主问题 → 追问 → 落卷」：追问的措辞本身就依赖主问题（"刚认过的这几味
// 药…"、"为什么挑这件药宝"），抢先问会问在没有发生过的事情上。
const extraIndex = ref(0)
const extraCommit = ref('')
// 主问题答完才开始追问；第一步（没有追问的幕）直接落卷。
const extraActive = ref(false)
const extraBubbleOpen = ref(false)
let extraTimer: ReturnType<typeof setTimeout> | undefined

const activeExtraQuestion = computed(() => {
  if (!extraActive.value || !interactionReady.value) return null
  const list = storyQuestions.value
  if (!list.length) return null
  // 最后一问答完但还没换幕时，仍停在那一题上：选项保持金色选中，
  // 屏幕上不会突然空一块，也不会闪回上一道题。
  return list[Math.min(extraIndex.value, list.length - 1)] ?? null
})
const extraFinished = computed(() => extraIndex.value >= storyQuestions.value.length)
// 「还有题在问」= 屏幕上停着一道题且不是最后一题答完那一刻。
// 只看 activeExtraQuestion 会把"最后一题答完、正在落卷"也当成在问，
// 结果 canSubmit 永远为 false，最后一问答完就卡在那一屏（真实页面验证抓到过）。
const extraPending = computed(() => Boolean(activeExtraQuestion.value) && !extraFinished.value)
// 追问进行中，主场景只看不点：这一轮已经在问下一件事了。
const stageBlocked = computed(() => submitting.value || extraPending.value)

/** 进入追问。返回 true 表示还有追问要走，false 表示可以落卷了。 */
const beginExtras = () => {
  if (!storyQuestions.value.length) return false
  extraIndex.value = 0
  extraCommit.value = ''
  // 没有预设选项的题（"为什么挑这件药宝"）直接把输入框打开，别让人先点一下才能写。
  extraBubbleOpen.value = !storyQuestions.value[0]?.options.length
  extraActive.value = true
  return true
}

/** 主问题答完的统一下一步：有追问就接着问，没有就落卷。 */
const afterMainAnswer = () => {
  if (beginExtras()) return
  void submitCurrentStep()
}

const scheduleExtraAdvance = () => {
  if (extraTimer) clearTimeout(extraTimer)
  extraTimer = setTimeout(() => {
    extraTimer = undefined
    advanceExtra()
  }, apprenticeVisual.replyCommitMs)
}

const chooseExtraAnswer = (option: string) => {
  const question = activeExtraQuestion.value
  if (!question || submitting.value || extraCommit.value) return
  storyAnswers[question.id] = option
  extraCommit.value = option
  extraBubbleOpen.value = false
  showToast(`记下了 · ${option}`)
  scheduleExtraAdvance()
}

const submitExtraNote = (text: string) => {
  const note = text.trim()
  const question = activeExtraQuestion.value
  if (!note || !question || submitting.value || extraCommit.value) return
  storyAnswers[question.id] = note
  extraCommit.value = note
  extraBubbleOpen.value = false
  showToast('记下了')
  scheduleExtraAdvance()
}

const advanceExtra = () => {
  const total = storyQuestions.value.length
  const next = Math.min(total, extraIndex.value + 1)
  extraIndex.value = next
  extraBubbleOpen.value = false
  if (next < total) {
    // 换到下一题：上一题的金色选中要清掉，否则新题会带着旧答案。
    extraCommit.value = ''
    // 下一题若没有预设选项，同样直接把输入框打开。
    extraBubbleOpen.value = !storyQuestions.value[next]?.options.length
    return
  }
  // 最后一问答完：题目留在屏上（选中项仍是金色），带着这一轮的全部答案落卷。
  void submitCurrentStep()
}
const resourceTypes = computed(() => step.value?.payload.resource_types ?? [])
const currentQuestion = computed(() => questions.value[activeQuestionIndex.value] ?? null)
const isLastQuizQuestion = computed(() => activeQuestionIndex.value >= questions.value.length - 1)

const currentQuizPrompt = computed(() => {
  const question = currentQuestion.value
  if (!question) return ''
  // 派生题自带不点名的剧情题面（性味 / 使用注意 / 相似药），直接用。
  if (question.story_prompt) return question.story_prompt
  // Never name the herb here: the whole point of this screen is that the
  // disciple recognises it from the photograph.
  if (question.skill === '功效识记') return apprenticeQuizPrompt
  return question.question
})

const INTERACTIVE_KINDS = ['identity', 'quiz', 'prescription', 'resource', 'rule']
const interactionReady = computed(() => videoEnded.value && INTERACTIVE_KINDS.includes(scene.value.kind))

// While the film runs the box carries whoever is speaking at that second; once it
// ends the box switches to a narrator that asks the question.
const playbackLine = computed(() => dialogueAt(scene.value, playbackSeconds.value))

// 对话框的「一句话 + 谁在说」只在这一处决定。
//
// 旧实现把 text 和 speaker 分成两个 computed 各判一次，两边条件不一致时就会
// 出现「旁白：拜师礼成」这种错配；而且落卷期间 text 被"师父正在看你的回答"
// 固定住，礼成那段的字幕全被盖掉。
const dialogueLine = computed<{ text: string; speaker: string }>(() => {
  // 影片里已经有人说话时字幕优先：落卷要等模型（礼成那一段十几秒），
  // 让字幕照常走，用户才知道礼已经成了。
  if (submitting.value) {
    const line = playbackLine.value
    if (line.text) return line
    return { text: '（师父正在看你的回答…）', speaker: '旁白' }
  }
  if (interactionReady.value) {
    // 追问优先：这一轮他要问的就是这题，答案给在右侧。
    const extra = activeExtraQuestion.value
    if (extra) return { text: extra.prompt, speaker: '老中医' }
    if (scene.value.kind === 'quiz' && currentQuestion.value) return { text: currentQuizPrompt.value, speaker: '旁白' }
    return { text: apprenticeStoryNarration[scene.value.sceneId] ?? scene.value.dialogue, speaker: '旁白' }
  }
  return playbackLine.value
})

const dialogueText = computed(() => dialogueLine.value.text)
const speakerName = computed(() => dialogueLine.value.speaker)
const speakerTone = computed<'master' | 'narration'>(() => (speakerName.value === '旁白' ? 'narration' : 'master'))

const displayChapter = computed(() => scene.value.chapter)

const progressLabel = computed(() => {
  if (scene.value.kind !== 'quiz' || !currentQuestion.value) return ''
  return `药圃图鉴 ${String(activeQuestionIndex.value + 1).padStart(2, '0')} / ${String(questions.value.length).padStart(2, '0')}`
})

const showQuizChoices = computed(() => scene.value.kind === 'quiz' && videoEnded.value && !extraActive.value)
// The closing screen keeps the dialogue box too: the master's last line stays in
// the box under the certificate, instead of the certificate talking to itself.
const showDialogue = computed(() => ['performance', 'identity', 'quiz', 'prescription', 'resource', 'rule', 'complete'].includes(scene.value.kind))

const videoFixture = computed<StoryVideoFixtureMode>(() => {
  const fixture = route.query.storyVideoFixture
  return import.meta.env.DEV && (fixture === 'ready' || fixture === 'failed') ? fixture : null
})

const herbPhotoFor = (question: ProfileSessionQuestion) => {
  const photoById: Record<string, string> = {
    herb_taizishen: '/story/herbs/taizishen.jpg',
    herb_dangshen: '/story/herbs/dangshen.jpg',
    herb_huangqi: '/story/herbs/huangqi.jpg',
    herb_chenpi: '/story/herbs/chenpi.jpg',
    herb_jinyinhua: '/story/herbs/jinyinhua.jpg',
  }
  return question.image_url || photoById[question.herb_id ?? ''] || '/story/herbs/field-card.svg'
}

const canSubmit = computed(() => {
  if (!canSubmitStep(state.value) || submitting.value) return false
  if (extraPending.value) return false
  // 追问还在进行时不能落卷；全部问完的那一瞬要放行——此时面板留着让用户
  // 看见自己的最后一句，但答案控件已经收起。
  if (extraActive.value && !extraFinished.value) return false
  if (scene.value.kind === 'prescription') return Boolean(caseText.value.trim() && prescription.value.trim())
  if (scene.value.kind === 'rule') return dailyMinutes.value !== null && targetDays.value !== null
  return true
})

const showToast = (message: string) => {
  toastMessage.value = message
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toastMessage.value = ''
  }, 1800)
}

const persist = () => window.localStorage.setItem(storageKey.value, JSON.stringify(state.value))

const clearChoiceTimer = () => {
  if (choiceTimer) clearTimeout(choiceTimer)
  choiceTimer = undefined
}

const startQuizClock = () => {
  quizShownAt = Date.now()
}

// Null means "never measured". It must stay null rather than fall back to 0,
// otherwise an unmeasured answer would be reported as a suspiciously fast one.
const stepElapsedMs = (): number | null => (stepShownAt === null ? null : Math.max(0, Date.now() - stepShownAt))

// Stop before the 420ms commit animation so it never lands in the reaction time.
const stopQuizClock = (questionId: string) => {
  if (quizShownAt === null) return
  questionResponseTimes[questionId] = Math.max(0, Date.now() - quizShownAt)
  quizShownAt = null
}

const resetStepFields = () => {
  clearChoiceTimer()
  if (extraTimer) {
    clearTimeout(extraTimer)
    extraTimer = undefined
  }
  extraIndex.value = 0
  extraCommit.value = ''
  extraActive.value = false
  extraBubbleOpen.value = false
  committingOption.value = ''
  Object.keys(questionAnswers).forEach((key) => delete questionAnswers[key])
  Object.keys(questionNotes).forEach((key) => delete questionNotes[key])
  Object.keys(questionResponseTimes).forEach((key) => delete questionResponseTimes[key])
  Object.keys(storyAnswers).forEach((key) => delete storyAnswers[key])
  quizShownAt = null
  stepShownAt = null
  activeQuestionIndex.value = 0
  quizBubbleOpen.value = false
  identityBubbleOpen.value = false
  resourceBubbleOpen.value = false
  prescriptionField.value = ''
  ruleField.value = ''
  identityCard.value = identityCards.value[0] ?? '临床学徒'
  resourceType.value = resourceTypes.value[0] ?? '知识图谱'
  resourceCustom.value = ''
  dailyMinutes.value = null
  targetDays.value = null
  utterance.value = ''
}

// Two fields, one bubble: opening the second closes the first, so the stage never
// grows a form again.
const togglePrescriptionField = (field: string) => {
  prescriptionField.value = prescriptionField.value === field ? '' : field
}

const toggleRuleField = (field: string) => {
  ruleField.value = ruleField.value === field ? '' : field
}

const advanceQuiz = () => {
  if (isLastQuizQuestion.value) afterMainAnswer()
  else {
    activeQuestionIndex.value += 1
    quizBubbleOpen.value = false
  }
}

const chooseQuizOption = (option: string) => {
  const question = currentQuestion.value
  if (!question || submitting.value || committingOption.value) return
  stopQuizClock(question.id)
  questionAnswers[question.id] = option
  questionNotes[question.id] = ''
  committingOption.value = option
  showToast(`已作答 · ${option}`)
  clearChoiceTimer()
  choiceTimer = setTimeout(() => {
    committingOption.value = ''
    advanceQuiz()
  }, apprenticeVisual.replyCommitMs)
}

const submitQuizNote = (text: string) => {
  const question = currentQuestion.value
  if (!question || submitting.value || committingOption.value) return
  const note = text.trim()
  if (!note) return
  stopQuizClock(question.id)
  questionNotes[question.id] = note
  questionAnswers[question.id] = ''
  committingOption.value = 'note'
  showToast('已呈上自述')
  clearChoiceTimer()
  choiceTimer = setTimeout(() => {
    committingOption.value = ''
    advanceQuiz()
  }, apprenticeVisual.replyCommitMs)
}

const chooseIdentity = (card: string) => {
  if (submitting.value || committingOption.value || !canSubmit.value) return
  identityCard.value = card
  utterance.value = ''
  committingOption.value = card
  showToast(`已记录 · ${card}`)
  afterMainAnswer()
}

const submitIdentityNote = (text: string) => {
  const note = text.trim()
  if (!note || submitting.value) return
  if (!identityCard.value) identityCard.value = identityCards.value[0] ?? '临床学徒'
  utterance.value = note
  committingOption.value = 'identity-note'
  showToast('已呈上补充')
  afterMainAnswer()
}

const chooseResource = (resource: string) => {
  if (submitting.value || committingOption.value || !canSubmit.value) return
  resourceType.value = resource
  resourceCustom.value = ''
  committingOption.value = resource
  showToast(`已记录 · ${resource}`)
  afterMainAnswer()
}

const submitResourceNote = (text: string) => {
  const note = text.trim()
  if (!note || submitting.value) return
  if (!resourceType.value) resourceType.value = resourceTypes.value[0] ?? '知识图谱'
  resourceCustom.value = note
  committingOption.value = 'resource-note'
  showToast('已呈上补充')
  afterMainAnswer()
}

// One in-flight handshake shared by the prefetch and every retry: two parallel
// calls would open two sessions on the backend.
const takeSession = async (): Promise<ProfileSessionResponse | null> => {
  if (state.value.session) return state.value.session
  if (!prefetchPromise) {
    prefetchPromise = startProfileSession('apprentice').catch((cause: unknown) => {
      prefetchError = cause instanceof Error ? cause.message : '收徒会话启动失败'
      return null
    })
  }
  const session = await prefetchPromise
  // A failed handshake must stay retryable.
  if (!session) prefetchPromise = null
  return session
}

const startSession = async () => {
  if (state.value.session) return
  loading.value = true
  error.value = ''
  try {
    const session = await takeSession()
    if (!session) {
      error.value = prefetchError || '收徒会话启动失败'
      return
    }
    state.value = applySession(state.value, session)
    resetStepFields()
    persist()
  } finally {
    loading.value = false
  }
}

// The prologue film is the loading screen. When it ends the question has to be
// already there, on the frozen last frame, with no spinner and no rewind.
const openPrologueQuestion = async () => {
  const session = await takeSession()
  if (!session) {
    error.value = prefetchError || '收徒会话启动失败'
    return
  }
  if (!state.value.session) {
    state.value = applySession(state.value, session)
    resetStepFields()
    return
  }
  if (state.value.performanceIndex < 3) {
    state.value = { ...state.value, performanceIndex: 3, sceneId: sceneForStep(state.value.session.step).sceneId }
  }
}

const advanceScene = async () => {
  error.value = ''
  if (state.value.performanceIndex < 2) {
    state.value = advancePerformance(state.value)
    persist()
    return
  }
  await openPrologueQuestion()
}

const stepPayload = (): Record<string, unknown> => {
  // Omitted when unmeasured, so the backend falls back to the session step's own
  // start time instead of receiving an invented duration.
  const elapsed = stepElapsedMs()
  const timing = elapsed === null ? {} : { response_time_ms: elapsed }
  const extras = Object.keys(storyAnswers).length ? { story_answers: { ...storyAnswers } } : {}
  if (scene.value.kind === 'identity') return { identity_card: identityCard.value, identity_custom: '', utterance: utterance.value.trim(), ...timing, ...extras }
  if (scene.value.kind === 'quiz') return { answers: questions.value.map((question) => ({ question_id: question.id, selected: questionAnswers[question.id]?.trim() || '', free_text: questionNotes[question.id]?.trim() || '', topic: question.topic, clue_type: question.skill, herb_id: question.herb_id, herb_name: question.herb_name, response_time_ms: questionResponseTimes[question.id] ?? 0 })), ...extras }
  if (scene.value.kind === 'prescription') return { case: caseText.value, prescription: prescription.value, ...timing, ...extras }
  if (scene.value.kind === 'resource') return { resource_type: resourceType.value, resource_custom: resourceCustom.value.trim(), ...timing, ...extras }
  return { daily_minutes: dailyMinutes.value, target_days: targetDays.value, ...timing, ...extras }
}

// 表单幕的按钮：先走追问，再落卷。
const onSceneSubmit = () => {
  if (!canSubmit.value || submitting.value) return
  afterMainAnswer()
}

const submitCurrentStep = async () => {
  const current = state.value.session?.step
  if (!current || !canSubmit.value) return
  state.value = beginStepSubmission(state.value)
  submitting.value = true
  error.value = ''
  try {
    const next = await answerProfileSession(state.value.session!.session_id, current.id, stepPayload())
    state.value = applySession(state.value, next)
    resetStepFields()
    if (next.finished) {
      const finished = await finishProfileSession(next.session_id)
      state.value = applySession(state.value, finished)
      if (auth.user) saveOnboardingState(auth.user.user_id, { status: 'completed', mode: 'apprentice' })
      await profileStore.loadProfile()
      void profileStore.ensureNarrativeLoaded()
      window.localStorage.removeItem(storageKey.value)
    } else persist()
  } catch (cause) {
    // A transport error must remain retryable, while duplicate clicks during a
    // live request stay locked to the same backend step.
    state.value = { ...state.value, completing: false, submittedStepIds: state.value.submittedStepIds.filter((id) => id !== current.id) }
    committingOption.value = ''
    // 落卷失败不要退回主问题：那等于把前面答过的几道又重来一遍。
    // 退回"最后一问"那一屏、清掉这一问的选中，用户重答一次就能重试提交。
    if (extraActive.value && storyQuestions.value.length) {
      extraIndex.value = storyQuestions.value.length
      extraCommit.value = ''
      extraBubbleOpen.value = !storyQuestions.value[storyQuestions.value.length - 1]?.options.length
    }
    error.value = cause instanceof Error ? cause.message : '本次作答未能提交'
    persist()
  } finally {
    submitting.value = false
  }
}

const restartFromSafePoint = async () => {
  error.value = ''
  const recovered = restoreApprenticeStoryState(window.localStorage.getItem(storageKey.value))
  if (recovered) {
    state.value = recovered
    resetStepFields()
    return
  }
  await startSession()
}

const redirectTarget = () => (typeof route.query.redirect === 'string' && route.query.redirect !== '/apprentice' ? route.query.redirect : '/onboarding')
const leaveStory = () => router.push(redirectTarget())

const startFreshStory = () => {
  window.localStorage.removeItem(storageKey.value)
  state.value = initialApprenticeStoryState()
  loading.value = false
  submitting.value = false
  error.value = ''
  playbackSeconds.value = 0
  videoEnded.value = false
  prefetchPromise = null
  prefetchError = ''
  resetStepFields()
  // Hide the handshake behind the film instead of in front of it.
  void takeSession()
}

// The film decides when the question may appear. A performance beat has nothing
// to answer, so the end of its shot simply hands over to the next beat.
const onVideoEnded = (played: boolean) => {
  videoEnded.value = true
  if (scene.value.kind !== 'performance') {
    stepShownAt = Date.now()
    return
  }
  if (played) {
    void openPrologueQuestion()
    return
  }
  // No film to watch (static fallback, reduced motion, broken source): keep the
  // line on screen long enough to read, then move the beat along.
  if (performanceTimer) clearTimeout(performanceTimer)
  performanceTimer = setTimeout(() => void advanceScene(), apprenticeVisual.autoPerformanceMs)
}

watch(state, persist, { deep: true })

// A scene change only restarts the clock when the film itself changes. The
// prologue's three beats share one shot, and resetting the clock there made the
// dialogue box fall back to the scene's own line for a quarter of a second
// before the cue table caught up - two lines fighting over the same beat.
// The same rule keeps `videoEnded` across the prologue -> question handover, so
// the box goes straight to the narrator instead of flashing the master's line.
const sceneSource = computed(() => storyAssetForStage(scene.value.sceneId, videoFixture.value).video?.src ?? '')

watch(sceneSource, (source, previous) => {
  if (source === previous) return
  videoEnded.value = false
  playbackSeconds.value = 0
})

watch(() => scene.value.sceneId, () => {
  clearChoiceTimer()
  if (performanceTimer) clearTimeout(performanceTimer)
  // 换幕就从头问起：上一幕的追问序号不能带到这一幕。
  if (extraTimer) {
    clearTimeout(extraTimer)
    extraTimer = undefined
  }
  extraIndex.value = 0
  extraCommit.value = ''
  extraActive.value = false
  extraBubbleOpen.value = false
  committingOption.value = ''
  quizBubbleOpen.value = false
  identityBubbleOpen.value = false
  resourceBubbleOpen.value = false
  prescriptionField.value = ''
  ruleField.value = ''
  quizShownAt = null
  // A normal step change restarts the clock when its film ends. The prologue
  // handover keeps `videoEnded` true across the scene switch, so there the clock
  // has to start now - no new film will end to start it.
  stepShownAt = videoEnded.value ? Date.now() : null
})

// The card is mounted by `showQuizChoices` (quiz scene + film finished), so
// starting the clock here keeps "the photograph appeared" and "the clock started"
// on the same frame. `activeQuestionIndex` is watched too because the film stays
// finished between questions - only the index changes.
watch([showQuizChoices, activeQuestionIndex], ([choicesVisible]) => {
  if (choicesVisible && currentQuestion.value) startQuizClock()
  else quizShownAt = null
})

// section-one is a single continuous shot covering the whole prologue, so its
// three beats ride the film's own timeline. The cut points sit on the cue
// boundaries in apprenticeStoryCues so a beat never changes mid-sentence.
// Advancing by playback time keeps the <video> element mounted; cutting to a new
// element would restart the shot from zero, which is the flash it used to have.
const PROLOGUE_BEATS = [4, 14]

watch(playbackSeconds, (seconds) => {
  if (state.value.session || scene.value.kind !== 'performance') return
  const target = PROLOGUE_BEATS.reduce((reached, at, position) => (seconds >= at ? position + 1 : reached), 0)
  while (state.value.performanceIndex < target) state.value = advancePerformance(state.value)
})

onMounted(startFreshStory)

onBeforeUnmount(() => {
  if (performanceTimer) clearTimeout(performanceTimer)
  if (choiceTimer) clearTimeout(choiceTimer)
  if (toastTimer) clearTimeout(toastTimer)
  if (extraTimer) clearTimeout(extraTimer)
  quizShownAt = null
})
</script>

<style scoped lang="scss">
.apprentice-story {
  position: relative;
  display: grid;
  height: 100dvh;
  overflow: hidden;
  background: var(--story-stage);
}

.apprentice-story__layer {
  position: relative;
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: var(--qh-space-4);
  height: 100dvh;
  min-height: 0;
  padding: 64px var(--story-dialog-inset) var(--story-dialog-inset);
}

.apprentice-story__stage {
  display: flex;
  flex: 1;
  align-items: flex-end;
  justify-content: center;
  min-height: 0;
  overflow-y: auto;
  padding-bottom: var(--qh-space-2);
}

.apprentice-story__dialog {
  flex: none;
  width: 100%;
}

.apprentice-story__state {
  position: absolute;
  inset: 0;
  z-index: 15;
  display: grid;
  place-items: center;
  padding: var(--qh-space-8);
}

@media (max-width: 767px) {
  .apprentice-story__layer {
    gap: var(--qh-space-3);
    padding: 56px var(--qh-space-4) var(--qh-space-4);
  }
}
</style>
