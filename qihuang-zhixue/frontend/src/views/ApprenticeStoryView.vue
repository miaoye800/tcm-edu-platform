<template>
  <main class="apprentice-story" :class="{ 'is-form-open': isInteractive }">
    <StoryStage :scene="scene" :video-fixture="videoFixture" />
    <header class="story-controls">
      <p>{{ scene.chapter }} <span aria-hidden="true">/</span> {{ progressLabel }}</p>
      <div>
        <el-tooltip :content="muted ? '开启音效' : '静音'" placement="bottom">
          <button class="qh-icon-button" type="button" :aria-label="muted ? '开启音效' : '静音'" :aria-pressed="muted" @click="muted = !muted">
            <el-icon><Microphone v-if="muted" /><MuteNotification v-else /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="退出收徒剧情" placement="bottom">
          <button class="qh-icon-button" type="button" aria-label="退出收徒剧情" @click="leaveStory"><el-icon><Close /></el-icon></button>
        </el-tooltip>
      </div>
    </header>

    <aside class="story-dialogue" aria-live="polite">
      <p v-if="scene.speaker" class="speaker">{{ scene.speaker }}</p>
      <p>{{ scene.dialogue }}</p>
    </aside>

    <section v-if="error" class="story-panel is-state">
      <StateError title="这一幕暂时无法继续" :description="error" action-text="重新连接" size="sm" @action="restartFromSafePoint" />
    </section>
    <section v-else-if="loading" class="story-panel is-state"><StateLoading title="药灯正在亮起" description="正在接续本次收徒会话。" size="sm" /></section>
    <section v-else-if="isInteractive" class="story-panel" aria-label="当前问答">
      <form @submit.prevent="submitCurrentStep">
        <fieldset :disabled="submitting">
          <legend>{{ step?.title }}</legend>
          <p class="prompt">{{ step?.prompt }}</p>

          <div v-if="scene.kind === 'identity'" class="choice-list">
            <button v-for="card in identityCards" :key="card" class="qh-option-button" type="button" :class="{ selected: identityCard === card }" :aria-pressed="identityCard === card" @click="identityCard = card">{{ card }}</button>
            <label class="free-answer"><span>也可补充一句来意（仅随本次会话保存）</span><textarea v-model="utterance" rows="2" placeholder="例如：想把本草学得更明白。" /></label>
          </div>

          <div v-else-if="scene.kind === 'quiz'" class="question-list">
            <article v-for="(question, index) in questions" :key="question.id">
              <p>{{ index + 1 }}. {{ question.question }}</p>
              <div class="choice-list"><button v-for="option in question.options" :key="option" class="qh-option-button" type="button" :class="{ selected: questionAnswers[question.id] === option }" :aria-pressed="questionAnswers[question.id] === option" @click="questionAnswers[question.id] = option">{{ option }}</button></div>
            </article>
          </div>

          <div v-else-if="scene.kind === 'prescription'" class="input-grid">
            <label><span>病例</span><textarea v-model="caseText" rows="3" placeholder="如：热毒咽痛，咽喉肿痛。" /></label>
            <label><span>自由抓药方</span><textarea v-model="prescription" rows="3" placeholder="如：金银花15g，连翘12g。" /></label>
          </div>

          <div v-else-if="scene.kind === 'resource'" class="choice-list"><button v-for="resource in resourceTypes" :key="resource" class="qh-option-button" type="button" :class="{ selected: resourceType === resource }" :aria-pressed="resourceType === resource" @click="resourceType = resource">{{ resource }}</button></div>

          <div v-else-if="scene.kind === 'rule'" class="rule-grid">
            <label><span>每日时长（分钟）</span><input v-model.number="dailyMinutes" type="number" min="5" max="180" /></label>
            <label><span>目标周期（天）</span><input v-model.number="targetDays" type="number" min="3" max="90" /></label>
            <label class="check"><input v-model="acceptChallenge" type="checkbox" /> 接受每周一次挑战</label>
          </div>
          <footer><span v-if="!canSubmit" class="hint">{{ submitHint }}</span><button class="primary qh-button qh-button--primary" type="submit" :aria-busy="submitting" :disabled="!canSubmit">{{ submitting ? '记录中…' : scene.kind === 'rule' ? '立下此约' : '如实作答' }}</button></footer>
        </fieldset>
      </form>
    </section>
    <section v-else-if="scene.kind === 'complete'" class="story-panel completion" aria-live="polite">
      <p>本轮收集到 {{ state.session?.evidence_count ?? 0 }} 条证据。画像与雷达将由现有后端证据链更新。</p>
      <button class="primary qh-button qh-button--primary" type="button" @click="openProfile">查看弟子档案</button>
    </section>
    <section v-else class="story-panel action-panel"><button class="primary qh-button qh-button--primary" type="button" @click="advanceScene">{{ scene.canSkip ? '继续' : '进入问答' }}</button><button v-if="scene.canSkip" class="quiet qh-button qh-button--secondary" type="button" @click="skipPerformance">跳过演出</button></section>

    <section v-if="prescriptionEvidence" class="evaluation-panel" aria-live="polite">
      <div><strong>处方记录</strong><span>{{ provenanceText }}</span></div>
      <p>{{ prescriptionEvidence.fact }}</p>
      <ul v-if="findings.length"><li v-for="(finding, index) in findings" :key="index">{{ finding.reason || finding.kind || finding.quote }}</li></ul>
      <p v-else class="muted-copy">后端未返回逐项 finding；已保留本次证据记录。</p>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Close, Microphone, MuteNotification } from '@element-plus/icons-vue'
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
import type { EvidenceCard, ProfileSessionQuestion } from '../types/profile'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const profileStore = useProfileStore()
const state = ref<ApprenticeStoryState>(initialApprenticeStoryState())
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const muted = ref(true)
const identityCard = ref('临床学徒')
const utterance = ref('')
const questionAnswers = reactive<Record<string, string>>({})
const caseText = ref('热毒咽痛，咽喉肿痛。')
const prescription = ref('金银花15g，连翘12g。')
const resourceType = ref('知识图谱')
const dailyMinutes = ref(30)
const targetDays = ref(14)
const acceptChallenge = ref(true)
const submittedEvidence = ref<EvidenceCard | null>(null)

const storageKey = computed(() => `${apprenticeVisual.storagePrefix}${auth.user?.user_id ?? 'anonymous'}`)
const scene = computed(() => sceneForState(state.value))
const videoFixture = computed<StoryVideoFixtureMode>(() => {
  const fixture = route.query.storyVideoFixture
  return import.meta.env.DEV && (fixture === 'ready' || fixture === 'failed') ? fixture : null
})
const step = computed(() => state.value.session?.step ?? null)
const isInteractive = computed(() => ['identity', 'quiz', 'prescription', 'resource', 'rule'].includes(scene.value.kind))
const identityCards = computed(() => step.value?.payload.identity_cards ?? [])
const questions = computed<ProfileSessionQuestion[]>(() => step.value?.payload.questions ?? [])
const resourceTypes = computed(() => step.value?.payload.resource_types ?? [])
const progressLabel = computed(() => state.value.session ? `${state.value.session.evidence_count} 条证据` : `${state.value.performanceIndex + 1} / 3`)
const findings = computed(() => {
  const items = submittedEvidence.value?.objective_signal.prescription_findings
  return Array.isArray(items) ? items.filter((item): item is Record<string, string> => Boolean(item && typeof item === 'object')) : []
})
const prescriptionEvidence = computed(() => submittedEvidence.value?.source.includes('抓药') ? submittedEvidence.value : null)
const provenanceText = computed(() => {
  const method = String(prescriptionEvidence.value?.objective_signal.extraction_method || '')
  return method === 'no_key_rule_fallback' ? '规则评分（未配置模型密钥）' : method ? `后端评估：${method}` : '后端证据记录'
})
const canSubmit = computed(() => {
  if (!canSubmitStep(state.value) || submitting.value) return false
  if (scene.value.kind === 'quiz') return questions.value.length > 0 && questions.value.every((question) => Boolean(questionAnswers[question.id]))
  if (scene.value.kind === 'prescription') return Boolean(caseText.value.trim() && prescription.value.trim())
  return true
})
const submitHint = computed(() => scene.value.kind === 'quiz' ? '请完成每一道辨药问答。' : '本节点已经提交，正在等待下一幕。')

const persist = () => window.localStorage.setItem(storageKey.value, JSON.stringify(state.value))
const resetStepFields = () => {
  Object.keys(questionAnswers).forEach((key) => delete questionAnswers[key])
  identityCard.value = identityCards.value[0] ?? '临床学徒'
  resourceType.value = resourceTypes.value[0] ?? '知识图谱'
  utterance.value = ''
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
  if (scene.value.kind === 'identity') return { identity_card: identityCard.value, utterance: utterance.value.trim(), response_time_ms: responseTime }
  if (scene.value.kind === 'quiz') return { answers: questions.value.map((question) => ({ question_id: question.id, selected: questionAnswers[question.id], topic: question.topic, clue_type: question.skill, response_time_ms: responseTime })) }
  if (scene.value.kind === 'prescription') return { case: caseText.value, prescription: prescription.value, response_time_ms: responseTime }
  if (scene.value.kind === 'resource') return { resource_type: resourceType.value, response_time_ms: responseTime }
  return { daily_minutes: dailyMinutes.value, target_days: targetDays.value, accept_challenge: acceptChallenge.value, response_time_ms: responseTime }
}
const submitCurrentStep = async () => {
  const current = state.value.session?.step
  if (!current || !canSubmit.value) return
  state.value = beginStepSubmission(state.value)
  submitting.value = true
  error.value = ''
  try {
    const next = await answerProfileSession(state.value.session!.session_id, current.id, stepPayload())
    submittedEvidence.value = next.evidence_cards?.find((item) => item.source.includes('抓药')) ?? submittedEvidence.value
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
    error.value = cause instanceof Error ? cause.message : '本次作答未能提交'
    persist()
  } finally { submitting.value = false }
}
const restartFromSafePoint = async () => { error.value = ''; const recovered = restoreApprenticeStoryState(window.localStorage.getItem(storageKey.value)); if (recovered) { state.value = recovered; resetStepFields(); return } await startSession() }
const redirectTarget = () => typeof route.query.redirect === 'string' && route.query.redirect !== '/apprentice' ? route.query.redirect : '/onboarding'
const leaveStory = () => router.push(redirectTarget())
const openProfile = () => router.push('/profile')

watch(state, persist, { deep: true })
onMounted(async () => { const recovered = restoreApprenticeStoryState(window.localStorage.getItem(storageKey.value)); if (recovered) { state.value = recovered; resetStepFields() } })
</script>

<style scoped lang="scss">
.apprentice-story { position: relative; display: grid; min-height: 100dvh; overflow: hidden; color: var(--qh-text); background: var(--qh-color-ink); }
.story-stage { grid-area: 1 / 1; min-height: 100dvh; }
.story-controls { position: absolute; inset: 0 0 auto; z-index: 3; display: flex; align-items: center; justify-content: space-between; gap: var(--qh-space-3); padding: var(--qh-space-4) clamp(var(--qh-space-4), 4vw, var(--qh-space-8)); }
.story-controls p { margin: 0; color: var(--qh-text-muted); font-size: 12px; }
.story-controls p span { margin: 0 var(--qh-space-1); color: var(--qh-color-gold); }
.story-controls div { display: flex; gap: var(--qh-space-2); }
.story-dialogue { position: absolute; z-index: 2; right: clamp(var(--qh-space-4), 8vw, 10vw); bottom: 18%; left: clamp(var(--qh-space-4), 8vw, 10vw); pointer-events: none; }
.story-dialogue p { max-width: 780px; margin: 0; color: var(--qh-text); font-family: var(--qh-font-reading); font-size: clamp(17px, 2vw, 24px); line-height: 1.7; text-shadow: 0 2px 8px var(--qh-color-ink); }
.story-dialogue .speaker { margin-bottom: var(--qh-space-1); color: var(--qh-color-gold); font-family: var(--qh-font-body); font-size: 12px; font-weight: 800; letter-spacing: 1px; }
.story-panel { position: absolute; z-index: 4; right: clamp(var(--qh-space-4), 8vw, 10vw); bottom: var(--qh-space-5); left: clamp(var(--qh-space-4), 8vw, 10vw); max-width: 860px; padding: var(--qh-space-4); border: 1px solid var(--qh-color-cream-line); border-radius: var(--qh-radius-stage); background: var(--qh-color-night-overlay); box-shadow: var(--qh-shadow-deep); backdrop-filter: blur(16px); }
.action-panel { display: flex; align-items: center; gap: var(--qh-space-2); }
.story-panel fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.story-panel legend { color: var(--qh-color-gold); font-family: var(--qh-font-reading); font-size: 20px; }
.prompt { margin: var(--qh-space-2) 0 var(--qh-space-3); color: var(--qh-text-muted); font-size: 14px; line-height: 1.7; }
.choice-list { display: flex; flex-wrap: wrap; gap: var(--qh-space-2); }
.choice-list button.selected { border-color: var(--qh-button-primary-border); color: var(--qh-text); background: var(--qh-button-option-active); }
.free-answer { flex-basis: 100%; margin-top: var(--qh-space-2); }
.free-answer span, .input-grid span, .rule-grid span { display: block; margin-bottom: var(--qh-space-1); color: var(--qh-text-muted); font-size: 12px; }
.story-panel textarea, .story-panel input[type=number] { box-sizing: border-box; width: 100%; border: 1px solid var(--qh-border); border-radius: var(--qh-radius-stage); color: var(--qh-text); background: var(--qh-color-night-soft); font: inherit; }
.story-panel textarea { resize: vertical; padding: var(--qh-space-2); }
.story-panel input[type=number] { min-height: var(--qh-control-height); padding: 0 var(--qh-space-2); }
.question-list { display: grid; max-height: 34dvh; gap: var(--qh-space-3); overflow: auto; }
.question-list article { padding-top: var(--qh-space-2); border-top: 1px solid var(--qh-border); }
.question-list p { margin: 0 0 var(--qh-space-2); color: var(--qh-text); line-height: 1.6; }
.input-grid, .rule-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--qh-space-3); }
.rule-grid .check { display: flex; align-items: center; gap: var(--qh-space-2); color: var(--qh-text-muted); font-size: 14px; }
footer { display: flex; align-items: center; justify-content: space-between; gap: var(--qh-space-3); margin-top: var(--qh-space-3); }
.hint { color: var(--qh-text-muted); font-size: 12px; }
.evaluation-panel { position: absolute; z-index: 5; top: 112px; right: clamp(var(--qh-space-4), 4vw, var(--qh-space-8)); width: min(340px, calc(100vw - 2 * var(--qh-space-4))); padding: var(--qh-space-3); border: 1px solid var(--qh-color-jade-line); border-radius: var(--qh-radius-stage); background: var(--qh-color-night-overlay); box-shadow: var(--qh-shadow-soft); }
.evaluation-panel div { display: flex; justify-content: space-between; gap: var(--qh-space-2); }
.evaluation-panel strong { color: var(--qh-color-cream); }
.evaluation-panel span, .evaluation-panel p, .evaluation-panel li { color: var(--qh-text-muted); font-size: 12px; line-height: 1.6; }
.evaluation-panel span { color: var(--qh-color-gold); text-align: right; }
.evaluation-panel p { margin: var(--qh-space-2) 0; }
.evaluation-panel ul { max-height: 150px; margin: 0; padding-left: var(--qh-space-4); overflow: auto; }
.completion p { margin: 0 0 var(--qh-space-3); color: var(--qh-text-muted); line-height: 1.7; }
.is-state { right: auto; bottom: 50%; left: 50%; width: min(460px, calc(100% - 2 * var(--qh-space-4))); transform: translate(-50%, 50%); }

@media (max-width: 700px) {
  .story-dialogue { bottom: 30%; }
  .story-dialogue p { font-size: 18px; }
  .story-panel { bottom: var(--qh-space-3); padding: var(--qh-space-3); }
  .input-grid, .rule-grid { grid-template-columns: 1fr; }
  .question-list { max-height: 31dvh; }
  .evaluation-panel { top: 100px; }
  .evaluation-panel ul { max-height: 86px; }
  .is-form-open .story-dialogue { display: none; }
  footer { align-items: flex-start; flex-direction: column; }
  .primary { width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .apprentice-story, .story-panel { scroll-behavior: auto; }
  .story-controls .qh-icon-button { transition: none; }
}
</style>
