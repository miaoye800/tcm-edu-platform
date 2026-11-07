export type ResourceType = 'doc' | 'graph' | 'mindmap' | 'quiz' | 'story' | 'ppt'
export type ResourceStatus = 'generating' | 'ready' | 'failed'

export interface ResourceSource { [key: string]: string | undefined; source?: string; title?: string; quote?: string; ref?: string; '出处'?: string; '原文摘录'?: string; '定位'?: string }
export interface ResourceItem { id: string; node_id: string; type: ResourceType; title: string; content?: Record<string, unknown>; sources?: ResourceSource[]; notes?: string; status: ResourceStatus; created_at: string; favorited?: boolean; meta?: Record<string, unknown>; node_name?: string }
export interface ResourceListResponse { items: ResourceItem[]; total: number; offset: number; limit: number }
export type DocBlockType = 'heading' | 'paragraph' | 'bullet' | 'table' | 'callout' | 'quote'
export interface ResourceDocBlock { type: DocBlockType | string; text: string }
/** 依据等级：教材实证 / 图谱关系 / 模型推断。 */
export type MindmapEvidence = 'textbook' | 'graph' | 'model'
/** 三档依据的节点数。三数之和恒等于节点总数，底栏占比直接除它。 */
export interface MindmapEvidenceCounts { textbook: number; graph: number; model: number }
/** 节点在树里的身份。根与一级分支画法不同，缩略图也靠它取舍。 */
export type MindmapNodeRole = 'root' | 'branch' | 'leaf'
/** 导图逐节点事件：父节点必定先于子节点到达，前端靠 id 去重。 */
export interface ResourceMindmapNode {
  id: string
  parent_id: string | null
  topic: string
  depth: number
  order: number
  direction: number
  source_indexes: number[]
  node_total: number
  /** 后端 9 步流水线开始下发；旧任务没有这个字段。 */
  evidence?: MindmapEvidence
  role?: MindmapNodeRole
}
/** 校验步的统计：只裁剪越界依据，不删节点。 */
export interface ResourceMindmapStats { node_count: number; grounded: number; trimmed: number }
/** 校色步的统计：只统计，不改内容。 */
export interface ResourceDeckStats { slides: number; bullets: number; columns: number; grounded: number; notes: number }
/** Back end emits body citations as zero-based indexes, such as [0] for sources[0]. */
export interface ResourceTaskPayload {
  step_id?: string
  resource_id?: string
  message?: string
  detail?: string
  index?: number
  block?: ResourceDocBlock
  node?: ResourceMindmapNode
  stats?: ResourceMindmapStats | ResourceDeckStats
  /** 导图校验步：三档依据的节点数，与 stats 同一时刻算出。 */
  evidence?: MindmapEvidenceCounts
  /** 问法判定结果。`intent_note` 是后端写好的一句话，前端不自己拼中文。 */
  intent?: string
  intent_label?: string
  intent_note?: string
  degraded?: boolean
  template_id?: string
  /** 幻灯谋篇的大纲页与总页数。 */
  outline?: ResourceOutlineSlide[]
  total?: number
  deck_title?: string
  /** 敷演步逐页推送的整页内容。 */
  slide?: ResourceSlide
  /** 暂停原因：`await_outline` 表示等用户确认大纲。 */
  reason?: string
  theme_id?: string
}

/** 幻灯事件里的页数据，结构直接复用 `types/ppt.ts` 的 SlideNode。 */
export interface ResourceSlide {
  id: string
  kind: string
  title: string
  subtitle: string
  bullets: string[]
  columns: Array<{ heading: string; items: string[] }>
  note: string
  source_indexes: number[]
}

export interface ResourceOutlineSlide {
  id: string
  title: string
  points: string[]
  source_indexes: number[]
}

export interface ResourceTaskEvent {
  /**
   * `pause` 是幻灯「先定大纲」模式的终止事件：服务端推完大纲就断流，
   * 前端必须把它当成一次正常结束，否则会重连风暴（重连后又收一遍 outline）。
   */
  event: 'step_start' | 'step_done' | 'block' | 'token' | 'done' | 'error' | 'node' | 'mindmap_verify' | 'outline' | 'slide' | 'deck_verify' | 'pause'
  step_index: number
  total_steps: number
  step_label: string
  payload: ResourceTaskPayload
}
export interface ResourceFilters { type?: ResourceType; nodeId?: string; favorited?: boolean; q?: string; offset?: number; limit?: number }

export const displayCitation = (zeroBased: number) => zeroBased + 1
/** Table blocks arrive as Markdown text; malformed or non-table text intentionally falls back to a paragraph. */
export const parseMarkdownTable = (text: string): string[][] => text.split(/\r?\n/).map(line => line.trim()).filter(line => line.startsWith('|')).filter((line, index) => index !== 1 || !/^\|?\s*:?-{3,}/.test(line.split('|').join('').trim())).map(line => line.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim()))
