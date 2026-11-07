/**
 * 幻灯工坊的节奏与尺寸参数。
 *
 * 与导图工坊的区别：导图是「一个节点一个节点长」，这里是「一页一页翻」。
 * 260ms/页 是实测值——再快看不清页与页的差别，再慢 16 页要等 5 秒以上。
 */

/** 相邻页进入舞台的间隔。与 `.slide-enter` 动画时长（220ms）错开一点，避免叠帧。 */
export const SLIDE_INTERVAL_MS = 260
/** 编辑后的自动保存防抖，与导图一致。 */
export const SAVE_DEBOUNCE_MS = 1200
/** 手动新增页的默认标题（后端要求标题非空，空字符串会被 422 打回）。 */
export const NEW_SLIDE_TITLE = '新的一页'

/** 四道工序的中文刻度，用于左栏进度。id 与后端 ALCHEMY_STEPS['ppt'] 对应。 */
export const PPT_STEPS = [
  { id: 'frame', label: '立骨', short: '骨' },
  { id: 'outline', label: '谋篇', short: '篇' },
  { id: 'compose', label: '敷演', short: '演' },
  { id: 'polish', label: '校色', short: '色' },
] as const

/** 生成节奏。一键 = 一路跑完；先定大纲 = 谋篇后停下等确认。 */
export const PPT_MODES = [
  { value: 'one_shot', label: '一键出稿', hint: '四道工序跑完，直接给整份幻灯' },
  { value: 'outline', label: '先定大纲', hint: '谋篇后停下，你改完页序再敷演' },
] as const

/** 页数档位。上限 16 是后端 normalize 的裁剪上限。 */
export const PAGE_CHOICES = [8, 12, 16] as const

/** 受众档位，会写进 prompt 影响用词深浅。 */
export const AUDIENCE_CHOICES = [
  { value: '中医药专业本科生', label: '本科' },
  { value: '中医规培与临床医师', label: '规培' },
  { value: '中医药爱好者', label: '科普' },
] as const

/** 自由主题统一挂在这个真实节点下：`user_resource.node_id` 是外键，不能自造。 */
export const FREE_TOPIC_NODE_ID = 'general'
