import type { GraphEntityType, GraphRelationType } from '../types/graph'

/**
 * 图谱视觉参数。数值来源见 docs/graph-visual-spec.md（参考图像素采样）。
 *
 * 三条铁律：
 * 1. 不用色相区分节点类型 —— 四档层级是同一蓝色系的明度阶梯；
 * 2. 边只做暗示：1px 直线、单色、无箭头无虚线无曲线；
 * 3. 没有容器：无边框、无圆角卡、无阴影、无网格。
 */
export const graphVisual = {
  /** 节点类型（只用于筛选器与详情面板，不用于图上的颜色区分） */
  entity: {
    herb: { label: '药材', color: 'var(--qh-graph-node-herb)' },
    syndrome: { label: '证候', color: 'var(--qh-graph-node-syndrome)' },
    formula: { label: '方剂', color: 'var(--qh-graph-node-formula)' },
    category: { label: '类属', color: 'var(--qh-graph-node-category)' },
  } satisfies Record<GraphEntityType, { label: string; color: string }>,
  /** 关系分组（默认不显色，仅选中节点时高亮相连的边） */
  relation: {
    配伍协同: { color: 'var(--qh-graph-edge-compatible)', group: '配伍协同', description: '相须、相使：共同增强功效' },
    配伍制约: { color: 'var(--qh-graph-edge-restraint)', group: '配伍制约', description: '相畏、相杀、相恶：说明制约关系' },
    配伍禁忌: { color: 'var(--qh-graph-edge-forbidden)', group: '配伍禁忌', description: '相反：重要用药禁忌' },
    功效互参: { color: 'var(--qh-graph-edge-benefit)', group: '功效互参', description: '功效相通、性味相合' },
    方药组成: { color: 'var(--qh-graph-edge-composition)', group: '方药组成', description: '方剂中包含的药材' },
    药证方链: { color: 'var(--qh-graph-edge-evidence)', group: '药证方链', description: '药材关联证候，证候关联方剂' },
    鉴别对照: { color: 'var(--qh-graph-edge-contrast)', group: '鉴别对照', description: '功效或性味差异，帮助辨析' },
    归属聚类: { color: 'var(--qh-graph-edge-cluster)', group: '归属聚类', description: '归经、药类等所属关系' },
  },
  /**
   * 四档层级。font 是 three.js 场景单位字号（不是 CSS px）。
   * 参考图实测：33/20/17/14 px @881 宽，按「占画布宽比例」换算到桌面容器。
   * shell 是球壳半径，节点越多标签越小，靠 labelScale 压缩，球壳不扩张。
   */
  level: [
    { font: 64, color: 'var(--qh-graph-l0)', dot: 0, weight: 600, shell: 0 },
    { font: 38, color: 'var(--qh-graph-l1)', dot: 5.0, weight: 400, shell: 340 },
    { font: 32, color: 'var(--qh-graph-l2)', dot: 3.8, weight: 400, shell: 480 },
    { font: 26, color: 'var(--qh-graph-l3)', dot: 3.0, weight: 400, shell: 600 },
  ],
  edge: {
    line: 'var(--qh-graph-edge-line)',
    opacity: 0.48,
    opacityBack: 0.18,
    focusOpacity: 0.85,
  },
  depth: {
    /** 近浓远淡：opacity = floor + (1 - floor) × t，t 由相机深度归一化得到 */
    minOpacity: 0.56,
    /** 一跳是主体信息，给高地板保证同层明度一致，只留一点点景深 */
    l1Floor: 0.8,
    /** 背面节点标签额外乘的系数。0.42 会把二跳背面压到 #bfd2eb 几乎不可读，收到 0.7 */
    backLabelFactor: 0.7,
    /** 透视修正上限，超过会破坏层级语义 */
    perspectiveClamp: 0.15,
  },
  /**
   * 标签密度降级：节点越多，标签越小、显字的层级越少。
   * 参考图 9 个节点能全显大字；我们 60 个节点必须降级，否则必然叠字。
   */
  density: {
    /** 字号缩放：clamp(min, 1, sqrt(fullAt / n)) */
    fullAt: 20,
    min: 0.62,
    /** 节点数超过 all 时，二跳起不显字 */
    allLabelsUpTo: 24,
    /** 节点数超过 this 时，三跳起不显字且背面不显字 */
    frontOnlyFrom: 40,
    /** 屏幕空间去重叠时两个标签之间至少留的间隙（px） */
    labelGap: 4,
  },
  maxVisibleNodes: 60,
  motion: { introMs: 900, pulseMs: 3200, cameraMs: 420, panelMs: 260, reducedFrameMs: 50 },
  space: {
    centerRadius: 500,
    /** 长焦相机：fov 小 + 距离远，透视收缩温和，接近正交但不失立体感 */
    cameraDistance: 1550,
    fov: 28,
    /**
     * 相机极角系数，phi = π × elevation（three.js 的 phi 从 +y 轴量起）。
     * 0.5 = 完全水平；0.40 = 俯视 18°；0.47 = 俯视 5.4°。
     *
     * 必须贴近 0.5：一旦俯视，世界 z 会被投影到屏幕纵向，椭球的 x/z 对称被破坏，
     * 结果是「正面铺不宽、各方位角形状不一致、转到侧面就塌」。
     * 水平视角 + x/z 等长的椭球，投影与方位角无关，转到哪都是同一个形状。
     */
    elevation: 0.47,
    minCameraDistance: 700,
    maxCameraDistance: 2600,
    autoRotateSpeed: 0.009,
    autoRotateResumeMs: 3000,
    /**
     * 椭球压扁：桌面横屏垂直空间紧张，y 压缩利用水平空间。
     * 取 0.46 让投影长宽比 ≈ 2.17，正好贴合 1156×529 的画布（2.18:1）。
     */
    squashY: 0.46,
    /**
     * z 方向压扁系数。必须和 x 等长（≈1）—— 它决定「转到侧面还剩多少横向空间」。
     * 取 0.28 时 z 几乎没有分布，布局等价于一张扁盘，转 90° 直接塌成一条竖线。
     */
    squashZ: 1.0,
    /**
     * 归一化填充率：布局算完后按画布宽高比缩放到这个占比。
     * 桌面容器是 1156×529（2.18:1），不归一化的话节点少时横向只铺 41%。
     */
    fillX: 0.92,
    fillY: 0.86,
    /** 球壳半径随节点数扩张上限（当前固定，留作调节位） */
    growth: 0,
  },
  canvas: {
    fallbackColor: 'var(--qh-graph-l1)',
    labelFill: 'var(--qh-graph-l1)',
    labelMuted: 'var(--qh-graph-l3)',
    line: 'var(--qh-graph-edge-line)',
  },
} as const

export type GraphRelationGroup = keyof typeof graphVisual.relation
export type GraphLevel = 0 | 1 | 2 | 3

/** 层级钳制：三跳以上统一按三跳渲染 */
export const clampLevel = (level: number): GraphLevel => Math.min(3, Math.max(0, level)) as GraphLevel
export const levelStyle = (level: number) => graphVisual.level[clampLevel(level)]

export const relationGroup = (relation: string): GraphRelationGroup => {
  if (['相须', '相使', '配伍'].includes(relation)) return '配伍协同'
  if (['相畏', '相杀', '相恶'].includes(relation)) return '配伍制约'
  // 「十八反 / 十九畏」是 P2 重建后 T1 硬约束边的正式类型名（教材 p128 配伍禁忌）。
  // 旧数据用的是「相反」；不认这两个新名字，禁忌边会掉进 default 分支被染成
  // 「功效互参」——最该刺眼的一类边反而看起来最温和，而「配伍禁忌」筛选器
  // 会一条都筛不出来。
  if (['相反', '十八反', '十九畏'].includes(relation)) return '配伍禁忌'
  if (relation === '组成') return '方药组成'
  if (['药证链', '证方链'].includes(relation)) return '药证方链'
  if (['同类鉴别', '性味相反'].includes(relation)) return '鉴别对照'
  if (['归经聚类', '归属', '类属'].includes(relation)) return '归属聚类'
  return '功效互参'
}

export const relationStyle = (relation: string) => graphVisual.relation[relationGroup(relation)]
export const relationLabel = (relation: string) => relation as GraphRelationType
