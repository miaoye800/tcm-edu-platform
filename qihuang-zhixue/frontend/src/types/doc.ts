/**
 * Notion 式块模型。
 *
 * 设计取自 Notion 的真实数据模型，但按本项目的规模做了裁剪：
 *  - 继承：`type` 与 `props` 严格解耦。改块类型只改 `type`，`props` 原样保留，
 *    所以「待办 → 标题 → 待办」之后 checked 仍然是 true。这是"转成其他类型"不丢数据的根。
 *  - 裁剪：Notion 每块一条记录、`content` 存子块 id 的递归结构，是为亿级规模与协作设计的。
 *    本项目单用户、单文档 20–60 块，因此扁平成一个 JSON 数组，children 内联。
 *
 * `text` 存「内联 Markdown」字符串（`**粗**`、`` `码` ``、`[n]` 引文），
 * 而不是 Notion 的 interval 样式数组——后端 LLM 天然产出 Markdown，迁移成本最低。
 */

export type BlockType =
  | 'paragraph'
  | 'heading1'
  | 'heading2'
  | 'heading3'
  | 'bulleted'
  | 'numbered'
  | 'todo'
  | 'toggle'
  | 'quote'
  | 'callout'
  | 'divider'
  | 'code'
  | 'table'
  | 'image'
  | 'columns'
  | 'page'

export type CalloutTone = 'info' | 'warn' | 'tip'

export interface SourceRef {
  出处?: string
  原文摘录?: string
  定位?: string
  source?: string
  quote?: string
  ref?: string
  [key: string]: string | undefined
}

export interface TableProps {
  columns: string[]
  rows: string[][]
  header?: boolean
}

export interface CalloutProps {
  tone?: CalloutTone
  icon?: string
}

export interface CodeProps {
  lang?: string
}

export interface PageProps {
  nodeId?: string
  icon?: string
}

export interface ColumnsProps {
  widths?: number[]
}

export interface DocBlockProps {
  /** heading1/2/3 的折叠态；与 Notion 一致，折叠不改类型 */
  collapsed?: boolean
  /** todo */
  checked?: boolean
  /** callout */
  tone?: CalloutTone
  icon?: string
  /** code */
  lang?: string
  /** table */
  columns?: string[]
  rows?: string[][]
  header?: boolean
  /** image */
  assetId?: string
  alt?: string
  caption?: string
  ratio?: string
  /** columns */
  widths?: number[]
  /** page */
  nodeId?: string
  /** quote / 任意块可携带的出处 */
  source?: SourceRef
  /** 保留未识别字段：转类型时不能丢 */
  [key: string]: unknown
}

export interface DocBlock {
  /** 前端生成并持久化，稳定用于 key / 拖拽 / 撤销 */
  id: string
  type: BlockType
  /** 内联 Markdown */
  text?: string
  props?: DocBlockProps
  /** 仅 toggle / columns 使用 */
  children?: DocBlock[]
  meta?: {
    sourceIndex?: number
    /** 计划快照：这个块对应哪个知识点。有它才显示「在地图上看」。 */
    node_id?: string
    acupoint?: string
    domain_id?: string
  }
}

/** 后端可能仍下发旧格式：{ type, text }，其中标题层级编码在 text 的 `#` 前缀里 */
export interface LegacyDocBlock {
  id?: string
  type?: string
  text?: string
  props?: DocBlockProps
  children?: LegacyDocBlock[]
  [key: string]: unknown
}

export const HEADING_TYPES: BlockType[] = ['heading1', 'heading2', 'heading3']

export const isHeading = (type: BlockType): boolean => HEADING_TYPES.includes(type)

/** 容器块：可以容纳子块 */
export const isContainer = (type: BlockType): boolean => type === 'toggle' || type === 'columns'

/** 这些块的正文是纯文本编辑区；表格/图片/分割线/分栏不是 */
export const isTextual = (type: BlockType): boolean =>
  type !== 'divider' && type !== 'table' && type !== 'image' && type !== 'columns' && type !== 'page'

export const headingLevel = (type: BlockType): 1 | 2 | 3 | null =>
  type === 'heading1' ? 1 : type === 'heading2' ? 2 : type === 'heading3' ? 3 : null

/** 页面面包屑的一节 */
export interface Crumb {
  label: string
  to?: string
}

/** 右侧目录条目 */
export interface TocItem {
  id: string
  text: string
  level: number
}
