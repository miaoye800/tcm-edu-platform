/** Single source for Stage 4 animation parameters. Future visual tuning belongs here. */
export const stage4Visual = {
  motion: { duration: 0.32, ease: 'easeOut' },
  inspira: { beamSeconds: 5, meteorSeconds: 4.6, pulseSeconds: 2.8 },
  state: { loadingSeconds: 1.7 },
  document: {
    readingLineHeight: 1.82,
    measure: '70ch',
    paragraphGap: '1.25rem',
    blockRevealMs: 250,
    blockStaggerMs: 80,
    blockRevealDistance: '12px',
    citationTransitionMs: 240,
    actionBarBlur: '18px',
  },
  dialogue: {
    // A reveal is a moment of confirmation, not a transition to be missed.
    identityFlipMs: 1500,
  },
} as const

/**
 * The Meridian map's visual contract. Route data keeps its existing status
 * semantics; this object only maps those values to the six presentation states.
 */
export const meridianVisual = {
  direction: '周天星脉',
  layout: { stageHeight: 640, particleCount: 16, nodeRadius: 13, trunkWidth: 6, branchWidth: 2.25 },
  motion: {
    nodePressSeconds: 0.18,
    nodeHoverSeconds: 0.2,
    currentPulseSeconds: 2.8,
    flowSweepSeconds: 9.2,
    unlockSeconds: 0.46,
    qiFlowSeconds: 1.28,
    transitionSeconds: 0.38,
    transitionEase: 'power2.inOut',
    qiFlowEase: 'power1.inOut',
  },
  statusMap: {
    locked: 'locked',
    recommended: 'available',
    free: 'available',
    custom: 'available',
    current: 'current',
    mastered: 'lit',
    deferred: 'deferred',
    exam_hot: 'priority',
  },
  states: {
    locked: { label: '未解锁', ring: 'var(--qh-color-jade-d)', core: 'var(--qh-color-night-soft)', opacity: 0.42, ringWidth: 1.25, dash: '0', glow: '0', badge: false },
    available: { label: '可学习', ring: 'var(--qh-color-jade)', core: 'var(--qh-color-night-soft)', opacity: 1, ringWidth: 1.75, dash: '0', glow: '0 0 10px color-mix(in srgb, var(--qh-color-jade) 28%, transparent)', badge: false },
    current: { label: '当前学习', ring: 'var(--qh-color-jade)', core: 'var(--qh-color-jade-d)', opacity: 1, ringWidth: 2.5, dash: '0', glow: '0 0 16px color-mix(in srgb, var(--qh-color-jade) 50%, transparent)', badge: false },
    lit: { label: '已点亮', ring: 'var(--qh-color-gold)', core: 'var(--qh-color-gold)', opacity: 1, ringWidth: 2, dash: '0', glow: '0 0 14px color-mix(in srgb, var(--qh-color-gold) 38%, transparent)', badge: false },
    deferred: { label: '暂缓', ring: 'var(--qh-color-mist)', core: 'var(--qh-color-night-soft)', opacity: 0.52, ringWidth: 1.5, dash: '3 4', glow: '0', badge: false },
    priority: { label: '考研高频', ring: 'var(--qh-color-gold)', core: 'var(--qh-color-night-soft)', opacity: 1, ringWidth: 2.25, dash: '0', glow: '0 0 12px color-mix(in srgb, var(--qh-color-gold) 34%, transparent)', badge: true },
  },
  particles: [
    [7, 86, 0.18, 0], [14, 68, 0.12, 1.1], [20, 94, 0.16, 2.8], [29, 76, 0.11, 4.2],
    [36, 90, 0.14, 1.8], [44, 73, 0.12, 3.6], [51, 92, 0.15, 0.7], [57, 80, 0.1, 4.8],
    [64, 88, 0.13, 2.2], [70, 70, 0.11, 5.4], [76, 93, 0.15, 1.3], [82, 76, 0.12, 3.1],
    [88, 89, 0.16, 4.4], [92, 72, 0.1, 0.4], [96, 95, 0.14, 2.5], [3, 78, 0.11, 5.8],
  ],
} as const
