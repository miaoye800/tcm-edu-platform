import fishboneContrast from './mindmap-themes/fishbone-contrast.json'
import handScroll from './mindmap-themes/hand-scroll.json'
import herbGarden from './mindmap-themes/herb-garden.json'
import inkScroll from './mindmap-themes/ink-scroll.json'
import neonNight from './mindmap-themes/neon-night.json'
import starChart from './mindmap-themes/star-chart.json'
import timelineProcess from './mindmap-themes/timeline-process.json'
import warmGold from './mindmap-themes/warm-gold.json'

export type MindmapLayout = 'tree' | 'fishbone' | 'timeline' | 'radial'

export interface MindmapTheme {
  id: string
  name: string
  stance: string
  scene: string
  palette: string[]
  layout: MindmapLayout
  nodeStyle: { shape: string; radius: string; density: string; emphasis: string }
  lineStyle: { kind: string; width: string; subWidth: string; decoration: string }
  cssVars: Record<string, string>
}

export const mindmapThemes = [inkScroll, warmGold, neonNight, herbGarden, fishboneContrast, timelineProcess, starChart, handScroll] as MindmapTheme[]
