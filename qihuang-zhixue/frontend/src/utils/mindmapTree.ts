/**
 * 导图树的纯函数工具。
 *
 * 全部无副作用：生长队列、脏标记、保存前的净化都靠这些小函数，
 * 组件里只剩「什么时候调」，不再有一层层的嵌套遍历。
 */

/** 依据等级：教材实证 / 图谱关系 / 模型推断。只在 model 上做标记。 */
export type MindmapEvidence = 'textbook' | 'graph' | 'model'

export interface MindmapTreeNode {
  id: string
  topic: string
  direction?: number
  evidence?: MindmapEvidence
  children?: MindmapTreeNode[]
  meta?: {
    source_indexes?: number[]
    evidence?: MindmapEvidence
    /** 计划快照里的知识点 id。叶子有它就能「去地图」。 */
    node_id?: string
    acupoint?: string
    branch_id?: string
  }
}

export interface SaveTreeNode {
  id: string
  topic: string
  direction: number
  evidence?: MindmapEvidence
  meta: { source_indexes: number[]; [key: string]: unknown }
  children: SaveTreeNode[]
}

/**
 * 计划快照的锚点。
 *
 * 叶子上带 `node_id`，点一下能跳回地图那一格；带着它存盘，下次打开还认得。
 * 这三个键必须在**两个方向**的转换里都保留：往库里灌（`toSimpleTree`）丢一次、
 * 从库里读（`simpleToTree` / `normalizeTree`）再丢一次，锚点就没了——
 * 表现是"叶子上写着知识点名，点了没反应"，而且不会有任何报错。
 */
const ANCHOR_KEYS = ['node_id', 'acupoint', 'branch_id'] as const

export const anchorsOf = (meta: unknown): Record<string, string> => {
  const source = (meta ?? {}) as Record<string, unknown>
  const out: Record<string, string> = {}
  for (const key of ANCHOR_KEYS) {
    const value = source[key]
    if (typeof value === 'string' && value) out[key] = value
  }
  return out
}

export const countNodes = (node: MindmapTreeNode | null | undefined): number => {
  if (!node) return 0
  return 1 + (node.children ?? []).reduce((total, child) => total + countNodes(child), 0)
}

/** 层数按「根算第一层」计，和保存接口返回的 depth 同一口径。 */
export const depthOf = (node: MindmapTreeNode | null | undefined): number => {
  if (!node) return 0
  return 1 + Math.max(0, ...(node.children ?? []).map(child => depthOf(child)))
}

/** 层序展开。父节点必定排在子节点之前。 */
export const flatten = (root: MindmapTreeNode | null | undefined): MindmapTreeNode[] => {
  if (!root) return []
  const queue: MindmapTreeNode[] = [root]
  const result: MindmapTreeNode[] = []
  while (queue.length) {
    const node = queue.shift() as MindmapTreeNode
    result.push(node)
    queue.push(...(node.children ?? []))
  }
  return result
}

export const findParentId = (root: MindmapTreeNode | null | undefined, id: string): string | null => {
  if (!root || !id) return null
  const queue: Array<{ node: MindmapTreeNode; parent: string | null }> = [{ node: root, parent: null }]
  while (queue.length) {
    const { node, parent } = queue.shift() as { node: MindmapTreeNode; parent: string | null }
    if (node.id === id) return parent
    queue.push(...(node.children ?? []).map(child => ({ node: child, parent: node.id })))
  }
  return null
}

/** 只保留导图结构字段：渲染库的 getData() 会带 uid/expand/style 等排版字段。 */
export const toSaveTree = (node: MindmapTreeNode): SaveTreeNode => ({
  id: node.id,
  topic: node.topic,
  direction: node.direction === 1 ? 1 : 0,
  ...(node.evidence ? { evidence: node.evidence } : {}),
  meta: { source_indexes: [...(node.meta?.source_indexes ?? [])], ...anchorsOf(node.meta) },
  children: (node.children ?? []).map(child => toSaveTree(child)),
})

/** 按 id 去重，保留首次出现（后端重连时会重推同一批节点）。 */
export const dedupeById = <T extends { id: string }>(nodes: T[]): T[] => {
  const seen = new Set<string>()
  return nodes.filter(node => {
    if (!node.id || seen.has(node.id)) return false
    seen.add(node.id)
    return true
  })
}

/* ------------------------------------------------------------------
 * simple-mind-map 适配层
 *
 * 0.14 的输入格式与旧库完全不同，两处硬性差异（已在 node_modules 核实）：
 *   1. 树是 wrapper 结构：每个节点 { data: {...}, children: [...] }，
 *      而不是扁平的 { id, topic, children }（src/layouts/Base.js:238）。
 *   2. 节点文字字段叫 text，不再叫 topic
 *      （src/core/render/node/nodeCreateContents.js:236 读 getData('text')）。
 *
 * 这两条踩错任意一条，画布上都只会出现一个没有文字的空节点，
 * 而且不会报错——所以转换必须集中在这里，不散落到组件里。
 * ------------------------------------------------------------------ */

export interface SimpleTreeNode {
  data: { id: string; text: string; [key: string]: unknown }
  children: SimpleTreeNode[]
}

export interface SimpleTreeOptions {
  /**
   * 四档层级。
   *
   * 库只有 root / second / node 三档（`Style.js:86`），第二与第三层共用
   * `node` 样式。参考图的写法是「一级药牌、二级纯文字、三级白底细框」，
   * 所以这里把第二层的底板与描边抹掉、字号降自重，让它退成纯文字标签。
   * `Style.merge` 优先读节点自身属性，所以这些键会覆盖主题（已核实）。
   */
  fourTier?: boolean
  /** 「模型推断」角标的文案，传空则不打标。 */
  inferredTag?: string
  /**
   * 该结构能清楚表达的层数上限。
   *
   * 超过就折叠到这一层（`data.expand = false`，库里 0.14 认这个字段，
   * 见 `MindMapNode.js:206`），并让节点自己长出「+N」的展开角标。
   * 折叠而不是丢弃：点开还在，只是先不画。
   */
  maxDepth?: number
}

const INFERRED_TAG = '推断'

/** 后端/存储格式 → simple-mind-map 渲染格式。 */
export const toSimpleTree = (
  node: MindmapTreeNode,
  options: SimpleTreeOptions = {},
  depth = 0,
): SimpleTreeNode => {
  const evidence = node.evidence ?? node.meta?.evidence
  const tag = options.inferredTag === '' ? '' : options.inferredTag ?? INFERRED_TAG
  const children = node.children ?? []
  // 到上限那层就不再往下画：depth 从 0 起，所以第 maxDepth 层的序号是 maxDepth-1。
  // 注意只折「画不画」，不删子节点：库里折叠的节点仍在数据中（`expand=false`），
  // 这样换回向右分支时 20 个节点一个不少。若在这里把 children 清空，
  // 渲染数据就真的少了——「切一下结构少 8 个节点」是最难解释的那种 bug（踩过）。
  const fold = options.maxDepth != null && depth >= options.maxDepth - 1 && children.length > 0
  // 依据档位写两份：`data.evidence` 给渲染层（角标要看它），
  // `data.meta.evidence` 给回读——库的 getData() 只保证 `data` 里的自有字段回来，
  // 多留一份，任一条被吞掉都不会让整棵树的档位全退化成「教材实证」。
  const meta: Record<string, unknown> = { ...(node.meta ?? {}) }
  if (evidence) meta.evidence = evidence
  return {
    data: {
      id: node.id,
      text: node.topic,
      ...(Object.keys(meta).length ? { meta } : {}),
      ...(typeof node.direction === 'number' ? { direction: node.direction } : {}),
      ...(evidence ? { evidence } : {}),
      ...(fold ? { expand: false } : {}),
      // 角标只给「模型推断」：满屏徽标等于没有徽标。
      ...(evidence === 'model' && tag ? { tag: [tag] } : {}),
      ...(options.fourTier && depth === 2
        ? { fillColor: 'transparent', borderWidth: 0, borderColor: 'transparent', fontSize: 14, fontWeight: '500', paddingX: 4, paddingY: 2 }
        : {}),
    },
    children: children.map(child => toSimpleTree(child, options, depth + 1)),
  }
}

/** 被结构层数上限折叠掉的节点数：状态栏要能说出「藏了多少」，而不是静默。 */
export const countFolded = (
  node: MindmapTreeNode | null | undefined,
  maxDepth: number,
  depth = 0,
): number => {
  if (!node) return 0
  const children = node.children ?? []
  if (depth >= maxDepth - 1) {
    return children.reduce((total, child) => total + countNodes(child), 0)
  }
  return children.reduce((total, child) => total + countFolded(child, maxDepth, depth + 1), 0)
}

/** simple-mind-map 渲染格式 → 后端/存储格式。 */
export const fromSimpleTree = (node: any): MindmapTreeNode => dedupeTreeIds(simpleToTree(node))

const simpleToTree = (node: any): MindmapTreeNode => {
  const evidence = node?.data?.evidence ?? node?.data?.meta?.evidence
  const sourceIndexes = node?.data?.meta?.source_indexes ?? node?.meta?.source_indexes
  return {
    id: String(node?.data?.id ?? ''),
    topic: String(node?.data?.text ?? ''),
    ...(evidence ? { evidence } : {}),
    meta: {
      source_indexes: Array.isArray(sourceIndexes) ? [...sourceIndexes] : [],
      ...anchorsOf(node?.data?.meta ?? node?.meta),
    },
    children: (node?.children ?? []).map(simpleToTree),
  }
}

/**
 * id 去重。
 *
 * 库的「复制粘贴」只重新生成 `uid`，**保留 `data.id`**：粘出来的节点和源节点
 * id 一模一样。渲染层靠 uid 认人，所以画布上看不出问题；但保存接口要求 id 唯一，
 * 于是用户每粘一次，自动保存就静默 422 一次——看起来像「粘贴坏了」。
 * 这里在转换时补一个后缀，让存下去的树始终是合法输入。
 */
export const dedupeTreeIds = (root: MindmapTreeNode): MindmapTreeNode => {
  const seen = new Set<string>()
  let serial = 0
  const walk = (node: MindmapTreeNode): MindmapTreeNode => {
    let id = node.id || `n-${serial++}`
    if (seen.has(id)) {
      let candidate = `${id}-${serial++}`
      while (seen.has(candidate)) candidate = `${candidate}-${serial++}`
      id = candidate
    }
    seen.add(id)
    return { ...node, id, children: (node.children ?? []).map(walk) }
  }
  return walk(root)
}
