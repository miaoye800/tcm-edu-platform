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
        :class="['radar-axis-line', { 'is-low': item.axis.lowConfidence }]"
      />
      <text
        v-for="item in axisEnds"
        :key="`label-${item.axis.key}`"
        :x="item.labelPoint.x"
        :y="item.labelPoint.y"
        :class="['radar-label', { 'is-low': item.axis.lowConfidence }]"
        text-anchor="middle"
        dominant-baseline="middle"
      >
        {{ item.axis.label }}
      </text>
      <polygon :points="fillPoints" class="radar-fill" />
      <line
        v-for="(segment, index) in segments"
        :key="`segment-${index}`"
        :x1="segment.start.x"
        :y1="segment.start.y"
        :x2="segment.end.x"
        :y2="segment.end.y"
        :class="['radar-data-segment', { 'is-low': segment.lowConfidence }]"
        :style="{ '--segment-index': index }"
      />
      <circle
        v-for="(point, index) in radarPoints"
        :key="`point-${axes[index].key}`"
        :cx="point.x"
        :cy="point.y"
        r="5"
        :class="['radar-point', { 'is-low': axes[index].lowConfidence }]"
      />
      <circle :cx="center" :cy="center" r="4" class="radar-center" />
    </svg>
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

const props = defineProps<{
  axes: RadarAxis[]
}>()

const center = 210
const radius = 132
const labelRadius = 173

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

const radarPoints = computed(() => props.axes.map((axis, index) => pointFor(index, axis.value)))
const fillPoints = computed(() => radarPoints.value.map(pointString).join(' '))
const segments = computed(() =>
  props.axes.map((axis, index) => {
    const nextIndex = (index + 1) % props.axes.length
    return {
      start: radarPoints.value[index],
      end: radarPoints.value[nextIndex],
      lowConfidence: axis.lowConfidence || props.axes[nextIndex].lowConfidence,
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

.radar-axis-line.is-low {
  stroke: rgba(223, 236, 228, 0.18);
  stroke-dasharray: 5 5;
}

.radar-label {
  fill: rgba(238, 245, 239, 0.86);
  font-family: var(--qh-font-body);
  font-size: 13px;
  font-weight: 900;
}

.radar-label.is-low {
  fill: rgba(223, 236, 228, 0.42);
}

.radar-fill {
  fill: url("#radarFill");
}

.radar-data-segment {
  stroke: var(--qh-color-jade);
  stroke-linecap: round;
  stroke-width: 3;
  filter: drop-shadow(0 0 8px rgba(143, 208, 173, 0.36));
}

.radar-data-segment.is-low {
  stroke: rgba(159, 182, 214, 0.38);
  stroke-dasharray: 7 7;
  stroke-width: 2.5;
}

.radar-point {
  fill: var(--qh-color-jade);
  stroke: rgba(17, 29, 32, 0.9);
  stroke-width: 2;
}

.radar-point.is-low {
  fill: rgba(223, 236, 228, 0.4);
}

.radar-center {
  fill: var(--qh-color-gold);
  filter: drop-shadow(0 0 10px rgba(231, 194, 116, 0.8));
}
</style>
