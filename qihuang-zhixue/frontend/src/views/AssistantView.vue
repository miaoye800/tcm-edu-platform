<template>
  <main class="assistant">
    <aside class="assistant-side" aria-label="会话列表">
      <button class="side-new" type="button" @click="newConversation">＋ 新对话</button>
      <div class="side-list">
        <button
          v-for="conversation in conversations"
          :key="conversation.conversation_id"
          type="button"
          class="side-item"
          :class="{ 'is-active': conversation.conversation_id === activeId }"
          @click="openConversation(conversation.conversation_id)"
        >
          <span class="side-item-title">{{ conversation.title || '新对话' }}</span>
          <span class="side-item-time">{{ shortTime(conversation.updated_at) }}</span>
        </button>
        <p v-if="!conversations.length" class="side-empty">还没有对话。右边说一句就开始了。</p>
      </div>
      <p class="side-foot">小岐 · 岐黄智学</p>
    </aside>

    <section class="assistant-main">
      <header class="assistant-head">
        <div>
          <h1>{{ activeTitle || 'AI 服务助手' }}</h1>
          <p>能生成导图、幻灯、文稿；能查教材关系；生成前先给方案。</p>
        </div>
        <button class="head-artifacts" type="button" :aria-expanded="railOpen" @click="toggleRail">
          本次产出 {{ artifacts.length ? `· ${artifacts.length}` : '' }}
        </button>
      </header>

      <div ref="scroller" class="assistant-scroll">
        <AssistantEmpty v-if="!items.length" @ask="send" />
        <div v-else class="assistant-thread">
          <MessageItem
            v-for="item in items"
            :key="item.id"
            :data-item-id="item.id"
            :item="item"
            :plan-busy="Boolean(item.content.type === 'plan' && planBusy[item.content.taskId])"
            :progress="progress"
            @confirm-plan="confirmPlan"
            @retry="retry"
            @action="goAction"
          />
        </div>
      </div>

      <p v-if="banner" class="assistant-banner">{{ banner }}</p>

      <ComposerBar
        :busy="busy"
        :resources="resourceOptions"
        :refs-loading="refsLoading"
        @send="send"
        @stop="stop"
        @look-resources="loadResources"
      />
    </section>

    <aside v-if="railOpen" class="assistant-rail" aria-label="本次产出">
      <p class="rail-title">本次产出</p>
      <ul v-if="artifacts.length" class="rail-list">
        <li v-for="artifact in artifacts" :key="artifact.artifact_id || artifact.task_id">
          <button type="button" class="rail-item" @click="openArtifact(artifact)">
            <span class="rail-kind">{{ kindLabel(artifact.kind) }}</span>
            <span class="rail-time">{{ shortTime(artifact.created_at) }}</span>
            <span class="rail-state" :data-state="artifact.state">{{ stateLabel(artifact.state) }}</span>
          </button>
        </li>
      </ul>
      <p v-else class="rail-empty">还没有产物。生成完成后会列在这里。</p>
    </aside>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AssistantEmpty from '../components/assistant/AssistantEmpty.vue'
import ComposerBar from '../components/assistant/ComposerBar.vue'
import MessageItem from '../components/assistant/MessageItem.vue'
import {
  cancelRun,
  confirmAssistantPlan,
  getConversation,
  listConversations,
  streamAssistantArtifact,
  streamAssistantTurn,
} from '../services/assistantApi'
import { listResources } from '../services/resourceApi'
import type {
  ArtifactSummary,
  AssistantEvent,
  AssistantMessage,
  ChatItem,
  ConversationSummary,
  StepProgress,
  ToolStep,
} from '../types/assistant'
import type { ResourcePlan } from '../types/resource'

const router = useRouter()

const conversations = ref<ConversationSummary[]>([])
const items = ref<ChatItem[]>([])
const artifacts = ref<ArtifactSummary[]>([])
const activeId = ref('')
const activeTitle = ref('')
const busy = ref(false)
const railOpen = ref(false)
/** 用户手动开合过右栏之后，就不再替他们自动展开。 */
const railTouched = ref(false)
const banner = ref('')
const progress = ref<Record<string, StepProgress>>({})
const planBusy = ref<Record<string, boolean>>({})
const scroller = ref<HTMLElement>()

let runId = ''
let abort: AbortController | null = null
/** 当前这条助手消息在 items 里的下标。流式追加只改它。 */
let cursor = -1

const uid = () => `local_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`

const scrollToBottom = async () => {
  await nextTick()
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

/**
 * 把某条消息顶到可视区顶部。
 *
 * 方案卡是这页里最高的一块（六个单元加页脚，能顶满一屏）。如果照常滚到底，
 * 用户看到的是第 5、6 单元和确认按钮，卡头写着「这份方案讲什么」反而在屏幕外——
 * 于是「确认」变成盲签。
 */
const scrollToItem = async (id: string) => {
  await nextTick()
  const el = scroller.value
  const target = el?.querySelector<HTMLElement>(`[data-item-id="${id}"]`)
  if (!el || !target) return
  el.scrollTo({ top: Math.max(target.offsetTop - 8, 0), behavior: 'smooth' })
}

const toggleRail = () => {
  railTouched.value = true
  railOpen.value = !railOpen.value
}

const shortTime = (iso: string) => {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const minutes = (Date.now() - date.getTime()) / 60000
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))} 分钟前`
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} 小时前`
  return `${date.getMonth() + 1}/${date.getDate()}`
}

const kindLabel = (kind: string) => ({ mindmap: '导图', ppt: '幻灯', doc: '文稿', quiz: '试卷', story: '漫剧' }[kind] || '产物')

type ResourceOption = { id: string; title: string; type: string }
const resourceOptions = ref<ResourceOption[]>([])
const refsLoading = ref(false)

/** 打「@」时才拉一次资源清单。进页面就拉，是拿一次没人要的请求换首屏更慢。 */
const loadResources = async () => {
  if (refsLoading.value || resourceOptions.value.length) return
  refsLoading.value = true
  try {
    const result = await listResources({ limit: 20 })
    resourceOptions.value = (result.items || []).map((item) => ({ id: item.id, title: item.title, type: item.type }))
  } catch {
    // 拿不到清单不影响发言：@ 候选会显示「没找到」，纯输入照旧可用。
  } finally {
    refsLoading.value = false
  }
}
const stateLabel = (state: string) =>
  ({ accepted: '生成中', awaiting_plan: '待确认', done: '已完成', failed: '没炼成' }[state] || state)

const refreshConversations = async () => {
  try {
    const result = await listConversations()
    conversations.value = result.items
    // 标题由后端按第一句话自动取，这里跟一次，免得页头一直写着「AI 服务助手」。
    const current = result.items.find((item) => item.conversation_id === activeId.value)
    if (current?.title) activeTitle.value = current.title
  } catch {
    // 列表拿不到不该打断对话：当前这一屏仍然是可用的。
  }
}

/** 动作按钮点下去才跳。都是站内地址，不新开窗。 */
const goAction = (url: string) => {
  if (!url) return
  void router.push(url)
}

const toItem = (message: AssistantMessage): ChatItem => ({
  id: message.message_id,
  role: message.role === 'user' ? 'user' : 'assistant',
  content: message.content,
  steps: 'steps' in message.content ? message.content.steps : undefined,
})

const openConversation = async (id: string) => {
  if (busy.value) return
  const detail = await getConversation(id)
  activeId.value = detail.conversation_id
  activeTitle.value = detail.title
  artifacts.value = detail.artifacts || []
  if (!railTouched.value) railOpen.value = (detail.artifacts || []).length > 0
  items.value = (detail.messages || []).filter((message) => message.role !== 'tool').map(toItem)
  await scrollToBottom()
}

const newConversation = () => {
  if (busy.value) return
  activeId.value = ''
  activeTitle.value = ''
  items.value = []
  artifacts.value = []
  progress.value = {}
  banner.value = ''
}

const upsertArtifact = (payload: Record<string, unknown>, state: ArtifactSummary['state']) => {
  const taskId = String(payload.task_id || '')
  if (!taskId) return
  // 右栏默认不占位（空着只是白掉 196px），有第一份产物才让出来。
  if (!railTouched.value) railOpen.value = true
  const existing = artifacts.value.find((item) => item.task_id === taskId)
  if (existing) {
    existing.state = state
    existing.resource_id = String(payload.resource_id || existing.resource_id || '')
    existing.open_url = String(payload.open_url || existing.open_url || '')
    return
  }
  artifacts.value = [
    ...artifacts.value,
    {
      artifact_id: '',
      run_id: runId,
      call_id: String(payload.call_id || ''),
      capability: String(payload.capability || ''),
      task_id: taskId,
      resource_id: String(payload.resource_id || ''),
      kind: String(payload.kind || 'resource'),
      open_url: String(payload.open_url || ''),
      state,
      created_at: '',
    },
  ]
}

const currentAssistant = (): ChatItem | null => {
  const item = items.value[cursor]
  return item && item.role === 'assistant' ? item : null
}

const applyEvent = (event: AssistantEvent) => {
  const payload = event.payload || {}
  // 方案卡 / 产物卡出现时要把它顶到顶部而不是滚到底，理由见 scrollToItem。
  let pinTo = ''
  switch (event.event) {
    case 'message_start': {
      // 第一句话之后后端才建出会话，左栏要跟着长出一条；不然用户看到的是
      // 「还没有对话」，而右边已经聊了三轮。
      const fresh = !activeId.value
      activeId.value = String(payload.conversation_id || activeId.value)
      runId = String(payload.run_id || runId)
      if (fresh) void refreshConversations()
      break
    }
    case 'token': {
      const item = currentAssistant()
      if (item && item.content.type === 'text') {
        item.content.text += String(payload.text || '')
      }
      break
    }
    case 'tool_start': {
      const item = currentAssistant()
      if (!item) break
      const step: ToolStep = {
        call_id: String(payload.call_id || ''),
        tool: String(payload.tool || ''),
        capability: String(payload.capability || ''),
        title: String(payload.title || ''),
        args: (payload.args as Record<string, unknown>) || {},
        state: 'running',
      }
      item.steps = [...(item.steps || []), step]
      progress.value = { ...progress.value, [step.call_id]: { index: 0, total: 0, label: '' } }
      break
    }
    case 'tool_progress': {
      const callId = String(payload.call_id || '')
      progress.value = {
        ...progress.value,
        [callId]: {
          index: Number(event.step_index || 0),
          total: Number(event.total_steps || 0),
          label: String(event.step_label || ''),
        },
      }
      break
    }
    case 'tool_result': {
      const callId = String(payload.call_id || '')
      const item = currentAssistant()
      const step = item?.steps?.find((entry) => entry.call_id === callId)
      if (step) {
        step.state = payload.ok ? 'ok' : 'failed'
        step.summary = String(payload.summary || '')
        step.error_code = String(payload.error_code || '')
        step.remedy = String(payload.remedy || '')
        step.task_id = String(payload.task_id || '')
      }
      break
    }
    case 'plan_card': {
      const taskId = String(payload.task_id || '')
      items.value = [
        ...items.value,
        {
          id: `plan_${taskId}`,
          role: 'assistant',
          content: {
            type: 'plan',
            taskId,
            capability: String(payload.capability || ''),
            plan: payload.plan as ResourcePlan,
            design: (payload.design as Record<string, unknown>) || {},
          },
        },
      ]
      upsertArtifact(payload, 'awaiting_plan')
      pinTo = `plan_${taskId}`
      break
    }
    case 'artifact': {
      upsertArtifact(payload, 'done')
      // 快照型产物（plan.present）没有 task_id，用 resource_id 做键，
      // 否则同一轮导两张图会互相覆盖。
      pinTo = `artifact_${String(payload.task_id || payload.resource_id || '')}`
      items.value = [
        ...items.value,
        {
          id: pinTo,
          role: 'assistant',
          content: {
            type: 'artifact',
            taskId: String(payload.task_id || ''),
            capability: String(payload.capability || ''),
            kind: String(payload.kind || 'resource'),
            title: String(payload.title || ''),
            resourceId: String(payload.resource_id || ''),
            openUrl: String(payload.open_url || ''),
            verify: (payload.verify as Record<string, unknown>) || {},
          },
        },
      ]
      break
    }
    case 'ui_actions': {
      // 定位成功的动作按钮。写进当前这条消息（刷新后从库里回来还是同一份）。
      const item = currentAssistant()
      if (!item || item.content.type !== 'text') break
      item.content.ui_actions = (payload.actions as { label: string; url: string }[]) || []
      break
    }
    case 'error':
      items.value = [
        ...items.value,
        { id: uid(), role: 'assistant', content: { type: 'error', text: String(payload.message || '这一步没走通') } },
      ]
      break
    case 'done':
      busy.value = false
      runId = ''
      // 刷新一次列表：排序按 updated_at，否则这一条会一直沉在下面。
      void refreshConversations()
      break
    default:
      break
  }
  if (pinTo) void scrollToItem(pinTo)
  else void scrollToBottom()
}

/** 上一次真正发出去的内容。失败重试复用它，用户不必重新打一遍。 */
const lastSent = ref<{ text: string; refs: string[] }>({ text: '', refs: [] })

const send = async (text: string, refs: string[] = []) => {
  if (busy.value || !text.trim()) return
  banner.value = ''
  lastSent.value = { text, refs }
  items.value = [...items.value, { id: uid(), role: 'user', content: { type: 'text', text } }]
  items.value = [...items.value, { id: uid(), role: 'assistant', content: { type: 'text', text: '' }, steps: [], streaming: true }]
  cursor = items.value.length - 1
  busy.value = true
  abort = new AbortController()
  await scrollToBottom()

  try {
    await streamAssistantTurn(
      { conversation_id: activeId.value || undefined, text, client_turn_id: uid(), refs },
      applyEvent,
      { signal: abort.signal },
    )
  } catch (error) {
    if (!abort?.signal.aborted) {
      banner.value = error instanceof Error ? error.message : '发送失败，请重试。'
    }
  } finally {
    const item = currentAssistant()
    if (item) item.streaming = false
    busy.value = false
    abort = null
    await syncAfterTurn()
  }
}

/**
 * 重试：把上一次的问题重新发一遍。
 *
 * 刻意**不**复用上一次的 ``client_turn_id``。轮次幂等的用途是「同一次点击被
 * 发了两次」（网络重试、用户手抖点两下），服务端据此认出「这是同一问」；
 * 而用户点「重试」时那一轮已经失败了，幂等键只会让它原样回放一次失败，
 * 连重新生成的机会都没有。
 */
const retry = () => {
  const { text, refs } = lastSent.value
  if (!text || busy.value) return
  void send(text, refs)
}

const syncAfterTurn = async () => {
  await refreshConversations()
  if (!activeId.value) return
  try {
    const detail = await getConversation(activeId.value)
    activeTitle.value = detail.title
    artifacts.value = detail.artifacts || []
  } catch {
    // 同步失败可接受：界面上的内容就是刚才流出来的那一份。
  }
}

const stop = async () => {
  if (!busy.value) return
  abort?.abort()
  busy.value = false
  if (runId) {
    try {
      await cancelRun(runId)
    } catch {
      banner.value = '停止指令没送到，生成可能还在继续。'
    }
  }
}

const confirmPlan = async (taskId: string, plan: ResourcePlan) => {
  const target = items.value.find((item) => item.content.type === 'plan' && item.content.taskId === taskId)
  if (!target || target.content.type !== 'plan' || planBusy.value[taskId]) return
  planBusy.value = { ...planBusy.value, [taskId]: true }
  try {
    await confirmAssistantPlan(taskId, plan, target.content.design)
    target.content.confirmed = true
    // 确认后由服务端续跑，事件形状与对话流一致，所以复用同一个 applyEvent。
    await streamAssistantArtifact(taskId, applyEvent)
    await syncAfterTurn()
  } catch (error) {
    banner.value = error instanceof Error ? error.message : '确认没成功，方案还在，可以再试。'
  } finally {
    planBusy.value = { ...planBusy.value, [taskId]: false }
  }
}

const openArtifact = (artifact: ArtifactSummary) => {
  if (artifact.open_url) void router.push(artifact.open_url)
}

onMounted(async () => {
  await refreshConversations()
})
</script>

<style scoped lang="scss">
@use '../styles/tokens.scss' as *;

.assistant {
  display: grid;
  grid-template-columns: 212px minmax(0, 1fr) auto;
  gap: 18px;
  height: calc(100vh - 128px);
  min-height: 520px;
}

.assistant-side {
  display: flex;
  flex-direction: column;
  min-height: 0;
  gap: 10px;
  padding-right: 12px;
  border-right: 1px solid var(--qh-border);
}

.side-new {
  padding: 9px 12px;
  border: 1px solid var(--qh-border);
  border-radius: var(--qh-radius-stage);
  background: var(--qh-surface);
  color: var(--qh-text);
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
  text-align: left;
}

.side-new:hover {
  border-color: var(--qh-accent-line);
}

.side-list {
  display: grid;
  flex: 1;
  align-content: start;
  gap: 3px;
  overflow-y: auto;
}

.side-item {
  display: grid;
  gap: 2px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--qh-text);
  cursor: pointer;
  text-align: left;
}

.side-item:hover {
  background: var(--qh-surface);
}

.side-item.is-active {
  border-color: var(--qh-accent-line);
  background: var(--qh-accent-soft);
}

.side-item-title {
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.side-item-time {
  color: var(--qh-text-muted);
  font-size: 11px;
}

.side-empty {
  margin: 8px 0;
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.7;
}

.side-foot {
  margin: 0;
  padding-top: 8px;
  border-top: 1px solid var(--qh-border);
  color: var(--qh-text-muted);
  font-size: 11px;
}

.assistant-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  gap: 10px;
}

.assistant-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.assistant-head > div {
  min-width: 0;
}

.assistant-head h1 {
  margin: 0;
  overflow: hidden;
  font-family: var(--qh-font-reading);
  font-size: 17px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assistant-head p {
  margin: 4px 0 0;
  color: var(--qh-text-muted);
  font-size: 12px;
}

.head-artifacts {
  flex: 0 0 auto;
  padding: 6px 12px;
  border: 1px solid var(--qh-border);
  border-radius: 999px;
  background: transparent;
  color: var(--qh-text-muted);
  cursor: pointer;
  font-size: 12px;
}

.assistant-scroll {
  position: relative; /* scrollToItem 用 offsetTop 定位，需要这个包含块 */
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 6px;
}

.assistant-thread {
  display: grid;
  max-width: 760px;
  margin: 0 auto;
  gap: 22px;
  padding: 8px 0 20px;
}

.assistant-banner {
  margin: 0;
  padding: 7px 12px;
  border-left: 2px solid var(--qh-color-gold);
  color: var(--qh-color-gold-readable);
  font-size: 12px;
}

.assistant-rail {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 196px;
  padding-left: 14px;
  border-left: 1px solid var(--qh-border);
  overflow-y: auto;
}

.rail-title {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.rail-list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.rail-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--qh-border);
  border-radius: 6px;
  background: var(--qh-surface);
  color: var(--qh-text);
  cursor: pointer;
  font-size: 12px;
}

.rail-kind {
  font-weight: 700;
}

.rail-time {
  color: var(--qh-text-muted);
  font-size: 11px;
}

.rail-state {
  color: var(--qh-text-muted);
}

.rail-state[data-state='done'] {
  color: var(--qh-color-jade);
}

.rail-state[data-state='awaiting_plan'] {
  color: var(--qh-accent);
}

.rail-empty {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.7;
}

@media (max-width: 1100px) {
  .assistant {
    grid-template-columns: 180px minmax(0, 1fr);
  }

  .assistant-rail {
    display: none;
  }
}

@media (max-width: 820px) {
  .assistant {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
  }

  .assistant-side {
    display: none;
  }
}
</style>
