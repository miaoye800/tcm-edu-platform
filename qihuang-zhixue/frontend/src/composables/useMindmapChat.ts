import { computed, ref, watch } from 'vue'
import type { useMindmapStudio } from './useMindmapStudio'

/**
 * 左对话栏的状态机。
 *
 * 刻意做得很薄：真正的生成、推流、落库仍由 useMindmapStudio 承担，
 * 这里只负责「一句话变成一次调用」和「把过程翻译成人话」两件事。
 *
 * 之所以不把生成逻辑搬进来，是因为这套对话未来要接到通用对话 Agent 上
 * （那时一次对话里可能先出导图、再出文档）。到那一天，换掉的是调用方，
 * 而不是把生成链路复制一份。
 */

export type ChatPhase = 'idle' | 'submitting' | 'streaming' | 'ready' | 'failed'

export type ChatMessageKind = 'text' | 'progress' | 'result' | 'error'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  kind: ChatMessageKind
  text?: string
  /** 结果卡绑定的资源；点「打开画布」用它。 */
  resourceId?: string
  title?: string
  nodes?: number
  depth?: number
  inferred?: number
  structureId?: string
  themeId?: string
  /** 后端写好的一句话：「按「对比鉴别」组织」或域外提示。 */
  note?: string
  /** 域外主题：整棵树都是推断，卡片要把这句话摆出来。 */
  degraded?: boolean
  createdAt: number
}

/** 冷启动建议：四条都落在中药学语料范围内，点了就有货。 */
export const CHAT_SUGGESTIONS = [
  '解表药的分类',
  '麻黄与桂枝的区别',
  '十八反有哪些',
  '入肺经的温性药',
] as const

const makeId = () => `m_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`

/**
 * 幂等键。
 *
 * 形如 `mindmap:<uuid>`，随任务一起提交。对话 Agent 一定会重试（超时、断流、
 * 用户点两下），没有这个键就会生成两张图、占两条历史记录。
 */
const makeInvocationId = () =>
  `mindmap:${(globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`)}`

type Studio = ReturnType<typeof useMindmapStudio>

export const useMindmapChat = (studio: Studio) => {
  const messages = ref<ChatMessage[]>([])
  const draft = ref('')
  const chatPhase = ref<ChatPhase>('idle')
  const collapsed = ref(false)
  /** 最近一次提问：失败重试要原样再发一遍。 */
  const lastPrompt = ref('')
  const lastInvocationId = ref('')

  const canSend = computed(() => chatPhase.value !== 'submitting' && chatPhase.value !== 'streaming')

  const push = (message: Omit<ChatMessage, 'id' | 'createdAt'>) => {
    const full: ChatMessage = { ...message, id: makeId(), createdAt: Date.now() }
    messages.value = [...messages.value, full]
    return full
  }

  /** 生成中的进度消息只有一条，靠改写它而不是不断追加，避免刷屏。 */
  let progressId = ''
  /** 最近一张完成卡：收卷后节点数还会变（队列排空、用户随手增删），要跟着改。 */
  let resultId = ''

  const patchResult = (stats: { nodes: number; depth: number; model: number }) => {
    if (!resultId) return
    const target = resultId
    messages.value = messages.value.map(message =>
      message.id === target
        ? { ...message, nodes: stats.nodes, depth: stats.depth, inferred: stats.model }
        : message,
    )
  }

  const ask = async (text: string) => {
    const prompt = text.trim()
    if (!prompt || !canSend.value) return
    draft.value = ''
    lastPrompt.value = prompt
    lastInvocationId.value = makeInvocationId()
    // 上一张卡的数不该被这张图覆盖：清掉锚点，等新卡落位再接管。
    resultId = ''
    push({ role: 'user', kind: 'text', text: prompt })
    chatPhase.value = 'submitting'
    progressId = push({ role: 'assistant', kind: 'progress', text: '正在辨明主题…' }).id
    await studio.ask(prompt, lastInvocationId.value)
  }

  const retry = async () => {
    if (!lastPrompt.value || !canSend.value) return
    chatPhase.value = 'submitting'
    progressId = push({ role: 'assistant', kind: 'progress', text: '正在重试…' }).id
    // 同一个幂等键：真的重发时后端应当返回同一张图，而不是再造一张。
    await studio.ask(lastPrompt.value, lastInvocationId.value)
  }

  const reset = () => {
    messages.value = []
    chatPhase.value = 'idle'
    lastPrompt.value = ''
    lastInvocationId.value = ''
    progressId = ''
    resultId = ''
    studio.clearCanvas()
  }

  const toggleCollapsed = () => { collapsed.value = !collapsed.value }

  /** 生成阶段：把后端的工序名直接当进度文案，不另造一套词汇。 */
  watch(() => studio.phase.value, next => {
    if (next === 'generating') chatPhase.value = 'streaming'
  })

  watch(() => studio.stepIndex.value, index => {
    if (!progressId || chatPhase.value !== 'streaming') return
    const label = studio.steps.value[index]
    if (!label) return
    messages.value = messages.value.map(message =>
      message.id === progressId ? { ...message, text: `${label}…` } : message,
    )
  })

  // 完成卡上的三个数是「这张图现在多大」，不是「生成结束那一刻多大」。
  // 收卷要等生长队列排空，done 之后队列里还剩十几个节点是常态；
  // 一开始做成快照，卡片就会长期显示「层数 0」，和底栏自相矛盾。
  watch(() => studio.stats.value, patchResult, { deep: true })

  watch(() => studio.phase.value, next => {
    if (!progressId) return
    if (next === 'editing') {
      messages.value = messages.value.filter(message => message.id !== progressId)
      progressId = ''
      chatPhase.value = 'ready'
      resultId = push({
        role: 'assistant',
        kind: 'result',
        resourceId: studio.resourceId.value,
        title: studio.title.value || studio.topicLabel.value,
        nodes: studio.stats.value.nodes,
        depth: studio.stats.value.depth,
        inferred: studio.stats.value.model,
        structureId: studio.structureId.value,
        themeId: studio.themeId.value,
        note: studio.intentNote.value,
        degraded: studio.degraded.value,
      }).id
    } else if (next === 'awaiting-plan') {
      // 方案在等人确认：既不是「还在铺枝」，也不是「已经出图」。
      // 保持 streaming（任务确实还在进行），只换掉进度行这句话。
      messages.value = messages.value.map(message =>
        message.id === progressId ? { ...message, text: '方案拟好了，等你确认后开始铺枝。' } : message,
      )
    } else if (next === 'idle') {
      // 用户主动退出协商：任务停下，进度行不能挂着。
      messages.value = messages.value.filter(message => message.id !== progressId)
      progressId = ''
      chatPhase.value = 'idle'
    } else if (next === 'failed') {
      messages.value = messages.value.filter(message => message.id !== progressId)
      progressId = ''
      chatPhase.value = 'failed'
      push({
        role: 'assistant',
        kind: 'error',
        text: studio.errorMessage.value || '这次没生成出来，可以重试一次。',
      })
    }
  })

  return {
    messages,
    draft,
    chatPhase,
    collapsed,
    canSend,
    lastPrompt,
    ask,
    retry,
    reset,
    toggleCollapsed,
  }
}
