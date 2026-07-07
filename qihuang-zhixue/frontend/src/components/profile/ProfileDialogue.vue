<template>
  <section class="profile-dialogue" aria-label="问诊式画像对话">
    <main class="dialogue-main">
      <div class="dialogue-head">
        <div>
          <p class="eyebrow">小岐问诊</p>
          <h2>边聊边显影的弟子画像</h2>
        </div>
        <span class="status-pill" :class="{ 'is-ready': converged }">{{ converged ? '已收束' : '取证中' }}</span>
      </div>

      <div ref="flowRef" class="bubble-flow" aria-live="polite">
        <article v-for="message in messages" :key="message.id" :class="['chat-bubble', `is-${message.role}`]">
          <span class="speaker">{{ message.role === 'assistant' ? '小岐' : '我' }}</span>
          <p>{{ message.text }}</p>
          <span v-if="message.role === 'assistant' && message.typing" class="typing-caret" />
        </article>
      </div>

      <footer class="answer-dock">
        <div class="quick-chips" aria-label="快捷回答">
          <button
            v-for="chip in dialogueQuestionChips"
            :key="chip.id"
            class="quick-chip"
            type="button"
            @click="sendQuickReply(chip.quickReply)"
          >
            {{ chip.label }}
          </button>
        </div>
        <form class="answer-form" @submit.prevent="sendUtterance">
          <el-input
            v-model="draft"
            size="large"
            placeholder="也可以自由说说你的学习目标、节奏或困惑..."
            clearable
            aria-label="自由回答"
          />
          <el-button type="primary" :icon="Promotion" native-type="submit" :disabled="!draft.trim()">发送</el-button>
        </form>
      </footer>
    </main>

    <aside class="destiny-panel" aria-label="命格卡推断">
      <div class="card-grid">
        <button
          v-for="card in displayCards"
          :key="card.name"
          type="button"
          :class="[
            'destiny-card',
            `tone-${card.tone}`,
            {
              'is-flipped': card.flipped,
              'is-inferred': card.inferred,
              'is-selected': card.selected,
            },
          ]"
          :style="{ '--score': `${card.score}%`, '--delay': `${card.index * 90}ms` }"
          @click="selectCard(card.name)"
        >
          <span class="card-face card-back">
            <span class="seal is-blur">{{ card.seal }}</span>
            <span>待显影</span>
          </span>
          <span class="card-face card-front">
            <span class="match-ring" :aria-label="`${card.name}匹配度${card.score}%`">
              <span>{{ card.score }}</span>
            </span>
            <span class="card-copy">
              <strong>{{ card.name }}</strong>
              <small>{{ card.summary }}</small>
            </span>
          </span>
        </button>
      </div>

      <section class="confirm-panel">
        <div>
          <p class="eyebrow">AI 画像速写</p>
          <h3>{{ selectedMeta.name }}</h3>
          <p>{{ selectedMeta.sketch }}</p>
        </div>
        <div class="confirm-actions">
          <el-button :icon="Check" type="primary" @click="confirmSelected">直接选卡</el-button>
          <el-button :icon="Switch" @click="skipSelection">跳过</el-button>
        </div>
      </section>

      <section class="mini-radar" :class="{ 'is-drawing': radar.length > 0 }">
        <div class="mini-radar__head">
          <p class="eyebrow">七维旁证</p>
          <span>{{ radar.length }} 格</span>
        </div>
        <RadarChart v-if="radar.length" :axes="radar" />
        <p v-else class="radar-empty">暂无雷达数据时，小岐仍可用模板问题继续取证。</p>
      </section>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { Check, Promotion, Switch } from '@element-plus/icons-vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { dialogueQuestionChips } from '../../config/dialogueQuestions'
import type { RadarAxis } from '../../types/profile'
import RadarChart from './RadarChart.vue'
import {
  bestScoredCard,
  cardIsFlipped,
  cardScoreFor,
  createAnswerPayload,
  createConfirmPayload,
  identityCards,
  type CardScores,
  type IdentityCardName,
} from './profileDialogueModel'

type ChatMessage = {
  id: number
  role: 'assistant' | 'user'
  text: string
  typing?: boolean
}

const props = withDefaults(
  defineProps<{
    prompt: string
    cardScores?: CardScores
    inferredCard?: IdentityCardName | string | null
    radar?: RadarAxis[]
    converged?: boolean
  }>(),
  {
    cardScores: () => ({}),
    inferredCard: null,
    radar: () => [],
    converged: false,
  },
)

const emit = defineEmits<{
  answer: [payload: ReturnType<typeof createAnswerPayload>]
  confirm: [payload: ReturnType<typeof createConfirmPayload>]
}>()

const draft = ref('')
const flowRef = ref<HTMLElement | null>(null)
const selectedCard = ref<IdentityCardName | string | null>(props.inferredCard)
const messages = ref<ChatMessage[]>([])
let messageId = 0
let typingTimer: ReturnType<typeof window.setInterval> | null = null

const scrollFlow = async () => {
  await nextTick()
  if (!flowRef.value) return
  flowRef.value.scrollTop = flowRef.value.scrollHeight
}

const stopTyping = () => {
  if (!typingTimer) return
  window.clearInterval(typingTimer)
  typingTimer = null
}

const addAssistantPrompt = (prompt: string) => {
  stopTyping()
  const text = prompt.trim() || '先从一个轻松问题开始：你最近最想解决的学习困惑是什么？'
  const message: ChatMessage = {
    id: ++messageId,
    role: 'assistant',
    text: '',
    typing: true,
  }
  messages.value.push(message)
  let index = 0
  typingTimer = window.setInterval(() => {
    message.text = text.slice(0, index + 1)
    index += 1
    void scrollFlow()
    if (index >= text.length) {
      message.typing = false
      stopTyping()
    }
  }, 24)
}

const addUserMessage = (text: string) => {
  messages.value.push({
    id: ++messageId,
    role: 'user',
    text,
  })
  void scrollFlow()
}

const sendUtterance = () => {
  const utterance = draft.value.trim()
  if (!utterance) return
  addUserMessage(utterance)
  emit('answer', createAnswerPayload('utterance', utterance))
  draft.value = ''
}

const sendQuickReply = (quickReply: string) => {
  addUserMessage(quickReply)
  emit('answer', createAnswerPayload('quick_reply', quickReply))
}

const selectCard = (cardName: IdentityCardName | string) => {
  selectedCard.value = cardName
}

const confirmSelected = () => {
  emit('confirm', createConfirmPayload(selectedCard.value ?? props.inferredCard ?? bestScoredCard(props.cardScores)))
}

const skipSelection = () => {
  emit('confirm', createConfirmPayload(null))
}

const displayCards = computed(() =>
  identityCards.map((card, index) => ({
    ...card,
    index,
    score: cardScoreFor(card.name, props.cardScores),
    flipped: cardIsFlipped(card.name, props.cardScores),
    inferred: props.converged && props.inferredCard === card.name,
    selected: selectedCard.value === card.name,
  })),
)

const selectedMeta = computed(() => {
  const fallback = props.inferredCard ?? bestScoredCard(props.cardScores)
  return identityCards.find((card) => card.name === (selectedCard.value ?? fallback)) ?? identityCards[0]
})

watch(
  () => props.prompt,
  (prompt) => addAssistantPrompt(prompt),
  { immediate: true },
)

watch(
  () => props.inferredCard,
  (card) => {
    if (card) selectedCard.value = card
  },
)

onBeforeUnmount(stopTyping)
</script>

<style scoped lang="scss">
@use "../../styles/tokens.scss" as *;

.profile-dialogue {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(360px, 0.92fr);
  gap: 18px;
  width: min(1320px, 100%);
}

.dialogue-main,
.destiny-panel,
.confirm-panel,
.mini-radar {
  @include glass-panel(24px);
}

.dialogue-main {
  display: grid;
  grid-template-rows: auto minmax(360px, 1fr) auto;
  min-height: 720px;
  overflow: hidden;
}

.dialogue-head,
.answer-dock {
  padding: 22px;
}

.dialogue-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  border-bottom: 1px solid rgba(143, 208, 173, 0.1);
}

.eyebrow {
  margin: 0 0 7px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;
}

h2,
h3 {
  margin: 0;
  color: var(--qh-text);
  font-family: var(--qh-font-display);
  font-weight: 900;
  line-height: 1.15;
}

h2 {
  font-size: clamp(26px, 3vw, 38px);
}

h3 {
  font-size: 22px;
}

.status-pill {
  flex: 0 0 auto;
  padding: 8px 12px;
  border: 1px solid rgba(159, 182, 214, 0.22);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-color-blue);
  background: rgba(159, 182, 214, 0.1);
  font-size: 12px;
  font-weight: 900;
}

.status-pill.is-ready {
  border-color: rgba(231, 194, 116, 0.42);
  color: var(--qh-color-gold);
  background: rgba(231, 194, 116, 0.13);
}

.bubble-flow {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 22px;
  overflow: auto;
}

.chat-bubble {
  max-width: min(620px, 88%);
  padding: 14px 16px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 18px;
  background: rgba(238, 245, 239, 0.055);
}

.chat-bubble.is-assistant {
  align-self: flex-start;
  border-top-left-radius: 8px;
  background:
    linear-gradient(135deg, rgba(143, 208, 173, 0.14), rgba(159, 182, 214, 0.08)),
    rgba(238, 245, 239, 0.045);
}

.chat-bubble.is-user {
  align-self: flex-end;
  border-top-right-radius: 8px;
  border-color: rgba(231, 194, 116, 0.2);
  background: rgba(231, 194, 116, 0.1);
}

.speaker {
  display: block;
  margin-bottom: 5px;
  color: var(--qh-color-gold);
  font-size: 12px;
  font-weight: 900;
}

.chat-bubble p {
  display: inline;
  margin: 0;
  color: rgba(238, 245, 239, 0.88);
  line-height: 1.8;
}

.typing-caret {
  display: inline-block;
  width: 8px;
  height: 1.1em;
  margin-left: 3px;
  vertical-align: text-bottom;
  border-radius: 4px;
  background: var(--qh-color-jade);
  animation: pulse 1s ease-in-out infinite;
}

.answer-dock {
  display: grid;
  gap: 14px;
  border-top: 1px solid rgba(143, 208, 173, 0.1);
}

.quick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
}

.quick-chip {
  min-height: 34px;
  padding: 7px 12px;
  border: 1px solid rgba(143, 208, 173, 0.18);
  border-radius: var(--qh-radius-pill);
  color: rgba(238, 245, 239, 0.84);
  background: rgba(143, 208, 173, 0.08);
  cursor: pointer;
  font-size: 13px;
  font-weight: 900;
}

.quick-chip:hover,
.quick-chip:focus-visible {
  border-color: rgba(231, 194, 116, 0.46);
  color: var(--qh-text);
}

.quick-chip:focus-visible,
.destiny-card:focus-visible {
  @include focus-ring;
}

.answer-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
}

.destiny-panel {
  display: grid;
  gap: 14px;
  align-content: start;
  padding: 18px;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.destiny-card {
  --tone-color: var(--qh-color-jade);
  position: relative;
  display: grid;
  min-height: 174px;
  padding: 0;
  border: 1px solid rgba(143, 208, 173, 0.14);
  border-radius: 18px;
  background: rgba(238, 245, 239, 0.05);
  cursor: pointer;
  perspective: 1000px;
  opacity: 0;
  transform: translateY(12px);
  animation: card-rise 420ms ease forwards;
  animation-delay: var(--delay);
}

.destiny-card.tone-gold {
  --tone-color: var(--qh-color-gold);
}

.destiny-card.tone-blue {
  --tone-color: var(--qh-color-blue);
}

.destiny-card.tone-coral {
  --tone-color: var(--qh-color-coral);
}

.card-face {
  grid-area: 1 / 1;
  display: grid;
  place-items: center;
  min-height: 174px;
  padding: 16px;
  border-radius: 18px;
  backface-visibility: hidden;
  transition:
    transform 520ms ease,
    opacity 260ms ease,
    filter 260ms ease;
  transform-style: preserve-3d;
}

.card-back {
  gap: 10px;
  color: rgba(238, 245, 239, 0.46);
  background:
    linear-gradient(145deg, rgba(20, 34, 33, 0.84), rgba(10, 18, 20, 0.62)),
    radial-gradient(circle at 50% 35%, rgba(238, 245, 239, 0.08), transparent 48%);
}

.card-front {
  grid-template-columns: auto minmax(0, 1fr);
  gap: 12px;
  justify-items: start;
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--tone-color) 18%, transparent), rgba(238, 245, 239, 0.055)),
    rgba(18, 31, 31, 0.78);
  transform: rotateY(180deg);
}

.destiny-card.is-flipped .card-back {
  transform: rotateY(180deg);
  opacity: 0;
}

.destiny-card.is-flipped .card-front {
  transform: rotateY(360deg);
}

.destiny-card.is-inferred,
.destiny-card.is-selected {
  border-color: color-mix(in srgb, var(--tone-color) 70%, white);
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--tone-color) 35%, transparent),
    0 16px 32px rgba(0, 0, 0, 0.28);
}

.seal {
  display: grid;
  width: 52px;
  height: 52px;
  place-items: center;
  border: 1px solid rgba(238, 245, 239, 0.14);
  border-radius: 16px;
  color: var(--tone-color);
  font-family: var(--qh-font-display);
  font-size: 26px;
  font-weight: 900;
}

.seal.is-blur {
  filter: blur(1px);
  opacity: 0.58;
}

.match-ring {
  display: grid;
  width: 64px;
  height: 64px;
  place-items: center;
  border-radius: 50%;
  background:
    radial-gradient(circle, #172421 54%, transparent 56%),
    conic-gradient(var(--tone-color) var(--score), rgba(238, 245, 239, 0.11) 0);
  color: var(--tone-color);
  font-family: var(--qh-font-display);
  font-weight: 900;
}

.match-ring span::after {
  content: "%";
  font-size: 11px;
}

.card-copy {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.card-copy strong {
  color: var(--qh-text);
  font-size: 17px;
  font-weight: 900;
}

.card-copy small {
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.65;
}

.confirm-panel {
  display: grid;
  gap: 14px;
  padding: 18px;
}

.confirm-panel p:last-child,
.radar-empty {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.75;
}

.confirm-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.mini-radar {
  padding: 16px;
}

.mini-radar__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.mini-radar__head span {
  color: var(--qh-color-gold);
  font-size: 12px;
  font-weight: 900;
}

.mini-radar :deep(.radar-wrap) {
  max-width: 310px;
}

.mini-radar.is-drawing :deep(.radar-data-segment) {
  stroke-dasharray: 260;
  stroke-dashoffset: 260;
  animation: radar-draw 720ms ease forwards;
  animation-delay: calc(var(--segment-index, 0) * 80ms);
}

@keyframes card-rise {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes radar-draw {
  to {
    stroke-dashoffset: 0;
  }
}

@media (max-width: 1080px) {
  .profile-dialogue {
    grid-template-columns: 1fr;
  }

  .dialogue-main {
    min-height: 620px;
  }
}

@media (max-width: 680px) {
  .dialogue-head,
  .answer-form {
    grid-template-columns: 1fr;
  }

  .dialogue-head {
    display: grid;
  }

  .card-grid {
    grid-template-columns: 1fr;
  }

  .answer-form {
    display: grid;
  }
}
</style>
