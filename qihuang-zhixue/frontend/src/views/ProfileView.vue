<template>
  <section v-loading="profileStore.loading" class="profile-view">
    <div class="profile-hero">
      <div class="hero-copy">
        <p class="eyebrow">中医个性化学习画像</p>
        <h2>七维修行罗盘</h2>
        <p>当前账号：{{ auth.displayName }} · {{ updatedAtText }}</p>
      </div>

      <div class="hero-bubble">
        <XiaoqiMascot expression="default" :size="48" alt="小岐" />
        <img :src="xiaoqi" alt="小岐" />
        <div>
          <strong>小岐提示</strong>
          <span>先取证，再会诊；画像越亮，路径越准。</span>
        </div>
      </div>

      <GlassCard v-if="!profileStore.usingMock" class="trust-card" padding="md">
        <el-tooltip content="师父会根据你的自述与实测是否相符调整信任" placement="bottom">
          <div class="trust-card__label">师父信任度</div>
        </el-tooltip>
        <strong>{{ trust?.tier ?? '读取中' }}</strong>
        <span>{{ trust ? `${Math.round(trust.trust * 100)}%` : '' }}</span>
      </GlassCard>
    </div>

    <el-alert
      v-if="profileStore.error"
      class="profile-alert"
      type="warning"
      :title="profileStore.error"
      show-icon
      :closable="false"
    >
      <template #default>
        <el-button class="hint-action" size="small" type="primary" :loading="profileStore.loading" @click="refreshProfileView">
          重新读取档案
        </el-button>
      </template>
    </el-alert>

    <el-alert
      v-if="showLowConfidenceHint"
      class="profile-alert"
      type="info"
      title="当前多为待取证维度，去构建更准"
      show-icon
      :closable="false"
    >
      <template #default>
        <el-button class="hint-action" size="small" type="primary" @click="goOnboarding">焕醒画像</el-button>
      </template>
    </el-alert>

    <el-alert
      v-if="noKeyFallbackDimensions.length"
      class="profile-alert"
      type="warning"
      title="当前未配置模型，以下维度暂不评分"
      :description="`受影响维度：${noKeyFallbackDimensions.join('、')}`"
      show-icon
      :closable="false"
    />

    <section class="stats-grid" aria-label="画像概览">
      <StatCard v-for="stat in stats" :key="stat.label" v-bind="stat" />
    </section>

    <el-alert
      v-if="dialogueSummaryVisible"
      class="profile-alert radar-change-banner"
      type="success"
      :title="profileStore.latestDialogueSummary"
      show-icon
      @close="dismissRadarHighlight"
    />

    <section class="profile-grid" @click="dismissRadarHighlight">
      <GlassCard class="radar-panel" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">七维画像</p>
            <h3>修行罗盘</h3>
          </div>
          <Badge tone="gold" :label="`${pendingAxes.length} 维待取证`" />
        </div>

        <div class="radar-layout">
          <RadarChart :axes="radarAxes" :highlighted-keys="highlightedRadarKeys" />
          <div class="dim-list">
            <div
              v-for="axis in evidencedAxes"
              :key="axis.key"
              :class="['dim-change-wrap', { 'is-highlighted': highlightedRadarKeys.includes(axis.key) }]"
            >
              <DimRow :axis="axis" />
              <span v-if="profileStore.radarHighlightActive && radarChangeByKey.get(axis.key)" class="radar-change-note">
                {{ radarChangeText(radarChangeByKey.get(axis.key)) }}
              </span>
            </div>
            <!-- 没取过证的维度不逐条编结论，只留一行提示；雷达上仍以虚线标注。 -->
            <p v-if="pendingAxes.length" class="dim-pending-hint">
              其余 {{ pendingAxes.length }} 维尚未取证：{{ pendingAxes.map((axis) => axis.sourceLabel).join('、') }}
            </p>
          </div>
        </div>
      </GlassCard>

      <GlassCard class="narrative-panel" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">叙述画像</p>
            <h3>档案卷</h3>
          </div>
          <Badge :tone="narrativeBadgeTone" :label="narrativeMethodText" />
        </div>
        <el-alert
          v-if="showNarrativeRetry"
          class="narrative-alert"
          type="warning"
          :title="profileStore.narrativeError"
          show-icon
          :closable="false"
        >
          <template #default>
            <el-button class="hint-action" size="small" type="primary" @click="retryNarrative">重新生成</el-button>
          </template>
        </el-alert>

        <template v-if="showNarrativePlaceholder">
          <p class="narrative-text narrative-pending">{{ narrativePendingText }}</p>

          <div class="narrative-columns">
            <section class="narrative-placeholder-block">
              <span>{{ narrativeLoadingText }}</span>
              <i class="narrative-skeleton-line narrative-skeleton-line-lg"></i>
              <i class="narrative-skeleton-line"></i>
              <i class="narrative-skeleton-line narrative-skeleton-line-sm"></i>
            </section>
            <section class="narrative-placeholder-block">
              <span>{{ narrativeLoadingText }}</span>
              <i class="narrative-skeleton-line"></i>
              <i class="narrative-skeleton-line narrative-skeleton-line-sm"></i>
              <i class="narrative-skeleton-line narrative-skeleton-line-lg"></i>
            </section>
          </div>
        </template>

        <template v-else>
          <p class="narrative-text">{{ narrativeText }}</p>
          <p v-if="showTemplateSource" class="narrative-source">{{ narrativeTemplateSourceText }}</p>

          <div class="narrative-columns">
            <section>
              <span>{{ narrativeStrengthLabel }}</span>
              <p v-if="!advantageItems.length">{{ narrativeStrengthEmptyText }}</p>
              <p v-for="item in advantageItems" v-else :key="`adv-${item.dimension_key}`">{{ item.summary }}</p>
            </section>
            <section>
              <span>{{ narrativeWeaknessLabel }}</span>
              <p v-if="!weaknessItems.length">{{ narrativeWeaknessEmptyText }}</p>
              <p v-for="item in weaknessItems" v-else :key="`weak-${item.dimension_key}`">{{ item.summary }}</p>
            </section>
          </div>
        </template>
      </GlassCard>

      <GlassCard padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">热区</p>
            <h3>薄弱点列表</h3>
          </div>
          <Badge tone="coral" :label="weakItems.length || '0'" />
        </div>

        <div v-if="weakItems.length" class="weak-list">
          <div v-for="item in weakItems" :key="item.label">
            <span>{{ item.label }}</span>
            <strong>{{ item.strength }} 次</strong>
          </div>
        </div>
        <p v-else class="empty-line">当前暂无高频薄弱标签。</p>
      </GlassCard>
    </section>

    <GlassCard v-if="!profileStore.usingMock" class="goal-card" padding="lg">
      <div class="panel-head"><div><p class="eyebrow">学习目标</p><h3>请小岐复述</h3></div></div>
      <template v-if="goalCandidate">
        <div class="goal-restatement"><img :src="xiaoqi" alt="小岐" /><p>{{ goalCandidate.restatement }}</p></div>
        <div class="goal-actions"><el-button type="primary" :loading="goalConfirming" @click="confirmGoal">确认</el-button><el-button @click="resetGoal">重新说</el-button></div>
      </template>
      <template v-else>
        <el-input v-model="goalText" maxlength="2000" placeholder="例如：两个月内准备好方剂学期末考试" @keyup.enter="proposeGoal" />
        <el-button type="primary" :loading="goalProposing" @click="proposeGoal">让小岐复述</el-button>
      </template>
    </GlassCard>

    <GlassCard v-if="!profileStore.usingMock" class="change-log" padding="lg">
      <div class="panel-head"><div><p class="eyebrow">档案留痕</p><h3>画像变迁录</h3></div></div>
      <div v-if="profileStore.profile.change_log?.length" class="change-log__list">
        <article v-for="item in profileStore.profile.change_log" :key="`${item.ts}-${item.dim}`">
          <span class="change-icon">{{ calibrationChange(item.source_event) ? '◈' : '·' }}</span>
          <div><strong>{{ dimensionName(item.dim) }}</strong><small>{{ formatDateTime(item.ts) }}</small><p>{{ changeValue(item.old_value) }} → {{ changeValue(item.new_value) }}</p><em>{{ item.reason }}</em></div>
        </article>
      </div>
      <p v-else class="empty-line">暂未记录画像变迁。</p>
      <div class="change-log__pager"><el-button :disabled="changeOffset === 0" @click="previousChanges">上一页</el-button><span>第 {{ Math.floor(changeOffset / changeLimit) + 1 }} 页</span><el-button :disabled="(profileStore.profile.change_log?.length ?? 0) < changeLimit" @click="nextChanges">下一页</el-button></div>
    </GlassCard>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import xiaoqi from '../assets/mascot/xiaoqi-avatar.png'
import XiaoqiMascot from '../components/ui/XiaoqiMascot.vue'
import DimRow from '../components/profile/DimRow.vue'
import JuryCard from '../components/profile/JuryCard.vue'
import RadarChart from '../components/profile/RadarChart.vue'
import StatCard from '../components/profile/StatCard.vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import { confirmProfileGoal, proposeProfileGoal, readProfileTrust, readProfile } from '../services/profileApi'
import type { GoalCandidate, ProfileTrust, RadarChange, TeacherName } from '../types/profile'
import { dimensionKeys } from '../types/profile'
import {
  buildRadarAxes,
  confidenceFor,
  dimensionLabels,
  formatDateTime,
  isNoKeyProvenance,
  lowConfidenceThreshold,
  profileEvidenceCards,
  weakPointItems,
} from '../utils/profileMath'

const profileStore = useProfileStore()
const auth = useAuthStore()
const router = useRouter()
const trust = ref<ProfileTrust | null>(null)
const goalText = ref('')
const goalCandidate = ref<GoalCandidate | null>(null)
const goalProposing = ref(false)
const goalConfirming = ref(false)
const changeLimit = 20
const changeOffset = ref(0)

const radarAxes = computed(() => buildRadarAxes(profileStore.profile, profileStore.narrative))
// 档案卷逐条展开"有材料"的维度：既有已经出分的，也有"收了几项、分还没算
// 出来"的。后者不是空维度——它该显示"已收集 2/5、评分暂缓"，而不是被并进
// 一排"尚未取证"里，让用户以为这一维从来没被问到。
const evidencedAxes = computed(() => radarAxes.value.filter((axis) => axis.lit || axis.confidence > 0))
const pendingAxes = computed(() => radarAxes.value.filter((axis) => !axis.lit && axis.confidence <= 0))
const evidenceCards = computed(() => profileEvidenceCards(profileStore.profile))
const weakItems = computed(() => weakPointItems(profileStore.profile, profileStore.narrative))
const updatedAtText = computed(() => formatDateTime(profileStore.profile.updated_at))
// "高置信"必须同时是"有结论"。只看置信度会把动机这种收了 2/5 组件、
// 分还没算出来的维度算成 2/7 高置信，而雷达上它根本没有尖角。
const readyDimensionCount = computed(
  () => radarAxes.value.filter((axis) => axis.lit && !axis.lowConfidence).length,
)
const noKeyFallbackDimensions = computed(() =>
  dimensionKeys.filter((key) => isNoKeyProvenance(profileStore.profile.provenance?.[key])),
)
const lowConfidenceNames = computed(() =>
  dimensionKeys
    .filter((key) => confidenceFor(profileStore.profile, profileStore.narrative, key) < lowConfidenceThreshold)
    .map((key) => dimensionLabels[key]),
)
const showLowConfidenceHint = computed(
  () => !profileStore.loading && !profileStore.usingMock && (profileStore.isOverallLowConfidence || lowConfidenceNames.value.length >= 4),
)
const dialogueSummaryVisible = computed(() => profileStore.radarHighlightActive && Boolean(profileStore.latestDialogueSummary))
const highlightedRadarKeys = computed(() =>
  profileStore.radarHighlightActive ? profileStore.latestRadarChanges.map((change) => change.key) : [],
)
const radarChangeByKey = computed(
  () => new Map(profileStore.latestRadarChanges.map((change) => [change.key, change] as const)),
)

const retryNarrativeLabel = '\u91cd\u65b0\u751f\u6210'
const narrativePendingText = '\u5c0f\u5c90\u6b63\u5728\u6574\u7406\u8bc1\u636e\u5e76\u751f\u6210\u753b\u50cf\u53d9\u8ff0\uff0c\u4f60\u73b0\u5728\u53ef\u4ee5\u7ee7\u7eed\u6d4f\u89c8\u5176\u4ed6\u5185\u5bb9\u3002'
const narrativeLoadingText = '\u751f\u6210\u4e2d'
const narrativeTemplateSourceText = computed(() =>
  noKeyFallbackDimensions.value.length
    ? '当前展示的是规则模板叙述（未配置模型，暂不评分）。'
    : '当前展示的是规则模板叙述。',
)
const narrativeStrengthLabel = '\u6240\u957f'
const narrativeStrengthEmptyText = '\u5f85\u8865\u5145\u3002'
const narrativeWeaknessLabel = '\u6240\u77ed'
const narrativeWeaknessEmptyText = '\u6682\u65e0\u9ad8\u7f6e\u4fe1\u8584\u5f31\u70b9\u3002'

let radarFadeTimer: number | undefined

const stats = computed(() => [
  { label: '高置信维度', value: `${readyDimensionCount.value}/7`, tone: 'jade' as const },
  { label: '证据卡', value: String(evidenceCards.value.length), tone: 'blue' as const },
  { label: '低置信维', value: String(lowConfidenceNames.value.length), tone: 'coral' as const },
  { label: '叙述方式', value: narrativeMethodText.value, tone: 'gold' as const },
])

const showNarrativePlaceholder = computed(() => !profileStore.usingMock && !profileStore.narrativeLoaded)
const showNarrativeRetry = computed(
  () => !profileStore.usingMock && !profileStore.narrativeLoaded && !profileStore.narrativeLoading && Boolean(profileStore.narrativeError),
)
const showTemplateSource = computed(() => !showNarrativePlaceholder.value && profileStore.narrative.method === 'template')
const narrativeBadgeTone = computed(() => {
  if (profileStore.usingMock) return 'jade'
  if (profileStore.narrativeLoading) return 'gold'
  if (!profileStore.narrativeLoaded && profileStore.narrativeError) return 'coral'
  return profileStore.narrative.method === 'llm' ? 'blue' : 'jade'
})
const narrativeMethodText = computed(() => {
  if (profileStore.usingMock) return '模板演示'
  if (profileStore.narrativeLoading) return '生成中'
  if (!profileStore.narrativeLoaded && profileStore.narrativeError) return '待重试'
  return profileStore.narrative.method === 'llm' ? 'LLM 编排' : '模板渲染'
})
const narrativeText = computed(() => {
  if (!profileStore.usingMock && !profileStore.narrativeLoaded) {
    return '小岐正在基于当前证据整理叙述，生成完成后会自动替换为真实画像。'
  }
  if (profileStore.narrative.description) return profileStore.narrative.description
  return '当前暂无高置信维度。系统会先收集诊断小测、问答互动和资源偏好证据，再生成稳定叙述画像。'
})

const advantageItems = computed(() => ((profileStore.usingMock || profileStore.narrativeLoaded) ? (profileStore.narrative.advantages ?? []) : []))
const weaknessItems = computed(() => ((profileStore.usingMock || profileStore.narrativeLoaded) ? (profileStore.narrative.weak_points ?? []) : []))

const teacherRoles: Record<TeacherName, string> = {
  严师: '校准分数边界',
  宽师: '发现可迁移优势',
  中立: '平衡证据权重',
}

const teacherComments = computed(() =>
  (['严师', '宽师', '中立'] as TeacherName[]).map((teacher) => {
    const entries = dimensionKeys.flatMap((key) => {
      const item = profileStore.profile[key].jury?.[teacher]
      if (!item) return []
      const comment = item.comment || item.reasoning_chain?.join('；') || ''
      if (!comment) return []
      return [
        {
          dimension: dimensionLabels[key],
          score: item.score ?? null,
          comment,
        },
      ]
    })

    return {
      name: teacher,
      role: teacherRoles[teacher],
      entries,
      pending: entries.length === 0,
    }
  }),
)

const goOnboarding = async () => {
  await router.push('/onboarding?redirect=/profile')
}

const radarChangeText = (change?: RadarChange): string => {
  if (!change) return ''
  const litText = change.newly_lit ? '新点亮' : '已更新'
  const deltaText = change.delta > 0 ? `+${change.delta}` : String(change.delta)
  return `${litText} · ${change.from_score} → ${change.to_score}（${deltaText}）`
}

const dismissRadarHighlight = () => {
  profileStore.fadeDialogueRadarChanges()
}

const retryNarrative = () => {
  void profileStore.loadNarrative(true)
}

const proposeGoal = async () => {
  if (!goalText.value.trim()) return
  goalProposing.value = true
  try { goalCandidate.value = await proposeProfileGoal(goalText.value.trim()) } finally { goalProposing.value = false }
}
const resetGoal = () => { goalCandidate.value = null; goalText.value = '' }
const confirmGoal = async () => {
  if (!goalCandidate.value) return
  goalConfirming.value = true
  try { await confirmProfileGoal(goalCandidate.value); goalCandidate.value = null; await refreshProfileView() } finally { goalConfirming.value = false }
}
const loadTrust = async () => { try { trust.value = await readProfileTrust() } catch { trust.value = null } }
const loadChanges = async (offset: number) => {
  changeOffset.value = Math.max(0, offset)
  try { profileStore.profile = await readProfile({ limit: changeLimit, offset: changeOffset.value }) } catch { /* retain last successful profile */ }
}
const previousChanges = () => void loadChanges(changeOffset.value - changeLimit)
const nextChanges = () => void loadChanges(changeOffset.value + changeLimit)
const calibrationChange = (source: string) => source === 'calibration' || source.includes('claim') || source.includes('goal_confirm')
const dimensionName = (key: string) => dimensionLabels[key as keyof typeof dimensionLabels] ?? key
const changeValue = (value: unknown) => {
  if (value === null || value === undefined) return '未设定'
  if (typeof value !== 'object') return String(value)
  const record = value as Record<string, unknown>
  return String(record.label ?? record.normalized ?? record.score ?? record.confidence ?? '已更新')
}

const refreshProfileView = async () => {
  await profileStore.loadProfile()
  void profileStore.ensureNarrativeLoaded()
  if (!profileStore.usingMock) void loadTrust()
}

const scheduleRadarHighlightFade = () => {
  if (radarFadeTimer !== undefined) window.clearTimeout(radarFadeTimer)
  if (!profileStore.radarHighlightActive) return
  radarFadeTimer = window.setTimeout(() => {
    profileStore.fadeDialogueRadarChanges()
  }, 5000)
}

onMounted(() => {
  void refreshProfileView()
  scheduleRadarHighlightFade()
})

onBeforeUnmount(() => {
  if (radarFadeTimer !== undefined) window.clearTimeout(radarFadeTimer)
})

watch(
  () => auth.user?.user_id,
  (userId, oldUserId) => {
    if (!userId || userId === oldUserId) return
    void refreshProfileView()
  },
)

watch(
  () => [profileStore.radarHighlightActive, profileStore.latestRadarChanges.length],
  () => scheduleRadarHighlightFade(),
)
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.profile-view {
  display: grid;
  gap: 18px;
  width: min(1440px, 100%);
  margin: 0 auto;
}

.profile-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
  align-items: stretch;
  gap: 18px;
}
.trust-card { display: grid; align-content: center; gap: 5px; min-width: 170px; }
.trust-card__label, .trust-card span { color: var(--qh-text-muted); font-size: 12px; }
.trust-card strong { color: var(--qh-color-gold); font-family: var(--qh-font-display); font-size: 22px; }
.goal-card { display: grid; gap: 14px; }
.goal-card :deep(.el-input) { max-width: 640px; }
.goal-actions { display: flex; gap: 10px; }
.goal-restatement { display: flex; align-items: flex-start; gap: 10px; max-width: 720px; color: var(--qh-text-muted); }
.goal-restatement img { width: 38px; height: 38px; border-radius: 50%; object-fit: cover; }
.goal-restatement p { margin: 7px 0 0; line-height: 1.7; }
.change-log { display: grid; gap: 14px; }
.change-log__list { display: grid; gap: 10px; }
.change-log__list article { display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 10px; padding: 12px; border: 1px solid rgba(143,208,173,.14); border-radius: 8px; }
.change-icon { color: var(--qh-color-gold); font-size: 18px; }.change-log__list small { margin-left: 10px; color: var(--qh-text-muted); }.change-log__list p { margin: 6px 0; color: var(--qh-text); }.change-log__list em { color: var(--qh-text-muted); font-size: 12px; font-style: normal; }.change-log__pager { display: flex; align-items: center; justify-content: flex-end; gap: 10px; color: var(--qh-text-muted); font-size: 12px; }

.hero-copy {
  @include glass-panel;
  min-height: 180px;
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
  font-size: clamp(38px, 6vw, 68px);
}

h3 {
  color: var(--qh-text);
  font-size: 24px;
}

.hero-copy p:last-child {
  margin: 12px 0 0;
  color: var(--qh-text-muted);
  font-size: 14px;
}

.hero-bubble {
  @include glass-panel;
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  align-items: center;
  gap: 14px;
  padding: 18px;
}

.hero-bubble img {
  width: 82px;
  height: 82px;
  border-radius: 22px;
  object-fit: cover;
  transform-origin: 50% 80%;
  animation:
    bob 5s ease-in-out infinite,
    wave 9s ease-in-out infinite;
}

.hero-bubble div {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.hero-bubble strong {
  font-family: var(--qh-font-display);
  font-size: 18px;
  font-weight: 900;
}

.hero-bubble span {
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.7;
}

.profile-alert {
  border-radius: 16px;
}

.radar-change-banner {
  border-color: var(--qh-line-strong);
}

.hint-action {
  margin-top: 8px;
  font-weight: 900;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.profile-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.18fr) minmax(360px, 0.82fr);
  gap: 18px;
  align-items: stretch;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 18px;
}

.radar-layout {
  display: grid;
  grid-template-columns: minmax(340px, 520px) minmax(280px, 1fr);
  gap: 18px;
  align-items: center;
}

.dim-list {
  display: grid;
  gap: 10px;
}

.dim-pending-hint {
  margin: 0;
  padding: 10px 12px;
  border: 1px dashed var(--qh-line-strong);
  border-radius: 12px;
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.dim-change-wrap {
  display: grid;
  gap: 7px;
}

.dim-change-wrap.is-highlighted :deep(.dim-row) {
  border-color: var(--qh-color-gold);
  box-shadow: 0 0 0 1px var(--qh-line-strong), 0 16px 42px var(--qh-line);
  animation: radar-axis-glow 900ms ease both;
}

.radar-change-note {
  justify-self: end;
  padding: 3px 8px;
  border-radius: 999px;
  color: var(--qh-color-gold);
  background: var(--qh-line);
  font-size: 12px;
  font-weight: 900;
}

.narrative-panel {
  min-height: 100%;
}

.narrative-panel .eyebrow,
.narrative-panel .narrative-source,
.narrative-panel .narrative-columns span {
  color: var(--qh-color-gold-readable);
}

.narrative-text {
  margin: 0;
  color: var(--qh-narrative-text);
  font-size: 16px;
  line-height: 1.95;
}

.narrative-pending {
  color: var(--qh-text-muted);
}

.narrative-alert {
  margin-bottom: 14px;
  border-radius: 14px;
}

.narrative-source {
  margin: 10px 0 0;
  color: var(--qh-color-gold);
  font-size: 12px;
  font-weight: 900;
}

.narrative-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
}

.narrative-placeholder-block {
  display: grid;
  gap: 10px;
}

.narrative-skeleton-line {
  display: block;
  height: 12px;
  border-radius: 999px;
  background: var(--qh-narrative-skeleton);
}

.narrative-skeleton-line-lg {
  width: 92%;
}

.narrative-skeleton-line-sm {
  width: 68%;
}

.narrative-columns section {
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--qh-narrative-border);
  border-radius: 16px;
  background: var(--qh-narrative-surface);
}

.narrative-columns span {
  display: block;
  margin-bottom: 8px;
  color: var(--qh-color-gold);
  font-weight: 900;
}

.narrative-columns p,
.empty-line,
.evidence-list p {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.75;
}

.jury-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.lower-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(300px, 0.75fr);
  gap: 18px;
}

.evidence-list {
  display: grid;
  gap: 10px;
}

.evidence-list article,
.weak-list div {
  border: 1px solid var(--qh-line);
  border-radius: 14px;
  background: var(--qh-line);
}

.evidence-list article {
  padding: 13px;
}

.evidence-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  margin-bottom: 7px;
}

.evidence-meta strong {
  color: var(--qh-color-jade);
}

.evidence-meta span {
  color: var(--qh-text-muted);
  font-size: 12px;
}

.signal-line {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 10px;
}

.weak-list {
  display: grid;
  gap: 10px;
}

.weak-list div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  gap: 12px;
  padding: 12px 13px;
}

.weak-list span {
  min-width: 0;
  font-weight: 900;
}

.weak-list strong {
  flex: 0 0 auto;
  color: var(--qh-color-coral);
  font-family: var(--qh-font-display);
  font-weight: 900;
}

@keyframes radar-axis-glow {
  from {
    transform: translateY(4px);
    opacity: 0.72;
  }

  to {
    transform: translateY(0);
    opacity: 1;
  }
}

@media (max-width: 1200px) {
  .profile-grid,
  .lower-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .profile-hero,
  .stats-grid,
  .radar-layout,
  .jury-grid {
    grid-template-columns: 1fr;
  }

  .radar-layout {
    justify-items: stretch;
  }
}

@media (max-width: 640px) {
  .hero-copy,
  .hero-bubble {
    padding: 18px;
  }

  .narrative-columns {
    grid-template-columns: 1fr;
  }
}
</style>
