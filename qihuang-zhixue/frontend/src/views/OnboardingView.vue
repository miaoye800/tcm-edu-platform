<template>
  <section v-loading="pageLoading" class="onboarding-view">
    <div class="onboarding-hero">
      <div>
        <p class="eyebrow">画像入口</p>
        <h2>{{ resultReady ? '画像已点亮' : session ? activeStep?.title : '焕醒你的弟子画像' }}</h2>
        <p>
          {{
            resultReady
              ? '已根据本轮证据更新七维画像。低置信维度仍会以虚线显示，后续边学边补。'
              : session
                ? activeStep?.prompt
                : '选一种舒服的方式开始。也可以先随便逛逛，系统只在你使用时做被动取证。'
          }}
        </p>
      </div>

      <div class="hero-actions">
        <el-button v-if="!resultReady" :icon="View" @click="skipOnboarding">先随便逛逛 / 跳过</el-button>
        <el-button v-if="session && !resultReady" :icon="Refresh" @click="resetFlow">换一种方式</el-button>
      </div>
    </div>

    <el-alert v-if="error" class="onboarding-alert" type="warning" :title="error" show-icon :closable="false" />

    <section v-if="!session && !resultReady" class="mode-grid" aria-label="选择画像构建方式">
      <GlassCard v-for="mode in modeCards" :key="mode.mode" class="mode-card" padding="lg">
        <div class="mode-icon">
          <el-icon><component :is="mode.icon" /></el-icon>
        </div>
        <div>
          <p class="eyebrow">{{ mode.kicker }}</p>
          <h3>{{ mode.title }}</h3>
          <p>{{ mode.description }}</p>
        </div>
        <div class="mode-foot">
          <Badge tone="gold" :label="mode.duration" />
          <el-button type="primary" :icon="ArrowRight" @click="mode.mode === 'passive' ? skipOnboarding() : startMode(mode.mode)">
            {{ mode.mode === 'passive' ? '开启旁观' : '开始' }}
          </el-button>
        </div>
      </GlassCard>
    </section>

    <ProfileDialogue
      v-else-if="session && !resultReady && activeMode === 'dialogue'"
      :prompt="dialoguePrompt"
      :card-scores="dialogueCardScores"
      :inferred-card="dialogueInferredCard"
      :radar="dialogueRadarAxes"
      :profile-progress="dialogueProfileProgress"
      :evidence-panel="dialogueEvidencePanel"
      :identity-cards-view="dialogueIdentityCards"
      :active-target="dialogueActiveTarget"
      :active-target-label="dialogueActiveTargetLabel"
      :converged="dialogueConverged"
      :quick-replies="dialogueQuickReplies"
      :stream-events="dialogueStreamEvents"
      :pending="dialoguePending"
      :disabled="!activeStep"
      @answer="answerDialogue"
      @confirm="confirmDialogue"
    />

    <GlassCard v-else-if="session && !resultReady" class="session-card" padding="lg">
      <div class="session-head">
        <div>
          <p class="eyebrow">当前流程</p>
          <h3>{{ activeStep?.title }}</h3>
        </div>
        <Badge tone="blue" :label="`${session.evidence_count} 张证据卡`" />
      </div>

      <el-progress class="session-progress" :percentage="progressPercent" :show-text="false" />

      <section v-if="activeStep?.kind === 'identity'" class="step-block">
        <p class="field-label">选择身份卡</p>
        <el-radio-group v-model="form.identityCard" class="choice-grid">
          <el-radio-button v-for="card in identityCards" :key="card" :label="card" :value="card" />
        </el-radio-group>
      </section>

      <section v-else-if="activeStep?.kind === 'identity_quiz'" class="step-block">
        <p class="field-label">身份卡</p>
        <el-radio-group v-model="form.identityCard" class="choice-grid">
          <el-radio-button v-for="card in identityCards" :key="card" :label="card" :value="card" />
        </el-radio-group>
        <QuestionSet v-model="form.questionAnswers" :questions="questions" />
      </section>

      <section v-else-if="activeStep?.kind === 'diagnostic_quiz'" class="step-block">
        <QuestionSet v-model="form.questionAnswers" :questions="questions" />
      </section>

      <section v-else-if="activeStep?.kind === 'prescription'" class="step-block two-fields">
        <label>
          <span class="field-label">病例</span>
          <el-input v-model="form.caseText" type="textarea" :rows="3" placeholder="如：热毒咽痛，咽喉肿痛。" />
        </label>
        <label>
          <span class="field-label">处方</span>
          <el-input v-model="form.prescription" type="textarea" :rows="4" placeholder="如：金银花15g，连翘12g。" />
        </label>
      </section>

      <section v-else-if="activeStep?.kind === 'resource_choice'" class="step-block">
        <p class="field-label">挑一件师父药宝</p>
        <el-radio-group v-model="form.resourceType" class="choice-grid">
          <el-radio-button v-for="resource in resourceTypes" :key="resource" :label="resource" :value="resource" />
        </el-radio-group>
      </section>

      <section v-else-if="activeStep?.kind === 'learning_rule'" class="step-block rule-grid">
        <label>
          <span class="field-label">每日时长</span>
          <el-input-number v-model="form.dailyMinutes" :min="5" :max="180" :step="5" controls-position="right" />
        </label>
        <label>
          <span class="field-label">目标周期</span>
          <el-input-number v-model="form.targetDays" :min="3" :max="90" :step="1" controls-position="right" />
        </label>
        <label class="switch-line">
          <span class="field-label">接受挑战</span>
          <el-switch v-model="form.acceptChallenge" />
        </label>
      </section>

      <footer class="session-actions">
        <el-button @click="skipOnboarding">先随便逛逛 / 跳过</el-button>
        <el-button type="primary" :icon="Check" :disabled="!canSubmitStep" @click="submitStep">
          {{ session.finished ? '生成画像' : '下一步' }}
        </el-button>
      </footer>
    </GlassCard>

    <section v-else class="result-grid">
      <GlassCard class="radar-result" padding="lg">
        <div class="session-head">
          <div>
            <p class="eyebrow">七维画像</p>
            <h3>实线为较稳维度，虚线为待取证维度</h3>
          </div>
          <Badge tone="gold" :label="`${lowConfidenceCount} 维待取证`" />
        </div>
        <RadarChart :axes="radarAxes" />
      </GlassCard>

      <GlassCard class="narrative-result" padding="lg">
        <div class="session-head">
          <div>
            <p class="eyebrow">弟子档案卷</p>
            <h3>{{ auth.displayName }}</h3>
          </div>
          <Badge :tone="resultNarrativeBadgeTone" :label="resultNarrativeBadgeText" />
        </div>
        <el-alert
          v-if="showNarrativeRetry"
          class="onboarding-alert"
          type="warning"
          :title="profileStore.narrativeError"
          show-icon
          :closable="false"
        >
          <template #default>
            <el-button size="small" type="primary" @click="retryNarrative">重新生成</el-button>
          </template>
        </el-alert>

        <template v-if="showNarrativePlaceholder">
          <p>小岐正在整理本轮证据并生成画像档案，你现在可以继续进入主页。</p>
          <div class="resource-grid narrative-loading-grid">
            <article v-for="index in 3" :key="`narrative-loading-${index}`">
              <strong>生成中</strong>
              <span>画像叙述完成后，这里会替换成真实内容。</span>
            </article>
          </div>
        </template>

        <template v-else>
          <p>{{ narrativeText }}</p>
          <p v-if="showTemplateSource" class="narrative-source">当前展示的是模板叙述（MOCK 演示来源）。</p>
        </template>

        <div class="resource-grid">
          <article v-for="card in resourceCards" :key="card.title">
            <strong>{{ card.title }}</strong>
            <span>{{ card.desc }}</span>
          </article>
        </div>
        <div class="result-actions">
          <el-button :icon="Refresh" @click="resetFlow">重塑画像</el-button>
          <el-button type="primary" :icon="ArrowRight" @click="enterApp">进入主页面</el-button>
        </div>
      </GlassCard>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Check, Compass, FirstAidKit, MagicStick, Refresh, Tickets, View } from '@element-plus/icons-vue'
import ProfileDialogue from '../components/profile/ProfileDialogue.vue'
import RadarChart from '../components/profile/RadarChart.vue'
import type { AnswerPayload, CardScores, ConfirmPayload } from '../components/profile/profileDialogueModel'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { answerProfileSession, finishProfileSession, startProfileSession, streamProfileDialogueChat } from '../services/profileApi'
import { saveOnboardingState } from '../services/onboardingStorage'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import type { ProfileBuildMode, ProfileDialogueStreamUiEvent, ProfileSessionQuestion, ProfileSessionResponse } from '../types/profile'
import { buildRadarAxes } from '../utils/profileMath'

type ModeCard = {
  mode: ProfileBuildMode
  kicker: string
  title: string
  description: string
  duration: string
  icon: typeof MagicStick
}

const QuestionSet = defineComponent({
  name: 'QuestionSet',
  props: {
    modelValue: {
      type: Object,
      required: true,
    },
    questions: {
      type: Array<ProfileSessionQuestion>,
      required: true,
    },
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const update = (questionId: string, selected: string) => {
      emit('update:modelValue', { ...(props.modelValue as Record<string, string>), [questionId]: selected })
    }

    return () =>
      h(
        'div',
        { class: 'question-set' },
        props.questions.map((question, index) =>
          h('article', { class: 'question-card', key: question.id }, [
            h('strong', `${index + 1}. ${question.question}`),
            h(
              'div',
              { class: 'answer-grid' },
              question.options.map((option) =>
                h(
                  'button',
                  {
                    class: ['answer-option', { 'is-selected': (props.modelValue as Record<string, string>)[question.id] === option }],
                    type: 'button',
                    onClick: () => update(question.id, option),
                  },
                  option,
                ),
              ),
            ),
          ]),
        ),
      )
  },
})

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const profileStore = useProfileStore()

const retryNarrativeLabel = '\u91cd\u65b0\u751f\u6210'
const resultNarrativePendingText = '\u5c0f\u5c90\u6b63\u5728\u6574\u7406\u672c\u8f6e\u8bc1\u636e\u5e76\u751f\u6210\u753b\u50cf\u6863\u6848\uff0c\u4f60\u73b0\u5728\u53ef\u4ee5\u7ee7\u7eed\u8fdb\u5165\u4e3b\u9875\u3002'
const resultNarrativeLoadingTitle = '\u751f\u6210\u4e2d'
const resultNarrativeLoadingBody = '\u753b\u50cf\u53d9\u8ff0\u5b8c\u6210\u540e\uff0c\u8fd9\u91cc\u4f1a\u66ff\u6362\u6210\u771f\u5b9e\u5185\u5bb9\u3002'
const narrativeMockSourceText = '\u5f53\u524d\u5c55\u793a\u7684\u662f\u6a21\u677f\u53d9\u8ff0\uff08MOCK \u6f14\u793a\u6765\u6e90\uff09\u3002'

const busy = ref(false)
const dialoguePending = ref(false)
const dialogueStreamEvents = ref<ProfileDialogueStreamUiEvent[]>([])
const error = ref('')
const session = ref<ProfileSessionResponse | null>(null)
const activeMode = ref<ProfileBuildMode | null>(null)
const resultReady = ref(false)
const stepStartedAt = ref(Date.now())
let dialogueStreamEventId = 0
let dialogueStreamInFlight: Promise<void> | null = null

const form = reactive({
  identityCard: '临床学徒',
  questionAnswers: {} as Record<string, string>,
  caseText: '热毒咽痛，咽喉肿痛。',
  prescription: '金银花15g，连翘12g。',
  resourceType: '知识图谱',
  dailyMinutes: 30,
  targetDays: 14,
  acceptChallenge: true,
})

const modeCards: ModeCard[] = [
  {
    mode: 'apprentice',
    kicker: '剧情式',
    title: '收徒游戏',
    description: '跟着药圃剧情完成身份、辨药、开方和学习约定。',
    duration: '约 6-8 分钟',
    icon: MagicStick,
  },
  {
    mode: 'dialogue',
    kicker: '问诊式',
    title: '对话问诊(dialogue)',
    description: '像和小岐聊天一样说目标、节奏和困惑，边聊边显影身份卡。',
    duration: '约 2-4 分钟',
    icon: Tickets,
  },
  {
    mode: 'practice',
    kicker: '实战式',
    title: '开药实战',
    description: '直接提交一则病例处方，用客观信号建立证据。',
    duration: '约 3 分钟',
    icon: FirstAidKit,
  },
  {
    mode: 'passive',
    kicker: '不打扰',
    title: '佛系旁观(passive)',
    description: '先进入主页面，后续由点击、停留和跳过行为慢慢补证。',
    duration: '立即进入',
    icon: Compass,
  },
]

const activeStep = computed(() => session.value?.step ?? null)
const questions = computed(() => activeStep.value?.payload.questions ?? [])
const identityCards = computed(() => activeStep.value?.payload.identity_cards ?? ['考前冲刺', '临床学徒', '草木好奇者', '研读派'])
const resourceTypes = computed(() => activeStep.value?.payload.resource_types ?? ['知识图谱', '拟人化短剧', '汤头歌诀', '医案文选'])
const radarAxes = computed(() => buildRadarAxes(profileStore.profile, profileStore.narrative))
const dialoguePrompt = computed(() => session.value?.next_question || activeStep.value?.prompt || '先从一个轻松问题开始：你最近最想解决的学习困惑是什么？')
const dialogueCardScores = computed<CardScores>(() => session.value?.card_scores ?? activeStep.value?.payload.card_scores ?? {})
const dialogueInferredCard = computed(() => session.value?.inferred_card ?? activeStep.value?.payload.inferred_card ?? null)
const dialogueRadarAxes = computed(() => session.value?.radar?.axes ?? [])
const dialogueProfileProgress = computed(() => session.value?.profile_progress ?? null)
const dialogueEvidencePanel = computed(() => session.value?.evidence_panel ?? [])
const dialogueIdentityCards = computed(() => session.value?.identity_cards ?? [])
const dialogueActiveTarget = computed(() => session.value?.target ?? activeStep.value?.payload.target ?? null)
const dialogueActiveTargetLabel = computed(() => session.value?.target_label ?? null)
const dialogueConverged = computed(() => Boolean(session.value?.converged || activeStep.value?.kind === 'confirm'))
const dialogueQuickReplies = computed(() => activeStep.value?.payload.quick_replies ?? [])
const lowConfidenceCount = computed(() => radarAxes.value.filter((axis) => axis.lowConfidence).length)
const showNarrativePlaceholder = computed(() => !profileStore.usingMock && !profileStore.narrativeLoaded)
const showNarrativeRetry = computed(
  () => !profileStore.usingMock && !profileStore.narrativeLoaded && !profileStore.narrativeLoading && Boolean(profileStore.narrativeError),
)
const showTemplateSource = computed(() => !showNarrativePlaceholder.value && profileStore.narrative.method === 'template')
const resultNarrativeBadgeTone = computed(() => {
  if (profileStore.usingMock) return 'jade'
  if (profileStore.narrativeLoading) return 'gold'
  if (!profileStore.narrativeLoaded && profileStore.narrativeError) return 'coral'
  return 'jade'
})
const resultNarrativeBadgeText = computed(() => {
  if (profileStore.usingMock) return '模板演示'
  if (profileStore.narrativeLoading) return '生成中'
  if (!profileStore.narrativeLoaded && profileStore.narrativeError) return '待重试'
  return '已更新'
})
const narrativeText = computed(
  () =>
    (!profileStore.usingMock && !profileStore.narrativeLoaded)
      ? '小岐正在基于本轮证据生成画像叙述，完成后会自动替换为真实内容。'
      : (profileStore.narrative.description || '画像已写入。继续学习后，小岐会把更多证据补进档案卷。'),
)
const progressPercent = computed(() => {
  if (!activeMode.value) return 0
  if (activeMode.value !== 'apprentice') return 55
  const order = [
    'apprentice_prologue',
    'apprentice_act1_quiz',
    'apprentice_act2_prescription',
    'apprentice_act3_resource',
    'apprentice_act4_rule',
  ]
  const index = order.indexOf(activeStep.value?.id ?? '')
  return Math.max(12, Math.round(((index + 1) / order.length) * 100))
})
const canSubmitStep = computed(() => {
  const step = activeStep.value
  if (!step) return false
  if (step.kind === 'diagnostic_quiz' || step.kind === 'identity_quiz') {
    return questions.value.length > 0 && questions.value.every((question) => Boolean(form.questionAnswers[question.id]))
  }
  if (step.kind === 'prescription') return Boolean(form.caseText.trim() && form.prescription.trim())
  return true
})
const resourceCards = computed(() => [
  { title: '中药图谱', desc: '从资源偏好维度继续补证，适合先看结构关系。' },
  { title: '题库测评', desc: lowConfidenceCount.value > 0 ? '把虚线维度变成实线，最快来自小测取证。' : '保持手感，继续校准薄弱标签。' },
  { title: '智能学习', desc: '让小岐按当前档案卷生成解释、练习和复盘。' },
])
const pageLoading = computed(() => profileStore.loading || (busy.value && !(activeMode.value === 'dialogue' && session.value && !resultReady.value)))

const redirectTarget = (): string => {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
  return redirect === '/onboarding' ? '/dashboard' : redirect
}

const resetStepForm = () => {
  form.questionAnswers = {}
  form.resourceType = resourceTypes.value[0] ?? '知识图谱'
  form.identityCard = identityCards.value.includes(form.identityCard) ? form.identityCard : (identityCards.value[0] ?? '临床学徒')
  stepStartedAt.value = Date.now()
}

const startMode = async (mode: ProfileBuildMode) => {
  busy.value = true
  dialoguePending.value = false
  error.value = ''
  try {
    activeMode.value = mode
    profileStore.clearDialogueRadarChanges()
    dialogueStreamEvents.value = []
    session.value = await startProfileSession(mode)
    resultReady.value = false
    resetStepForm()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '画像流程启动失败'
  } finally {
    busy.value = false
  }
}

const stepPayload = (): Record<string, unknown> => {
  const step = activeStep.value
  const responseTime = Date.now() - stepStartedAt.value
  if (!step) return {}

  if (step.kind === 'identity') {
    return { identity_card: form.identityCard, response_time_ms: responseTime }
  }
  if (step.kind === 'identity_quiz') {
    return { ...quizPayload(responseTime), identity_card: form.identityCard, elapsed_seconds: Math.ceil(responseTime / 1000) }
  }
  if (step.kind === 'diagnostic_quiz') {
    return quizPayload(responseTime)
  }
  if (step.kind === 'prescription') {
    return { case: form.caseText, prescription: form.prescription, response_time_ms: responseTime }
  }
  if (step.kind === 'resource_choice') {
    return { resource_type: form.resourceType, response_time_ms: responseTime }
  }
  if (step.kind === 'learning_rule') {
    return {
      daily_minutes: form.dailyMinutes,
      target_days: form.targetDays,
      accept_challenge: form.acceptChallenge,
      response_time_ms: responseTime,
    }
  }
  return { events: [{ event_type: 'click', topic: '画像构建入口', response_time_ms: responseTime }] }
}

const quizPayload = (responseTime: number): Record<string, unknown> => ({
  answers: questions.value.map((question) => ({
    question_id: question.id,
    selected: form.questionAnswers[question.id],
    topic: question.topic,
    clue_type: question.skill,
    response_time_ms: responseTime,
  })),
})

const submitStep = async () => {
  if (!session.value || !activeStep.value) return
  busy.value = true
  error.value = ''
  try {
    const next = await answerProfileSession(session.value.session_id, activeStep.value.id, stepPayload())
    session.value = next
    if (next.finished) {
      await finishCurrentSession()
    } else {
      resetStepForm()
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '本步提交失败'
  } finally {
    busy.value = false
  }
}

const pushDialogueStreamEvent = (type: ProfileDialogueStreamUiEvent['type'], delta = '') => {
  dialogueStreamEvents.value.push({ id: ++dialogueStreamEventId, type, delta })
}

const dialogueUtteranceFromPayload = (payload: AnswerPayload): string =>
  String('utterance' in payload ? payload.utterance : payload.quick_reply || '').trim()

const answerDialogue = async (payload: AnswerPayload) => {
  if (!session.value) {
    error.value = '对话尚未就绪，请稍候重试'
    return
  }
  const utterance = dialogueUtteranceFromPayload(payload)
  if (!utterance) return
  busy.value = true
  dialoguePending.value = true
  error.value = ''
  pushDialogueStreamEvent('reset')
  const streamPromise = streamProfileDialogueChat(session.value.session_id, utterance, ({ event, data }) => {
    if (event === 'thinking') {
      pushDialogueStreamEvent('thinking', String(data.delta ?? ''))
      return
    }
    if (event === 'message') {
      pushDialogueStreamEvent('message', String(data.delta ?? ''))
      return
    }
    if (event === 'message_done') {
      pushDialogueStreamEvent('done')
      dialoguePending.value = false
      busy.value = false
      return
    }
    if (event === 'evidence' && session.value) {
      const cards = Array.isArray(data.cards) ? data.cards : []
      session.value = {
        ...session.value,
        evidence_count: session.value.evidence_count + cards.length,
        evidence_cards: [...(session.value.evidence_cards ?? []), ...(cards as NonNullable<ProfileSessionResponse['evidence_cards']>)],
      }
      return
    }
    if (event === 'identity_cards' && session.value) {
      const items = Array.isArray(data.items) ? data.items : []
      session.value = { ...session.value, identity_cards: items as NonNullable<ProfileSessionResponse['identity_cards']> }
      return
    }
    if (event === 'radar' && session.value) {
      session.value = { ...session.value, radar: data as ProfileSessionResponse['radar'] }
      return
    }
    if (event === 'state' && session.value) {
      session.value = {
        ...session.value,
        ...data,
        converged: Boolean(data.converged),
        finished: Boolean(data.finished ?? session.value.finished),
      } as ProfileSessionResponse
      return
    }
    if (event === 'error') {
      error.value = String(data.message ?? '画像后台更新暂时延迟')
      return
    }
    if (event === 'done') pushDialogueStreamEvent('done')
  })
  dialogueStreamInFlight = streamPromise
  try {
    await streamPromise
    stepStartedAt.value = Date.now()
  } catch (err) {
    pushDialogueStreamEvent('done')
    error.value = err instanceof Error ? err.message : '对话提交失败'
  } finally {
    if (dialogueStreamInFlight === streamPromise) dialogueStreamInFlight = null
    dialoguePending.value = false
    busy.value = false
  }
}

const confirmDialogue = async (_payload: ConfirmPayload) => {
  if (!session.value) {
    error.value = '对话尚未就绪，请稍候重试'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await finishCurrentSession()
    await router.replace('/profile')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '画像确认失败'
  } finally {
    busy.value = false
  }
}

const finishCurrentSession = async () => {
  if (!session.value || !auth.user) return
  if (activeMode.value === 'dialogue' && dialogueStreamInFlight) {
    await dialogueStreamInFlight.catch(() => undefined)
  }
  const finished = await finishProfileSession(session.value.session_id)
  session.value = finished
  saveOnboardingState(auth.user.user_id, { status: 'completed', mode: activeMode.value ?? finished.mode })
  await profileStore.loadProfile()
  void profileStore.ensureNarrativeLoaded()
  if (finished.mode === 'dialogue') {
    profileStore.setDialogueRadarChanges(finished.radar_changes ?? [], finished.summary_text ?? '')
  } else {
    profileStore.clearDialogueRadarChanges()
  }
  resultReady.value = finished.mode !== 'dialogue'
  ElMessage.success('画像已更新')
}

const skipOnboarding = async () => {
  if (!auth.user) return
  busy.value = true
  error.value = ''
  try {
    const passive = await startProfileSession('passive')
    await answerProfileSession(passive.session_id, passive.step?.id ?? 'passive', {
      events: [{ event_type: 'skip', topic: '画像引导', response_time_ms: 300 }],
    })
    await finishProfileSession(passive.session_id)
    saveOnboardingState(auth.user.user_id, { status: 'skipped', mode: 'passive' })
    ElMessage.success('已跳过，之后可从侧栏回来重塑画像')
    await router.replace(redirectTarget())
  } catch (err) {
    error.value = err instanceof Error ? err.message : '跳过失败，请稍后再试'
  } finally {
    busy.value = false
  }
}

const resetFlow = () => {
  session.value = null
  activeMode.value = null
  resultReady.value = false
  dialoguePending.value = false
  dialogueStreamEvents.value = []
  error.value = ''
  resetStepForm()
}

const retryNarrative = () => {
  void profileStore.loadNarrative(true)
}

const enterApp = async () => {
  await router.replace(redirectTarget())
}
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.onboarding-view {
  display: grid;
  gap: 18px;
  width: min(1320px, 100%);
  margin: 0 auto;
}

.onboarding-hero,
.session-card,
.radar-result,
.narrative-result {
  min-width: 0;
}

.onboarding-hero {
  @include glass-panel(26px);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 26px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;
}

h2,
h3 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-weight: 900;
  line-height: 1.1;
}

h2 {
  @include text-sheen;
  font-size: clamp(34px, 5vw, 58px);
}

h3 {
  color: var(--qh-text);
  font-size: 23px;
}

.onboarding-hero p:last-child,
.mode-card p,
.narrative-result p {
  margin: 12px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.8;
}

.hero-actions,
.session-actions,
.result-actions,
.mode-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.mode-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.mode-card {
  display: grid;
  min-height: 270px;
  align-content: space-between;
  gap: 18px;
}

.mode-icon {
  display: grid;
  width: 46px;
  height: 46px;
  place-items: center;
  border: 1px solid rgba(143, 208, 173, 0.18);
  border-radius: 16px;
  color: var(--qh-color-gold);
  background: rgba(238, 245, 239, 0.06);
  font-size: 22px;
}

.onboarding-alert {
  border-radius: 16px;
}

.session-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 18px;
}

.session-progress {
  margin-bottom: 20px;
}

.step-block {
  display: grid;
  gap: 16px;
}

.field-label {
  display: block;
  margin: 0 0 8px;
  color: var(--qh-color-gold);
  font-size: 13px;
  font-weight: 900;
}

.choice-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

:deep(.el-radio-button__inner) {
  border: 1px solid rgba(143, 208, 173, 0.18);
  border-radius: 14px !important;
  background: rgba(238, 245, 239, 0.06);
  color: rgba(238, 245, 239, 0.78);
  font-weight: 900;
}

:deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  border-color: rgba(143, 208, 173, 0.68);
  background: rgba(90, 168, 134, 0.34);
  box-shadow: none;
}

:deep(.question-set) {
  display: grid;
  gap: 12px;
}

:deep(.question-card) {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 16px;
  background: rgba(238, 245, 239, 0.055);
}

:deep(.question-card strong) {
  color: var(--qh-text);
  line-height: 1.6;
}

:deep(.answer-grid) {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

:deep(.answer-option) {
  min-height: 42px;
  padding: 8px 10px;
  border: 1px solid rgba(159, 182, 214, 0.18);
  border-radius: 12px;
  color: rgba(238, 245, 239, 0.82);
  background: rgba(159, 182, 214, 0.08);
  cursor: pointer;
  font-weight: 800;
}

:deep(.answer-option.is-selected) {
  border-color: rgba(231, 194, 116, 0.64);
  color: var(--qh-text);
  background: rgba(231, 194, 116, 0.16);
}

.two-fields {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.rule-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: end;
}

.switch-line {
  display: grid;
  gap: 8px;
  justify-items: start;
}

.session-actions {
  margin-top: 22px;
}

.result-grid {
  display: grid;
  grid-template-columns: minmax(360px, 0.9fr) minmax(420px, 1.1fr);
  gap: 18px;
}

.resource-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 20px;
}

.narrative-loading-grid {
  margin-top: 16px;
}

.resource-grid article {
  display: grid;
  gap: 7px;
  min-height: 112px;
  padding: 14px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 16px;
  background: rgba(238, 245, 239, 0.055);
}

.resource-grid strong {
  color: var(--qh-color-jade);
}

.resource-grid span {
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.7;
}

.narrative-source {
  color: var(--qh-color-gold);
  font-size: 12px;
  font-weight: 900;
}

.result-actions {
  margin-top: 20px;
}

@media (max-width: 1180px) {
  .mode-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .result-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .onboarding-hero,
  .mode-foot,
  .session-head {
    display: grid;
  }

  .mode-grid,
  .two-fields,
  .rule-grid,
  :deep(.answer-grid),
  .resource-grid {
    grid-template-columns: 1fr;
  }
}
</style>
