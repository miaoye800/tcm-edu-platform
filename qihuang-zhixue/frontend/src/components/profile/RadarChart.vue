<template>
  <div class="radar-wrap">
    <svg class="radar-svg" viewBox="0 0 420 420" role="img" aria-label="七维修行罗盘">
      <defs>
        <radialGradient id="radarFill" cx="50%" cy="50%" r="60%">
          <stop offset="0%" stop-color="#e7c274" stop-opacity="0.28" />
          <stop offset="70%" stop-color="#8fd0ad" stop-opacity="0.16" />
          <stop offset="100%" stop-color="#9fb6d6" stop-opacity="0.08" />
        </radialGradient>
      </defs>
      <polygon
        v-for="(points, index) in gridPolygons"
        :key="`grid-${index}`"
        :points="points"
        class="radar-grid"
      />
      <line
        v-for="item in axisEnds"
        :key="`axis-${item.axis.key}`"
        :x1="center"
        :y1="center"
        :x2="item.end.x"
        :y2="item.end.y"
        :class="['radar-axis-line', axisStatusClass(item.axis), { 'is-highlighted': isHighlighted(item.axis) }]"
      />
      <text
        v-for="item in axisEnds"
        :key="`label-${item.axis.key}`"
        :x="item.labelPoint.x"
        :y="item.labelPoint.y"
        :class="['radar-label', axisStatusClass(item.axis), { 'is-highlighted': isHighlighted(item.axis) }]"
        text-anchor="middle"
        dominant-baseline="middle"
      >
        <tspan>{{ item.axis.label }}</tspan>
        <tspan :x="item.labelPoint.x" dy="15" class="radar-confidence">{{ confidenceLabel(item.axis) }}</tspan>
      </text>
      <polygon :points="fillPoints" class="radar-fill" />
      <line
        v-for="(segment, index) in segments"
        :key="`segment-${index}`"
        :x1="segment.start.x"
        :y1="segment.start.y"
        :x2="segment.end.x"
        :y2="segment.end.y"
        :class="['radar-data-segment', { 'is-dashed': segment.dashed, 'is-dark': segment.dark }]"
        :style="{ '--segment-index': index }"
      />
      <circle
        v-for="(point, index) in radarPoints"
        :key="`point-${axes[index].key}`"
        :cx="point.x"
        :cy="point.y"
        r="5"
        :class="['radar-point', axisStatusClass(axes[index]), { 'is-highlighted': isHighlighted(axes[index]) }]"
      />
      <circle :cx="center" :cy="center" r="4" class="radar-center" />
    </svg>
    <div class="radar-legend" aria-label="雷达图确认度图例"><span class="is-solid">实线：确认度至少 50%</span><span class="is-dashed">虚线：已收集但仍待确认</span></div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { RadarAxis } from '../../types/profile'
import { clampScore } from '../../utils/profileMath'

type Point = {
  x: number
  y: number
}

const props = withDefaults(
  defineProps<{
    axes: RadarAxis[]
    highlightedKeys?: string[]
  }>(),
  {
    highlightedKeys: () => [],
  },
)

const center = 210
const radius = 132
const labelRadius = 173

const axisStatus = (axis: RadarAxis): 'dark' | 'dashed' | 'solid' => {
  if (axis.status === 'dark' || axis.lit === false) return 'dark'
  if (axis.status === 'dashed' || axis.lowConfidence) return 'dashed'
  return 'solid'
}

const axisStatusClass = (axis: RadarAxis) => `is-${axisStatus(axis)}`
const isHighlighted = (axis: RadarAxis) => props.highlightedKeys.includes(axis.key)
const confidenceLabel = (axis: RadarAxis) => axisStatus(axis) === 'dark' ? '待取证' : `确认 ${Math.round(Math.max(0, Math.min(1, axis.confidence)) * 100)}%`

const pointFor = (index: number, value: number, activeRadius = radius): Point => {
  const angle = -Math.PI / 2 + (Math.PI * 2 * index) / props.axes.length
  const distance = (clampScore(value) / 100) * activeRadius
  return {
    x: center + Math.cos(angle) * distance,
    y: center + Math.sin(angle) * distance,
  }
}

const pointString = (point: Point): string => `${point.x.toFixed(2)},${point.y.toFixed(2)}`

const gridPolygons = computed(() =>
  [20, 40, 60, 80, 100].map((level) => props.axes.map((_, index) => pointString(pointFor(index, level))).join(' ')),
)

const axisEnds = computed(() =>
  props.axes.map((axis, index) => ({
    axis,
    end: pointFor(index, 100),
    labelPoint: pointFor(index, 100, labelRadius),
  })),
)

const radarPoints = computed(() => props.axes.map((axis, index) => pointFor(index, axisStatus(axis) === 'dark' ? 0 : axis.value)))
const fillPoints = computed(() => radarPoints.value.map(pointString).join(' '))
const segments = computed(() =>
  props.axes.map((axis, index) => {
    const nextIndex = (index + 1) % props.axes.length
    return {
      start: radarPoints.value[index],
      end: radarPoints.value[nextIndex],
      // A segment touching a collected-but-low-confidence axis must stay
      // visibly dashed, even when its other endpoint is still uncollected.
      dashed: axisStatus(axis) === 'dashed' || axisStatus(props.axes[nextIndex]) === 'dashed',
      dark: axisStatus(axis) === 'dark' && axisStatus(props.axes[nextIndex]) === 'dark',
    }
  }),
)
</script>

<style scoped lang="scss">
.radar-wrap {
  position: relative;
  width: 100%;
  max-width: 490px;
  margin: 0 auto;
}

.radar-wrap::before {
  position: absolute;
  inset: 8%;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(231, 194, 116, 0.16), transparent 65%);
  filter: blur(12px);
  animation: pulse 4s ease-in-out infinite;
  content: "";
}

.radar-svg {
  position: relative;
  width: 100%;
  aspect-ratio: 1;
  overflow: visible;
}

.radar-grid {
  fill: none;
  stroke: rgba(143, 208, 173, 0.16);
  stroke-width: 1;
}

.radar-axis-line {
  stroke: rgba(159, 182, 214, 0.3);
  stroke-width: 1.1;
}

.radar-axis-line.is-dashed {
  stroke: rgba(231, 194, 116, 0.68);
  stroke-dasharray: 5 5;
}

.radar-axis-line.is-dark {
  stroke: rgba(223, 236, 228, 0.1);
}

.radar-axis-line.is-highlighted {
  stroke: var(--qh-color-gold);
  stroke-width: 2.2;
  filter: drop-shadow(0 0 8px rgba(231, 194, 116, 0.75));
}

.radar-label {
  fill: var(--qh-radar-label);
  font-family: var(--qh-font-body);
  font-size: 13px;
  font-weight: 900;
}

.radar-label.is-dashed {
  fill: var(--qh-radar-label-muted);
}

.radar-label.is-dark {
  fill: var(--qh-radar-label-faint);
}

.radar-label.is-highlighted {
  fill: var(--qh-color-gold);
}

.radar-confidence { fill: var(--qh-radar-label-muted); font-family: var(--qh-font-body); font-size: 10px; font-weight: 700; }
.radar-label.is-dashed .radar-confidence { fill: var(--qh-color-gold); }
.radar-label.is-dark .radar-confidence { fill: var(--qh-radar-label-faint); }

.radar-fill {
  fill: url("#radarFill");
}

.radar-data-segment {
  stroke: var(--qh-color-jade);
  stroke-linecap: round;
  stroke-width: 3;
  filter: drop-shadow(0 0 8px rgba(143, 208, 173, 0.36));
}

.radar-data-segment.is-dashed {
  stroke: var(--qh-color-gold);
  stroke-dasharray: 8 7;
  stroke-width: 2.8;
  filter: drop-shadow(0 0 6px rgba(231, 194, 116, 0.62));
}

.radar-data-segment.is-dark {
  stroke: rgba(159, 182, 214, 0.16);
  stroke-dasharray: 2 9;
  stroke-width: 1.8;
  filter: none;
}

.radar-point {
  fill: var(--qh-color-jade);
  stroke: rgba(17, 29, 32, 0.9);
  stroke-width: 2;
}

.radar-point.is-dashed {
  fill: var(--qh-color-gold);
  stroke: var(--qh-color-paper-ink);
  stroke-dasharray: 2 2;
  filter: drop-shadow(0 0 5px rgba(231, 194, 116, 0.68));
}

.radar-point.is-dark {
  fill: rgba(223, 236, 228, 0.2);
}

.radar-point.is-highlighted {
  fill: var(--qh-color-gold);
  stroke: rgba(238, 245, 239, 0.96);
  stroke-width: 3;
  transform-box: fill-box;
  transform-origin: center;
  filter: drop-shadow(0 0 10px rgba(231, 194, 116, 0.9));
  animation: highlight-pop 900ms ease both;
}

.radar-center {
  fill: var(--qh-color-gold);
  filter: drop-shadow(0 0 10px rgba(231, 194, 116, 0.8));
}

.radar-legend { display:flex; flex-wrap:wrap; justify-content:center; gap:12px; margin-top:-8px; color:var(--qh-text-muted); font-size:11px; }
.radar-legend span { display:inline-flex; align-items:center; gap:5px; }
.radar-legend span::before { width:22px; height:0; border-top:2px solid var(--qh-color-jade); content:""; }
.radar-legend .is-dashed::before { border-top-color:var(--qh-color-gold); border-top-style:dashed; }

@keyframes highlight-pop {
  0% {
    opacity: 0.5;
    transform: scale(0.82);
  }

  100% {
    opacity: 1;
    transform: scale(1);
  }
}
</style>
