<template>
  <main class="apprentice-story" :class="{ 'note-open': showQuizFreeAnswer }">
    <StoryStage :scene="scene" :video-fixture="videoFixture" :muted="false" :paused="scene.kind === 'quiz' && quizVideoPaused" :hold-at-end="scene.sceneId === 'master-offer'" :pause-after-seconds="scene.kind === 'quiz' ? 3.6 : undefined" @quiz-intro-complete="quizVideoPaused = true" @playback-time="playbackSeconds = $event" />

    <header class="story-controls">
      <p class="story-chapter"><span class="story-chapter__seal">岐</span><span>{{ displayChapter }}</span></p>
      <button class="exit-story" type="button" @click="leaveStory">退出剧情</button>
    </header>

    <button v-if="isDialogueVisible" class="story-dialogue" type="button" @click="finishDialogue">
      <span class="dialogue-name" aria-hidden="true"><span class="dialogue-name__main">老中医</span><span class="dialogue-name__role">药庐师尊</span></span>
      <span class="dialogue-copy" aria-hidden="true">{{ visibleDialogue }}<span v-if="typingDialogue" class="type-caret">▍</span></span>
      <span class="dialogue-seal" aria-hidden="true">岐黄</span>
      <span v-if="!typingDialogue" class="dialogue-diamond" aria-hidden="true"></span>
      <span class="story-sr" aria-live="polite">{{ dialogueText }}</span>
    </button>

    <section v-if="error" class="story-note is-state">
      <StateError title="这一幕暂时无法继续" :description="error" action-text="重新连接" size="sm" @action="restartFromSafePoint" />
    </section>
    <section v-else-if="loading" class="story-note is-state"><StateLoading title="药灯正在亮起" description="正在接续本次收徒会话。" size="sm" /></section>
    <section v-else-if="scene.kind === 'quiz' && !quizVideoPaused" class="story-note quiz-intro" aria-live="polite">
      <span class="note-kicker">师门试炼 · 二 · 识药</span>
      <strong>先听师父讲药圃旧事</strong>
      <p>片刻之后画面定格，再逐味辨认药材。</p>
    </section>

    <template v-else-if="scene.kind === 'quiz' && currentQuestion">
      <article class="herb-sheet" :aria-label="`${currentQuestion.herb_name}药材标本`">
        <span class="herb-sheet__index">药圃图鉴 · {{ String(activeQuestionIndex + 1).padStart(2, '0') }} / {{ String(questions.length).padStart(2, '0') }}</span>
        <div class="herb-sheet__photo">
          <img :src="herbPhotoFor(currentQuestion)" :alt="`${currentQuestion.herb_name}中药高清识别图`" fetchpriority="high" />
          <span class="photo-corner photo-corner--tl" aria-hidden="true"></span>
          <span class="photo-corner photo-corner--tr" aria-hidden="true"></span>
          <span class="photo-corner photo-corner--bl" aria-hidden="true"></span>
          <span class="photo-corner photo-corner--br" aria-hidden="true"></span>
        </div>
        <div class="herb-sheet__label">
          <span class="hand">标本 · 辨识</span>
          <strong>{{ currentQuestion.herb_name }}</strong>
          <em>{{ currentQuestion.image_caption || currentQuestion.herb_hint }}</em>
          <span class="seal-stamp" aria-hidden="true">药圃</span>
        </div>
      </article>

      <div class="story-dock">
        <div v-if="showQuizFreeAnswer" class="reply-note">
          <div class="reply-note__head"><span>自述辨析</span><span>{{ (questionNotes[currentQuestion.id] || '').trim().length }} 字</span></div>
          <label class="story-sr" :for="`quiz-note-${currentQuestion.id}`">自述辨析</label>
          <textarea :id="`quiz-note-${currentQuestion.id}`" v-model="questionNotes[currentQuestion.id]" rows="3" placeholder="例如：太子参味甘微苦、性平，归脾肺经，能益气健脾、生津润肺。" />
          <button class="reply-note__submit" type="button" :disabled="submitting || !(questionNotes[currentQuestion.id] || '').trim()" @click="submitQuizNote">呈上此答</button>
        </div>
        <div class="story-tags" role="group" :aria-label="currentQuizPrompt">
          <button v-for="(option, index) in currentQuestion.options" :key="option" class="dialogue-option" type="button" :class="{ 'is-committing': committingOption === option }" :aria-pressed="questionAnswers[currentQuestion.id] === option" :disabled="submitting || Boolean(committingOption)" @click="chooseQuizOption(option)">
            <span class="tag-medal" aria-hidden="true">{{ ['甲', '乙', '丙', '丁'][index] || index + 1 }}</span>
            <span class="tag-copy">{{ option }}</span>
            <span class="tag-mark" aria-hidden="true"></span>
          </button>
          <button class="dialogue-option is-ghost" type="button" :aria-expanded="showQuizFreeAnswer" :disabled="submitting || Boolean(committingOption)" @click="showQuizFreeAnswer = !showQuizFreeAnswer">
            <span class="tag-medal tag-medal--brush" aria-hidden="true">笔</span>
            <span class="tag-copy">我想自己作答 / 补充辨析</span>
            <span class="tag-mark tag-mark--plus" aria-hidden="true">{{ showQuizFreeAnswer ? '×' : '+' }}</span>
          </button>
        </div>
      </div>
    </template>

    <template v-else-if="isInteractive">
      <div v-if="scene.kind === 'identity'" class="story-dock">
        <div v-if="showIdentityNote" class="reply-note">
          <div class="reply-note__head"><span>补充你的学习经历</span><span>可选</span></div>
          <label class="reply-field"><span>你曾学过什么</span><textarea v-model="identityCustom" rows="2" placeholder="例如：我学过基础药理，但对中药性味还不熟。" /></label>
          <label class="reply-field"><span>你希望从岐黄学习中达成什么</span><textarea v-model="utterance" rows="2" placeholder="例如：我想先掌握常用药材，再学会按病例辨证配伍。" /></label>
          <button class="reply-note__submit" type="button" :disabled="submitting || (!identityCustom.trim() && !utterance.trim())" @click="submitIdentityNote">呈上此答</button>
        </div>
        <div class="story-tags" role="group" aria-label="身份回答">
          <button v-for="(card, index) in identityCards" :key="card" class="dialogue-option" type="button" :class="{ 'is-committing': committingOption === card }" :aria-pressed="identityCard === card" :disabled="submitting || Boolean(committingOption)" @click="chooseIdentity(card)">
            <span class="tag-medal" aria-hidden="true">{{ ['考', '临', '草', '研'][index] || '答' }}</span>
            <span class="tag-copy">{{ card }}</span>
            <span class="tag-mark" aria-hidden="true"></span>
          </button>
          <button class="dialogue-option is-ghost" type="button" :aria-expanded="showIdentityNote" :disabled="submitting || Boolean(committingOption)" @click="showIdentityNote = !showIdentityNote">
            <span class="tag-medal tag-medal--brush" aria-hidden="true">笔</span>
            <span class="tag-copy">自己说说</span>
            <span class="tag-mark tag-mark--plus" aria-hidden="true">{{ showIdentityNote ? '×' : '+' }}</span>
          </button>
        </div>
      </div>

      <div v-else-if="scene.kind === 'resource'" class="story-dock">
        <div v-if="showResourceNote" class="reply-note">
          <div class="reply-note__head"><span>也可以自己说想要的法门</span><span>可选</span></div>
          <label class="reply-field"><span>你想要的学习形态</span><textarea v-model="resourceCustom" rows="2" placeholder="例如：我想要带着药材图片的短剧和病例拆解。" /></label>
          <button class="reply-note__submit" type="button" :disabled="submitting || !resourceCustom.trim()" @click="submitResourceNote">呈上此答</button>
        </div>
        <div class="story-tags" role="group" aria-label="药宝回答">
          <button v-for="(resource, index) in resourceTypes" :key="resource" class="dialogue-option" type="button" :class="{ 'is-committing': committingOption === resource }" :aria-pressed="resourceType === resource" :disabled="submitting || Boolean(committingOption)" @click="chooseResource(resource)">
            <span class="tag-medal" aria-hidden="true">{{ ['谱', '剧', '歌', '案'][index] || '宝' }}</span>
            <span class="tag-copy">{{ resource }}</span>
            <span class="tag-mark" aria-hidden="true"></span>
          </button>
          <button class="dialogue-option is-ghost" type="button" :aria-expanded="showResourceNote" :disabled="submitting || Boolean(committingOption)" @click="showResourceNote = !showResourceNote">
            <span class="tag-medal tag-medal--brush" aria-hidden="true">笔</span>
            <span class="tag-copy">自己想要别的</span>
            <span class="tag-mark tag-mark--plus" aria-hidden="true">{{ showResourceNote ? '×' : '+' }}</span>
          </button>
        </div>
      </div>

      <form v-else-if="scene.kind === 'prescription'" class="story-rx" @submit.prevent="submitCurrentStep">
        <header class="story-rx__head">
          <div class="story-rx__title"><span>岐黄药庐</span><strong>处方笺</strong></div>
          <span class="story-rx__no">第 〇〇一 号</span>
        </header>
        <p class="story-rx__prompt"><span>师嘱</span>{{ scene.dialogue }}</p>
        <label class="story-rx__field"><span>病例</span><textarea v-model="caseText" rows="2" placeholder="如：热毒咽痛，咽喉肿痛。" /></label>
        <label class="story-rx__field"><span>抓药方</span><textarea v-model="prescription" rows="3" placeholder="如：金银花15g，连翘12g。" /></label>
        <fieldset class="story-rx__methods">
          <legend>偏好的法门 · 可多选</legend>
          <div class="story-stamps">
            <button v-for="method in prescriptionMethods" :key="method" class="story-stamp-btn" type="button" :aria-pressed="prescriptionPreferences.includes(method)" @click="togglePrescriptionPreference(method)">{{ method }}</button>
          </div>
        </fieldset>
        <label class="story-rx__note"><span>也可以补充你的配伍想法</span><input v-model="prescriptionPreferenceNote" type="text" placeholder="例如：我喜欢先辨寒热，再按君臣佐使配伍" /></label>
        <footer class="story-rx__foot">
          <span class="story-rx__sign">弟子 <b>＿＿＿＿</b></span>
          <button class="story-rx__submit" type="submit" :aria-busy="submitting" :disabled="!canSubmit">{{ submitting ? '记录中…' : '落笔呈上' }} <span class="story-rx__seal" aria-hidden="true">呈</span></button>
        </footer>
      </form>

      <form v-else-if="scene.kind === 'rule'" class="story-rule" @submit.prevent="submitCurrentStep">
        <header class="story-rule__head"><span>师门 · 学规帖</span><strong>立下学规</strong></header>
        <p class="story-rule__prompt">{{ scene.dialogue }}</p>
        <div class="story-rule__grid">
          <label><span>每日时长（分钟）</span><input v-model.number="dailyMinutes" type="number" min="5" max="180" /></label>
          <label><span>目标周期（天）</span><input v-model.number="targetDays" type="number" min="3" max="90" /></label>
        </div>
        <label class="story-rule__check"><input v-model="acceptChallenge" type="checkbox" /> 接受每周一次挑战</label>
        <label class="story-rule__note"><span>你愿意守住的学习方式</span><textarea v-model="learningNote" rows="2" placeholder="例如：先看图识药，再用病例复盘；如果出差就改成周末集中学习。" /></label>
        <button class="story-rule__submit" type="submit" :aria-busy="submitting" :disabled="!canSubmit">{{ submitting ? '记录中…' : '立下此约' }} <span class="story-rule__seal" aria-hidden="true">约</span></button>
      </form>
    </template>

    <section v-else-if="scene.kind === 'complete'" class="story-cert" aria-live="polite">
      <div class="story-cert__roller" aria-hidden="true"></div>
      <div class="story-cert__paper">
        <span class="story-cert__kicker">终 · 灯明</span>
        <strong>收徒礼成</strong>
        <span class="story-cert__rule" aria-hidden="true"></span>
        <p>今日所答，皆已留作证据。<br>档案卷会随日后所学慢慢显影。</p>
        <div class="story-cert__count"><b>{{ state.session?.evidence_count ?? 0 }}</b><span>条证据</span></div>
        <span class="story-cert__seal" aria-hidden="true">礼成</span>
        <button class="story-cert__btn" type="button" @click="openProfile">查看弟子档案</button>
      </div>
      <div class="story-cert__roller" aria-hidden="true"></div>
    </section>

    <section v-else-if="scene.kind !== 'performance'" class="story-note action-panel">
      <span class="note-kicker">师门试炼 · {{ scene.chapter }}</span>
      <p>{{ scene.dialogue }}</p>
      <button class="note-button" type="button" @click="advanceScene">进入问答 <span aria-hidden="true">→</span></button>
    </section>

    <div v-if="toastMessage" class="story-toast" role="status" aria-live="polite">{{ toastMessage }}</div>
  </main>
</template>

<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StoryStage from '../components/story/StoryStage.vue'
import StateError from '../components/state/StateError.vue'
import StateLoading from '../components/state/StateLoading.vue'
import { apprenticeVisual, sceneForStep } from '../config/apprenticeStory'
import type { StoryVideoFixtureMode } from '../config/apprenticeStoryAssets'
import { advancePerformance, applySession, beginStepSubmission, canSubmitStep, initialApprenticeStoryState, restoreApprenticeStoryState, sceneForState, type ApprenticeStoryState } from '../composables/apprenticeStoryMachine'
import { answerProfileSession, finishProfileSession, startProfileSession } from '../services/profileApi'
import { saveOnboardingState } from '../services/onboardingStorage'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import type { ProfileSessionQuestion } from '../types/profile'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const profileStore = useProfileStore()
const state = ref<ApprenticeStoryState>(initialApprenticeStoryState())
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const playbackSeconds = ref(0)
const visibleDialogue = ref('')
const typingDialogue = ref(false)
const toastMessage = ref('')
let dialogueTimer: ReturnType<typeof setInterval> | undefined
let choiceTimer: ReturnType<typeof setTimeout> | undefined
let toastTimer: ReturnType<typeof setTimeout> | undefined
const identityCard = ref('临床学徒')
const identityCustom = ref('')
const utterance = ref('')
const questionAnswers = reactive<Record<string, string>>({})
const questionNotes = reactive<Record<string, string>>({})
const caseText = ref('热毒咽痛，咽喉肿痛。')
const prescription = ref('金银花15g，连翘12g。')
const resourceType = ref('知识图谱')
const resourceCustom = ref('')
const dailyMinutes = ref(30)
const targetDays = ref(14)
const acceptChallenge = ref(true)
const learningNote = ref('')
const activeQuestionIndex = ref(0)
const showQuizFreeAnswer = ref(false)
const showIdentityNote = ref(false)
const showResourceNote = ref(false)
const committingOption = ref('')
const quizVideoPaused = ref(false)
const prescriptionMethods = ['先辨证后配伍', '按君臣佐使', '重视炮制差异', '优先经典方', '喜欢尝试加减']
const prescriptionPreferences = ref<string[]>([])
const prescriptionPreferenceNote = ref('')

const storageKey = computed(() => `${apprenticeVisual.storagePrefix}${auth.user?.user_id ?? 'anonymous'}`)
const scene = computed(() => sceneForState(state.value))
const dialogueText = computed(() => {
  if (scene.value.sceneId === 'wake-apothecary') {
    if (playbackSeconds.value >= 21) return '我观你眼中有向学之光，可愿随我习这岐黄之术'
    if (playbackSeconds.value >= 16) return '快醒醒'
    if (playbackSeconds.value >= 14) return '孩子快醒醒'
    if (playbackSeconds.value >= 4) return '眼前的景象渐渐远去——你开始想起，自己是如何在雨后的山阶上受伤的。'
    return '晨雾未散，你闻到鼻间陈皮与艾草的微苦，慢慢在一间陌生的中药房醒来。'
  }
  return scene.value.kind === 'quiz' && currentQuestion.value ? currentQuizPrompt.value : scene.value.dialogue
})
const revealDialogue = () => {
  if (dialogueTimer) clearInterval(dialogueTimer)
  visibleDialogue.value = ''
  const copy = dialogueText.value
  let index = 0
  typingDialogue.value = true
  dialogueTimer = setInterval(() => {
    index = Math.min(copy.length, index + 1)
    visibleDialogue.value = copy.slice(0, index)
    if (index >= copy.length) { if (dialogueTimer) clearInterval(dialogueTimer); typingDialogue.value = false }
  }, 42)
}
const finishDialogue = () => {
  if (dialogueTimer) clearInterval(dialogueTimer)
  visibleDialogue.value = dialogueText.value
  typingDialogue.value = false
}
const showToast = (message: string) => {
  toastMessage.value = message
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastMessage.value = '' }, 1800)
}
const videoFixture = computed<StoryVideoFixtureMode>(() => {
  const fixture = route.query.storyVideoFixture
  return import.meta.env.DEV && (fixture === 'ready' || fixture === 'failed') ? fixture : null
})
const step = computed(() => state.value.session?.step ?? null)
const isInteractive = computed(() => ['identity', 'quiz', 'prescription', 'resource', 'rule'].includes(scene.value.kind))
const isDialogueVisible = computed(() => scene.value.kind === 'quiz' || scene.value.kind === 'identity')
const identityCards = computed(() => step.value?.payload.identity_cards ?? [])
const questions = computed<ProfileSessionQuestion[]>(() => step.value?.payload.questions ?? [])
const resourceTypes = computed(() => step.value?.payload.resource_types ?? [])
const displayChapter = computed(() => {
  if (scene.value.sceneId !== 'wake-apothecary') return scene.value.chapter
  if (playbackSeconds.value < 4) return '序 · 药房苏醒'
  if (playbackSeconds.value < 13) return '序 · 回忆摔倒'
  return '序 · 老者端药'
})
const currentQuestion = computed(() => questions.value[activeQuestionIndex.value] ?? null)
const isLastQuizQuestion = computed(() => activeQuestionIndex.value >= questions.value.length - 1)
const currentQuizPrompt = computed(() => {
  const question = currentQuestion.value
  if (!question) return ''
  if (question.skill === '功效识记') return `这幅图中，${question.herb_name}的代表功效之一是？`
  return question.question
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
  if (scene.value.kind === 'prescription') return Boolean(caseText.value.trim() && prescription.value.trim())
  return true
})

const persist = () => window.localStorage.setItem(storageKey.value, JSON.stringify(state.value))
const clearChoiceTimer = () => {
  if (choiceTimer) clearTimeout(choiceTimer)
  choiceTimer = undefined
}
const resetStepFields = () => {
  clearChoiceTimer()
  committingOption.value = ''
  Object.keys(questionAnswers).forEach((key) => delete questionAnswers[key])
  Object.keys(questionNotes).forEach((key) => delete questionNotes[key])
  activeQuestionIndex.value = 0
  showQuizFreeAnswer.value = false
  showIdentityNote.value = false
  showResourceNote.value = false
  identityCard.value = identityCards.value[0] ?? '临床学徒'
  identityCustom.value = ''
  resourceType.value = resourceTypes.value[0] ?? '知识图谱'
  resourceCustom.value = ''
  utterance.value = ''
  learningNote.value = ''
  prescriptionPreferences.value = []
  prescriptionPreferenceNote.value = ''
}
const togglePrescriptionPreference = (method: string) => {
  prescriptionPreferences.value = prescriptionPreferences.value.includes(method)
    ? prescriptionPreferences.value.filter((item) => item !== method)
    : [...prescriptionPreferences.value, method]
}
const chooseQuizOption = (option: string) => {
  const question = currentQuestion.value
  if (!question || submitting.value || committingOption.value) return
  questionAnswers[question.id] = option
  questionNotes[question.id] = ''
  committingOption.value = option
  showToast(`已作答 · ${option}`)
  clearChoiceTimer()
  choiceTimer = setTimeout(() => {
    committingOption.value = ''
    if (isLastQuizQuestion.value) void submitCurrentStep()
    else {
      activeQuestionIndex.value += 1
      showQuizFreeAnswer.value = false
    }
  }, apprenticeVisual.replyCommitMs)
}
const submitQuizNote = () => {
  const question = currentQuestion.value
  if (!question || submitting.value || committingOption.value) return
  const note = (questionNotes[question.id] || '').trim()
  if (!note) return
  questionAnswers[question.id] = ''
  committingOption.value = 'note'
  showToast('已呈上自述')
  clearChoiceTimer()
  choiceTimer = setTimeout(() => {
    committingOption.value = ''
    if (isLastQuizQuestion.value) void submitCurrentStep()
    else {
      activeQuestionIndex.value += 1
      showQuizFreeAnswer.value = false
    }
  }, apprenticeVisual.replyCommitMs)
}
const chooseIdentity = (card: string) => {
  if (submitting.value || committingOption.value || !canSubmit.value) return
  identityCard.value = card
  identityCustom.value = ''
  utterance.value = ''
  committingOption.value = card
  showToast(`已记录 · ${card}`)
  void submitCurrentStep()
}
const submitIdentityNote = () => {
  if (!identityCustom.value.trim() && !utterance.value.trim()) return
  if (!identityCard.value) identityCard.value = identityCards.value[0] ?? '临床学徒'
  committingOption.value = 'identity-note'
  showToast('已呈上补充')
  void submitCurrentStep()
}
const chooseResource = (resource: string) => {
  if (submitting.value || committingOption.value || !canSubmit.value) return
  resourceType.value = resource
  resourceCustom.value = ''
  committingOption.value = resource
  showToast(`已记录 · ${resource}`)
  void submitCurrentStep()
}
const submitResourceNote = () => {
  if (!resourceCustom.value.trim()) return
  if (!resourceType.value) resourceType.value = resourceTypes.value[0] ?? '知识图谱'
  committingOption.value = 'resource-note'
  showToast('已呈上补充')
  void submitCurrentStep()
}
const startSession = async () => {
  if (state.value.session || loading.value) return
  loading.value = true
  try { state.value = applySession(state.value, await startProfileSession('apprentice')); resetStepFields(); persist() } catch (cause) { error.value = cause instanceof Error ? cause.message : '收徒会话启动失败' } finally { loading.value = false }
}
const advanceScene = async () => {
  error.value = ''
  if (state.value.performanceIndex < 2) { state.value = advancePerformance(state.value); persist(); return }
  await startSession()
}
const skipPerformance = async () => { state.value = { ...state.value, performanceIndex: 3, sceneId: sceneForStep(state.value.session?.step ?? null).sceneId }; await startSession() }
const stepPayload = (): Record<string, unknown> => {
  const responseTime = 1000
  if (scene.value.kind === 'identity') return { identity_card: identityCard.value, identity_custom: identityCustom.value.trim(), utterance: utterance.value.trim(), response_time_ms: responseTime }
  if (scene.value.kind === 'quiz') return { answers: questions.value.map((question) => ({ question_id: question.id, selected: questionAnswers[question.id]?.trim() || '', free_text: questionNotes[question.id]?.trim() || '', topic: question.topic, clue_type: question.skill, herb_id: question.herb_id, herb_name: question.herb_name, response_time_ms: responseTime })) }
  if (scene.value.kind === 'prescription') return { case: caseText.value, prescription: prescription.value, preferences: prescriptionPreferences.value, preference_note: prescriptionPreferenceNote.value.trim(), response_time_ms: responseTime }
  if (scene.value.kind === 'resource') return { resource_type: resourceType.value, resource_custom: resourceCustom.value.trim(), response_time_ms: responseTime }
  return { daily_minutes: dailyMinutes.value, target_days: targetDays.value, accept_challenge: acceptChallenge.value, learning_note: learningNote.value.trim(), response_time_ms: responseTime }
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
    error.value = cause instanceof Error ? cause.message : '本次作答未能提交'
    persist()
  } finally { submitting.value = false }
}
const restartFromSafePoint = async () => { error.value = ''; const recovered = restoreApprenticeStoryState(window.localStorage.getItem(storageKey.value)); if (recovered) { state.value = recovered; resetStepFields(); return } await startSession() }
const redirectTarget = () => typeof route.query.redirect === 'string' && route.query.redirect !== '/apprentice' ? route.query.redirect : '/onboarding'
const leaveStory = () => router.push(redirectTarget())
const openProfile = () => router.push('/profile')
const startFreshStory = () => {
  // The route may be kept alive by the router. Re-entry must replay the
  // prologue and ask the learning-history question before the herb quiz.
  window.localStorage.removeItem(storageKey.value)
  state.value = initialApprenticeStoryState()
  loading.value = false
  submitting.value = false
  error.value = ''
  playbackSeconds.value = 0
  resetStepFields()
}

watch(state, persist, { deep: true })
watch(() => scene.value.sceneId, () => { quizVideoPaused.value = false; playbackSeconds.value = 0 })
watch(() => scene.value.sceneId, (sceneId) => {
  if (sceneId === 'wake-apothecary') playbackSeconds.value = 0
})
watch(() => scene.value.sceneId, () => {
  clearChoiceTimer()
  committingOption.value = ''
  showQuizFreeAnswer.value = false
  showIdentityNote.value = false
  showResourceNote.value = false
})
let performanceTimer: ReturnType<typeof setTimeout> | undefined
watch(() => scene.value.sceneId, (sceneId) => {
  if (performanceTimer) clearTimeout(performanceTimer)
  if (scene.value.kind !== 'performance') return
  const duration = sceneId === 'wake-apothecary' ? 26500 : apprenticeVisual.autoPerformanceMs
  performanceTimer = setTimeout(() => {
    // section-one contains the whole three-part prologue; do not advance into
    // the placeholder static scenes between its time ranges.
    if (sceneId === 'wake-apothecary') {
      if (state.value.session) {
        state.value = { ...state.value, performanceIndex: 3, sceneId: sceneForStep(state.value.session.step).sceneId }
        persist()
      } else void startSession()
    }
    else void advanceScene()
  }, duration)
}, { immediate: true })
watch(() => [dialogueText.value, activeQuestionIndex.value], revealDialogue, { immediate: true })
onMounted(async () => {
  startFreshStory()
})
onActivated(startFreshStory)
onBeforeUnmount(() => {
  if (dialogueTimer) clearInterval(dialogueTimer)
  if (performanceTimer) clearTimeout(performanceTimer)
  if (choiceTimer) clearTimeout(choiceTimer)
  if (toastTimer) clearTimeout(toastTimer)
})
</script>

<style scoped lang="scss">
.apprentice-story {
  --story-ink: var(--qh-story-ink-deep);
  --story-ink-soft: var(--qh-story-paper-ink);
  --story-paper-strong: var(--qh-story-paper-strong);
  --story-paper: var(--qh-story-paper);
  --story-paper-deep: var(--qh-story-paper-deep);
  --story-cinnabar: var(--qh-story-cinnabar);
  --story-cinnabar-deep: var(--qh-story-cinnabar-deep);
  --story-gold: var(--qh-color-gold);
  --story-gold-deep: var(--qh-story-gold-deep);
  --story-serif: var(--qh-font-reading);
  position: relative;
  display: grid;
  min-height: 100dvh;
  overflow: hidden;
  color: var(--story-ink);
  background: var(--qh-color-night-soft);
  font-family: var(--qh-font-body);
}
.story-stage { grid-area: 1 / 1; min-height: 100dvh; }
.story-controls { position: absolute; inset: 0 0 auto; z-index: 10; display: flex; align-items: center; justify-content: space-between; gap: var(--qh-space-3); padding: clamp(16px, 2.4vw, 30px) clamp(18px, 3.4vw, 50px); background: linear-gradient(180deg, color-mix(in srgb, var(--story-ink) 58%, transparent), transparent); }
.story-chapter { display: flex; align-items: center; gap: 10px; margin: 0; color: var(--story-paper-strong); font-size: 12px; letter-spacing: .12em; text-shadow: 0 2px 12px color-mix(in srgb, var(--story-ink) 70%, transparent); }
.story-chapter__seal { display: grid; width: 30px; height: 30px; place-items: center; border: 1px solid color-mix(in srgb, var(--story-paper-strong) 55%, transparent); border-radius: 2px; color: var(--story-gold); font-family: var(--story-serif); font-size: 15px; transform: rotate(-2deg); }
.exit-story { appearance: none; padding: 4px 0; border: 0; border-bottom: 1px solid color-mix(in srgb, var(--story-paper-strong) 35%, transparent); background: transparent; color: var(--story-paper); font: inherit; font-size: 11px; letter-spacing: .14em; cursor: pointer; }
.exit-story:hover { color: var(--story-paper-strong); border-bottom-color: var(--story-gold); }
.exit-story:focus-visible { outline: 2px solid var(--story-gold); outline-offset: 3px; }

.story-dialogue { position: absolute; z-index: 8; right: 3.2vw; bottom: 5%; left: 3.2vw; display: block; width: auto; min-height: 148px; margin: 0; padding: 30px 120px 24px 104px; appearance: none; border: 0; background: transparent; color: var(--story-ink); font: inherit; text-align: left; cursor: pointer; filter: drop-shadow(0 18px 28px color-mix(in srgb, var(--story-ink) 42%, transparent)); }
.story-dialogue::before { position: absolute; inset: 0; z-index: -1; background: repeating-linear-gradient(0deg, color-mix(in srgb, var(--story-ink-soft) 3.5%, transparent) 0 1px, transparent 1px 5px), repeating-linear-gradient(90deg, color-mix(in srgb, var(--story-ink-soft) 2.8%, transparent) 0 1px, transparent 1px 6px), linear-gradient(100deg, var(--story-paper-strong) 0%, var(--story-paper) 56%, var(--story-paper-deep) 100%); clip-path: polygon(0 2.2%, 5% .4%, 10% 2.4%, 15% .6%, 21% 2.2%, 27% .8%, 33% 2.5%, 39% .5%, 45% 2.2%, 51% .7%, 57% 2.5%, 63% .5%, 69% 2.1%, 75% .8%, 81% 2.4%, 87% .6%, 93% 2.2%, 97% .5%, 100% 2%, 100% 98%, 95% 99.8%, 89% 98%, 83% 99.7%, 77% 98%, 71% 99.8%, 65% 98%, 59% 99.6%, 53% 98%, 47% 99.8%, 41% 98%, 35% 99.6%, 29% 98%, 23% 99.8%, 17% 98%, 11% 99.6%, 5% 98%, 0 99.8%); box-shadow: inset 0 0 46px color-mix(in srgb, var(--story-ink-soft) 13%, transparent), inset 0 0 0 1px color-mix(in srgb, var(--story-ink-soft) 8%, transparent); content: ""; }
.story-dialogue:hover { filter: drop-shadow(0 18px 28px color-mix(in srgb, var(--story-ink) 46%, transparent)) brightness(1.02); }
.story-dialogue:focus-visible { outline: 2px solid var(--story-gold); outline-offset: 4px; }
.dialogue-copy { display: block; max-width: 86%; font-family: var(--story-serif); font-size: clamp(18px, 1.6vw, 24px); font-weight: 600; line-height: 1.85; letter-spacing: .04em; }
.story-dialogue.is-typing .dialogue-diamond { visibility: hidden; }
.dialogue-diamond { position: absolute; right: 34px; bottom: 22px; width: 10px; height: 10px; background: var(--story-gold); box-shadow: 0 0 0 1px var(--story-gold-deep); transform: rotate(45deg); animation: story-blink 1.2s steps(2, start) infinite; }
.dialogue-seal { position: absolute; top: 20px; right: 34px; display: grid; width: 42px; height: 42px; place-items: center; border: 2px solid var(--story-cinnabar); color: var(--story-cinnabar); font-family: var(--story-serif); font-size: 10px; letter-spacing: .04em; opacity: .72; transform: rotate(-6deg); }
.dialogue-name { position: absolute; top: -34px; left: 26px; display: flex; flex-direction: row-reverse; gap: 4px; width: 52px; padding: 16px 6px 10px; border: 1px solid var(--story-gold); border-radius: 3px; color: var(--story-paper); background: linear-gradient(90deg, color-mix(in srgb, var(--story-ink) 92%, var(--story-gold-deep)), color-mix(in srgb, var(--story-ink) 74%, var(--story-gold-deep)) 45%, color-mix(in srgb, var(--story-ink) 92%, var(--story-gold-deep))); box-shadow: 0 8px 18px color-mix(in srgb, var(--story-ink) 40%, transparent); transform: rotate(-1deg); }
.dialogue-name::before { position: absolute; top: 6px; left: 50%; width: 5px; height: 5px; margin-left: -2.5px; border-radius: 50%; background: var(--story-ink); box-shadow: inset 0 1px 0 color-mix(in srgb, var(--story-paper-strong) 18%, transparent); content: ""; }
.dialogue-name::after { position: absolute; top: -20px; left: 50%; width: 1px; height: 20px; background: linear-gradient(180deg, var(--story-cinnabar-deep), var(--story-cinnabar)); content: ""; }
.dialogue-name__main { writing-mode: vertical-rl; font-family: var(--story-serif); font-size: 16px; letter-spacing: .18em; }
.dialogue-name__role { writing-mode: vertical-rl; color: var(--story-gold); font-size: 8px; letter-spacing: .12em; opacity: .9; }
.type-caret { color: var(--story-gold-deep); animation: story-blink .7s steps(2, start) infinite; }
.story-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
@keyframes story-blink { to { opacity: 0; } }

.story-dock { position: absolute; z-index: 7; right: clamp(20px, 3.4vw, 50px); bottom: calc(5% + 178px); display: flex; flex-direction: column; gap: 10px; width: min(430px, 34vw); max-height: calc(100% - 140px); overflow-y: auto; scrollbar-width: thin; }
.story-tags { display: flex; flex-direction: column; gap: 8px; }
.dialogue-option { position: relative; display: flex; width: 100%; min-height: 54px; align-items: center; padding: 7px 18px 7px 12px; appearance: none; border: 1px solid color-mix(in srgb, var(--story-gold) 42%, transparent); border-radius: 6px; color: var(--story-paper-strong); background: linear-gradient(180deg, color-mix(in srgb, var(--story-ink) 84%, var(--story-gold-deep)), color-mix(in srgb, var(--story-ink) 93%, var(--story-gold-deep)) 55%, var(--story-ink)); box-shadow: inset 0 1px 0 color-mix(in srgb, var(--story-paper-strong) 8%, transparent), inset 0 0 0 2px color-mix(in srgb, var(--story-gold) 14%, transparent), 0 8px 18px color-mix(in srgb, var(--story-ink) 32%, transparent); font: inherit; text-align: left; cursor: pointer; transition: transform 180ms cubic-bezier(.2, .7, .2, 1), border-color 180ms ease, box-shadow 180ms ease, background 180ms ease, color 180ms ease; }
.dialogue-option::before { position: absolute; inset: 0; border-radius: inherit; background: repeating-linear-gradient(92deg, transparent 0 7px, color-mix(in srgb, var(--story-paper-strong) 2.5%, transparent) 7px 8px), repeating-linear-gradient(0deg, transparent 0 3px, color-mix(in srgb, var(--story-ink) 6%, transparent) 3px 4px); content: ""; opacity: .85; pointer-events: none; }
.dialogue-option::after { position: absolute; inset: 3px; border: 1px solid color-mix(in srgb, var(--story-gold) 22%, transparent); border-radius: 3px; content: ""; pointer-events: none; }
.dialogue-option:hover:not(:disabled) { transform: translateX(-8px); border-color: color-mix(in srgb, var(--story-gold) 80%, transparent); box-shadow: inset 0 1px 0 color-mix(in srgb, var(--story-paper-strong) 12%, transparent), inset 0 0 0 2px color-mix(in srgb, var(--story-gold) 25%, transparent), 0 12px 24px color-mix(in srgb, var(--story-ink) 42%, transparent); }
.dialogue-option:active:not(:disabled) { transform: translateX(-4px) scale(.995); }
.dialogue-option:disabled { cursor: default; }
.dialogue-option:focus-visible { outline: 2px solid var(--story-gold); outline-offset: 3px; }
.tag-medal { position: relative; z-index: 1; display: grid; flex: none; width: 38px; height: 38px; margin-right: 12px; place-items: center; border: 1px solid var(--story-gold-deep); border-radius: 50%; color: var(--story-ink-soft); background: radial-gradient(circle at 35% 30%, var(--story-paper-strong), var(--story-paper) 70%, var(--story-paper-deep)); box-shadow: 0 2px 4px color-mix(in srgb, var(--story-ink) 35%, transparent), inset 0 0 0 1px color-mix(in srgb, var(--story-paper-strong) 40%, transparent); font-family: var(--story-serif); font-size: 15px; font-weight: 700; }
.tag-copy { position: relative; z-index: 1; flex: 1; font-family: var(--story-serif); font-size: 16px; font-weight: 600; line-height: 1.4; letter-spacing: .05em; }
.tag-mark { position: relative; z-index: 1; display: grid; flex: none; width: 18px; height: 18px; margin-left: 10px; place-items: center; color: var(--story-gold); }
.tag-mark::before { width: 7px; height: 7px; border-top: 1.5px solid currentColor; border-right: 1.5px solid currentColor; content: ""; transform: rotate(45deg); }
.dialogue-option.is-committing { color: var(--story-ink); background: linear-gradient(120deg, var(--story-paper-strong), var(--story-paper) 70%, var(--story-paper-deep)); border-color: var(--story-cinnabar); box-shadow: 0 10px 22px color-mix(in srgb, var(--story-ink) 35%, transparent), inset 0 0 0 2px color-mix(in srgb, var(--story-cinnabar) 16%, transparent); transform: translateX(-8px) rotate(-.4deg); }
.dialogue-option.is-committing .tag-medal { border-color: var(--story-cinnabar-deep); color: var(--story-paper-strong); background: var(--story-cinnabar); }
.dialogue-option.is-committing::after { border-color: color-mix(in srgb, var(--story-cinnabar) 32%, transparent); }
.dialogue-option.is-committing .tag-mark { width: 24px; height: 24px; border: 1.5px solid var(--story-cinnabar); color: var(--story-cinnabar); font-family: var(--story-serif); font-size: 11px; font-weight: 700; transform: rotate(-8deg); }
.dialogue-option.is-committing .tag-mark::before { width: auto; height: auto; border: 0; content: "答"; transform: none; }
.dialogue-option.is-ghost { border-style: dashed; border-color: color-mix(in srgb, var(--story-gold) 34%, transparent); background: linear-gradient(180deg, color-mix(in srgb, var(--story-ink) 62%, var(--story-gold-deep)), color-mix(in srgb, var(--story-ink) 78%, var(--story-gold-deep)) 60%, color-mix(in srgb, var(--story-ink) 88%, var(--story-gold-deep))); }
.dialogue-option.is-ghost .tag-medal { color: var(--story-ink-soft); background: linear-gradient(180deg, var(--story-paper-strong), var(--story-paper-deep)); }
.dialogue-option.is-ghost:hover:not(:disabled) { border-color: color-mix(in srgb, var(--story-gold) 70%, transparent); }
.tag-medal--brush { font-size: 14px; }
.tag-mark--plus::before { width: auto; height: auto; border: 0; content: "+"; transform: none; font-family: var(--qh-font-body); font-size: 18px; font-weight: 400; }

.reply-note { position: relative; width: 100%; padding: 18px 18px 16px; border: 1px solid color-mix(in srgb, var(--story-ink-soft) 32%, transparent); color: var(--story-ink); background: repeating-linear-gradient(0deg, color-mix(in srgb, var(--story-ink-soft) 4%, transparent) 0 1px, transparent 1px 6px), linear-gradient(145deg, var(--story-paper-strong), var(--story-paper-deep)); clip-path: polygon(0 1%, 98% 0, 100% 4%, 97% 10%, 100% 17%, 97% 24%, 100% 31%, 97% 38%, 100% 45%, 97% 52%, 100% 59%, 97% 66%, 100% 73%, 97% 80%, 100% 87%, 97% 94%, 99% 100%, 0 99%); filter: drop-shadow(0 12px 20px color-mix(in srgb, var(--story-ink) 30%, transparent)); }
.reply-note__head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 8px; color: var(--story-cinnabar); font-size: 10px; letter-spacing: .14em; }
.reply-note__head span:last-child { color: color-mix(in srgb, var(--story-ink-soft) 55%, transparent); letter-spacing: .04em; }
.reply-note textarea, .reply-field textarea { display: block; width: 100%; min-height: 86px; padding: 0 2px; resize: vertical; border: 0; border-radius: 0; color: var(--story-ink); background: repeating-linear-gradient(0deg, transparent 0 27px, color-mix(in srgb, var(--story-ink-soft) 20%, transparent) 27px 28px); font-family: var(--story-serif); font-size: 15px; line-height: 28px; }
.reply-note textarea::placeholder, .reply-field textarea::placeholder { color: color-mix(in srgb, var(--story-ink-soft) 42%, transparent); }
.reply-note textarea:focus-visible, .reply-field textarea:focus-visible { outline: 2px solid color-mix(in srgb, var(--story-cinnabar) 45%, transparent); outline-offset: 2px; }
.reply-field { display: block; margin-top: 10px; }
.reply-field > span { display: block; margin-bottom: 2px; color: var(--story-cinnabar); font-size: 10px; letter-spacing: .1em; }
.reply-note__submit { display: flex; width: 100%; min-height: 40px; align-items: center; justify-content: center; gap: 8px; margin-top: 10px; appearance: none; border: 1px solid var(--story-cinnabar-deep); border-radius: 2px; color: var(--story-paper-strong); background: var(--story-cinnabar); box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--story-paper-strong) 14%, transparent); font: inherit; font-size: 12px; letter-spacing: .14em; cursor: pointer; }
.reply-note__submit:hover:not(:disabled) { background: color-mix(in srgb, var(--story-cinnabar) 84%, var(--story-cinnabar-deep)); }
.reply-note__submit:disabled { opacity: .45; cursor: not-allowed; }

.herb-sheet { position: absolute; z-index: 4; bottom: calc(5% + 178px); left: clamp(20px, 3.4vw, 50px); width: min(390px, 31vw); padding: 20px 20px 18px; color: var(--story-ink); background: repeating-linear-gradient(0deg, color-mix(in srgb, var(--story-ink-soft) 3.5%, transparent) 0 1px, transparent 1px 5px), linear-gradient(150deg, var(--story-paper-strong), var(--story-paper) 60%, var(--story-paper-deep)); clip-path: polygon(0 1%, 97% 0, 94% 6%, 98% 12%, 95% 19%, 99% 26%, 95% 34%, 98% 42%, 95% 50%, 99% 58%, 95% 66%, 98% 74%, 95% 82%, 99% 90%, 96% 100%, 0 99%); filter: drop-shadow(0 22px 30px color-mix(in srgb, var(--story-ink) 42%, transparent)); transform: rotate(-1.4deg); }
.herb-sheet__index { display: inline-block; padding-bottom: 4px; border-bottom: 1px solid color-mix(in srgb, var(--story-cinnabar) 35%, transparent); color: color-mix(in srgb, var(--story-gold-deep) 80%, var(--story-ink-soft)); font-size: 10px; letter-spacing: .14em; }
.herb-sheet__photo { position: relative; margin: 12px 2px 10px; padding: 8px; background: var(--story-paper-strong); box-shadow: 0 2px 6px color-mix(in srgb, var(--story-ink) 22%, transparent); transform: rotate(.6deg); }
.herb-sheet__photo img { display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: cover; filter: sepia(.08) contrast(1.02); }
.photo-corner { position: absolute; z-index: 2; width: 18px; height: 18px; border-color: color-mix(in srgb, var(--story-gold-deep) 70%, var(--story-ink-soft)); border-style: solid; }
.photo-corner--tl { top: -2px; left: -2px; border-width: 2px 0 0 2px; }
.photo-corner--tr { top: -2px; right: -2px; border-width: 2px 2px 0 0; }
.photo-corner--bl { bottom: -2px; left: -2px; border-width: 0 0 2px 2px; }
.photo-corner--br { right: -2px; bottom: -2px; border-width: 0 2px 2px 0; }
.herb-sheet__label { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 1px 10px; align-items: end; padding: 8px 2px 0; border-top: 1px solid color-mix(in srgb, var(--story-gold-deep) 28%, transparent); }
.hand { grid-column: 1; color: var(--story-cinnabar); font-size: 9px; letter-spacing: .18em; }
.herb-sheet__label strong { grid-column: 1; font-family: var(--story-serif); font-size: clamp(24px, 2.4vw, 31px); line-height: 1.05; letter-spacing: .06em; }
.herb-sheet__label em { grid-column: 1; color: color-mix(in srgb, var(--story-gold-deep) 70%, var(--story-ink-soft)); font-size: 11px; font-style: normal; letter-spacing: .04em; }
.seal-stamp { grid-column: 2; grid-row: 2 / 4; display: grid; width: 46px; height: 46px; place-items: center; align-self: center; border: 2px solid var(--story-cinnabar); border-radius: 2px; color: var(--story-cinnabar); font-family: var(--story-serif); font-size: 11px; letter-spacing: .04em; opacity: .8; transform: rotate(-8deg); writing-mode: vertical-rl; }

.story-rx { position: absolute; z-index: 6; bottom: 5%; left: 5vw; width: min(620px, 50vw); max-height: 78%; overflow-y: auto; padding: 26px 34px 22px; color: var(--story-ink); background: linear-gradient(90deg, transparent 0 92.6%, color-mix(in srgb, var(--story-cinnabar) 16%, transparent) 92.6% 93%, transparent 93%), repeating-linear-gradient(0deg, color-mix(in srgb, var(--story-ink-soft) 3.5%, transparent) 0 1px, transparent 1px 6px), linear-gradient(150deg, var(--story-paper-strong), var(--story-paper) 65%, var(--story-paper-deep)); border: 0; clip-path: polygon(0 0, 100% 1%, 99.4% 98%, 96% 100%, 0 99%); filter: drop-shadow(0 24px 34px color-mix(in srgb, var(--story-ink) 46%, transparent)); transform: rotate(-.7deg); scrollbar-width: thin; }
.story-rx__head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; padding-bottom: 10px; border-bottom: 2px solid color-mix(in srgb, var(--story-cinnabar) 55%, transparent); }
.story-rx__title { display: flex; align-items: baseline; gap: 14px; }
.story-rx__title span { color: var(--story-cinnabar); font-family: var(--story-serif); font-size: 12px; letter-spacing: .18em; }
.story-rx__title strong { font-family: var(--story-serif); font-size: 28px; letter-spacing: .16em; }
.story-rx__no { color: color-mix(in srgb, var(--story-gold-deep) 80%, var(--story-ink-soft)); font-size: 10px; letter-spacing: .1em; }
.story-rx__prompt { margin: 12px 0 14px; color: color-mix(in srgb, var(--story-gold-deep) 70%, var(--story-ink-soft)); font-size: 12px; line-height: 1.7; }
.story-rx__prompt span { margin-right: 8px; padding: 1px 5px; border: 1px solid color-mix(in srgb, var(--story-cinnabar) 50%, transparent); color: var(--story-cinnabar); font-size: 10px; letter-spacing: .1em; }
.story-rx__field { display: block; margin-bottom: 8px; }
.story-rx__field > span { display: block; color: var(--story-cinnabar); font-size: 10px; letter-spacing: .18em; }
.story-rx__field textarea { display: block; width: 100%; min-height: 64px; padding: 0 2px; resize: vertical; border: 0; border-radius: 0; color: var(--story-ink); background: repeating-linear-gradient(0deg, transparent 0 31px, color-mix(in srgb, var(--story-ink-soft) 22%, transparent) 31px 32px); font-family: var(--story-serif); font-size: 17px; line-height: 32px; }
.story-rx__field textarea:focus-visible { outline: 2px solid color-mix(in srgb, var(--story-cinnabar) 40%, transparent); outline-offset: 2px; }
.story-rx__methods { margin: 8px 0 14px; padding: 0; border: 0; }
.story-rx__methods legend { margin-bottom: 7px; color: color-mix(in srgb, var(--story-gold-deep) 80%, var(--story-ink-soft)); font-size: 10px; letter-spacing: .14em; }
.story-stamps { display: flex; flex-wrap: wrap; gap: 8px; }
.story-stamp-btn { position: relative; appearance: none; padding: 8px 11px; border: 1px solid color-mix(in srgb, var(--story-gold-deep) 38%, transparent); border-radius: 2px; color: color-mix(in srgb, var(--story-gold-deep) 80%, var(--story-ink-soft)); background: transparent; font-family: var(--story-serif); font-size: 12px; cursor: pointer; transform: rotate(-1deg); }
.story-stamp-btn:nth-child(even) { transform: rotate(1.1deg); }
.story-stamp-btn:hover { border-color: var(--story-cinnabar); color: var(--story-cinnabar); }
.story-stamp-btn[aria-pressed="true"] { color: var(--story-cinnabar); border-color: var(--story-cinnabar); background: color-mix(in srgb, var(--story-cinnabar) 6%, transparent); }
.story-stamp-btn[aria-pressed="true"]::before { position: absolute; inset: -3px; border: 2px solid color-mix(in srgb, var(--story-cinnabar) 55%, transparent); border-radius: 50% / 42%; content: ""; opacity: .62; transform: rotate(-4deg); }
.story-rx__note { display: block; margin-bottom: 14px; }
.story-rx__note > span { display: block; color: color-mix(in srgb, var(--story-gold-deep) 80%, var(--story-ink-soft)); font-size: 10px; letter-spacing: .14em; }
.story-rx__note input { display: block; width: 100%; padding: 6px 2px; border: 0; border-bottom: 1px solid color-mix(in srgb, var(--story-ink-soft) 22%, transparent); border-radius: 0; color: var(--story-ink); background: transparent; font-family: var(--story-serif); font-size: 14px; }
.story-rx__note input:focus-visible { outline: 2px solid color-mix(in srgb, var(--story-cinnabar) 40%, transparent); outline-offset: 2px; }
.story-rx__foot { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-top: 10px; border-top: 1px solid color-mix(in srgb, var(--story-gold-deep) 28%, transparent); }
.story-rx__sign { color: color-mix(in srgb, var(--story-gold-deep) 80%, var(--story-ink-soft)); font-family: var(--story-serif); font-size: 12px; letter-spacing: .08em; }
.story-rx__sign b { color: var(--story-ink); font-weight: 400; }
.story-rx__submit { display: inline-flex; align-items: center; gap: 9px; appearance: none; padding: 9px 16px; border: 1px solid var(--story-cinnabar-deep); border-radius: 2px; color: var(--story-paper-strong); background: var(--story-cinnabar); box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--story-paper-strong) 15%, transparent); font: inherit; font-family: var(--story-serif); font-size: 14px; letter-spacing: .12em; cursor: pointer; transform: rotate(-.5deg); }
.story-rx__submit:hover:not(:disabled) { background: color-mix(in srgb, var(--story-cinnabar) 84%, var(--story-cinnabar-deep)); }
.story-rx__submit:disabled { opacity: .5; cursor: not-allowed; }
.story-rx__seal { display: grid; width: 24px; height: 24px; place-items: center; border: 1px solid color-mix(in srgb, var(--story-paper-strong) 85%, transparent); border-radius: 50%; font-size: 10px; transform: rotate(9deg); }

.story-rule { position: absolute; z-index: 6; bottom: 4%; left: 50%; width: min(620px, 52vw); max-height: 74%; overflow-y: auto; padding: 26px 34px 22px; color: var(--story-ink); background: repeating-linear-gradient(0deg, color-mix(in srgb, var(--story-ink-soft) 3.5%, transparent) 0 1px, transparent 1px 6px), linear-gradient(150deg, var(--story-paper-strong), var(--story-paper) 65%, var(--story-paper-deep)); border: 1px solid color-mix(in srgb, var(--story-gold-deep) 28%, transparent); clip-path: polygon(0 1%, 99% 0, 96% 5%, 100% 11%, 97% 18%, 100% 25%, 97% 32%, 100% 39%, 97% 46%, 100% 53%, 97% 60%, 100% 67%, 97% 74%, 100% 81%, 97% 88%, 100% 94%, 97% 100%, 0 99%); filter: drop-shadow(0 24px 34px color-mix(in srgb, var(--story-ink) 46%, transparent)); transform: translateX(-50%); scrollbar-width: thin; }
.story-rule__head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding-bottom: 10px; border-bottom: 2px solid color-mix(in srgb, var(--story-cinnabar) 55%, transparent); }
.story-rule__head span { color: var(--story-cinnabar); font-family: var(--story-serif); font-size: 11px; letter-spacing: .18em; }
.story-rule__head strong { font-family: var(--story-serif); font-size: 28px; letter-spacing: .16em; }
.story-rule__prompt { margin: 12px 0 16px; color: color-mix(in srgb, var(--story-gold-deep) 70%, var(--story-ink-soft)); font-size: 12px; line-height: 1.7; }
.story-rule__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.story-rule__grid label > span { display: block; color: var(--story-cinnabar); font-size: 10px; letter-spacing: .14em; }
.story-rule__grid input { width: 100%; padding: 7px 2px; border: 0; border-bottom: 1px solid color-mix(in srgb, var(--story-ink-soft) 24%, transparent); border-radius: 0; color: var(--story-ink); background: transparent; font-family: var(--story-serif); font-size: 18px; }
.story-rule__grid input:focus-visible, .story-rule__note textarea:focus-visible { outline: 2px solid color-mix(in srgb, var(--story-cinnabar) 40%, transparent); outline-offset: 2px; }
.story-rule__check { display: flex; align-items: center; gap: 8px; margin: 16px 0; color: color-mix(in srgb, var(--story-gold-deep) 80%, var(--story-ink-soft)); font-size: 13px; }
.story-rule__check input { accent-color: var(--story-cinnabar); width: 16px; height: 16px; }
.story-rule__note { display: block; }
.story-rule__note > span { display: block; color: var(--story-cinnabar); font-size: 10px; letter-spacing: .14em; }
.story-rule__note textarea { display: block; width: 100%; min-height: 70px; padding: 0 2px; resize: vertical; border: 0; border-radius: 0; color: var(--story-ink); background: repeating-linear-gradient(0deg, transparent 0 27px, color-mix(in srgb, var(--story-ink-soft) 20%, transparent) 27px 28px); font-family: var(--story-serif); font-size: 15px; line-height: 28px; }
.story-rule__submit { display: flex; width: 100%; align-items: center; justify-content: center; gap: 9px; margin-top: 16px; appearance: none; padding: 10px 16px; border: 1px solid var(--story-cinnabar-deep); border-radius: 2px; color: var(--story-paper-strong); background: var(--story-cinnabar); box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--story-paper-strong) 15%, transparent); font: inherit; font-family: var(--story-serif); font-size: 14px; letter-spacing: .12em; cursor: pointer; }
.story-rule__submit:disabled { opacity: .5; cursor: not-allowed; }
.story-rule__seal { display: grid; width: 24px; height: 24px; place-items: center; border: 1px solid color-mix(in srgb, var(--story-paper-strong) 85%, transparent); border-radius: 50%; font-size: 10px; transform: rotate(9deg); }

.story-cert { position: absolute; z-index: 6; top: 50%; left: 28%; width: min(420px, 32vw); filter: drop-shadow(0 24px 36px color-mix(in srgb, var(--story-ink) 50%, transparent)); transform: translate(-50%, -50%); }
.story-cert__roller { position: relative; height: 16px; border-radius: 8px; background: linear-gradient(180deg, color-mix(in srgb, var(--story-ink) 55%, var(--story-gold-deep)), color-mix(in srgb, var(--story-ink) 85%, var(--story-gold-deep)) 50%, color-mix(in srgb, var(--story-ink) 58%, var(--story-gold-deep))); box-shadow: inset 0 1px 0 color-mix(in srgb, var(--story-paper-strong) 25%, transparent), 0 4px 8px color-mix(in srgb, var(--story-ink) 40%, transparent); }
.story-cert__roller::before, .story-cert__roller::after { position: absolute; top: 1px; width: 18px; height: 14px; border-radius: 4px; background: linear-gradient(180deg, var(--story-gold), var(--story-gold-deep)); content: ""; }
.story-cert__roller::before { left: -6px; }
.story-cert__roller::after { right: -6px; }
.story-cert__paper { position: relative; margin: 0 10px; padding: 30px 30px 32px; text-align: center; color: var(--story-ink); background: repeating-linear-gradient(0deg, color-mix(in srgb, var(--story-ink-soft) 3.5%, transparent) 0 1px, transparent 1px 5px), repeating-linear-gradient(90deg, color-mix(in srgb, var(--story-ink-soft) 2.5%, transparent) 0 1px, transparent 1px 7px), linear-gradient(150deg, var(--story-paper-strong), var(--story-paper) 60%, var(--story-paper-deep)); clip-path: polygon(0 0, 100% 0, 97% 4%, 100% 9%, 97% 14%, 100% 19%, 97% 24%, 100% 29%, 97% 34%, 100% 39%, 97% 44%, 100% 49%, 97% 54%, 100% 59%, 97% 64%, 100% 69%, 97% 74%, 100% 79%, 97% 84%, 100% 89%, 97% 94%, 100% 100%, 0 100%); }
.story-cert__kicker { display: block; color: var(--story-cinnabar); font-size: 10px; letter-spacing: .24em; }
.story-cert__paper strong { display: block; margin: 9px 0 12px; font-family: var(--story-serif); font-size: clamp(26px, 2.6vw, 34px); font-weight: 700; letter-spacing: .14em; }
.story-cert__rule { display: block; width: 56px; height: 1px; margin: 0 auto 14px; background: var(--story-gold-deep); }
.story-cert__paper p { margin: 0 0 16px; color: color-mix(in srgb, var(--story-gold-deep) 70%, var(--story-ink-soft)); font-family: var(--story-serif); font-size: 13px; line-height: 1.9; }
.story-cert__count { display: inline-flex; align-items: baseline; gap: 6px; margin-bottom: 14px; padding: 5px 14px; border-top: 1px solid color-mix(in srgb, var(--story-gold-deep) 30%, transparent); border-bottom: 1px solid color-mix(in srgb, var(--story-gold-deep) 30%, transparent); }
.story-cert__count b { color: var(--story-cinnabar); font-family: var(--story-serif); font-size: 26px; }
.story-cert__count span { color: color-mix(in srgb, var(--story-gold-deep) 80%, var(--story-ink-soft)); font-size: 10px; letter-spacing: .14em; }
.story-cert__seal { position: absolute; top: 22px; right: 24px; display: grid; width: 62px; height: 62px; place-items: center; border: 3px double var(--story-cinnabar); border-radius: 3px; color: var(--story-cinnabar); font-family: var(--story-serif); font-size: 17px; letter-spacing: .08em; opacity: .78; transform: rotate(-7deg); writing-mode: vertical-rl; }
.story-cert__btn { display: flex; width: 100%; min-height: 42px; align-items: center; justify-content: center; gap: 8px; appearance: none; border: 1px solid var(--story-cinnabar); border-radius: 2px; color: var(--story-cinnabar-deep); background: color-mix(in srgb, var(--story-cinnabar) 8%, transparent); font: inherit; font-family: var(--story-serif); font-size: 13px; letter-spacing: .1em; cursor: pointer; }
.story-cert__btn:hover { background: color-mix(in srgb, var(--story-cinnabar) 14%, transparent); }

.story-note { position: absolute; z-index: 9; top: 50%; left: 50%; width: min(420px, calc(100% - 2 * var(--qh-space-4))); padding: var(--qh-space-5); color: var(--story-ink); background: repeating-linear-gradient(0deg, color-mix(in srgb, var(--story-ink-soft) 3.5%, transparent) 0 1px, transparent 1px 6px), linear-gradient(150deg, var(--story-paper-strong), var(--story-paper) 65%, var(--story-paper-deep)); border: 1px solid color-mix(in srgb, var(--story-gold-deep) 30%, transparent); box-shadow: 0 22px 50px color-mix(in srgb, var(--story-ink) 38%, transparent); transform: translate(-50%, -50%); }
.story-note.is-state { width: min(470px, calc(100% - 2 * var(--qh-space-4))); padding: var(--qh-space-3); --qh-text: var(--story-ink); --qh-text-muted: color-mix(in srgb, var(--story-ink-soft) 66%, transparent); }
.story-note.is-state .state-error h3, .story-note.is-state .state-loading h3 { color: var(--story-ink); }
.note-kicker { display: block; margin-bottom: 8px; color: var(--story-cinnabar); font-size: 10px; letter-spacing: .2em; }
.story-note strong { display: block; font-family: var(--story-serif); font-size: 24px; letter-spacing: .08em; }
.story-note p { margin: 10px 0 0; color: color-mix(in srgb, var(--story-gold-deep) 70%, var(--story-ink-soft)); font-size: 12px; line-height: 1.8; }
.note-button { display: flex; width: 100%; min-height: 44px; align-items: center; justify-content: center; gap: 8px; margin-top: 16px; appearance: none; border: 1px solid var(--story-cinnabar-deep); border-radius: 2px; color: var(--story-paper-strong); background: var(--story-cinnabar); font: inherit; font-family: var(--story-serif); font-size: 14px; letter-spacing: .12em; cursor: pointer; }
.note-button:hover { background: color-mix(in srgb, var(--story-cinnabar) 84%, var(--story-cinnabar-deep)); }
.quiz-intro { text-align: center; }
.action-panel { text-align: center; }

.story-toast { position: absolute; z-index: 12; top: 76px; left: 50%; padding: 8px 16px; border: 1px solid color-mix(in srgb, var(--story-gold) 45%, transparent); border-radius: 2px; color: var(--story-paper-strong); background: color-mix(in srgb, var(--story-ink) 90%, transparent); box-shadow: 0 8px 18px color-mix(in srgb, var(--story-ink) 35%, transparent); font-size: 11px; letter-spacing: .08em; transform: translateX(-50%); animation: story-toast-in 180ms ease both; }
@keyframes story-toast-in { from { opacity: 0; transform: translate(-50%, -8px); } }

@media (max-width: 980px) {
  .story-controls { padding: 14px 16px; }
  .story-chapter { font-size: 11px; }
  .exit-story { font-size: 10px; }
  .story-dialogue { right: 14px; bottom: 14px; left: 14px; min-height: 118px; padding: 26px 58px 18px 82px; }
  .dialogue-copy { max-width: 100%; font-size: 15px; line-height: 1.8; }
  .dialogue-name { top: -28px; left: 14px; width: 42px; padding: 13px 4px 8px; }
  .dialogue-name__main { font-size: 14px; }
  .dialogue-name__role { font-size: 7px; }
  .dialogue-seal { top: 16px; right: 20px; width: 30px; height: 30px; font-size: 8px; }
  .dialogue-diamond { right: 24px; bottom: 16px; }
  .herb-sheet { top: 70px; right: 14px; bottom: auto; left: 14px; display: grid; grid-template-columns: 132px minmax(0, 1fr); gap: 10px; width: auto; align-items: center; padding: 10px; clip-path: none; transform: none; }
  .herb-sheet__index { position: absolute; top: 8px; left: 10px; z-index: 2; padding: 1px 4px; border: 0; background: color-mix(in srgb, var(--story-paper-strong) 82%, transparent); font-size: 8px; }
  .herb-sheet__photo { margin: 0; padding: 4px; }
  .herb-sheet__photo img { aspect-ratio: 1 / 1; }
  .herb-sheet__label { padding: 0 0 0 10px; border-top: 0; border-left: 1px solid color-mix(in srgb, var(--story-gold-deep) 28%, transparent); align-content: center; }
  .herb-sheet__label strong { font-size: 23px; }
  .photo-corner { width: 12px; height: 12px; }
  .seal-stamp { width: 38px; height: 38px; font-size: 9px; }
  .story-dock { right: 14px; bottom: 158px; left: 14px; width: auto; max-height: calc(100% - 300px); }
  .dialogue-option { min-height: 48px; padding: 5px 14px 5px 10px; }
  .tag-medal { width: 32px; height: 32px; margin-right: 9px; font-size: 13px; }
  .tag-copy { font-size: 14px; }
  .reply-note textarea, .reply-field textarea { min-height: 74px; font-size: 14px; line-height: 26px; background: repeating-linear-gradient(0deg, transparent 0 25px, color-mix(in srgb, var(--story-ink-soft) 20%, transparent) 25px 26px); }
  .story-rx { top: 64px; right: 14px; bottom: 14px; left: 14px; width: auto; max-height: none; padding: 22px 20px 18px; transform: none; }
  .story-rx__title strong { font-size: 24px; }
  .story-rx__head { align-items: center; }
  .story-rx__prompt { font-size: 11px; }
  .story-rx__field textarea { font-size: 15px; line-height: 29px; min-height: 58px; background: repeating-linear-gradient(0deg, transparent 0 28px, color-mix(in srgb, var(--story-ink-soft) 22%, transparent) 28px 29px); }
  .story-rx__foot { align-items: flex-start; flex-direction: column; }
  .story-rx__submit { width: 100%; justify-content: center; }
  .story-rule { top: 64px; right: 14px; bottom: 14px; left: 14px; width: auto; max-height: none; padding: 22px 20px 18px; transform: none; clip-path: none; }
  .story-rule__head strong { font-size: 24px; }
  .story-cert { top: 48%; left: 50%; width: calc(100% - 36px); }
  .story-cert__paper { padding: 24px 22px 26px; }
  .story-cert__seal { top: 18px; right: 18px; width: 52px; height: 52px; font-size: 14px; }
  .story-note { width: calc(100% - 28px); }
  .story-toast { top: 58px; max-width: calc(100% - 28px); text-align: center; }
  .note-open .herb-sheet { visibility: hidden; opacity: 0; }
}

@media (max-width: 560px) {
  .story-dialogue { bottom: 12px; min-height: 108px; padding: 24px 46px 15px 74px; }
  .dialogue-copy { font-size: 14px; }
  .dialogue-name { width: 38px; }
  .dialogue-name__main { font-size: 13px; }
  .herb-sheet { grid-template-columns: 104px minmax(0, 1fr); gap: 8px; }
  .herb-sheet__label strong { font-size: 20px; }
  .story-dock { bottom: 146px; max-height: calc(100% - 280px); }
  .dialogue-option { min-height: 46px; }
  .tag-copy { font-size: 13px; }
  .story-rx { top: 58px; right: 10px; bottom: 10px; left: 10px; padding: 18px 15px 15px; }
  .story-rx__no { display: none; }
  .story-stamp-btn { padding: 6px 8px; font-size: 11px; }
  .story-rule { top: 58px; right: 10px; bottom: 10px; left: 10px; padding: 18px 15px 15px; }
  .story-rule__grid { grid-template-columns: 1fr; gap: 10px; }
  .story-cert__paper strong { font-size: 24px; }
  .story-cert__paper p { font-size: 12px; }
}

@media (max-height: 760px) and (min-width: 981px) {
  .herb-sheet { width: min(330px, 28vw); bottom: calc(4% + 150px); }
  .story-dock { bottom: calc(4% + 150px); width: min(390px, 32vw); }
  .story-dialogue { min-height: 124px; padding-top: 24px; padding-bottom: 18px; }
  .dialogue-copy { font-size: 20px; }
  .dialogue-option { min-height: 46px; }
  .tag-copy { font-size: 14px; }
}

@media (prefers-reduced-motion: reduce) {
  .story-controls .exit-story { transition: none; }
  .story-dialogue, .dialogue-option, .reply-note, .story-rx, .story-rule, .story-cert, .story-note, .story-toast { transition: none !important; animation: none !important; }
  .dialogue-option:hover, .dialogue-option:active, .dialogue-option.is-committing { transform: none; }
}
</style>
