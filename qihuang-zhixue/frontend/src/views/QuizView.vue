<template>
  <section class="quiz-view">
    <div class="quiz-hero">
      <div>
        <p class="eyebrow">题库测评</p>
        <h2>{{ learning.activeTopic }}</h2>
        <p>按当前画像生成 Bloom 分层题目，提交后回写 BKT 与 FSRS。</p>
      </div>
      <div class="quiz-actions">
        <el-input v-model="topicInput" placeholder="输入测评主题，如 麻黄" @keyup.enter="generateQuiz" />
        <el-button type="primary" :loading="learning.streaming" :icon="DocumentChecked" @click="generateQuiz">
          生成测评
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="learning.error"
      class="quiz-alert"
      type="warning"
      :title="learning.error"
      show-icon
      :closable="false"
    />

    <GlassCard padding="lg" class="quiz-panel">
      <QuizPanel
        v-if="quizPayload"
        :questions="quizPayload.questions"
        :topic="learning.activeTopic"
        @submit="submitItems"
      />
      <ResourceSkeleton v-else label="题库生成中" />
    </GlassCard>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { DocumentChecked } from '@element-plus/icons-vue'
import GlassCard from '../components/ui/GlassCard.vue'
import QuizPanel from '../components/learning/QuizPanel.vue'
import ResourceSkeleton from '../components/learning/ResourceSkeleton.vue'
import { useLearningStore } from '../stores/learning'
import type { QuizPayload, QuizSubmitItem } from '../types/learning'

const learning = useLearningStore()
const topicInput = ref(learning.activeTopic || '麻黄')

const quizPayload = computed(() => learning.resources.quiz?.payload as QuizPayload | undefined)

const generateQuiz = async () => {
  await learning.generateResources(topicInput.value || '麻黄', ['quiz', 'explain'])
}

const submitItems = async (items: QuizSubmitItem[]) => {
  await learning.answerQuiz(items)
}

onMounted(() => {
  if (!quizPayload.value) void generateQuiz()
})
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.quiz-view {
  display: grid;
  gap: 18px;
  width: min(1280px, 100%);
  margin: 0 auto;
}

.quiz-hero {
  @include glass-panel(26px);
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 520px);
  gap: 18px;
  align-items: center;
  padding: 24px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;
}

h2 {
  @include text-sheen;
  margin: 0;
  font-family: var(--qh-font-display);
  font-size: clamp(36px, 5vw, 62px);
  font-weight: 900;
}

.quiz-hero p {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
}

.quiz-actions {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto;
  gap: 10px;
}

.quiz-alert {
  border-radius: 16px;
}

.quiz-panel {
  min-height: 620px;
}

@media (max-width: 860px) {
  .quiz-hero,
  .quiz-actions {
    grid-template-columns: 1fr;
  }
}
</style>
