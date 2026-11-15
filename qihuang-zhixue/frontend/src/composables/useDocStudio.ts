import { computed, onBeforeUnmount, ref } from 'vue'
import { DOC_STEPS, FREE_TOPIC_NODE_ID } from '../config/docStudio'
import { getKnowledgeTree } from '../services/pathApi'
import { confirmResourcePlan, createResourceTask, getResource, getResourceTask, reviseResourcePlan, saveDocument, streamResourceTask } from '../services/resourceApi'
import type { ResourcePlan } from '../types/resource'
import type { KnowledgeNode } from '../types/path'
import type { DocBlock, LegacyDocBlock } from '../types/doc'
import { locateBlock, normalizeBlock } from '../utils/docBlocks'
import { markdownToEditableBlocks, resolveDocBlocks } from '../utils/markdownToBlocks'
import type { ResourceTaskEvent, SectionSource, DocVerifyStats } from '../types/resource'

export type DocPhase = 'idle' | 'generating' | 'awaiting-plan' | 'ready' | 'failed'
export type DocSource = 'generate' | 'import' | 'resource'

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

const readable = (error: unknown) => {
  if (error instanceof Error && error.message) return error.message
  return '炼制失败，请稍后再试'
}

export const useDocStudio = () => {
  const phase = ref<DocPhase>('idle')
  const source = ref<DocSource>('generate')
  const blocks = ref<DocBlock[]>([])
  const resourceId = ref('')
  const title = ref('')
  /** 生成时判定的体裁（学习计划 / 对比分析 / 讲解…），用于页面标识。 */
  const docKind = ref('')
  const stepIndex = ref(-1)
  const errorMessage = ref('')
  const plan = ref<ResourcePlan | null>(null)
  const taskId = ref('')
  const planBusy = ref(false)
  /** 逐节依据：哪一节引了教材、哪一节是模型补充。整篇只标一个「有依据」等于没标。 */
  const sectionSources = ref<SectionSource[]>([])
  const docCheck = ref<DocVerifyStats | null>(null)
  /** 资源元信息：勾选要带着 doc_version 存回去，不然会撞版本冲突。 */
  const resourceMeta = ref<Record<string, unknown>>({})

  const domains = ref<KnowledgeNode[]>([])
  const nodesLoading = ref(false)
  const keyword = ref('')
  const selectedNode = ref<KnowledgeNode | null>(null)
  const freeTopic = ref('')

  const allNodes = computed(() => flattenNodes(domains.value))
  const filteredNodes = computed(() => {
    const key = keyword.value.trim()
    const rows = key ? allNodes.value.filter((row) => row.node.name.includes(key)) : allNodes.value
    return rows.slice(0, 60)
  })
  const topicLabel = computed(() => freeTopic.value.trim() || selectedNode.value?.name || '')
  const canGenerate = computed(() => phase.value !== 'generating' && Boolean(topicLabel.value))
  const stepStates = computed(() =>
    DOC_STEPS.map((step, index) => ({
      ...step,
      state: index < stepIndex.value ? 'done' : index === stepIndex.value ? 'active' : 'pending',
    })),
  )

  let controller: AbortController | null = null

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

  const pickNode = (node: KnowledgeNode) => {
    selectedNode.value = node
    freeTopic.value = ''
  }

  const pickFreeTopic = () => {
    selectedNode.value = null
  }

  const reset = () => {
    errorMessage.value = ''
    stepIndex.value = 0
    blocks.value = []
    resourceId.value = ''
    title.value = ''
    docKind.value = ''
    plan.value = null
    taskId.value = ''
    sectionSources.value = []
    docCheck.value = null
    resourceMeta.value = {}
  }

  const adopt = async (id: string) => {
    resourceId.value = id
    if (!id) return
    try {
      const resource = await getResource(id)
      title.value = resource.title
      resourceMeta.value = { ...(resource.meta || {}) }
      const content = (resource.content || {}) as { doc_kind?: unknown }
      docKind.value = typeof content.doc_kind === 'string' ? content.doc_kind : ''
    } catch {
      title.value = ''
    }
  }

  const handleEvent = (event: ResourceTaskEvent) => {
    switch (event.event) {
      case 'plan':
        plan.value = event.payload.plan ?? null
        break
      case 'pause':
        if (event.payload.reason === 'await_plan') phase.value = 'awaiting-plan'
        break
      case 'step_start':
        stepIndex.value = event.step_index
        break
      case 'step_done':
        stepIndex.value = Math.max(stepIndex.value, event.step_index)
        break
      case 'block': {
        // 后端发的是旧六值 type（heading/bullet…），必须归一成新块模型再上屏。
        const block = normalizeBlock(event.payload.block as LegacyDocBlock)
        if (block) blocks.value.push(block)
        break
      }
      case 'doc_verify':
        // 校验只报问题不改稿：章节是否回应方案、引文编号是否有效，都照实展示。
        docCheck.value = (event.payload.stats as unknown as DocVerifyStats) ?? null
        sectionSources.value = [...(event.payload.source_map ?? [])]
        break
      case 'done':
        stepIndex.value = Math.max(0, event.total_steps - 1)
        phase.value = 'ready'
        void adopt(event.payload.resource_id || '')
        break
      case 'error':
        phase.value = 'failed'
        errorMessage.value = event.payload.message || '炉火不稳，文档未能炼成'
        break
      default:
        break
    }
  }

  const generate = async () => {
    if (!canGenerate.value) return
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    reset()
    source.value = 'generate'
    phase.value = 'generating'

    const params: Record<string, unknown> = {}
    const free = freeTopic.value.trim()
    // 用户诉求要原样送进后端：截到 60 字会把「…，给我制订一份计划」这类关键要求切掉，
    // 后端只能按前半句写。200 字足够容纳一句完整诉求。
    if (free) params.topic = free.slice(0, 200)

    try {
      const task = await createResourceTask(selectedNode.value?.id ?? FREE_TOPIC_NODE_ID, 'doc', params)
      taskId.value = task.task_id
      await streamResourceTask(task.task_id, handleEvent, { signal, retries: 2 })
    } catch (error) {
      if (signal.aborted) return
      phase.value = 'failed'
      errorMessage.value = readable(error)
    }
  }

  /**
   * 修订方案。带上面板里手改过的那一版：用户改完标题又说「第二节拆开」，
   * 服务端若从最初那版重算，手改就被悄悄抹掉了。
   */
  const revisePlan = async (feedback: string, goal = '', draft?: ResourcePlan) => {
    if (!taskId.value || (!feedback.trim() && !goal.trim())) return
    planBusy.value = true
    try {
      const result = await reviseResourcePlan(taskId.value, feedback, { plan: draft ?? plan.value ?? undefined, goal })
      plan.value = result.plan
    } catch (error) { errorMessage.value = readable(error) }
    finally { planBusy.value = false }
  }

  /** 主动退出协商：不生成。任务留在服务端待确认，随时可再进来接着改。 */
  const cancelPlan = () => {
    controller?.abort()
    controller = null
    planBusy.value = false
    phase.value = 'idle'
  }

  const confirmPlan = async (value: ResourcePlan) => {
    if (!taskId.value) return
    planBusy.value = true
    controller?.abort()
    controller = new AbortController()
    phase.value = 'generating'
    try {
      await confirmResourcePlan(taskId.value, value)
      await streamResourceTask(taskId.value, handleEvent, { signal: controller.signal, retries: 2 })
    } catch (error) { phase.value = 'awaiting-plan'; errorMessage.value = readable(error) }
    finally { planBusy.value = false }
  }

  /**
   * 回到同一个任务（刷新、换设备、从资源库点回来）。
   *
   * 待确认的方案必须原样摆回来——重开一次就重新提案的话，用户改过的那些字
   * 等于白改；已确认但没跑完的任务直接续流，不重头生成。
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
      if (snapshot.awaiting_plan && snapshot.plan) {
        phase.value = 'awaiting-plan'
        stepIndex.value = 0
        return
      }
      if (snapshot.status === 'done' && snapshot.resource_id) {
        await openResource(snapshot.resource_id)
        return
      }
      phase.value = 'generating'
      stepIndex.value = Math.max(0, snapshot.current_step)
      await streamResourceTask(id, handleEvent, { signal, retries: 2 })
    } catch (error) {
      if (signal.aborted) return
      phase.value = 'failed'
      errorMessage.value = readable(error)
    }
  }

  /** 粘贴 markdown（含 Notion 导出）→ 本地预览。不入资源库：资源创建必须走任务管线。 */
  const importMarkdown = (markdown: string) => {
    controller?.abort()
    reset()
    source.value = 'import'
    blocks.value = markdownToEditableBlocks(markdown)
    title.value = '导入预览'
    phase.value = 'ready'
  }

  /** 打开已入库的旧稿（route.query.resource）。 */
  const openResource = async (id: string) => {
    controller?.abort()
    reset()
    source.value = 'resource'
    phase.value = 'generating'
    try {
      const resource = await getResource(id)
      blocks.value = resolveDocBlocks(resource.content)
      resourceId.value = resource.id
      title.value = resource.title
      // 版本号也要带着：工坊里勾选待办要按当前版本存回，拿默认 1 去存必撞 409。
      resourceMeta.value = { ...(resource.meta || {}) }
      const content = (resource.content || {}) as { doc_kind?: unknown }
      docKind.value = typeof content.doc_kind === 'string' ? content.doc_kind : ''
      phase.value = 'ready'
    } catch (error) {
      phase.value = 'failed'
      errorMessage.value = readable(error)
    }
  }

  onBeforeUnmount(() => controller?.abort())

  const replaceInTree = (list: DocBlock[], id: string, replacer: (block: DocBlock) => DocBlock): DocBlock[] =>
    list.map((item) => {
      if (item.id === id) return replacer(item)
      if (item.children?.length) return { ...item, children: replaceInTree(item.children, id, replacer) }
      return item
    })

  /**
   * 勾选 / 取消勾选待办。
   *
   * 预览里正文只读，但「这一步做完了没」是用户自己的进度标记：点不动会让人
   * 以为整篇文档都是死的。所以这里**只接受 checked 这一种改动**，其余字段
   * 的 update 一律忽略（工坊不是第二个编辑器），并顺手存回资源——只改本地
   * 等于下次打开又变回没做，正是「改了不生效」。
   *
   * 折叠块里的待办，回传的是父块整体（DocBlockView 的 updateChild 这样设计），
   * 所以要按「哪一项的 checked 变了」把它找出来，别拿父块当待办。
   */
  const toggleCheck = async (block: DocBlock) => {
    let todoId = ''
    let nextChecked = false
    if (block.type === 'todo') {
      todoId = block.id
      nextChecked = !!block.props?.checked
    } else {
      const stored = locateBlock(blocks.value, block.id)?.block
      const changed = (block.children || []).find(
        (child) => child.type === 'todo'
          && (stored?.children || []).find((old) => old.id === child.id)?.props?.checked !== child.props?.checked,
      )
      if (!changed) return
      todoId = changed.id
      nextChecked = !!changed.props?.checked
    }
    blocks.value = replaceInTree(blocks.value, todoId, (item) => ({
      ...item,
      props: { ...(item.props || {}), checked: nextChecked },
    }))
    // 还没入库（流式生成中、粘贴预览）没有可存的地方，本地勾选即可。
    if (!resourceId.value) return
    try {
      const saved = await saveDocument(resourceId.value, {
        blocks: blocks.value,
        version: Number(resourceMeta.value.doc_version || 1),
        meta: resourceMeta.value,
      })
      resourceMeta.value = { ...resourceMeta.value, doc_version: saved.version }
    } catch {
      // 存不上不拦用户：文档可能已被重新生成（版本冲突），下次打开以服务端为准。
    }
  }

  return {
    phase, source, blocks, resourceId, title, docKind, stepIndex, errorMessage, plan, planBusy, revisePlan, confirmPlan, cancelPlan,
    sectionSources, docCheck, toggleCheck,
    domains, nodesLoading, keyword, selectedNode, freeTopic,
    allNodes, filteredNodes, topicLabel, canGenerate, stepStates,
    loadNodes, pickNode, pickFreeTopic, generate, importMarkdown, openResource, restoreTask, reset,
  }
}
