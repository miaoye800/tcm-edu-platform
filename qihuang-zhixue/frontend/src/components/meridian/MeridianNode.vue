<template>
  <g
    class="meridian-node"
    :class="`is-${effectiveVisualState}`"
    :data-node-id="node.id"
    :data-visual-state="effectiveVisualState"
    :transform="`translate(${node.x} ${node.y})`"
    :style="nodeVars"
    :draggable="isDraggable"
    role="button"
    tabindex="0"
    :aria-label="`${node.label}，${visual.label}`"
    @click="$emit('select', node, $event)"
    @keydown.enter.prevent="$emit('select', node, $event)"
    @keydown.space.prevent="$emit('select', node, $event)"
    @contextmenu.prevent="$emit('defer', node)"
    @dragstart="$emit('dragstart', node)"
    @dragover.prevent="$emit('dragover')"
    @drop="$emit('drop', node)"
  >
    <circle class="node-halo" :r="radius + 24" />
    <circle v-if="effectiveVisualState === 'current'" class="node-ripple node-ripple-one" :r="radius + 11" />
    <circle v-if="effectiveVisualState === 'current'" class="node-ripple node-ripple-two" :r="radius + 11" />
    <circle v-if="effectiveVisualState === 'lit'" class="node-lit-orbit" :r="radius + 9" />
    <circle v-if="effectiveVisualState === 'priority'" class="node-double-ring" :r="radius + 8" />
    <circle class="node-ring" :r="radius + 4" />
    <circle class="node-core" :r="radius" />
    <path v-if="effectiveVisualState === 'lit'" class="node-check" d="M-4 0 L-1 3 L5-4" />
    <g v-if="visual.badge" class="node-badge" transform="translate(12 -18)">
      <circle r="7" /><path d="M0-3 L1.6-1.1 L4 0 L1.6 1.1 L0 3 L-1.6 1.1 L-4 0 L-1.6-1.1 Z" />
    </g>
    <g class="node-hover-tag" :transform="`translate(${tooltipX} -43)`">
      <rect :x="tooltipRectX" y="-14" :width="tooltipWidth" height="25" rx="4" />
      <text :x="tooltipTextX" y="3" text-anchor="middle">{{ node.label }}</text>
    </g>
    <text class="node-label" :x="labelX" y="-3" :text-anchor="labelAnchor">{{ node.label }}</text>
    <text class="node-detail" :x="labelX" y="14" :text-anchor="labelAnchor">{{ node.detail }}</text>
  </g>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { meridianVisual } from '../../config/stage4Visual'
import type { MapNode } from '../../types/path'
import type { MeridianPoint } from './meridianStatus'

type MeridianVisualState = keyof typeof meridianVisual.states

type MeridianNodeInput = MapNode | MeridianPoint
const props = defineProps<{ node: MeridianNodeInput; radius: number; visualState?: MeridianVisualState }>()
defineEmits<{
  select: [node: MeridianNodeInput, event: MouseEvent | KeyboardEvent]
  defer: [node: MeridianNodeInput]
  dragstart: [node: MeridianNodeInput]
  dragover: []
  drop: [node: MeridianNodeInput]
}>()

const effectiveVisualState = computed<MeridianVisualState>(() => props.visualState ?? meridianVisual.statusMap[props.node.status] as MeridianVisualState)
const visual = computed(() => meridianVisual.states[effectiveVisualState.value])
const isDraggable = computed(() => 'free' in props.node && props.node.free)
const customColor = computed(() => props.node.status === 'custom' && 'color' in props.node ? props.node.color : undefined)
const labelSide = computed(() => (props.node.x > 760 ? 'left' : 'right'))
const labelX = computed(() => (labelSide.value === 'left' ? -24 : 24))
const labelAnchor = computed(() => (labelSide.value === 'left' ? 'end' : 'start'))
const tooltipWidth = computed(() => Math.max(68, props.node.label.length * 14 + 20))
const tooltipX = computed(() => (labelSide.value === 'left' ? -tooltipWidth.value / 2 - 4 : tooltipWidth.value / 2 + 4))
const tooltipRectX = computed(() => -tooltipWidth.value / 2)
const tooltipTextX = computed(() => 0)
const nodeVars = computed(() => ({
  '--node-ring': customColor.value || visual.value.ring,
  '--node-core': customColor.value || visual.value.core,
  '--node-opacity': String(visual.value.opacity),
  '--node-ring-width': String(visual.value.ringWidth),
  '--node-dash': visual.value.dash,
  '--node-glow': visual.value.glow,
}))
</script>

<style scoped lang="scss">
.meridian-node {
  cursor: pointer;
  opacity: var(--node-opacity);
  transform-box: fill-box;
  transform-origin: center;
  transition: opacity var(--meridian-node-hover-seconds) ease-out;
}

.node-halo { fill: var(--node-ring); opacity: 0; filter: blur(8px); }
.node-ring { fill: var(--qh-color-night-soft); stroke: var(--node-ring); stroke-width: var(--node-ring-width); stroke-dasharray: var(--node-dash); filter: drop-shadow(var(--node-glow)); }
.node-core { fill: var(--node-core); stroke: var(--qh-color-cream); stroke-width: 0.9; }
.node-label { fill: var(--qh-text); font-family: var(--qh-font-display); font-size: 14px; font-weight: 800; letter-spacing: 0; pointer-events: none; }
.node-detail { fill: var(--qh-text-muted); font-family: var(--qh-font-body); font-size: 10px; letter-spacing: 0; pointer-events: none; }
.node-hover-tag { opacity: 0; pointer-events: none; transform-box: fill-box; transform-origin: center; transition: opacity var(--meridian-node-hover-seconds) ease-out; }
.node-hover-tag rect { fill: color-mix(in srgb, var(--qh-color-ink) 92%, transparent); stroke: color-mix(in srgb, var(--node-ring) 52%, transparent); stroke-width: 1; }
.node-hover-tag text { fill: var(--qh-text); font-family: var(--qh-font-body); font-size: 10px; font-weight: 700; }

.meridian-node:hover, .meridian-node:focus-visible { opacity: 1; }
.meridian-node:hover .node-hover-tag, .meridian-node:focus-visible .node-hover-tag { opacity: 1; }
.meridian-node:hover .node-ring, .meridian-node:focus-visible .node-ring { filter: drop-shadow(var(--node-glow)) drop-shadow(0 0 5px color-mix(in srgb, var(--node-ring) 28%, transparent)); }
.meridian-node:focus-visible { outline: none; }
.meridian-node:focus-visible .node-ring { stroke: var(--qh-color-cream); }

.is-available .node-halo { opacity: 0.12; }
.is-current .node-halo { opacity: 0.46; animation: meridian-breathe var(--meridian-pulse-seconds) ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
.node-ripple { fill: none; stroke: var(--node-ring); stroke-width: 1; opacity: 0; transform-box: fill-box; transform-origin: center; animation: meridian-ripple var(--meridian-pulse-seconds) ease-out infinite; }
.node-ripple-two { animation-delay: calc(var(--meridian-pulse-seconds) / -2); }
.node-lit-orbit { fill: none; stroke: var(--qh-color-cream); stroke-width: 1; stroke-dasharray: 4 12; opacity: 0.75; transform-box: fill-box; transform-origin: center; animation: meridian-lit-orbit 3.6s linear infinite; }
.node-check { fill: none; stroke: var(--qh-color-ink); stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.7; }
.node-double-ring { fill: none; stroke: var(--qh-color-gold); stroke-width: 1; opacity: 0.72; }
.node-badge circle { fill: var(--qh-color-night); stroke: var(--qh-color-gold); stroke-width: 1.25; }
.node-badge path { fill: var(--qh-color-gold); }

@keyframes meridian-breathe { 0%, 100% { opacity: 0.2; transform: scale(0.88); } 50% { opacity: 0.54; transform: scale(1.16); } }
@keyframes meridian-ripple { 0% { opacity: 0.48; transform: scale(0.78); } 100% { opacity: 0; transform: scale(1.85); } }
@keyframes meridian-lit-orbit { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  .node-halo, .node-ripple, .node-lit-orbit { animation: none; }
  .is-current .node-halo { opacity: 0.28; transform: scale(1); }
  .node-ripple { display: none; }
}
</style>
