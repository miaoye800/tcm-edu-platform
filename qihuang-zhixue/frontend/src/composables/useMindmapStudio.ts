import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { APPEARANCE_DEBOUNCE_MS, SAVE_DEBOUNCE_MS, STUDIO_STEPS } from '../config/mindmapStudio'
import { defaultThemeId, resolveThemeId } from '../config/mindmapThemes'
import { defaultStructureId, getStructure, type StructureId } from '../config/mindmapStructures'
import {
  MindmapSaveError,
  listRecentMindmaps,
  saveMindmapTree,
} from '../services/mindmapStudioApi'
import { generateMindmap, presentMindmap } from '../services/capabilityApi'
import { getKnowledgeTree } from '../services/pathApi'
import { getResource, streamResourceTask } from '../services/resourceApi'
import type { KnowledgeNode } from '../types/path'
import type { ResourceItem, ResourceMindmapNode, ResourceTaskEvent } from '../types/resource'
import { toSaveTree, type MindmapTreeNode } from '../utils/mindmapTree'

export type StudioPhase = 'idle' | 'generating' | 'editing' | 'failed'
export type SaveStatus = 'clean' | 'saving' | 'saved' | 'conflict' | 'error'

/** 画布对外的能力面。用 getter 取，避免 ref 声明顺序把 Composable 绑死在组件生命周期上。 */
export interface StudioCanvasBridge {
  reset: () => void
  pushNode: (node: ResourceMindmapNode) => void
  finish: () => void
  setTree: (data: unknown) => void
  getTree: () => MindmapTreeNode | null
}

/** 自由主题统一挂在这个真实节点下：`user_resource.node_id` 是外键，不能自造。 */
export const FREE_TOPIC_NODE_ID = 'general'

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

export interface StudioStats {
  nodes: number
  depth: number
  /** 教材实证 / 图谱关系 / 模型推断，三档分开计数。 */
  textbook: number
  graph: number
  model: number
  /** 当前结构层数放不下、被折叠起来的节点数（不是丢了，点开还在）。 */
  folded: number
}

export const useMindmapStudio = (canvas: () => StudioCanvasBridge | null) => {
  const phase = ref<StudioPhase>('idle')
  const errorMessage = ref('')
  const stepIndex = ref(-1)
  const stepLabels = ref<string[]>([])
  /** 后端报的总步数。前端不自己数——步数按题材变（导图 4 步、文稿 5 步）。 */
  const stepTotal = ref(0)
  const nodeTotal = ref(0)
  const stats = ref<StudioStats>({ nodes: 0, depth: 0, textbook: 0, graph: 0, model: 0, folded: 0 })
  const saveStatus = ref<SaveStatus>('clean')
  const saveMessage = ref('未藏')
  const version = ref(1)
  const resourceId = ref('')
  const title = ref('')
  const themeId = ref(defaultThemeId)
  const structureId = ref<StructureId>(defaultStructureId)

  const domains = ref<KnowledgeNode[]>([])
  const nodesLoading = ref(false)
  const keyword = ref('')
  const selectedNode = ref<KnowledgeNode | null>(null)
  const freeTopic = ref('')
  const depth = ref(3)
  const branches = ref(6)
  const style = ref<'exam' | 'understand' | 'compare'>('understand')
  const recent = ref<ResourceItem[]>([])

  /** 问法判定：对话栏那句「按「对比鉴别」组织」就来自这里。 */
  const intentKind = ref('')
  const intentLabel = ref('')
  const intentNote = ref('')
  /** 域外主题：整棵树都是模型推断，界面要明说，不能假装有据。 */
  const degraded = ref(false)
  /**
   * 用户有没有亲手选过结构。
   *
   * 没选过就让后端按问法给建议：证候题自动出鱼骨、分类题自动向下分支，
   * 比一律向右分支更像「这张图本来就该这样长」。选过就以用户为准。
   */
  const structureTouched = ref(false)

  const allNodes = computed(() => flattenNodes(domains.value))
  const filteredNodes = computed(() => {
    const key = keyword.value.trim()
    const rows = key ? allNodes.value.filter(row => row.node.name.includes(key)) : allNodes.value
    return rows.slice(0, 60)
  })
  const isFreeTopic = computed(() => !selectedNode.value && Boolean(freeTopic.value.trim()))
  const topicLabel = computed(() => freeTopic.value.trim() || selectedNode.value?.name || '')
  const canGenerate = computed(() => {
    if (phase.value === 'generating') return false
    return Boolean(selectedNode.value || freeTopic.value.trim())
  })
  const steps = computed(() => {
    // 总步数以**后端**为准：`stepLabels` 是按 step_index 稀疏填的，
    // 它到第几步长、长度就是几——拿它当分母会出现「第 3/3 步」而实际有 9 步。
    // `STUDIO_STEPS` 是导图 v2 那九道工序的镜像，后端没报总数时用它对上。
    const total = stepTotal.value || STUDIO_STEPS.length
    return Array.from({ length: total }, (_, index) =>
      stepLabels.value[index] ?? STUDIO_STEPS[index]?.label ?? `第 ${index + 1} 步`)
  })
  const stepStates = computed(() => steps.value.map((label, index) => ({
    label,
    index,
    state: index < stepIndex.value ? 'done' : index === stepIndex.value ? 'active' : 'pending',
  })))
  /** 依据占比：只在底栏提示，不打断操作。 */
  const evidence = computed(() => {
    const total = stats.value.nodes
    const pct = (value: number) => (total ? Math.round((value / total) * 100) : 0)
    return {
      total,
      textbook: pct(stats.value.textbook),
      graph: pct(stats.value.graph),
      model: pct(stats.value.model),
      /** 推断超过 40% 就该在顶栏提醒：这张图有相当一部分要自己核对。 */
      warn: total > 0 && stats.value.model / total > 0.4,
    }
  })
  const structure = computed(() => getStructure(structureId.value))

  /**
   * 生成中已经等了多久（秒）。
   *
   * 模型调用是一整段**静默**：`branch` 那一步推完 `step_start` 之后，到第一个节点
   * 事件之间可能 60~120 秒没有任何推送（一次调用拿到整棵树才吐节点）。没有这个
   * 数，用户看到的就是一个不动的圈——「怎么卡住了」的直觉全是这么来的。
   *
   * 纯前端计时，不依赖后端心跳：即使某天后端加了心跳，这里也仍然准。
   */
  const waitedSeconds = ref(0)
  let waitedTimer = 0

  const stopWaiting = () => {
    if (waitedTimer) {
      window.clearInterval(waitedTimer)
      waitedTimer = 0
    }
    waitedSeconds.value = 0
  }

  watch(phase, value => {
    stopWaiting()
    if (value !== 'generating') return
    // 步号归零交给 generate()：`watch` 默认 pre-flush，回调是异步的，
    // 在这里重置会把 generate() 里刚设好的 0 又改回 -1，第一步白丢。
    const startedAt = Date.now()
    waitedTimer = window.setInterval(() => {
      waitedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
    }, 1000)
  })

  let controller: AbortController | null = null
  let saveTimer = 0
  let appearanceTimer = 0
  let saving = false
  let pendingSave = false

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
      // 原来只取 3 条：用户存到第四张图，历史抽屉里就永远只有前三张。
      // 后端上限 100，抽屉自己滚动，这里给一个够用的窗口。
      recent.value = await listRecentMindmaps(30)
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

  const handleEvent = (event: ResourceTaskEvent) => {
    switch (event.event) {
      case 'step_start':
        stepIndex.value = event.step_index
        if (event.total_steps) stepTotal.value = event.total_steps
        if (event.step_label && stepLabels.value[event.step_index] !== event.step_label) {
          const next = [...stepLabels.value]
          next[event.step_index] = event.step_label
          stepLabels.value = next
        }
        break
      case 'step_done':
        stepIndex.value = Math.max(stepIndex.value, event.step_index)
        if (event.total_steps) stepTotal.value = event.total_steps
        break
      case 'node': {
        const node = event.payload.node as ResourceMindmapNode | undefined
        if (!node) break
        if (!nodeTotal.value) nodeTotal.value = node.node_total || 0
        canvas()?.pushNode(node)
        break
      }
      case 'mindmap_verify': {
        // 校验步先于落墨步到达，此刻画布还是空的：三档计数先用后端的，
        // 等树长完由画布实测覆盖（画布数的是真渲染出来的那棵树）。
        const payload = event.payload
        const counts = payload.stats as { node_count?: number; depth?: number } | undefined
        if (counts?.node_count) stats.value = { ...stats.value, nodes: counts.node_count, depth: Number(counts.depth ?? stats.value.depth) }
        const tiers = payload.evidence
        if (tiers) {
          stats.value = {
            ...stats.value,
            textbook: Number(tiers.textbook ?? 0),
            graph: Number(tiers.graph ?? 0),
            model: Number(tiers.model ?? 0),
          }
        }
        if (payload.intent) intentKind.value = payload.intent
        if (payload.intent_label) intentLabel.value = payload.intent_label
        if (payload.intent_note) intentNote.value = payload.intent_note
        degraded.value = Boolean(payload.degraded)
        break
      }
      case 'done':
        stepIndex.value = Math.max(0, event.total_steps - 1)
        void adopt(event.payload.resource_id || '')
        break
      case 'error':
        phase.value = 'failed'
        errorMessage.value = event.payload.message || '炉火不稳，导图未能炼成'
        break
      default:
        break
    }
  }

  /** 生成完成：收卷（等队列排空后才允许编辑），再把版本基线拉回来。 */
  const adopt = async (id: string) => {
    resourceId.value = id
    canvas()?.finish()
    phase.value = 'editing'
    saveStatus.value = 'clean'
    saveMessage.value = '未藏'
    if (!id) return
    try {
      const resource = await getResource(id)
      title.value = resource.title
      version.value = Number((resource.meta?.mindmap_version as number) ?? 1)
      applyAppearanceFrom(resource)
    } catch {
      version.value = 1
    }
    void refreshRecent()
  }

  const applyAppearanceFrom = (resource: ResourceItem) => {
    const meta = resource.meta ?? {}
    const storedTheme = String(meta.mindmap_theme ?? (resource.content as Record<string, unknown> | undefined)?.template_id ?? '')
    const storedStructure = String(meta.mindmap_structure ?? '')
    themeId.value = resolveThemeId(storedTheme || defaultThemeId)
    structureId.value = getStructure(storedStructure).id
    // 问法信息随资源存下来了：从历史里翻出来的图，也该说得出自己按什么组织。
    intentKind.value = String(meta.intent ?? '')
    intentLabel.value = String(meta.intent_label ?? '')
    intentNote.value = String(meta.intent_note ?? '')
    degraded.value = Boolean(meta.degraded)
  }

  const generate = async (invocationId = '') => {
    if (!canGenerate.value) return
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    phase.value = 'generating'
    errorMessage.value = ''
    stepIndex.value = 0
    stepLabels.value = []
    stepTotal.value = 0
    nodeTotal.value = 0
    stats.value = { nodes: 0, depth: 0, textbook: 0, graph: 0, model: 0, folded: 0 }
    intentKind.value = ''
    intentLabel.value = ''
    intentNote.value = ''
    degraded.value = false
    saveStatus.value = 'clean'
    saveMessage.value = '未藏'
    resourceId.value = ''
    title.value = ''
    canvas()?.reset()

    // 走能力契约，不走资源端点：左对话栏是 mindmap.generate 的第一个真实调用方。
    // 契约里的字段名与后端 input_model 一一对应，不做二次改名。
    const free = freeTopic.value.trim()
    // 契约里 query 上限 200 字：输入框同样限长，这里再兜一次，避免粘贴超长被 422。
    const query = (free || selectedNode.value?.name || '').slice(0, 200)
    try {
      // 结构没被用户动过就传 auto，让后端按问法建议（证候题给鱼骨、分类题给向下分支），
      // 生成完由资源 meta 回填到工具栏。
      const accepted = await generateMindmap({
        query,
        node_id: selectedNode.value?.id ?? FREE_TOPIC_NODE_ID,
        depth: depth.value,
        branches: branches.value,
        style: style.value,
        structure: structureTouched.value ? structureId.value : 'auto',
        theme: themeId.value,
        // 幂等键：对话侧一定会重试，重复提交不该出两张图。
        ...(invocationId ? { client_invocation_id: invocationId } : {}),
      })
      await streamResourceTask(accepted.task_id, handleEvent, { signal, retries: 2 })
    } catch (error) {
      if (signal.aborted) return
      phase.value = 'failed'
      errorMessage.value = readable(error)
    }
  }

  /**
   * 对话入口：一句话即一张图。
   *
   * 刻意不复用「选节点 + 点按钮」那条路径的状态：对话是自由输入，
   * 没有知识节点可选，所以统一挂到 FREE_TOPIC_NODE_ID 下。
   */
  const ask = async (text: string, invocationId = '') => {
    const prompt = text.trim()
    if (!prompt) return
    selectedNode.value = null
    freeTopic.value = prompt
    await generate(invocationId)
  }

  /** 「新对话」：清空画布与当前资源绑定，但不删已存的历史。 */
  const clearCanvas = () => {
    controller?.abort()
    controller = null
    canvas()?.reset()
    phase.value = 'idle'
    resourceId.value = ''
    title.value = ''
    errorMessage.value = ''
    stepIndex.value = -1
    stepLabels.value = []
    nodeTotal.value = 0
    stats.value = { nodes: 0, depth: 0, textbook: 0, graph: 0, model: 0, folded: 0 }
    intentKind.value = ''
    intentLabel.value = ''
    intentNote.value = ''
    degraded.value = false
    saveStatus.value = 'clean'
    saveMessage.value = '未藏'
  }

  /**
   * 换结构。记下「这是用户的选择」，之后生成就不再让后端覆盖。
   *
   * 单独开一个动作而不是直接 v-model 到 ref，是为了区分「用户点了」和
   * 「打开历史时程序回填」——后者不该把结构钉死。
   */
  const setStructure = (id: string) => {
    structureId.value = getStructure(id).id
    structureTouched.value = true
  }

  const save = async () => {
    if (!resourceId.value) return
    const tree = canvas()?.getTree()
    if (!tree) return
    if (saving) { pendingSave = true; return }
    saving = true
    saveStatus.value = 'saving'
    try {
      const result = await saveMindmapTree(resourceId.value, {
        version: version.value,
        template_id: themeId.value,
        nodeData: toSaveTree(tree),
      })
      version.value = Number.isFinite(result.version) ? result.version : version.value
      saveStatus.value = 'saved'
      saveMessage.value = `已藏 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false }).slice(0, 5)}`
      // 只接受「真的是个数」的回执：接口异常（超时网关、契约不一致）回了一个空对象时，
      // 若把 nodes 直接写成 undefined，底栏会瞬间归零、空态浮层重新盖到图上——
      // 看上去像「图没了」，其实只是回执没数字（实测踩过）。
      if (Number.isFinite(result.node_count) && Number.isFinite(result.depth)) {
        stats.value = { ...stats.value, nodes: result.node_count, depth: result.depth }
      }
    } catch (error) {
      if (error instanceof MindmapSaveError && error.status === 409) {
        saveStatus.value = 'conflict'
        saveMessage.value = '版本冲突'
      } else {
        saveStatus.value = 'error'
        saveMessage.value = readable(error)
      }
    } finally {
      saving = false
      if (pendingSave) { pendingSave = false; void save() }
    }
  }

  const saveAppearance = async () => {
    if (!resourceId.value || phase.value !== 'editing') return
    try {
      // 同样走契约：present 的语义就是「让它变成这样」，重复点同一个色卡是幂等的。
      await presentMindmap(resourceId.value, { structure: structureId.value, theme_id: themeId.value })
    } catch {
      // 外观是视图偏好：存不上不该打断编辑，下次保存整树时会带上主题。
    }
  }

  const markDirty = () => {
    if (phase.value !== 'editing' || !resourceId.value) return
    if (saveTimer) window.clearTimeout(saveTimer)
    saveTimer = window.setTimeout(() => { saveTimer = 0; void save() }, SAVE_DEBOUNCE_MS)
  }

  const applyResource = async (id: string) => {
    const resource = await getResource(id)
    resourceId.value = resource.id
    title.value = resource.title
    version.value = Number((resource.meta?.mindmap_version as number) ?? 1)
    applyAppearanceFrom(resource)
    const data = (resource.content as Record<string, unknown> | undefined)?.mindmap_data
    canvas()?.setTree(data)
    phase.value = 'editing'
    stepIndex.value = STUDIO_STEPS.length - 1
    saveStatus.value = 'clean'
    saveMessage.value = '未藏'
  }

  const openResource = async (id: string) => {
    try {
      await applyResource(id)
    } catch (error) {
      errorMessage.value = readable(error)
      phase.value = 'failed'
    }
  }

  /** 冲突处置一：放弃本地，拉服务端最新覆盖画布。 */
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
      version.value = Number((resource.meta?.mindmap_version as number) ?? version.value)
      saveStatus.value = 'clean'
      await save()
    } catch (error) {
      errorMessage.value = readable(error)
    }
  }

  const abandon = () => {
    controller?.abort()
    controller = null
    if (saveTimer) { window.clearTimeout(saveTimer); saveTimer = 0 }
    if (appearanceTimer) { window.clearTimeout(appearanceTimer); appearanceTimer = 0 }
  }

  // 结构/配色改动：攒 400ms 再落库，避免连点色卡发一串请求。
  watch([structureId, themeId], () => {
    if (phase.value !== 'editing' || !resourceId.value) return
    if (appearanceTimer) window.clearTimeout(appearanceTimer)
    appearanceTimer = window.setTimeout(() => { appearanceTimer = 0; void saveAppearance() }, APPEARANCE_DEBOUNCE_MS)
  })

  onBeforeUnmount(abandon)
  onBeforeUnmount(stopWaiting)

  return {
    phase,
    errorMessage,
    stepIndex,
    stepStates,
    steps,
    waitedSeconds,
    nodeTotal,
    stats,
    evidence,
    saveStatus,
    saveMessage,
    version,
    resourceId,
    title,
    themeId,
    structureId,
    structure,
    domains,
    allNodes,
    filteredNodes,
    nodesLoading,
    keyword,
    selectedNode,
    freeTopic,
    depth,
    branches,
    style,
    recent,
    isFreeTopic,
    topicLabel,
    canGenerate,
    intentKind,
    intentLabel,
    intentNote,
    degraded,
    structureTouched,
    setStructure,
    loadNodes,
    refreshRecent,
    pickNode,
    pickFreeTopic,
    generate,
    ask,
    clearCanvas,
    markDirty,
    save,
    openResource,
    reloadFromServer,
    keepMine,
    abandon,
  }
}
