<template>
  <section v-loading="profileStore.loading" class="profile-view">
    <div class="profile-hero">
      <div class="hero-copy">
        <p class="eyebrow">中医个性化学习画像</p>
        <h2>七维修行罗盘</h2>
        <p>当前账号：{{ auth.displayName }} · {{ updatedAtText }}</p>
      </div>

      <div class="hero-bubble">
        <img :src="xiaoqi" alt="小岐" />
        <div>
          <strong>小岐提示</strong>
          <span>先取证，再会诊；画像越亮，路径越准。</span>
        </div>
      </div>
    </div>

    <el-alert
      v-if="profileStore.error"
      class="profile-alert"
      type="warning"
      :title="profileStore.error"
      show-icon
      :closable="false"
    />

    <section class="stats-grid" aria-label="画像概览">
      <StatCard v-for="stat in stats" :key="stat.label" v-bind="stat" />
    </section>

    <section class="profile-grid">
      <GlassCard class="radar-panel" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">七维画像</p>
            <h3>修行罗盘</h3>
          </div>
          <Badge tone="gold" :label="`${lowConfidenceNames.length} 维待取证`" />
        </div>

        <div class="radar-layout">
          <RadarChart :axes="radarAxes" />
          <div class="dim-list">
            <DimRow v-for="axis in radarAxes" :key="axis.key" :axis="axis" />
          </div>
        </div>
      </GlassCard>

      <GlassCard class="narrative-panel" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">叙述画像</p>
            <h3>档案卷</h3>
          </div>
          <Badge :tone="profileStore.narrative.method === 'llm' ? 'blue' : 'jade'" :label="narrativeMethodText" />
        </div>

        <p class="narrative-text">{{ narrativeText }}</p>

        <div class="narrative-columns">
          <section>
            <span>所长</span>
            <p v-if="!advantageItems.length">待补充。</p>
            <p v-for="item in advantageItems" v-else :key="`adv-${item.dimension_key}`">{{ item.summary }}</p>
          </section>
          <section>
            <span>所短</span>
            <p v-if="!weaknessItems.length">暂无高置信薄弱点。</p>
            <p v-for="item in weaknessItems" v-else :key="`weak-${item.dimension_key}`">{{ item.summary }}</p>
          </section>
        </div>
      </GlassCard>
    </section>

    <section class="jury-grid" aria-label="三师会诊">
      <JuryCard
        v-for="teacher in teacherComments"
        :key="teacher.name"
        :title="teacher.name"
        :role="teacher.role"
        :pending="teacher.pending"
        :entries="teacher.entries"
      />
    </section>

    <section class="lower-grid">
      <GlassCard padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">取证</p>
            <h3>证据卡</h3>
          </div>
          <Badge tone="blue" :label="`${evidenceCards.length} 张`" />
        </div>

        <div v-if="evidenceCards.length" class="evidence-list">
          <article v-for="(card, index) in evidenceCards.slice(0, 8)" :key="`${card.dimension}-${index}`">
            <div class="evidence-meta">
              <strong>{{ card.dimensionName }}</strong>
              <span>{{ card.source }} · 权重 {{ card.weight }} · {{ card.timestampText }}</span>
            </div>
            <p>{{ card.fact }}</p>
            <div v-if="card.signals.length" class="signal-line">
              <Badge v-for="signal in card.signals" :key="signal" tone="muted" :label="signal" />
            </div>
          </article>
        </div>
        <p v-else class="empty-line">暂无证据卡。</p>
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
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import xiaoqi from '../assets/mascot/xiaoqi-avatar.png'
import DimRow from '../components/profile/DimRow.vue'
import JuryCard from '../components/profile/JuryCard.vue'
import RadarChart from '../components/profile/RadarChart.vue'
import StatCard from '../components/profile/StatCard.vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import type { TeacherName } from '../types/profile'
import { dimensionKeys } from '../types/profile'
import {
  buildRadarAxes,
  confidenceFor,
  dimensionLabels,
  formatDateTime,
  lowConfidenceThreshold,
  profileEvidenceCards,
  weakPointItems,
} from '../utils/profileMath'

const profileStore = useProfileStore()
const auth = useAuthStore()

const radarAxes = computed(() => buildRadarAxes(profileStore.profile, profileStore.narrative))
const evidenceCards = computed(() => profileEvidenceCards(profileStore.profile))
const weakItems = computed(() => weakPointItems(profileStore.profile, profileStore.narrative))
const updatedAtText = computed(() => formatDateTime(profileStore.profile.updated_at))
const readyDimensionCount = computed(
  () =>
    dimensionKeys.filter(
      (key) => confidenceFor(profileStore.profile, profileStore.narrative, key) >= lowConfidenceThreshold,
    ).length,
)
const lowConfidenceNames = computed(() =>
  dimensionKeys
    .filter((key) => confidenceFor(profileStore.profile, profileStore.narrative, key) < lowConfidenceThreshold)
    .map((key) => dimensionLabels[key]),
)

const stats = computed(() => [
  { label: '高置信维度', value: `${readyDimensionCount.value}/7`, tone: 'jade' as const },
  { label: '证据卡', value: String(evidenceCards.value.length), tone: 'blue' as const },
  { label: '低置信维', value: String(lowConfidenceNames.value.length), tone: 'coral' as const },
  { label: '叙述方式', value: narrativeMethodText.value, tone: 'gold' as const },
])

const narrativeMethodText = computed(() => (profileStore.narrative.method === 'llm' ? 'LLM 编排' : '模板渲染'))
const narrativeText = computed(() => {
  if (profileStore.narrative.description) return profileStore.narrative.description
  return '当前暂无高置信维度。系统会先收集诊断小测、问答互动和资源偏好证据，再生成稳定叙述画像。'
})

const advantageItems = computed(() => profileStore.narrative.advantages ?? [])
const weaknessItems = computed(() => profileStore.narrative.weak_points ?? [])

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

onMounted(() => {
  void profileStore.loadProfile()
})

watch(
  () => auth.user?.user_id,
  (userId, oldUserId) => {
    if (!userId || userId === oldUserId) return
    void profileStore.loadProfile()
  },
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

.hero-copy {
  @include glass-panel(26px);
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
  @include glass-panel(24px);
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

.narrative-panel {
  min-height: 100%;
}

.narrative-text {
  margin: 0;
  color: rgba(238, 245, 239, 0.86);
  font-size: 16px;
  line-height: 1.95;
}

.narrative-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
}

.narrative-columns section {
  min-width: 0;
  padding: 14px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 16px;
  background: rgba(238, 245, 239, 0.05);
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
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 14px;
  background: rgba(238, 245, 239, 0.055);
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
