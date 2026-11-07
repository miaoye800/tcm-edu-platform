<template>
  <section v-loading="learning.loadingPath" class="dashboard-view">
    <div class="dashboard-hero">
      <div>
        <p class="eyebrow">总览仪表盘</p>
        <h2>今日学情</h2>
        <p>{{ auth.displayName }} · {{ masteryText }}</p>
      </div>
      <div class="hero-actions">
        <el-button type="primary" :icon="MagicStick" @click="goLearn(recommendations[0]?.title || '麻黄')">
          开始学习
        </el-button>
        <el-button :icon="Timer" @click="router.push('/review')">今日复习</el-button>
      </div>
    </div>

    <el-alert
      v-if="learning.error"
      class="dashboard-alert"
      type="warning"
      :title="learning.error"
      show-icon
      :closable="false"
    />

    <section class="stats-grid">
      <StatCard label="已掌握关卡" :value="String(statusCount.mastered)" tone="jade" />
      <StatCard label="可学习关卡" :value="String(statusCount.unlocked + statusCount.in_progress)" tone="blue" />
      <StatCard label="今日到期" :value="String(reviewCount)" tone="coral" />
      <StatCard label="推荐入口" :value="String(recommendations.length)" tone="gold" />
    </section>

    <section class="dashboard-grid">
      <GlassCard padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Top3</p>
            <h3>精准推荐</h3>
          </div>
          <Badge tone="gold" :label="`${recommendations.length} 项`" />
        </div>
        <div class="recommend-list">
          <article v-for="item in recommendations" :key="item.kc_id" @click="goLearn(item.title)">
            <div>
              <strong>{{ item.title }}</strong>
              <p>{{ item.reason }}</p>
            </div>
            <Badge :tone="badgeTone(item.status)" :label="statusText(item.status)" />
          </article>
          <p v-if="!recommendations.length" class="empty-line">暂无推荐，可先进入任一已解锁关卡。</p>
        </div>
      </GlassCard>

      <GlassCard padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Review</p>
            <h3>今日复习提醒</h3>
          </div>
          <Badge tone="coral" :label="reviewCount" />
        </div>
        <div class="review-list">
          <article v-for="item in reviews.slice(0, 5)" :key="item.kc_id">
            <strong>{{ item.kc_id }}</strong>
            <span>紧迫度 {{ Math.round(item.urgency * 100) }}% · 尝试 {{ item.attempts }} 次</span>
          </article>
          <p v-if="!reviews.length" class="empty-line">今天没有到期复习项。</p>
        </div>
      </GlassCard>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { MagicStick, Timer } from '@element-plus/icons-vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import StatCard from '../components/profile/StatCard.vue'
import { useLearningStore } from '../stores/learning'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import type { SkillStatus } from '../types/learning'

const router = useRouter()
const learning = useLearningStore()
const auth = useAuthStore()
const profile = useProfileStore()

const recommendations = computed(() => learning.recommendations)
const reviews = computed(() => learning.reviews?.items ?? [])
const reviewCount = computed(() => learning.reviews?.suggested_count ?? 0)
const statusCount = computed(() => {
  const result = { locked: 0, unlocked: 0, in_progress: 0, mastered: 0 }
  learning.skillTree.forEach((node) => {
    result[node.status] += 1
  })
  return result
})
const masteryText = computed(() => {
  const total = learning.skillTree.length || 1
  return `掌握 ${statusCount.value.mastered}/${total} · 到期复习 ${reviewCount.value}`
})

const goLearn = (topic: string) => {
  void router.push({ name: 'learn', query: { topic } })
}

const statusText = (status: SkillStatus) =>
  ({ locked: '锁定', unlocked: '解锁', in_progress: '进行中', mastered: '已掌握' })[status]

const badgeTone = (status: SkillStatus) =>
  ({ locked: 'muted', unlocked: 'blue', in_progress: 'gold', mastered: 'jade' })[status] as 'muted' | 'blue' | 'gold' | 'jade'

onMounted(() => {
  void Promise.all([learning.loadPath(), profile.loadProfile()])
})
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.dashboard-view {
  display: grid;
  gap: 18px;
  width: min(1380px, 100%);
  margin: 0 auto;
}

.dashboard-hero {
  @include glass-panel;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 24px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-fg-muted);
  font-size: 13px;
  font-weight: 900;
}

h2,
h3 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-weight: 900;
}

h2 {
  @include text-sheen;
  font-size: clamp(36px, 5vw, 62px);
}

h3 {
  font-size: 22px;
}

.dashboard-hero p:last-child,
.empty-line {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.7;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.dashboard-alert {
  border-radius: 16px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(360px, 0.9fr);
  gap: 18px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 16px;
}

.recommend-list,
.review-list {
  display: grid;
  gap: 12px;
}

.recommend-list article,
.review-list article {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--qh-line);
  border-radius: 16px;
  background: var(--qh-line);
}

.recommend-list article {
  cursor: pointer;
}

.recommend-list strong,
.review-list strong {
  color: var(--qh-text);
  font-weight: 900;
}

.recommend-list p,
.review-list span {
  display: block;
  margin: 6px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.65;
}

@media (max-width: 980px) {
  .dashboard-hero,
  .dashboard-grid {
    display: grid;
    grid-template-columns: 1fr;
  }

  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
