<template>
  <section class="quiz-panel">
    <header>
      <div>
        <p class="eyebrow">Quiz</p>
        <strong>{{ questions.length }} 道随堂题</strong>
      </div>
      <el-button type="primary" :icon="Check" :disabled="!questions.length || submitting" @click="submitAll">
        提交判分
      </el-button>
    </header>

    <div class="question-list">
      <article v-for="(question, index) in questions" :key="questionKey(question, index)" class="question-card">
        <div class="question-head">
          <span>{{ index + 1 }}</span>
          <div>
            <h4>{{ question.stem }}</h4>
            <p>{{ question.type }} · Bloom {{ question.bloom_level }} · 难度 {{ question.difficulty }}</p>
          </div>
        </div>

        <el-radio-group
          v-if="question.type === '单选'"
          v-model="answers[questionKey(question, index)]"
          class="option-list"
        >
          <el-radio v-for="option in question.options" :key="option.key" :label="option.key">
            {{ option.key }}. {{ option.text }}
          </el-radio>
        </el-radio-group>

        <el-checkbox-group
          v-else-if="question.type === '多选'"
          v-model="multiAnswers[questionKey(question, index)]"
          class="option-list"
        >
          <el-checkbox v-for="option in question.options" :key="option.key" :label="option.key">
            {{ option.key }}. {{ option.text }}
          </el-checkbox>
        </el-checkbox-group>

        <el-input
          v-else
          v-model="answers[questionKey(question, index)]"
          :type="question.type === '填空' ? 'text' : 'textarea'"
          :rows="question.type === '填空' ? 1 : 3"
          placeholder="写下你的答案"
        />

        <div class="question-actions">
          <el-button size="small" text :icon="View" @click="toggleReveal(questionKey(question, index))">
            对答案
          </el-button>
          <Badge tone="gold" :label="question.source.name || question.source.type" />
        </div>

        <section v-if="revealed[questionKey(question, index)]" class="answer-box">
          <p><strong>答案：</strong>{{ question.answer.join('、') }}</p>
          <p><strong>解析：</strong>{{ question.explanation }}</p>
          <p v-if="question.options.length"><strong>干扰项：</strong>{{ rationaleText(question) }}</p>
        </section>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import { Check, View } from '@element-plus/icons-vue'
import Badge from '../ui/Badge.vue'
import type { QuizQuestion, QuizSubmitItem } from '../../types/learning'

const props = defineProps<{
  questions: QuizQuestion[]
  topic: string
  submitting?: boolean
}>()

const emit = defineEmits<{
  submit: [items: QuizSubmitItem[]]
}>()

const answers = reactive<Record<string, string>>({})
const multiAnswers = reactive<Record<string, string[]>>({})
const revealed = reactive<Record<string, boolean>>({})

const questionKey = (question: QuizQuestion, index: number) => `${question.stem}-${index}`

const toggleReveal = (key: string) => {
  revealed[key] = !revealed[key]
}

const rationaleText = (question: QuizQuestion) =>
  Object.entries(question.distractor_rationale)
    .map(([key, value]) => `${key}：${value}`)
    .join('；')

const answerFor = (question: QuizQuestion, index: number) => {
  const key = questionKey(question, index)
  if (question.type === '多选') return multiAnswers[key] ?? []
  const value = answers[key]
  return value ? [value] : []
}

const submitAll = () => {
  const items = props.questions.map((question, index) => ({
    question_id: questionKey(question, index),
    kc_id: question.source.name || props.topic,
    answer: answerFor(question, index),
    correct_answer: question.answer,
    difficulty: question.difficulty,
    bloom_level: question.bloom_level,
    topic: props.topic,
  }))
  emit('submit', items)
  props.questions.forEach((question, index) => {
    revealed[questionKey(question, index)] = true
  })
}
</script>

<style scoped lang="scss">
@use "../../styles/tokens.scss" as *;

.quiz-panel {
  display: grid;
  gap: 14px;
}

header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.eyebrow {
  margin: 0 0 5px;
  color: var(--qh-color-jade);
  font-size: 12px;
  font-weight: 900;
}

strong {
  font-family: var(--qh-font-display);
  font-size: 18px;
  font-weight: 900;
}

.question-list {
  display: grid;
  gap: 12px;
}

.question-card {
  display: grid;
  gap: 13px;
  padding: 16px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 18px;
  background: rgba(238, 245, 239, 0.045);
}

.question-head {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 12px;
  align-items: flex-start;
}

.question-head > span {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 12px;
  color: #10201d;
  font-family: var(--qh-font-display);
  font-weight: 900;
  background: linear-gradient(135deg, var(--qh-color-jade), var(--qh-color-gold));
}

h4 {
  margin: 0;
  color: var(--qh-text);
  font-size: 15px;
  line-height: 1.55;
}

.question-head p,
.answer-box p {
  margin: 5px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.75;
}

.option-list {
  display: grid;
  gap: 8px;
}

.question-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 10px;
}

.answer-box {
  padding: 12px;
  border: 1px solid rgba(231, 194, 116, 0.18);
  border-radius: 14px;
  background: rgba(231, 194, 116, 0.075);
}

.answer-box strong {
  color: var(--qh-color-gold);
  font-family: var(--qh-font-body);
  font-size: 13px;
}
</style>
