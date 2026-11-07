/**
 * 文档工坊的节奏与工序参数。
 *
 * doc 走后端 ALCHEMY_STEPS['doc'] 五步炼药管线；块是在「排版成卷」步一次性
 * 逐个 SSE 推出的，前端只需按事件到达顺序直接上屏，无需队列节拍。
 */

/** 逐块上屏的最小间隔兜底（与详情页 blockReveal 动画同一量级）。 */
export const BLOCK_REVEAL_INTERVAL_MS = 260

/** 五道工序的中文刻度，用于左栏进度。id 与后端 ALCHEMY_STEPS['doc'] 对应。
 *
 *  工序名按「写一篇文档」取，不按「讲一味药」取：用户要计划、对比、指南时
 *  同样走这五步，写「翻检本草」会与页面内容对不上。
 */
export const DOC_STEPS = [
  { id: 'source', label: '审题定调', short: '审' },
  { id: 'relate', label: '谋篇立序', short: '谋' },
  { id: 'distill', label: '落笔成稿', short: '稿' },
  { id: 'verify', label: '核对引文', short: '校' },
  { id: 'layout', label: '排版成卷', short: '卷' },
] as const

/** 自由主题统一挂在这个真实节点下：`user_resource.node_id` 是外键，不能自造。 */
export const FREE_TOPIC_NODE_ID = 'general'
