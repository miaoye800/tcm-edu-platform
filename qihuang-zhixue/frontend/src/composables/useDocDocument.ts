import { computed, ref } from 'vue'
import type { BlockType, DocBlock } from '../types/doc'
import { createBlock, locateBlock, newBlockId, toMarkdown } from '../utils/docBlocks'
import { saveDocument } from '../services/resourceApi'

export type SaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error'

/**
 * 文档状态 + 操作 + 撤销栈 + 防抖保存。
 *
 * 撤销栈用「整块数组快照」而不是 Notion 的 op + 逆操作：
 * 本项目单文档 20–60 块，一次快照不到 20KB，快照实现没有逆操作的边界 case；
 * 抄 Notion 的 op 体系在这里只会多出 bug。栈深 100，与 Notion 手感对齐。
 */
export const useDocDocument = (options: {
  resourceId: () => string
  canEdit: () => boolean
  initialBlocks?: DocBlock[]
  initialVersion?: number
  initialMeta?: Record<string, unknown>
}) => {
  const blocks = ref<DocBlock[]>(options.initialBlocks || [])
  const meta = ref<Record<string, unknown>>({ ...(options.initialMeta || {}) })
  const version = ref<number>(options.initialVersion || 1)
  const selectedId = ref('')

  const past = ref<DocBlock[][]>([])
  const future = ref<DocBlock[][]>([])
  const status = ref<SaveStatus>('idle')
  const errorMessage = ref('')
  let timer: number | undefined

  const MAX_HISTORY = 100

  const markdown = computed(() => toMarkdown(blocks.value))

  const snapshot = (): DocBlock[] => JSON.parse(JSON.stringify(blocks.value)) as DocBlock[]

  const record = () => {
    past.value.push(snapshot())
    if (past.value.length > MAX_HISTORY) past.value.shift()
    future.value = []
    status.value = 'dirty'
    schedule()
  }

  const schedule = () => {
    if (timer) window.clearTimeout(timer)
    timer = window.setTimeout(() => { void save() }, 1500)
  }

  const save = async (force = false) => {
    if (!options.canEdit()) return
    if (!force && status.value === 'saving') return
    if (timer) { window.clearTimeout(timer); timer = undefined }
    status.value = 'saving'
    errorMessage.value = ''
    try {
      const result = await saveDocument(options.resourceId(), {
        blocks: blocks.value,
        version: version.value,
        meta: meta.value,
      })
      version.value = result.version
      status.value = 'saved'
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存失败'
      if (message === 'VERSION_CONFLICT') {
        status.value = 'conflict'
        errorMessage.value = '该文档已被重新生成，当前编辑基于旧版本'
        return
      }
      status.value = 'error'
      errorMessage.value = message
    }
  }

  const replaceInTree = (list: DocBlock[], id: string, replacer: (block: DocBlock) => DocBlock): DocBlock[] =>
    list.map(block => {
      if (block.id === id) return replacer(block)
      if (block.children?.length) return { ...block, children: replaceInTree(block.children, id, replacer) }
      return block
    })

  const removeInTree = (list: DocBlock[], id: string): DocBlock[] =>
    list
      .filter(block => block.id !== id)
      .map(block => (block.children?.length ? { ...block, children: removeInTree(block.children, id) } : block))

  const update = (next: DocBlock) => {
    if (!options.canEdit()) return
    record()
    blocks.value = replaceInTree(blocks.value, next.id, () => next)
  }

  const updateMany = (list: DocBlock[]) => {
    if (!options.canEdit()) return
    record()
    blocks.value = list
  }

  const insertAfter = (id: string, block?: DocBlock) => {
    if (!options.canEdit()) return block || createBlock('paragraph')
    record()
    const next = block || createBlock('paragraph')
    const index = blocks.value.findIndex(item => item.id === id)
    if (index < 0) blocks.value = [...blocks.value, next]
    else blocks.value = [...blocks.value.slice(0, index + 1), next, ...blocks.value.slice(index + 1)]
    selectedId.value = next.id
    return next
  }

  const insertAt = (index: number, block?: DocBlock) => {
    if (!options.canEdit()) return block || createBlock('paragraph')
    record()
    const next = block || createBlock('paragraph')
    const clamped = Math.max(0, Math.min(index, blocks.value.length))
    blocks.value = [...blocks.value.slice(0, clamped), next, ...blocks.value.slice(clamped)]
    return next
  }

  const append = (block?: DocBlock) => {
    if (!options.canEdit()) return block || createBlock('paragraph')
    record()
    const next = block || createBlock('paragraph')
    blocks.value = [...blocks.value, next]
    return next
  }

  const remove = (id: string) => {
    if (!options.canEdit()) return
    record()
    const next = removeInTree(blocks.value, id)
    // 至少留一个可编辑块，否则页面会"空掉"且无法再输入
    blocks.value = next.length ? next : [createBlock('paragraph')]
  }

  const move = (sourceId: string, targetId: string, hint: 'before' | 'after') => {
    if (!options.canEdit() || sourceId === targetId) return
    record()
    const list = [...blocks.value]
    const fromIndex = list.findIndex(block => block.id === sourceId)
    if (fromIndex < 0) return
    const [moved] = list.splice(fromIndex, 1)
    const targetIndex = list.findIndex(block => block.id === targetId)
    if (targetIndex < 0) return
    list.splice(hint === 'after' ? targetIndex + 1 : targetIndex, 0, moved)
    blocks.value = list
  }

  /** 转类型只改 type，props 原样保留 —— 这是 Notion「转成」不丢数据的核心 */
  const turnInto = (id: string, type: BlockType) => {
    if (!options.canEdit()) return
    record()
    blocks.value = replaceInTree(blocks.value, id, block => {
      if (block.type === 'table' || block.type === 'image' || block.type === 'columns') return block
      return { ...block, type }
    })
  }

  const duplicate = (id: string) => {
    if (!options.canEdit()) return
    const found = locateBlock(blocks.value, id)
    if (!found) return
    record()
    const copy: DocBlock = {
      ...found.block,
      id: newBlockId(),
      children: found.block.children?.map(child => ({ ...child, id: newBlockId() })),
    }
    found.siblings.splice(found.index + 1, 0, copy)
    blocks.value = [...blocks.value]
  }

  const undo = () => {
    const previous = past.value.pop()
    if (!previous) return
    future.value.push(snapshot())
    blocks.value = previous
    status.value = 'dirty'
    schedule()
  }

  const redo = () => {
    const next = future.value.pop()
    if (!next) return
    past.value.push(snapshot())
    blocks.value = next
    status.value = 'dirty'
    schedule()
  }

  const setMeta = (patch: Record<string, unknown>) => {
    meta.value = { ...meta.value, ...patch }
    status.value = 'dirty'
    schedule()
  }

  /** 编辑器内部的块级数组直接替换（拖拽/流式追加用），不进撤销栈 */
  const setBlocksSilently = (next: DocBlock[]) => { blocks.value = next }

  const canUndo = computed(() => past.value.length > 0)
  const canRedo = computed(() => future.value.length > 0)

  return {
    blocks, meta, version, selectedId, status, errorMessage, markdown,
    canUndo, canRedo,
    update, updateMany, insertAfter, insertAt, append, remove, move, turnInto, duplicate,
    undo, redo, setMeta, setBlocksSilently, save,
  }
}
