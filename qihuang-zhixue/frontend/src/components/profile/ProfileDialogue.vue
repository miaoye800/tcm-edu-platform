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

      <div ref="flowRef" class="bubble-flow" aria-live="polite" @scroll="updateFlowPinned">
        <div v-for="message in messages" :key="message.id" :class="['bubble-row', `is-${message.role}`]">
          <img
            v-if="message.role === 'assistant'"
            class="bubble-avatar"
            :src="xiaoqiAvatar"
            alt="药灵·小岐"
          />
          <article :class="['chat-bubble', `is-${message.role}`]">
            <section
              v-if="message.role === 'assistant' && message.reasoning"
              class="message-reasoning"
              :class="{ 'is-streaming': message.reasoningStreaming }"
            >
              <button class="reasoning-toggle" type="button" @click="toggleReasoning(message.id)">
                <span>{{ message.reasoningStreaming ? '小岐正在思考' : reasoningTitle(message) }}</span>
                <small>{{ message.reasoningCollapsed ? '展开' : '收起' }}</small>
              </button>
              <p v-if="!message.reasoningCollapsed">
                {{ message.reasoning }}
                <span v-if="message.reasoningStreaming" class="reasoning-caret" />
              </p>
            </section>
            <span class="speaker">{{ message.role === 'assistant' ? '小岐' : '我' }}</span>
            <p>{{ message.text }}</p>
            <span v-if="message.role === 'assistant' && message.typing" class="typing-caret" />
          </article>
        </div>
      </div>

      <footer class="answer-dock">
        <div class="quick-chips" aria-label="快捷回答">
          <button
            v-for="chip in visibleQuickReplies"
            :key="chip.id"
            class="quick-chip"
            type="button"
            :disabled="disabled"
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
            :disabled="disabled"
            aria-label="自由回答"
          />
          <el-button type="primary" :icon="Promotion" native-type="submit" :disabled="disabled || !draft.trim()">发送</el-button>
        </form>
        <el-button class="finish-dialogue" :icon="Check" type="primary" plain @click="finishDialogue">结束对话</el-button>
      </footer>
    </main>

    <aside class="destiny-panel" aria-label="命格卡推断">
      <section v-if="hasDimensionCards" class="identity-board" aria-label="命格卡牌阵">
        <div class="identity-board__head">
          <div>
            <p class="eyebrow">命格牌阵</p>
            <h3>{{ activeDimensionLabel }}</h3>
          </div>
          <span>{{ formatConfidence(activeIdentityDimension?.confidence ?? 0) }}</span>
        </div>

        <div class="dimension-tabs" role="tablist" aria-label="切换命格维度">
          <button
            v-for="dimension in renderedIdentityCards"
            :key="dimension.key"
            type="button"
            :class="['dimension-tab', `tone-${dimensionTone(dimension.key)}`, { 'is-active': dimension.key === activeIdentityDimension?.key }]"
            role="tab"
            :aria-selected="dimension.key === activeIdentityDimension?.key"
            @click="selectedDimensionKey = dimension.key"
          >
            {{ dimensionLabel(dimension) }}
          </button>
        </div>

        <div class="tarot-card-grid" :class="`kind-${activeIdentityDimension?.kind ?? 'distribution'}`">
          <article
            v-for="card in activeDimensionCards"
            :key="card.id"
            :class="[
              'identity-tarot-card',
              `tone-${dimensionTone(activeIdentityDimension?.key)}`,
              {
                'is-flipped': activeIdentityDimension?.revealed,
                'is-drawing': !activeIdentityDimension?.revealed,
                'is-placeholder': card.placeholder,
              },
            ]"
            :aria-label="identityCardAria(card)"
          >
            <button
              class="card-info"
              type="button"
              aria-label="查看命格牌说明"
              :aria-describedby="tooltipId(card)"
              @click.stop="toggleTooltip(tooltipId(card))"
              @focus="activeTooltipId = tooltipId(card)"
              @blur="closeTooltip"
              @keydown.escape.stop="closeTooltip"
              @touchstart.passive="startInfoPress(tooltipId(card))"
              @touchend.passive="clearInfoPress"
              @touchcancel.passive="clearInfoPress"
            >
              ⓘ
            </button>
            <p
              :id="tooltipId(card)"
              :class="['card-tooltip', { 'is-open': activeTooltipId === tooltipId(card) }]"
              role="tooltip"
            >
              {{ tooltipText(card) }}
            </p>
            <div class="tarot-inner">
              <div class="tarot-face tarot-back">
                <span class="tarot-seal">{{ dimensionSeal(activeIdentityDimension?.key) }}</span>
                <span class="drawing-runes" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                <strong>绘制中</strong>
              </div>
              <div class="tarot-face tarot-front">
                <span class="tarot-kicker">{{ dimensionLabel(activeIdentityDimension) }}</span>
                <strong>{{ cardTitle(card) }}</strong>
                <span v-if="activeIdentityDimension?.kind === 'distribution'" class="card-value">{{ ratioPercent(card.raw) }}</span>
                <span v-else-if="activeIdentityDimension?.kind === 'level'" class="card-value">{{ scoreText(card.raw) }}</span>
                <span v-else class="card-value">薄弱标签</span>
              </div>
            </div>
          </article>
        </div>
      </section>

      <div v-else class="card-grid">
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
          <span>画像完成度 {{ profileCompletionText }}</span>
        </div>
        <RadarChart v-if="radar.length" :axes="radar" />
        <p v-else class="radar-empty">暂无雷达数据时，小岐仍可用模板问题继续取证。</p>
        <div v-if="pendingDimensions.length" class="pending-dimensions" aria-label="待取证维度">
          <span v-for="dimension in pendingDimensions" :key="dimension.key">{{ dimension.label }}待取证</span>
        </div>
        <details class="evidence-panel">
          <summary>
            <span>命格收藏册</span>
            <small>画像完成度 {{ profileCompletionText }}</small>
          </summary>
          <div class="collection-stack" aria-label="翻开的命格牌">
            <span
              v-for="card in revealedArchiveCards"
              :key="`stack-${card.dimensionKey}-${card.cardKey}`"
              class="collection-stack__card"
              :class="`tone-${dimensionTone(card.dimensionKey)}`"
            >
              {{ card.displayName }}
            </span>
            <small v-if="!revealedArchiveCards.length">暂无翻开的命格牌，继续对话后会自动归档。</small>
          </div>
          <div class="evidence-list collection-book">
            <article
              v-for="group in collectionGroups"
              :key="group.key"
              class="evidence-item collection-group"
              :class="{ 'is-pending': group.confidence < 0.5 }"
            >
              <div class="evidence-item__head">
                <strong>{{ group.label }}</strong>
                <span>{{ group.status }} · 置信度{{ formatConfidence(group.confidence) }}</span>
              </div>
              <p>{{ group.value }}</p>
              <div v-if="group.cards.length" class="collection-cards">
                <section v-for="card in group.cards" :key="card.cardKey" class="collection-card">
                  <div>
                    <strong>{{ card.displayName }}</strong>
                    <span>{{ card.metric }}</span>
                  </div>
                  <ul v-if="card.quotes.length">
                    <li v-for="quote in card.quotes" :key="`${card.cardKey}-${quote}`">“{{ quote }}”</li>
                  </ul>
                  <small v-else>暂无可追溯原文引用</small>
                </section>
              </div>
              <template v-else>
                <ul v-if="group.quotes.length">
                  <li v-for="quote in group.quotes" :key="`${group.key}-${quote}`">“{{ quote }}”</li>
                </ul>
                <small v-else>暂无可追溯原文引用</small>
              </template>
            </article>
          </div>
        </details>
      </section>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { Check, Promotion, Switch } from '@element-plus/icons-vue'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import xiaoqiAvatar from '../../assets/mascot/xiaoqi-avatar.png'
import { dialogueQuestionChips } from '../../config/dialogueQuestions'
import type {
  DimensionKey,
  EvidencePanelDimension,
  IdentityCardsDimensionView,
  IdentityDimensionCard,
  ProfileDialogueChatMessage,
  ProfileDialogueStreamUiEvent,
  ProfileProgress,
  RadarAxis,
} from '../../types/profile'
import RadarChart from './RadarChart.vue'
import {
  bestScoredCard,
  cardIsFlipped,
  cardScoreFor,
  createAnswerPayload,
  createConfirmPayload,
  identityCards as legacyIdentityCards,
  type CardScores,
  type IdentityCardName,
} from './profileDialogueModel'
import { dimensionLabelFor, identityCardDescriptionFor } from './profileIdentityCardDescriptions'
import {
  addProfileDialogueAssistantPrompt,
  addProfileDialogueUserMessage,
  applyProfileDialogueIdentityCards,
  applyProfileDialogueStreamEvent,
  createProfileDialogueIdentityCardState,
  createProfileDialogueMessageState,
  ensureProfileDialogueStreamingAssistant,
} from './profileDialogueStreamState'

const props = withDefaults(
  defineProps<{
    prompt: string
    cardScores?: CardScores
    inferredCard?: IdentityCardName | string | null
    radar?: RadarAxis[]
    profileProgress?: ProfileProgress | null
    evidencePanel?: EvidencePanelDimension[]
    identityCardsView?: IdentityCardsDimensionView[]
    activeTarget?: DimensionKey | string | null
    activeTargetLabel?: string | null
    converged?: boolean
    quickReplies?: string[]
    streamEvents?: ProfileDialogueStreamUiEvent[]
    disabled?: boolean
    pending?: boolean
  }>(),
  {
    cardScores: () => ({}),
    inferredCard: null,
    radar: () => [],
    profileProgress: null,
    evidencePanel: () => [],
    identityCardsView: () => [],
    activeTarget: null,
    activeTargetLabel: null,
    converged: false,
    quickReplies: () => [],
    streamEvents: () => [],
    disabled: false,
    pending: false,
  },
)

const emit = defineEmits<{
  answer: [payload: ReturnType<typeof createAnswerPayload>]
  confirm: [payload: ReturnType<typeof createConfirmPayload>]
}>()

const draft = ref('')
const flowRef = ref<HTMLElement | null>(null)
const selectedCard = ref<IdentityCardName | string | null>(props.inferredCard)
const messageState = reactive(createProfileDialogueMessageState())
const identityCardState = reactive(createProfileDialogueIdentityCardState())
const renderedIdentityCards = ref<IdentityCardsDimensionView[]>([])
const messages = computed(() => messageState.messages)
const flowPinned = ref(true)
const selectedDimensionKey = ref<DimensionKey | null>(null)
const activeTooltipId = ref('')
let consumedStreamEventCount = 0
let infoPressTimer: ReturnType<typeof window.setTimeout> | null = null
const flowBottomThreshold = 28

const isFlowNearBottom = () => {
  const flow = flowRef.value
  if (!flow) return true
  return flow.scrollHeight - flow.scrollTop - flow.clientHeight <= flowBottomThreshold
}

const updateFlowPinned = () => {
  flowPinned.value = isFlowNearBottom()
}

const scrollFlow = async (force = false) => {
  await nextTick()
  if (!flowRef.value) return
  if (!force && !flowPinned.value) return
  flowRef.value.scrollTop = flowRef.value.scrollHeight
  flowPinned.value = true
}

const rememberFlowPin = () => {
  flowPinned.value = isFlowNearBottom()
}

const addAssistantPrompt = (prompt: string) => {
  if (addProfileDialogueAssistantPrompt(messageState, prompt)) void scrollFlow(true)
}

const addUserMessage = (text: string) => {
  addProfileDialogueUserMessage(messageState, text)
  void scrollFlow(true)
}

const sendUtterance = () => {
  if (props.disabled) return
  const utterance = draft.value.trim()
  if (!utterance) return
  addUserMessage(utterance)
  startStreamTurn()
  emit('answer', createAnswerPayload('utterance', utterance))
  draft.value = ''
}

const sendQuickReply = (quickReply: string) => {
  if (props.disabled) return
  addUserMessage(quickReply)
  startStreamTurn()
  emit('answer', createAnswerPayload('quick_reply', quickReply))
}

const startStreamTurn = () => {
  ensureProfileDialogueStreamingAssistant(messageState)
  void scrollFlow(true)
}

const applyQueuedStreamEvent = (event: ProfileDialogueStreamUiEvent) => {
  if (event.type === 'thinking' || event.type === 'message') {
    rememberFlowPin()
  }
  const result = applyProfileDialogueStreamEvent(messageState, event)
  if (event.type === 'reset') {
    void scrollFlow(true)
    return
  }
  if (result === 'appended') {
    void scrollFlow()
  }
}

const consumeStreamEventQueue = () => {
  if (consumedStreamEventCount > props.streamEvents.length) consumedStreamEventCount = 0
  while (consumedStreamEventCount < props.streamEvents.length) {
    const event = props.streamEvents[consumedStreamEventCount]
    consumedStreamEventCount += 1
    applyQueuedStreamEvent(event)
  }
}

const appendAssistantDelta = (delta: string) => {
  rememberFlowPin()
  const result = applyProfileDialogueStreamEvent(messageState, {
    id: Number.MAX_SAFE_INTEGER,
    type: 'message',
    delta,
  })
  if (result === 'appended') void scrollFlow()
}

const appendAssistantReasoning = (delta: string) => {
  rememberFlowPin()
  const result = applyProfileDialogueStreamEvent(messageState, {
    id: Number.MAX_SAFE_INTEGER,
    type: 'thinking',
    delta,
  })
  if (result === 'appended') void scrollFlow()
}

const finishStreamingAssistant = () => {
  applyProfileDialogueStreamEvent(messageState, {
    id: Number.MAX_SAFE_INTEGER,
    type: 'done',
  })
}

const toggleReasoning = (messageId: number) => {
  const message = messages.value.find((item) => item.id === messageId)
  if (!message) return
  message.reasoningCollapsed = !message.reasoningCollapsed
  if (!message.reasoningCollapsed) void scrollFlow()
}

const reasoningTitle = (message: ProfileDialogueChatMessage) => {
  if (!message.reasoningDurationMs) return '思考过程'
  return `思考过程 · ${Math.max(1, Math.round(message.reasoningDurationMs / 1000))} 秒`
}

const axisStatus = (axis: RadarAxis): 'dark' | 'dashed' | 'solid' => {
  if (axis.status === 'dark' || axis.placeholder || axis.lit === false) return 'dark'
  if (axis.status === 'dashed' || axis.lowConfidence) return 'dashed'
  return 'solid'
}

const axisStatusLabel = (axis: RadarAxis) => {
  const status = axisStatus(axis)
  if (status === 'dark') return '待聊到'
  if (status === 'dashed') return `暂定 ${axis.value}`
  return `${axis.value}`
}

const profileCompletionText = computed(() => {
  const total = props.profileProgress?.total || props.radar.length || 7
  const collected = props.profileProgress?.collected ?? props.radar.filter((axis) => !axis.lowConfidence && axis.status !== 'dark').length
  return `${collected}/${total}`
})

const pendingDimensions = computed(() =>
  props.profileProgress?.dimensions.filter((dimension) => !dimension.collected) ??
  props.radar
    .filter((axis) => axis.lowConfidence || axis.status === 'dark')
    .map((axis) => ({ key: axis.key, label: axis.sourceLabel || axis.label })),
)

const visibleEvidencePanel = computed(() => props.evidencePanel.length ? props.evidencePanel : [])

const formatConfidence = (confidence: number) => `${Math.round(Math.max(0, Math.min(1, confidence)) * 100)}%`

type CollectionQuote = {
  text: string
}

type ArchivedIdentityCard = {
  dimensionKey: DimensionKey
  dimensionLabel: string
  cardKey: string
  displayName: string
  metric: string
  quotes: string[]
}

type CollectionGroup = {
  key: DimensionKey
  label: string
  value: string
  confidence: number
  status: string
  cards: ArchivedIdentityCard[]
  quotes: string[]
}

const evidenceByDimension = computed(() => {
  const entries = new Map<DimensionKey, EvidencePanelDimension>()
  props.evidencePanel.forEach((item) => entries.set(item.key, item))
  return entries
})

const uniqueTexts = (texts: string[]) => Array.from(new Set(texts.map((text) => text.trim()).filter(Boolean)))

const evidenceQuoteTexts = (item?: EvidencePanelDimension): string[] =>
  uniqueTexts((item?.quotes ?? []).map((quote: CollectionQuote) => quote.text))

const cardQuoteTexts = (card: IdentityDimensionCard, fallback?: EvidencePanelDimension): string[] => {
  const ownQuotes = uniqueTexts((card.quotes ?? []).map(String))
  return ownQuotes.length ? ownQuotes : evidenceQuoteTexts(fallback).slice(0, 2)
}

const cardMetric = (dimension: IdentityCardsDimensionView, card: IdentityDimensionCard) => {
  if (dimension.kind === 'distribution') return ratioPercent(card)
  if (dimension.kind === 'level') {
    const level = card.level_name || card.display_name || '等级'
    return `${level} · ${scoreText(card)}`
  }
  return card.label || card.display_name || '薄弱标签'
}

const archivedCardsFor = (dimension: IdentityCardsDimensionView): ArchivedIdentityCard[] => {
  const fallback = evidenceByDimension.value.get(dimension.key)
  return dimension.cards
    .filter((card) => !card.placeholder)
    .map((card, index) => ({
      dimensionKey: dimension.key,
      dimensionLabel: dimensionLabel(dimension),
      cardKey: `${dimension.key}-${card.display_name ?? card.level_name ?? card.label ?? card.canonical_label ?? index}`,
      displayName: card.display_name || card.level_name || card.label || card.canonical_label || '未命名命格牌',
      metric: cardMetric(dimension, card),
      quotes: cardQuoteTexts(card, fallback),
    }))
}

const revealedArchiveCards = computed(() =>
  renderedIdentityCards.value.flatMap((dimension) => (dimension.revealed ? archivedCardsFor(dimension) : [])),
)

const collectionGroups = computed<CollectionGroup[]>(() => {
  const identityGroups = renderedIdentityCards.value
    .filter((dimension) => dimension.revealed)
    .map((dimension) => {
      const fallback = evidenceByDimension.value.get(dimension.key)
      return {
        key: dimension.key,
        label: dimensionLabel(dimension),
        value: fallback?.value ?? (dimension.kind === 'level' ? '数值维度' : dimension.kind === 'tags' ? '标签维度' : '分布维度'),
        confidence: dimension.confidence,
        status: fallback?.status ?? (dimension.confidence >= 0.5 ? '宸插彇璇?' : '寰呭彇璇?'),
        cards: archivedCardsFor(dimension),
        quotes: evidenceQuoteTexts(fallback),
      }
    })

  const revealedKeys = new Set(identityGroups.map((group) => group.key))
  const evidenceOnlyGroups = props.evidencePanel
    .filter((item) => !revealedKeys.has(item.key))
    .map((item) => ({
      key: item.key,
      label: item.label,
      value: item.value,
      confidence: item.confidence,
      status: item.status,
      cards: [],
      quotes: evidenceQuoteTexts(item),
    }))

  return [...identityGroups, ...evidenceOnlyGroups]
})

type RenderedIdentityCard = {
  id: string
  raw: IdentityDimensionCard
  placeholder: boolean
  index: number
}

const hasDimensionCards = computed(() => renderedIdentityCards.value.length > 0)

const dimensionLabel = (dimension?: IdentityCardsDimensionView | null) => {
  if (!dimension) return props.activeTargetLabel || '命格卡'
  return dimension.target_label || dimensionLabelFor(dimension.key)
}

const activeDimensionLabel = computed(() => dimensionLabel(activeIdentityDimension.value))

const activeIdentityDimension = computed<IdentityCardsDimensionView | null>(() => {
  if (!renderedIdentityCards.value.length) return null
  let target: IdentityCardsDimensionView | undefined
  if (props.activeTarget) {
    target = renderedIdentityCards.value.find((dimension) => dimension.key === props.activeTarget)
  }
  if (target && !selectedDimensionKey.value) return target
  return (
    renderedIdentityCards.value.find((dimension) => dimension.key === selectedDimensionKey.value) ??
    target ??
    renderedIdentityCards.value.find((dimension) => !dimension.revealed) ??
    renderedIdentityCards.value[0]
  )
})

const activeDimensionCards = computed<RenderedIdentityCard[]>(() => {
  const dimension = activeIdentityDimension.value
  if (!dimension) return []
  const cards = dimension.cards.length ? dimension.cards : placeholderCardsFor(dimension)
  return cards.map((card, index) => ({
    id: `${dimension.key}-${card.display_name ?? card.level_name ?? card.label ?? card.canonical_label ?? 'slot'}-${index}`,
    raw: card,
    placeholder: Boolean(card.placeholder),
    index,
  }))
})

const placeholderCardsFor = (dimension: IdentityCardsDimensionView): IdentityDimensionCard[] => {
  const countByDimension: Partial<Record<DimensionKey, number>> = {
    learning_goal: 5,
    motivation: 4,
    resource_preference: 4,
    learning_style: 3,
    knowledge_base: 1,
    pace: 1,
    weak_points: 1,
  }
  const count = countByDimension[dimension.key] ?? (dimension.kind === 'level' ? 1 : 3)
  return Array.from({ length: count }, (_, index) => ({ placeholder: true, label: `slot-${index + 1}` }))
}

const dimensionTone = (dimension?: DimensionKey | null): 'jade' | 'gold' | 'blue' | 'coral' => {
  if (dimension === 'learning_goal' || dimension === 'motivation') return 'gold'
  if (dimension === 'resource_preference' || dimension === 'learning_style') return 'blue'
  if (dimension === 'weak_points') return 'coral'
  return 'jade'
}

const dimensionSeal = (dimension?: DimensionKey | null) => {
  const seals: Partial<Record<DimensionKey, string>> = {
    learning_goal: '志',
    motivation: '愿',
    resource_preference: '器',
    learning_style: '法',
    knowledge_base: '基',
    pace: '时',
    weak_points: '隙',
  }
  return dimension ? (seals[dimension] ?? '命') : '命'
}

const cardTitle = (card: RenderedIdentityCard) =>
  card.raw.display_name || card.raw.level_name || card.raw.label || card.raw.canonical_label || '待显影'

const ratioPercent = (card: IdentityDimensionCard) => `${Math.round(Math.max(0, Math.min(1, Number(card.ratio) || 0)) * 100)}%`

const scoreText = (card: IdentityDimensionCard) => {
  const score = Number(card.score)
  return Number.isFinite(score) ? `${Math.round(score)} 分` : '待取证'
}

const tooltipId = (card: RenderedIdentityCard) => `identity-card-tip-${card.id}`

const tooltipText = (card: RenderedIdentityCard) => {
  const dimension = activeIdentityDimension.value
  if (!dimension) return '命格牌说明'
  return identityCardDescriptionFor(dimension.key, dimension.kind, card.raw)
}

const identityCardAria = (card: RenderedIdentityCard) => {
  const dimension = activeIdentityDimension.value
  const label = dimensionLabel(dimension)
  if (!dimension?.revealed) return `${label}命格牌，背面朝上，绘制中`
  return `${label}命格牌，${cardTitle(card)}，${dimension.kind === 'distribution' ? ratioPercent(card.raw) : dimension.kind === 'level' ? scoreText(card.raw) : '薄弱标签'}`
}

const toggleTooltip = (id: string) => {
  activeTooltipId.value = activeTooltipId.value === id ? '' : id
}

const closeTooltip = () => {
  activeTooltipId.value = ''
}

const startInfoPress = (id: string) => {
  clearInfoPress()
  infoPressTimer = window.setTimeout(() => {
    activeTooltipId.value = id
  }, 420)
}

const clearInfoPress = () => {
  if (!infoPressTimer) return
  window.clearTimeout(infoPressTimer)
  infoPressTimer = null
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

const finishDialogue = () => {
  emit('confirm', createConfirmPayload(selectedCard.value ?? props.inferredCard ?? null))
}

const displayCards = computed(() =>
  legacyIdentityCards.map((card, index) => ({
    ...card,
    index,
    score: cardScoreFor(card.name, props.cardScores),
    flipped: cardIsFlipped(card.name, props.cardScores),
    inferred: props.converged && props.inferredCard === card.name,
    selected: selectedCard.value === card.name,
  })),
)

const visibleQuickReplies = computed(() => {
  if (props.quickReplies.length > 0) {
    return props.quickReplies.map((reply, index) => ({
      id: `backend-${index}-${reply}`,
      label: reply,
      quickReply: reply,
    }))
  }
  return dialogueQuestionChips
})

const selectedMeta = computed(() => {
  const fallback = props.inferredCard ?? bestScoredCard(props.cardScores)
  return legacyIdentityCards.find((card) => card.name === (selectedCard.value ?? fallback)) ?? legacyIdentityCards[0]
})

watch(
  () => props.prompt,
  (prompt) => {
    if (messages.value.length === 0) addAssistantPrompt(prompt)
  },
  { immediate: true },
)

watch(
  () => props.pending,
  (pending) => {
    if (pending) ensureProfileDialogueStreamingAssistant(messageState)
  },
)

watch(
  () => props.streamEvents.length,
  () => {
    consumeStreamEventQueue()
  },
)

watch(
  () => props.inferredCard,
  (card) => {
    if (card) selectedCard.value = card
  },
)

watch(
  () => props.identityCardsView,
  (cards) => {
    renderedIdentityCards.value = applyProfileDialogueIdentityCards(identityCardState, cards)
    if (
      selectedDimensionKey.value &&
      !renderedIdentityCards.value.some((dimension) => dimension.key === selectedDimensionKey.value)
    ) {
      selectedDimensionKey.value = null
    }
  },
  { immediate: true, deep: true },
)

watch(
  () => props.activeTarget,
  (target) => {
    if (target && renderedIdentityCards.value.some((dimension) => dimension.key === target)) {
      selectedDimensionKey.value = null
    }
  },
)

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
  grid-template-rows: auto minmax(0, 1fr) auto;
  height: clamp(560px, calc(100dvh - 176px), 720px);
  min-height: 560px;
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
  overflow-x: hidden;
  overflow-y: auto;
}

.bubble-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
}

.bubble-row.is-assistant {
  justify-content: flex-start;
}

.bubble-row.is-user {
  justify-content: flex-end;
}

.bubble-avatar {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 42%, transparent);
  border-radius: 50%;
  background: color-mix(in srgb, var(--qh-color-jade) 16%, transparent);
  object-fit: cover;
}

.chat-bubble {
  max-width: min(620px, 88%);
  padding: 14px 16px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 18px;
  background: rgba(238, 245, 239, 0.055);
}

.message-reasoning {
  display: grid;
  gap: 8px;
  margin: 0 0 10px;
  padding: 10px 12px;
  border: 1px solid rgba(159, 182, 214, 0.18);
  border-radius: 12px;
  background: rgba(159, 182, 214, 0.08);
}

.reasoning-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 0;
  border: 0;
  color: rgba(238, 245, 239, 0.78);
  background: transparent;
  cursor: pointer;
  font-weight: 900;
  text-align: left;
}

.reasoning-toggle small {
  flex: 0 0 auto;
  color: var(--qh-color-blue);
  font-size: 12px;
}

.message-reasoning p {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.reasoning-caret {
  display: inline-block;
  width: 7px;
  height: 1em;
  margin-left: 3px;
  vertical-align: text-bottom;
  border-radius: 4px;
  background: var(--qh-color-blue);
  animation: pulse 1s ease-in-out infinite;
}

.chat-bubble.is-assistant {
  border-top-left-radius: 8px;
  background:
    linear-gradient(135deg, rgba(143, 208, 173, 0.14), rgba(159, 182, 214, 0.08)),
    rgba(238, 245, 239, 0.045);
}

.chat-bubble.is-user {
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

.chat-bubble .message-reasoning p {
  display: block;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
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

.finish-dialogue {
  justify-self: end;
  min-width: 112px;
}

.destiny-panel {
  display: grid;
  gap: 14px;
  align-content: start;
  padding: 18px;
}

.identity-board {
  display: grid;
  gap: 12px;
}

.identity-board__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.identity-board__head span {
  color: var(--qh-color-gold);
  font-size: 12px;
  font-weight: 900;
}

.dimension-tabs {
  display: flex;
  gap: 7px;
  overflow-x: auto;
  padding-bottom: 2px;
}

.dimension-tab {
  --tone-color: var(--qh-color-jade);
  flex: 0 0 auto;
  min-height: 32px;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, var(--tone-color) 22%, transparent);
  border-radius: 999px;
  color: rgba(238, 245, 239, 0.76);
  background: color-mix(in srgb, var(--tone-color) 9%, transparent);
  cursor: pointer;
  font-size: 12px;
  font-weight: 900;
}

.dimension-tab.tone-gold,
.identity-tarot-card.tone-gold {
  --tone-color: var(--qh-color-gold);
}

.dimension-tab.tone-blue,
.identity-tarot-card.tone-blue {
  --tone-color: var(--qh-color-blue);
}

.dimension-tab.tone-coral,
.identity-tarot-card.tone-coral {
  --tone-color: var(--qh-color-coral);
}

.dimension-tab.is-active {
  border-color: color-mix(in srgb, var(--tone-color) 68%, white);
  color: var(--qh-text);
  background: color-mix(in srgb, var(--tone-color) 18%, transparent);
}

.dimension-tab:focus-visible,
.card-info:focus-visible {
  @include focus-ring;
}

.tarot-card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.tarot-card-grid.kind-level {
  grid-template-columns: 1fr;
}

.identity-tarot-card {
  --tone-color: var(--qh-color-jade);
  position: relative;
  min-height: 178px;
  perspective: 1100px;
}

.tarot-inner {
  display: grid;
  min-height: 178px;
  transform-style: preserve-3d;
  transition: transform 680ms cubic-bezier(0.2, 0.72, 0.18, 1);
}

.identity-tarot-card.is-flipped .tarot-inner {
  transform: rotateY(180deg);
}

.tarot-face {
  grid-area: 1 / 1;
  display: grid;
  min-height: 178px;
  align-content: center;
  justify-items: center;
  gap: 10px;
  padding: 18px 14px;
  border: 1px solid color-mix(in srgb, var(--tone-color) 22%, transparent);
  border-radius: 8px;
  overflow: hidden;
  backface-visibility: hidden;
  box-shadow: inset 0 0 0 1px rgba(238, 245, 239, 0.04);
}

.tarot-back {
  color: rgba(238, 245, 239, 0.64);
  background:
    radial-gradient(circle at 50% 28%, color-mix(in srgb, var(--tone-color) 22%, transparent), transparent 36%),
    repeating-linear-gradient(45deg, rgba(238, 245, 239, 0.035) 0 2px, transparent 2px 10px),
    linear-gradient(145deg, rgba(19, 33, 31, 0.96), rgba(8, 14, 16, 0.9));
}

.tarot-front {
  transform: rotateY(180deg);
  color: var(--qh-text);
  background:
    radial-gradient(circle at 18% 18%, color-mix(in srgb, var(--tone-color) 28%, transparent), transparent 36%),
    linear-gradient(145deg, rgba(238, 245, 239, 0.12), rgba(18, 31, 31, 0.9));
}

.identity-tarot-card.is-flipped .tarot-front::after {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(110deg, transparent 20%, rgba(255, 255, 255, 0.18) 45%, transparent 70%);
  animation: card-sheen 900ms ease both;
  content: "";
}

.tarot-seal {
  display: grid;
  width: 54px;
  height: 54px;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--tone-color) 48%, transparent);
  border-radius: 50%;
  color: var(--tone-color);
  font-family: var(--qh-font-display);
  font-size: 26px;
  font-weight: 900;
}

.drawing-runes {
  display: flex;
  gap: 5px;
}

.drawing-runes i {
  width: 6px;
  height: 24px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--tone-color) 52%, transparent);
  animation: rune-draw 980ms ease-in-out infinite;
}

.drawing-runes i:nth-child(2) {
  animation-delay: 140ms;
}

.drawing-runes i:nth-child(3) {
  animation-delay: 280ms;
}

.tarot-back strong,
.tarot-front strong {
  max-width: 100%;
  overflow-wrap: anywhere;
  text-align: center;
  font-weight: 900;
}

.tarot-kicker {
  color: var(--tone-color);
  font-size: 12px;
  font-weight: 900;
}

.card-value {
  color: rgba(238, 245, 239, 0.78);
  font-family: var(--qh-font-display);
  font-size: 20px;
  font-weight: 900;
}

.card-info {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 5;
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid rgba(238, 245, 239, 0.18);
  border-radius: 50%;
  color: rgba(238, 245, 239, 0.8);
  background: rgba(10, 18, 20, 0.68);
  cursor: help;
  font-size: 14px;
}

.card-tooltip {
  position: absolute;
  z-index: 6;
  top: 40px;
  right: 8px;
  width: min(260px, calc(100% - 16px));
  margin: 0;
  padding: 10px 12px;
  border: 1px solid rgba(238, 245, 239, 0.16);
  border-radius: 8px;
  color: rgba(238, 245, 239, 0.88);
  background: rgba(9, 16, 18, 0.96);
  box-shadow: 0 12px 26px rgba(0, 0, 0, 0.36);
  font-size: 12px;
  line-height: 1.65;
  opacity: 0;
  pointer-events: none;
  transform: translateY(-4px);
  transition:
    opacity 160ms ease,
    transform 160ms ease;
}

.card-info:hover + .card-tooltip,
.card-info:focus-visible + .card-tooltip,
.card-tooltip.is-open {
  opacity: 1;
  transform: translateY(0);
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

.pending-dimensions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 10px;
}

.pending-dimensions span {
  padding: 5px 8px;
  border: 1px solid rgba(159, 182, 214, 0.16);
  border-radius: 999px;
  color: rgba(223, 236, 228, 0.58);
  background: rgba(159, 182, 214, 0.07);
  font-size: 12px;
  font-weight: 900;
}

.evidence-panel {
  margin-top: 12px;
  border-top: 1px solid rgba(143, 208, 173, 0.1);
}

.evidence-panel summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 0 8px;
  color: var(--qh-color-jade);
  cursor: pointer;
  font-size: 13px;
  font-weight: 900;
}

.evidence-panel summary small {
  flex: 0 0 auto;
  color: var(--qh-color-gold);
  font-size: 12px;
}

.collection-stack {
  display: flex;
  min-height: 42px;
  align-items: center;
  padding: 4px 0 10px 12px;
  overflow-x: auto;
}

.collection-stack small {
  margin-left: -12px;
  color: var(--qh-text-muted);
  font-size: 12px;
}

.collection-stack__card {
  --tone-color: var(--qh-color-jade);
  flex: 0 0 auto;
  max-width: 130px;
  min-width: 74px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--tone-color) 28%, transparent);
  border-radius: 8px;
  overflow: hidden;
  color: rgba(238, 245, 239, 0.88);
  background: color-mix(in srgb, var(--tone-color) 12%, transparent);
  box-shadow: -10px 0 18px rgba(0, 0, 0, 0.18);
  font-size: 12px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.collection-stack__card + .collection-stack__card {
  margin-left: -12px;
}

.collection-stack__card.tone-gold {
  --tone-color: var(--qh-color-gold);
}

.collection-stack__card.tone-blue {
  --tone-color: var(--qh-color-blue);
}

.collection-stack__card.tone-coral {
  --tone-color: var(--qh-color-coral);
}

.evidence-list {
  display: grid;
  gap: 9px;
  max-height: 260px;
  padding-right: 4px;
  overflow-y: auto;
}

.collection-book {
  max-height: 340px;
}

.evidence-item {
  display: grid;
  gap: 7px;
  padding: 10px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 8px;
  background: rgba(238, 245, 239, 0.045);
}

.evidence-item.is-pending {
  opacity: 0.58;
}

.evidence-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.evidence-item__head strong {
  color: var(--qh-text);
  font-size: 13px;
}

.evidence-item__head span,
.evidence-item small {
  color: var(--qh-text-muted);
  font-size: 12px;
}

.evidence-item p {
  margin: 0;
  color: rgba(238, 245, 239, 0.78);
  font-size: 12px;
  line-height: 1.6;
}

.evidence-item ul {
  display: grid;
  gap: 5px;
  margin: 0;
  padding-left: 16px;
  color: rgba(238, 245, 239, 0.72);
  font-size: 12px;
  line-height: 1.55;
}

.collection-cards {
  display: grid;
  gap: 8px;
}

.collection-card {
  display: grid;
  gap: 6px;
  padding: 8px;
  border: 1px solid rgba(238, 245, 239, 0.08);
  border-radius: 8px;
  background: rgba(9, 16, 18, 0.16);
}

.collection-card > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.collection-card strong {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--qh-text);
  font-size: 12px;
}

.collection-card span {
  flex: 0 0 auto;
  color: var(--qh-color-gold);
  font-size: 12px;
  font-weight: 900;
}

.radar-axis-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 12px;
}

.radar-axis-chip {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 12px;
  background: rgba(238, 245, 239, 0.055);
}

.radar-axis-chip span {
  min-width: 0;
  overflow: hidden;
  color: rgba(238, 245, 239, 0.82);
  font-size: 12px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.radar-axis-chip strong {
  flex: 0 0 auto;
  color: var(--qh-color-jade);
  font-size: 12px;
}

.radar-axis-chip.is-dark {
  border-color: rgba(223, 236, 228, 0.08);
  opacity: 0.54;
}

.radar-axis-chip.is-dark strong {
  color: rgba(223, 236, 228, 0.58);
}

.radar-axis-chip.is-dashed strong {
  color: var(--qh-color-blue);
}

@keyframes card-rise {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes rune-draw {
  0%,
  100% {
    opacity: 0.26;
    transform: scaleY(0.58);
  }

  50% {
    opacity: 1;
    transform: scaleY(1);
  }
}

@keyframes card-sheen {
  from {
    transform: translateX(-120%);
  }

  to {
    transform: translateX(120%);
  }
}

@keyframes radar-draw {
  to {
    stroke-dashoffset: 0;
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.34;
  }

  50% {
    opacity: 1;
  }
}

@media (max-width: 1080px) {
  .profile-dialogue {
    grid-template-columns: 1fr;
  }

  .dialogue-main {
    height: clamp(520px, calc(100dvh - 148px), 680px);
    min-height: 520px;
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

  .dialogue-main {
    height: clamp(460px, calc(100dvh - 128px), 620px);
    min-height: 460px;
  }

  .card-grid {
    grid-template-columns: 1fr;
  }

  .tarot-card-grid {
    grid-template-columns: 1fr;
  }

  .answer-form {
    display: grid;
  }

  .finish-dialogue {
    justify-self: stretch;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tarot-inner,
  .card-tooltip {
    transition: none;
  }

  .identity-tarot-card.is-flipped .tarot-inner {
    transform: none;
  }

  .identity-tarot-card.is-flipped .tarot-back {
    display: none;
  }

  .identity-tarot-card.is-flipped .tarot-front {
    transform: none;
  }

  .identity-tarot-card.is-flipped .tarot-front::after,
  .drawing-runes i {
    animation: none;
  }
}
</style>
