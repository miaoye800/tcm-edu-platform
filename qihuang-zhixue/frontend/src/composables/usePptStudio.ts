import { computed, onBeforeUnmount, ref } from 'vue'
import {
  FREE_TOPIC_NODE_ID,
  NEW_SLIDE_TITLE,
  PPT_STEPS,
  PPT_STEP_SHORTS,
  SAVE_DEBOUNCE_MS,
  SLIDE_INTERVAL_MS,
} from '../config/pptStudio'
import { DEFAULT_THEME_ID, pptThemes } from '../config/pptThemes'
import { PptSaveError, confirmOutline as apiConfirmOutline, listRecentDecks, saveDeck } from '../services/pptStudioApi'
import { confirmResourcePlan, reviseResourcePlan } from '../services/resourceApi'
import type { ResourcePlan, ResourcePlanStep } from '../types/resource'
import type { DeckDesign, DocDesign, MindmapDesign, SlotResolution } from '../types/design'
import { getKnowledgeTree } from '../services/pathApi'
import { createResourceTask, getResource, getResourceTask, streamResourceTask } from '../services/resourceApi'
import type { KnowledgeNode } from '../types/path'
import type { OutlineSlide, PptMode, SlideNode } from '../types/ppt'
import { SLIDE_LIMITS } from '../types/ppt'
import type { ResourceItem, DeckPageCheck, DeckVerifyStats, ResourceTaskEvent } from '../types/resource'

export type PptPhase = 'idle' | 'generating' | 'awaiting-outline' | 'awaiting-plan' | 'editing' | 'failed'
export type SaveStatus = 'clean' | 'saving' | 'saved' | 'conflict' | 'error'
export type PptDesign = DeckDesign | DocDesign | MindmapDesign

/** 选材汇总：只用来在左栏写一句「三张图两张有据」，不参与渲染。 */
export interface SlotSummary {
  total?: number
  counts?: Record<string, number>
  unfilled?: string[]
}

const flattenNodes = (domains: KnowledgeNode[]) => {
  const rows: Array<{ node: KnowledgeNode; trail: string }> = []
  const walk = (nodes: KnowledgeNode[], trail: string[]) => {
    for (const node of nodes) {
      const path = [...trail, node.name]
      rows.push({ node, trail: path.slice(0, -1).join(' / ') })
      if (node.children?.length) walk(node.children, path)
    }
  }
  walk(domains, [])
  return rows
}

/** 新建页的 id 必须落在后端正则 `^[A-Za-z0-9_.:-]{1,64}$` 内，且同一份里唯一。 */
let slideSeq = 0
const nextSlideId = () => {
  slideSeq += 1
  return `n${Date.now().toString(36)}${slideSeq.toString(36)}`
}

const sanitizeSlide = (slide: SlideNode): SlideNode => ({
  id: slide.id,
  kind: slide.kind,
  title: slide.title.trim().slice(0, SLIDE_LIMITS.title) || NEW_SLIDE_TITLE,
  subtitle: slide.subtitle.trim().slice(0, SLIDE_LIMITS.subtitle),
  // 空要点会被后端判为非法（validator 拒绝空串），保存前必须过滤。
  bullets: slide.bullets.map((item) => item.trim()).filter(Boolean).slice(0, 6).map((item) => item.slice(0, SLIDE_LIMITS.bullet)),
  columns: slide.columns
    .slice(0, 2)
    .map((column) => ({
      heading: column.heading.trim().slice(0, SLIDE_LIMITS.columnHeading) || '对照',
      items: column.items.map((item) => item.trim()).filter(Boolean).slice(0, 5).map((item) => item.slice(0, SLIDE_LIMITS.bullet)),
    }))
    .filter((column) => column.items.length > 0),
  note: slide.note.slice(0, SLIDE_LIMITS.note),
  source_indexes: [...slide.source_indexes],
  // 结构化内容必须原样回传：后端 SlideNode 是 extra="forbid" 且这几项有默认值，
  // 漏传就把一页数据卡 / 矩阵静默降级成要点页。
  purpose: slide.purpose,
  blocks: slide.blocks ?? [],
  asset_refs: slide.asset_refs ?? [],
  // 保存也要把场景带回去：不带，改一个错别字就会把整页的排版结果弄丢，
  // 重新载入又退回旧渲染器（用户会以为「一保存就变丑」）。
  ...(slide.scene ? { scene: slide.scene } : {}),
})

/**
 * 事件里的页 → 编辑态。
 *
 * 之前这里逐字段手抄，新增 `purpose` / `blocks` / `asset_refs` 之后就成了漏字段的
 * 温床（漏一个，页就从数据卡退成要点页）。改成「缺什么补什么」的拷贝。
 *
 * 入参放宽成结构类型：流事件推来的页与 `content_json` 里存的页形状一致，
 * 但后者是历史数据，`subtitle` / `note` 可能整个键都不在。
 */
type SlideLike = {
  id: string
  kind: string
  title: string
  subtitle?: string
  bullets?: string[]
  columns?: Array<{ heading: string; items: string[] }>
  note?: string
  source_indexes?: number[]
  purpose?: string
  blocks?: Array<{
    type: string
    text?: string
    heading?: string
    items?: string[]
    columns?: Array<{ heading: string; items: string[] }>
    value?: string
    source?: string
  }>
  asset_refs?: Array<{
    slot: string
    asset_id: string
    result: 'parametric' | 'hit' | 'substitute' | 'generated' | 'none'
    provenance: 'parametric' | 'library' | 'generated' | 'none'
    license: string
    attribution: string
    file: string
    note: string
  }>
  /** 排版层的结果。**丢了它整份稿就退回旧渲染器**——「每个 PPT 一个样」的根就在这。 */
  scene?: unknown
}

const toSlideNode = (slide: SlideLike): SlideNode => ({
  id: slide.id,
  kind: slide.kind as SlideNode['kind'],
  title: slide.title,
  subtitle: slide.subtitle ?? '',
  bullets: [...(slide.bullets ?? [])],
  columns: (slide.columns ?? []).map((column) => ({ heading: column.heading, items: [...column.items] })),
  note: slide.note ?? '',
  source_indexes: [...(slide.source_indexes ?? [])],
  purpose: slide.purpose as SlideNode['purpose'],
  blocks: (slide.blocks ?? []).map((block) => ({
    type: block.type as NonNullable<SlideNode['blocks']>[number]['type'],
    text: block.text ?? '',
    heading: block.heading ?? '',
    items: [...(block.items ?? [])],
    columns: (block.columns ?? []).map((column) => ({ heading: column.heading, items: [...column.items] })),
    value: block.value ?? '',
    source: block.source ?? '',
  })),
  asset_refs: (slide.asset_refs ?? []).map((ref) => ({ ...ref })),
  // 排版层的场景必须跟着页走：`SlideRenderer` 有 scene 才走新渲染器。
  // 这里漏掉过一版，于是后台把每一页都排了、用户看到的还是旧版式。
  ...(slide.scene ? { scene: slide.scene as SlideNode['scene'] } : {}),
})

export const usePptStudio = () => {
  const phase = ref<PptPhase>('idle')
  const errorMessage = ref('')
  const stepIndex = ref(-1)
  const outline = ref<OutlineSlide[]>([])
  const plan = ref<ResourcePlan | null>(null)
  const planBusy = ref(false)
  /**
   * 设计单：版式、密度、槽位。与方案同批确认，确认后成稿只照它排——
   * 生成期再改版式会让用户在确认单上做的手改全部作废。
   */
  const design = ref<PptDesign | null>(null)
  /** 选材结果：逐槽解释「这张图为什么有 / 为什么没有」。只展示，不阻塞。 */
  const slotResolutions = ref<SlotResolution[]>([])
  const slotSummary = ref<SlotSummary>({})
  /** 服务端下发的工序表。空数组表示还没开跑，步骤条退回本地兜底。 */
  const remoteSteps = ref<ResourcePlanStep[]>([])
  /** 逐页核对结果：哪一页不在方案内、哪一页空、哪一页引用越界。只报问题，不改内容。 */
  const deckCheck = ref<DeckVerifyStats | null>(null)
  const deckPages = ref<DeckPageCheck[]>([])
  /** 校验后被定点重写过的页。校验本身不改内容；改的是这一页写错了的那几页。 */
  const deckRevised = ref<{ page: number; issues: string[] }[]>([])
  const deckTitle = ref('')
  const slides = ref<SlideNode[]>([])
  const current = ref(0)

  const saveStatus = ref<SaveStatus>('clean')
  const saveMessage = ref('未钤')
  const version = ref(1)
  const resourceId = ref('')
  const title = ref('')
  const themeId = ref(DEFAULT_THEME_ID)
  const taskId = ref('')

  const domains = ref<KnowledgeNode[]>([])
  const nodesLoading = ref(false)
  const keyword = ref('')
  const selectedNode = ref<KnowledgeNode | null>(null)
  const freeTopic = ref('')
  const pageCount = ref(12)
  const mode = ref<PptMode>('one_shot')
  const audience = ref('中医药专业本科生')
  const recent = ref<ResourceItem[]>([])

  const allNodes = computed(() => flattenNodes(domains.value))
  const filteredNodes = computed(() => {
    const key = keyword.value.trim()
    const rows = key ? allNodes.value.filter((row) => row.node.name.includes(key)) : allNodes.value
    return rows.slice(0, 60)
  })
  const topicLabel = computed(() => freeTopic.value.trim() || selectedNode.value?.name || '')
  const canGenerate = computed(() => phase.value !== 'generating' && Boolean(topicLabel.value))
  const currentSlide = computed(() => slides.value[current.value] ?? null)
  const stats = computed(() => ({
    slides: slides.value.length,
    bullets: slides.value.reduce((total, slide) => total + slide.bullets.length, 0),
    grounded: slides.value.filter((slide) => slide.source_indexes.length > 0).length,
  }))

  /**
   * 工序表：服务端下发优先，本地表只做兜底。
   *
   * 工序数量是会变的（v1 四步、v3 六步），前端若把步数写死，后端加一步
   * 步骤条就会少一格、最后一步永远高亮不了。所以下标语义交给 `stepIndex`，
   * 长度与标签都听服务端的。
   */
  const steps = computed<ResourcePlanStep[]>(() =>
    remoteSteps.value.length
      ? remoteSteps.value
      : PPT_STEPS.map((step) => ({ id: step.id, label: step.label })),
  )

  const stepStates = computed(() =>
    steps.value.map((step, index) => ({
      ...step,
      short: PPT_STEP_SHORTS[step.id] ?? step.label.slice(0, 1),
      state: index < stepIndex.value ? 'done' : index === stepIndex.value ? 'active' : 'pending',
    })),
  )

  let controller: AbortController | null = null
  let saveTimer = 0
  let saving = false
  let pendingSave = false

  // 逐页生长的队列：后端一次推完整份，前端按节拍放出来，才看得清「一页页翻开」。
  const queue: SlideNode[] = []
  let drainTimer = 0
  let draining = false
  let growthDone = false
  /**
   * 生成完成时要回灌的稿件 id。
   *
   * 流里的页是**敷演完、还没排版**的版本：没有 `scene`，只能走旧渲染器，
   * 于是用户看到的是旧版「01 02 03 04 + 半句标签」。落库的成品才带
   * 排版层的结果。所以要等逐页放完再换成成品页——换早了会长短不一地跳。
   */
  let pendingResourceId = ''

  const stopDrain = () => {
    if (drainTimer) {
      window.clearTimeout(drainTimer)
      drainTimer = 0
    }
    draining = false
    queue.length = 0
  }

  const finishGrowth = () => {
    if (!growthDone) return
    growthDone = false
    draining = false
    current.value = 0
    phase.value = 'editing'
    saveStatus.value = 'clean'
    saveMessage.value = '未钤'
    if (pendingResourceId) {
      const id = pendingResourceId
      pendingResourceId = ''
      void applyResource(id)
    }
  }

  /**
   * 放一页，再排下一拍。
   *
   * 队列空**不等于**生长结束：逐页敷演时两页之间隔着一次模型调用，
   * 空档几十秒是常态。以前这里在空队列时直接 `finishGrowth()`，而那时
   * `growthDone` 还是 false，它提前 return、`draining` 留在 true——
   * 之后推来的页全堆在队列里，一拍都不再放（表现为「只有第 1 页」且
   * 永远「炼制中」）。空队列必须把 `draining` 复位，让下一页能重新起拍；
   * 真正的收卷交给 `done`。
   */
  const drain = () => {
    drainTimer = 0
    const next = queue.shift()
    if (!next) {
      draining = false
      finishGrowth()
      return
    }
    slides.value.push(next)
    drainTimer = window.setTimeout(drain, SLIDE_INTERVAL_MS)
  }

  const enqueue = (slide: SlideNode) => {
    queue.push(slide)
    if (!draining) {
      draining = true
      drain()
    }
  }

  const loadNodes = async () => {
    if (nodesLoading.value || domains.value.length) return
    nodesLoading.value = true
    try {
      const response = await getKnowledgeTree()
      domains.value = response.domains ?? []
    } catch {
      domains.value = []
    } finally {
      nodesLoading.value = false
    }
  }

  const refreshRecent = async () => {
    try {
      recent.value = await listRecentDecks(3)
    } catch {
      recent.value = []
    }
  }

  const pickNode = (node: KnowledgeNode) => {
    selectedNode.value = node
    freeTopic.value = ''
  }

  const pickFreeTopic = () => {
    selectedNode.value = null
  }

  const readable = (error: unknown) => {
    if (error instanceof Error && error.message) return error.message
    return '炼制失败，请稍后再试'
  }

  const adopt = async (id: string) => {
    resourceId.value = id
    if (!id) return
    try {
      const resource = await getResource(id)
      title.value = resource.title
      version.value = Number((resource.meta?.ppt_version as number) ?? 1)
      const theme = String((resource.content as Record<string, unknown> | undefined)?.theme_id ?? '')
      if (pptThemes.some((item) => item.id === theme)) themeId.value = theme
    } catch {
      version.value = 1
    }
    void refreshRecent()
  }

  const handleEvent = (event: ResourceTaskEvent) => {
    // 工序表可能随事件一起下发（plan / design 两个事件都带）。只有服务端说了算：
    // 前端写死的四步表在 v3 是六步，长度对不上时最后一步永远高亮不了。
    if (event.payload.steps?.length) remoteSteps.value = [...event.payload.steps]
    switch (event.event) {
      case 'step_start':
        stepIndex.value = event.step_index
        break
      case 'step_done':
        stepIndex.value = Math.max(stepIndex.value, event.step_index)
        break
      case 'outline':
        outline.value = [...(event.payload.outline ?? [])]
        deckTitle.value = event.payload.deck_title || deckTitle.value
        break
      case 'plan':
        plan.value = event.payload.plan ?? null
        break
      case 'design':
        // 设计单与方案同批确认；续跑时服务端会再推一次，用来恢复刷新后的面板。
        if (event.payload.plan) plan.value = event.payload.plan
        design.value = event.payload.design ?? null
        break
      case 'slot_resolution':
        slotResolutions.value = [...(event.payload.slots ?? [])]
        slotSummary.value = event.payload.summary ?? {}
        break
      case 'slide': {
        const slide = event.payload.slide
        if (!slide) break
        enqueue(toSlideNode(slide))
        break
      }
      case 'deck_verify': {
        const theme = event.payload.theme_id || ''
        if (pptThemes.some((item) => item.id === theme)) themeId.value = theme
        // 校色步也会推同名事件，但只带校色统计、不带逐页结果：那一步不动核对结论。
        if (event.payload.pages) {
          deckPages.value = [...event.payload.pages]
          deckRevised.value = [...(event.payload.revised ?? [])]
          deckCheck.value = (event.payload.stats as unknown as DeckVerifyStats) ?? null
        }
        break
      }
      case 'pause':
        // 服务端推完大纲/方案就断流：这是正常终止，不是错误，也不是「生成完成」。
        phase.value = event.payload.reason === 'await_plan' ? 'awaiting-plan' : 'awaiting-outline'
        break
      case 'done':
        stepIndex.value = Math.max(0, event.total_steps - 1)
        growthDone = true
        // 成品页（带排版层结果）等逐页放完再载入：见 pendingResourceId 的注释。
        pendingResourceId = event.payload.resource_id || ''
        void adopt(event.payload.resource_id || '')
        // 队列已空时立刻收卷；否则等 drain 把最后一页放完再收。
        if (!draining && !queue.length) finishGrowth()
        break
      case 'error':
        stopDrain()
        phase.value = 'failed'
        errorMessage.value = event.payload.message || '炉火不稳，幻灯未能炼成'
        break
      default:
        break
    }
  }

  const reset = () => {
    stopDrain()
    growthDone = false
    pendingResourceId = ''
    errorMessage.value = ''
    stepIndex.value = 0
    outline.value = []
    plan.value = null
    design.value = null
    slotResolutions.value = []
    slotSummary.value = {}
    remoteSteps.value = []
    deckCheck.value = null
    deckPages.value = []
    deckRevised.value = []
    deckTitle.value = ''
    slides.value = []
    current.value = 0
    saveStatus.value = 'clean'
    saveMessage.value = '未钤'
    resourceId.value = ''
    title.value = ''
  }

  const stream = async (id: string, signal: AbortSignal) => {
    await streamResourceTask(id, handleEvent, { signal, retries: 2 })
  }

  const generate = async () => {
    if (!canGenerate.value) return
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    reset()
    phase.value = 'generating'

    const params: Record<string, unknown> = {
      mode: mode.value,
      theme: themeId.value,
      page_count: pageCount.value,
      audience: audience.value,
    }
    const free = freeTopic.value.trim()
    if (free) params.topic = free.slice(0, 60)

    try {
      const task = await createResourceTask(selectedNode.value?.id ?? FREE_TOPIC_NODE_ID, 'ppt', params)
      taskId.value = task.task_id
      await stream(task.task_id, signal)
    } catch (error) {
      if (signal.aborted) return
      stopDrain()
      phase.value = 'failed'
      errorMessage.value = readable(error)
    }
  }

  /** 大纲确认后续跑：写回页序 → 重新订阅同一条流，从敷演步开始。 */
  const confirm = async () => {
    if (!taskId.value || !outline.value.length) return
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    errorMessage.value = ''
    try {
      await apiConfirmOutline(taskId.value, outline.value)
      phase.value = 'generating'
      stepIndex.value = 2
      slides.value = []
      current.value = 0
      await stream(taskId.value, signal)
    } catch (error) {
      if (signal.aborted) return
      phase.value = 'awaiting-outline'
      errorMessage.value = readable(error)
    }
  }

  /** 修订方案。带上面板里手改过的那一版，避免服务端从最初那版重算把手改抹掉。 */
  const revisePlan = async (feedback: string, goal = '', draft?: ResourcePlan) => {
    if (!taskId.value || (!feedback.trim() && !goal.trim())) return
    planBusy.value = true
    try { plan.value = (await reviseResourcePlan(taskId.value, feedback, { plan: draft ?? plan.value ?? undefined, goal })).plan }
    catch (error) { errorMessage.value = readable(error) }
    finally { planBusy.value = false }
  }

  /** 主动退出协商：不生成。任务留在服务端待确认，随时可再进来接着改。 */
  const cancelPlan = () => {
    controller?.abort()
    controller = null
    planBusy.value = false
    phase.value = 'idle'
  }

  /**
   * 确认方案与设计单，从闸门后一步续跑。
   *
   * 设计单必须和方案一起交：只交方案，服务端只能自己再猜一遍版式，
   * 用户在确认单上改的版式、密度、图位全部作废。
   */
  const confirmPlan = async (value: ResourcePlan, nextDesign?: PptDesign | null) => {
    if (!taskId.value) return
    controller?.abort()
    controller = new AbortController()
    planBusy.value = true
    phase.value = 'generating'
    try {
      const accepted = await confirmResourcePlan(taskId.value, value, nextDesign ?? design.value)
      // 服务端把归一化后的设计单回传（空对象表示这个能力没有设计步）。
      const applied = accepted.design as PptDesign | undefined
      if (applied && typeof applied === 'object' && Object.keys(applied).length) design.value = applied
      // 续跑位置由服务端给：哪一步是闸门只有它知道（v3 落在 design）。
      stepIndex.value = accepted.current_step ?? stepIndex.value
      await stream(taskId.value, controller.signal)
    } catch (error) { phase.value = 'awaiting-plan'; errorMessage.value = readable(error) }
    finally { planBusy.value = false }
  }

  /** 大纲编辑：删页、改标题、改要点。ids 由后端给，前端只维护顺序与文案。 */
  const renameOutline = (index: number, value: string) => {
    const item = outline.value[index]
    if (!item) return
    outline.value[index] = { ...item, title: value.slice(0, SLIDE_LIMITS.title) }
  }

  const updateOutlinePoints = (index: number, points: string[]) => {
    const item = outline.value[index]
    if (!item) return
    outline.value[index] = { ...item, points: points.slice(0, 5) }
  }

  const removeOutlineSlide = (index: number) => {
    if (outline.value.length <= 1) return
    outline.value.splice(index, 1)
  }

  const moveOutlineSlide = (from: number, to: number) => {
    if (to < 0 || to >= outline.value.length || from === to) return
    const [item] = outline.value.splice(from, 1)
    outline.value.splice(to, 0, item)
  }

  const updateSlide = (index: number, patch: Partial<SlideNode>) => {
    const slide = slides.value[index]
    if (!slide) return
    slides.value[index] = { ...slide, ...patch }
    markDirty()
  }

  const setBullet = (index: number, bulletIndex: number, value: string) => {
    const slide = slides.value[index]
    if (!slide) return
    const bullets = [...slide.bullets]
    bullets[bulletIndex] = value.slice(0, SLIDE_LIMITS.bullet)
    updateSlide(index, { bullets })
  }

  const addBullet = (index: number) => {
    const slide = slides.value[index]
    if (!slide || slide.bullets.length >= 6) return
    updateSlide(index, { bullets: [...slide.bullets, ''] })
  }

  const removeBullet = (index: number, bulletIndex: number) => {
    const slide = slides.value[index]
    if (!slide) return
    updateSlide(index, { bullets: slide.bullets.filter((_, position) => position !== bulletIndex) })
  }

  const addSlide = () => {
    if (slides.value.length >= SLIDE_LIMITS.slides) return
    const slide: SlideNode = {
      id: nextSlideId(),
      kind: 'bullets',
      title: NEW_SLIDE_TITLE,
      subtitle: '',
      bullets: [''],
      columns: [],
      note: '',
      source_indexes: [],
    }
    slides.value.splice(current.value + 1, 0, slide)
    current.value += 1
    markDirty()
  }

  const removeSlide = (index: number) => {
    if (slides.value.length <= 1) return
    slides.value.splice(index, 1)
    current.value = Math.min(current.value, slides.value.length - 1)
    markDirty()
  }

  const moveSlide = (from: number, to: number) => {
    if (to < 0 || to >= slides.value.length || from === to) return
    const [item] = slides.value.splice(from, 1)
    slides.value.splice(to, 0, item)
    current.value = to
    markDirty()
  }

  const setTheme = (id: string) => {
    if (!pptThemes.some((item) => item.id === id)) return
    themeId.value = id
    markDirty()
  }

  const goTo = (index: number) => {
    if (index < 0 || index >= slides.value.length) return
    current.value = index
  }

  const step = (delta: number) => goTo(current.value + delta)

  const save = async () => {
    if (!resourceId.value || !slides.value.length) return
    if (saving) {
      pendingSave = true
      return
    }
    saving = true
    saveStatus.value = 'saving'
    try {
      const result = await saveDeck(resourceId.value, {
        version: version.value,
        theme_id: themeId.value,
        slides: slides.value.map(sanitizeSlide),
      })
      version.value = result.version
      saveStatus.value = 'saved'
      saveMessage.value = `已钤 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false }).slice(0, 5)}`
    } catch (error) {
      if (error instanceof PptSaveError && error.status === 409) {
        saveStatus.value = 'conflict'
        saveMessage.value = '版本冲突'
      } else {
        saveStatus.value = 'error'
        saveMessage.value = readable(error)
      }
    } finally {
      saving = false
      if (pendingSave) {
        pendingSave = false
        void save()
      }
    }
  }

  /**
   * 标记「内容变脏」并触发防抖保存。
   *
   * 只有进入 `editing` 之后才生效：生成过程中后端正在写库，此时保存会与
   * 生成结果抢同一行记录（而且用户根本没动过手，没有要保存的东西）。
   */
  const markDirty = () => {
    if (phase.value !== 'editing' || !resourceId.value) return
    if (saveTimer) window.clearTimeout(saveTimer)
    saveTimer = window.setTimeout(() => {
      saveTimer = 0
      void save()
    }, SAVE_DEBOUNCE_MS)
  }

  const applyResource = async (id: string) => {
    const resource = await getResource(id)
    const content = (resource.content ?? {}) as { theme_id?: string; slides?: SlideNode[] }
    stopDrain()
    growthDone = false
    resourceId.value = resource.id
    title.value = resource.title
    version.value = Number((resource.meta?.ppt_version as number) ?? 1)
    deckTitle.value = resource.title
    // 存盘里已经有 v3 的结构化字段；这里逐字段抄会漏掉 blocks，
    // 漏了就整份稿从数据卡退回要点页，所以走同一个白名单拷贝。
    slides.value = (content.slides ?? []).map((slide) => toSlideNode(slide))
    const theme = String(content.theme_id ?? '')
    themeId.value = pptThemes.some((item) => item.id === theme) ? theme : DEFAULT_THEME_ID
    // 载入一份旧稿时把讲题填回左栏：不填的话「再起一稿」一进来就是灰的，
    // 用户得先自己想一遍当初写的是什么。
    const savedTopic = String((resource.meta?.topic as string) ?? '').trim()
    if (savedTopic) freeTopic.value = savedTopic
    current.value = 0
    phase.value = 'editing'
    stepIndex.value = Math.max(0, steps.value.length - 1)
    saveStatus.value = 'clean'
    saveMessage.value = '未钤'
  }

  const openResource = async (id: string) => {
    try {
      await applyResource(id)
    } catch (error) {
      errorMessage.value = readable(error)
      phase.value = 'failed'
    }
  }

  /**
   * 回到同一个任务（刷新、从资源库点回来）。
   *
   * 待确认的方案原样摆回来，别重新提案——用户改过的那些字不能白改。
   */
  const restoreTask = async (id: string) => {
    if (!id) return
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal
    reset()
    taskId.value = id
    try {
      const snapshot = await getResourceTask(id)
      plan.value = snapshot.plan ?? null
      // 设计单跟着快照一起回来：否则用户改完版式一刷新，选择就丢了，
      // 面板还会拿「模型重算的那一版」冒充他的选择。
      design.value = snapshot.design ?? null
      if (snapshot.steps?.length) remoteSteps.value = [...snapshot.steps]
      if (snapshot.awaiting_plan && snapshot.plan) {
        phase.value = 'awaiting-plan'
        stepIndex.value = 0
        return
      }
      if (snapshot.status === 'done' && snapshot.resource_id) {
        await applyResource(snapshot.resource_id)
        return
      }
      phase.value = 'generating'
      stepIndex.value = Math.max(0, snapshot.current_step)
      await stream(id, signal)
    } catch (error) {
      if (signal.aborted) return
      stopDrain()
      phase.value = 'failed'
      errorMessage.value = readable(error)
    }
  }

  /** 冲突处置一：放弃本地，拉服务端最新覆盖舞台。 */
  const reloadFromServer = async () => {
    if (!resourceId.value) return
    try {
      await applyResource(resourceId.value)
    } catch (error) {
      errorMessage.value = readable(error)
    }
  }

  /** 冲突处置二：保留本地，取最新版本号后重发（服务端 last-write-wins）。 */
  const keepMine = async () => {
    if (!resourceId.value) return
    try {
      const resource = await getResource(resourceId.value)
      version.value = Number((resource.meta?.ppt_version as number) ?? version.value)
      saveStatus.value = 'clean'
      await save()
    } catch (error) {
      errorMessage.value = readable(error)
    }
  }

  const abandon = () => {
    controller?.abort()
    controller = null
    stopDrain()
    if (saveTimer) {
      window.clearTimeout(saveTimer)
      saveTimer = 0
    }
  }

  onBeforeUnmount(abandon)

  return {
    phase,
    errorMessage,
    stepIndex,
    stepStates,
    /** 服务端下发的工序表（空态时为本地兜底），左栏与空态文案都用它。 */
    steps,
    outline,
    plan,
    planBusy,
    /** 设计单：确认单与线框都读它。 */
    design,
    slotResolutions,
    slotSummary,
    deckCheck,
    deckPages,
    deckRevised,
    revisePlan,
    confirmPlan,
    cancelPlan,
    deckTitle,
    slides,
    current,
    currentSlide,
    saveStatus,
    saveMessage,
    version,
    resourceId,
    title,
    themeId,
    taskId,
    domains,
    allNodes,
    filteredNodes,
    nodesLoading,
    keyword,
    selectedNode,
    freeTopic,
    pageCount,
    mode,
    audience,
    recent,
    topicLabel,
    canGenerate,
    stats,
    loadNodes,
    refreshRecent,
    pickNode,
    pickFreeTopic,
    generate,
    confirm,
    renameOutline,
    updateOutlinePoints,
    removeOutlineSlide,
    moveOutlineSlide,
    updateSlide,
    setBullet,
    addBullet,
    removeBullet,
    addSlide,
    removeSlide,
    moveSlide,
    setTheme,
    goTo,
    step,
    save,
    markDirty,
    openResource,
    restoreTask,
    reloadFromServer,
    keepMine,
    abandon,
  }
}
