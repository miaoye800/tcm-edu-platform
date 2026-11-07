import { computed, onBeforeUnmount, ref } from 'vue'
import { FREE_TOPIC_NODE_ID, NEW_SLIDE_TITLE, PPT_STEPS, SAVE_DEBOUNCE_MS, SLIDE_INTERVAL_MS } from '../config/pptStudio'
import { DEFAULT_THEME_ID, pptThemes } from '../config/pptThemes'
import { PptSaveError, confirmOutline as apiConfirmOutline, listRecentDecks, saveDeck } from '../services/pptStudioApi'
import { getKnowledgeTree } from '../services/pathApi'
import { createResourceTask, getResource, streamResourceTask } from '../services/resourceApi'
import type { KnowledgeNode } from '../types/path'
import type { OutlineSlide, PptMode, SlideNode } from '../types/ppt'
import { SLIDE_LIMITS } from '../types/ppt'
import type { ResourceItem, ResourceTaskEvent } from '../types/resource'

export type PptPhase = 'idle' | 'generating' | 'awaiting-outline' | 'editing' | 'failed'
export type SaveStatus = 'clean' | 'saving' | 'saved' | 'conflict' | 'error'

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
})

export const usePptStudio = () => {
  const phase = ref<PptPhase>('idle')
  const errorMessage = ref('')
  const stepIndex = ref(-1)
  const outline = ref<OutlineSlide[]>([])
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

  const stepStates = computed(() =>
    PPT_STEPS.map((step, index) => ({
      ...step,
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
  }

  const drain = () => {
    const next = queue.shift()
    if (!next) {
      drainTimer = 0
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
      case 'slide': {
        const slide = event.payload.slide
        if (!slide) break
        enqueue({
          id: slide.id,
          kind: slide.kind as SlideNode['kind'],
          title: slide.title,
          subtitle: slide.subtitle,
          bullets: [...slide.bullets],
          columns: slide.columns.map((column) => ({ heading: column.heading, items: [...column.items] })),
          note: slide.note,
          source_indexes: [...slide.source_indexes],
        })
        break
      }
      case 'deck_verify': {
        const theme = event.payload.theme_id || ''
        if (pptThemes.some((item) => item.id === theme)) themeId.value = theme
        break
      }
      case 'pause':
        // 服务端推完大纲就断流：这是正常终止，不是错误，也不是「生成完成」。
        phase.value = 'awaiting-outline'
        break
      case 'done':
        stepIndex.value = Math.max(0, event.total_steps - 1)
        growthDone = true
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
    errorMessage.value = ''
    stepIndex.value = 0
    outline.value = []
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
    slides.value = (content.slides ?? []).map((slide) => ({
      id: slide.id,
      kind: slide.kind,
      title: slide.title,
      subtitle: slide.subtitle ?? '',
      bullets: [...(slide.bullets ?? [])],
      columns: (slide.columns ?? []).map((column) => ({ heading: column.heading, items: [...column.items] })),
      note: slide.note ?? '',
      source_indexes: [...(slide.source_indexes ?? [])],
    }))
    const theme = String(content.theme_id ?? '')
    themeId.value = pptThemes.some((item) => item.id === theme) ? theme : DEFAULT_THEME_ID
    // 载入一份旧稿时把讲题填回左栏：不填的话「再起一稿」一进来就是灰的，
    // 用户得先自己想一遍当初写的是什么。
    const savedTopic = String((resource.meta?.topic as string) ?? '').trim()
    if (savedTopic) freeTopic.value = savedTopic
    current.value = 0
    phase.value = 'editing'
    stepIndex.value = PPT_STEPS.length - 1
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
    outline,
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
    reloadFromServer,
    keepMine,
    abandon,
  }
}
