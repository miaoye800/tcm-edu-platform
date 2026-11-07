/**
 * 导图工坊的节奏参数。
 *
 * 这些值都是「观感 vs 时长」的取舍结果，集中放在这里方便统一调：
 * 每轮挂 5 个节点、间隔 90ms，60 节点的图约 1.1 秒长完——
 * 比旧版逐节点 42ms（2.6 秒）快，是因为 simple-mind-map 每轮 setData
 * 都要重建一次 DOM，逐节点的代价随节点数线性上升，反而更卡。
 */

/** 一轮 flush 最多挂几个节点。再多单帧渲染就会掉到 30fps 以下。 */
export const GROW_BATCH = 5
/** 相邻两轮的间隔。 */
export const NODE_INTERVAL_MS = 90
/** 单个节点的生长动画时长，与 `mm-grow` keyframes 保持一致。 */
export const GROW_MS = 320
/** 编辑后的自动保存防抖。 */
export const SAVE_DEBOUNCE_MS = 800
/** 切换结构/配色后的落库防抖：这类操作频繁点选，攒一下再发。 */
export const APPEARANCE_DEBOUNCE_MS = 400
/** 队列上限：超过说明后端推得异常，直接丢弃多余额外的节点防止内存膨胀。 */
export const QUEUE_LIMIT = 400

/**
 * 工序刻度。
 *
 * 后端 9 步流水线会用自己的 `step_label` 覆盖这里的文案，
 * 这张表只在「还没收到任何 step 事件」时兜底，避免进度条空白。
 */
export const STUDIO_STEPS = [
  { id: 'resolve', label: '辨明主题', short: '辨' },
  { id: 'gather', label: '采撷素材', short: '采' },
  { id: 'plan', label: '谋篇立骨', short: '谋' },
  { id: 'compose', label: '敷演枝叶', short: '敷' },
  { id: 'normalize', label: '收束形制', short: '收' },
  { id: 'check', label: '校验枝据', short: '校' },
  { id: 'render', label: '落墨成像', short: '墨' },
  { id: 'persist', label: '封存入库', short: '封' },
  { id: 'handoff', label: '开卷可改', short: '卷' },
] as const
