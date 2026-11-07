export const immersiveVisual = {
  transitionMs: 380,
  transitionEase: 'power2.inOut',
  inkEdgeFrequency: '.018',
  surface: 'rgba(19, 31, 29, .68)',
  qiqiSurface: 'rgba(46, 83, 68, .42)',
  userSurface: 'rgba(112, 83, 45, .28)',
  vignetteOpacity: '.12',
  mascotCrossfadeMs: 150,
  elixirReleaseMs: 480,
  cardStaggerMs: 70,
} as const

export const resourceTypeLabels = { doc: '书卷讲解', graph: '经络图谱', mindmap: '枝叶导图', quiz: '签筒问答', story: '戏台演绎', ppt: '讲稿幻灯' } as const

export const resourceStatusStyles = {
  generating: { border: 'rgba(143,208,173,.2)', accent: 'var(--qh-color-jade)', label: '生成中' },
  ready: { border: 'rgba(143,208,173,.2)', accent: 'var(--qh-color-gold)', label: '已收录' },
  failed: { border: 'rgba(233,167,156,.42)', accent: 'var(--qh-color-coral)', label: '待重炼' },
} as const
