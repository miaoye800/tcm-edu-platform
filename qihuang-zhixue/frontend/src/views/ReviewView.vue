<template>
  <section v-loading="learning.loadingReviews" class="review-view">
    <div class="review-hero">
      <div>
        <p class="eyebrow">复习计划</p>
        <h2>今日到期</h2>
        <p>建议完成 {{ learning.reviews?.suggested_count ?? 0 }} 题 · FSRS 动态安排</p>
      </div>
      <el-button type="primary" :icon="Refresh" @click="learning.loadReviews()">刷新计划</el-button>
    </div>

    <el-alert
      v-if="learning.error"
      type="warning"
      :title="learning.error"
      show-icon
      :closable="false"
      class="review-alert"
    />

    <section class="review-grid">
      <GlassCard padding="lg" class="review-card">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Due Queue</p>
            <h3>复习队列</h3>
          </div>
          <Badge tone="coral" :label="items.length" />
        </div>

        <div class="due-list">
          <article v-for="item in items" :key="item.kc_id">
            <div>
              <strong>{{ item.kc_id }}</strong>
              <p>到期 {{ formatDate(item.due_at) }} · 难度 {{ item.difficulty.toFixed(1) }} · 稳定性 {{ item.stability.toFixed(1) }}</p>
            </div>
            <div class="due-actions">
              <el-progress type="circle" :width="54" :percentage="Math.round(item.urgency * 100)" color="#e9a79c" />
              <el-button size="small" :icon="MagicStick" @click="reviewItem(item.kc_id)">复习</el-button>
            </div>
          </article>
          <p v-if="!items.length" class="empty-line">今天没有到期项，适合推进新关卡。</p>
        </div>
      </GlassCard>

      <GlassCard padding="lg" class="review-card">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Practice</p>
            <h3>一键复习作答</h3>
          </div>
          <Badge :tone="quizPayload ? 'jade' : 'muted'" :label="quizPayload ? 'Ready' : '待生成'" />
        </div>
        <QuizPanel
          v-if="quizPayload"
          :questions="quizPayload.questions"
          :topic="learning.activeTopic"
          @submit="itemsSubmit"
        />
        <ResourceSkeleton v-else label="选择左侧到期项生成题库" />
      </GlassCard>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { MagicStick, Refresh } from '@element-plus/icons-vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import QuizPanel from '../components/learning/QuizPanel.vue'
import ResourceSkeleton from '../components/learning/ResourceSkeleton.vue'
import { useLearningStore } from '../stores/learning'
import type { QuizPayload, QuizSubmitItem } from '../types/learning'

const learning = useLearningStore()

const items = computed(() => learning.reviews?.items ?? [])
const quizPayload = computed(() => learning.resources.quiz?.payload as QuizPayload | undefined)

const reviewItem = async (kcId: string) => {
  await learning.generateResources(kcId.replace(/^herb_/, ''), ['quiz', 'explain'])
}

const itemsSubmit = async (payload: QuizSubmitItem[]) => {
  await learning.answerQuiz(payload)
}

const formatDate = (value: string) => new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))

onMounted(() => {
  void learning.loadReviews()
})
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.review-view {
  display: grid;
  gap: 18px;
  width: min(1440px, 100%);
  margin: 0 auto;
}

.review-hero {
  @include glass-panel;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 24px;
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
}

h2 {
  @include text-sheen;
  font-size: clamp(36px, 5vw, 62px);
}

h3 {
  font-size: 22px;
}

.review-hero p,
.empty-line {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
}

.review-alert {
  border-radius: 16px;
}

.review-grid {
  display: grid;
  grid-template-columns: minmax(360px, 0.84fr) minmax(0, 1.16fr);
  gap: 18px;
  align-items: start;
}

.review-card {
  min-height: 560px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 16px;
}

.due-list {
  display: grid;
  gap: 12px;
}

.due-list article {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  padding: 14px;
  border: 1px solid var(--qh-line);
  border-radius: 16px;
  background: var(--qh-line);
}

.due-list strong {
  color: var(--qh-text);
  font-weight: 900;
}

.due-list p {
  margin: 7px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.65;
}

.due-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

@media (max-width: 1080px) {
  .review-hero,
  .review-grid {
    display: grid;
    grid-template-columns: 1fr;
  }
}
</style>
