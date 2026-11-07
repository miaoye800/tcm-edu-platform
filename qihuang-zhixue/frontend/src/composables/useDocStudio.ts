import { computed, onBeforeUnmount, ref } from 'vue'
import { DOC_STEPS, FREE_TOPIC_NODE_ID } from '../config/docStudio'
import { getKnowledgeTree } from '../services/pathApi'
import { createResourceTask, getResource, streamResourceTask } from '../services/resourceApi'
import type { KnowledgeNode } from '../types/path'
import type { DocBlock, LegacyDocBlock } from '../types/doc'
import { normalizeBlock } from '../utils/docBlocks'
import { markdownToEditableBlocks, resolveDocBlocks } from '../utils/markdownToBlocks'
import type { ResourceTaskEvent } from '../types/resource'

export type DocPhase = 'idle' | 'generating' | 'ready' | 'failed'
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
  }

  const adopt = async (id: string) => {
    resourceId.value = id
    if (!id) return
    try {
      const resource = await getResource(id)
      title.value = resource.title
      const content = (resource.content || {}) as { doc_kind?: unknown }
      docKind.value = typeof content.doc_kind === 'string' ? content.doc_kind : ''
    } catch {
      title.value = ''
    }
  }

  const handleEvent = (event: ResourceTaskEvent) => {
    switch (event.event) {
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
      await streamResourceTask(task.task_id, handleEvent, { signal, retries: 2 })
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
      const content = (resource.content || {}) as { doc_kind?: unknown }
      docKind.value = typeof content.doc_kind === 'string' ? content.doc_kind : ''
      phase.value = 'ready'
    } catch (error) {
      phase.value = 'failed'
      errorMessage.value = readable(error)
    }
  }

  onBeforeUnmount(() => controller?.abort())

  return {
    phase, source, blocks, resourceId, title, docKind, stepIndex, errorMessage,
    domains, nodesLoading, keyword, selectedNode, freeTopic,
    allNodes, filteredNodes, topicLabel, canGenerate, stepStates,
    loadNodes, pickNode, pickFreeTopic, generate, importMarkdown, openResource, reset,
  }
}
