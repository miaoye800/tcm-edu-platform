<template>
  <div class="wire-grid">
    <button
      v-for="(page, index) in pages"
      :key="page.unit_id || index"
      type="button"
      class="wire"
      :class="{ 'is-active': index === active }"
      @click="emit('select', index)"
    >
      <svg viewBox="0 0 100 56.25" role="img" :aria-label="`第 ${index + 1} 页：${titleOf(page, index)}`">
        <rect x="0" y="0" width="100" height="56.25" :fill="palette.bg" />

        <!--
          线框不是「另画一套」。它渲染的是**同一份场景**（`fallbackScene`，与生成期、
          导出期同源），只是把文字换成灰条、把图位换成虚线框。

          这样做的理由很实际：以前线框自己读 `frameFor()` 的坐标、自己写 paper→母题
          的 if 分支，于是出现了「线框封面铺远山、成品没有」「线框只画一个角花、成品
          四个」这类分叉——用户确认过的样子，拿到的不是那个样子。
        -->
        <template v-for="(item, i) in elementsOf(page, index)" :key="i">
          <!-- 母题：纸底与角花。母题本身是参数化 SVG，按各自 viewBox 缩放摆放 -->
          <g v-if="item.type === 'motif'" :transform="motifTransform(item)" v-html="motifMarkup(item)" />

          <!-- 图位：灰块 + 槽位说明。素材缺不缺，一眼看得出来 -->
          <g v-else-if="item.type === 'asset'">
            <rect
              :x="item.box[0]"
              :y="item.box[1]"
              :width="item.box[2]"
              :height="item.box[3]"
              :fill="palette.muted"
              fill-opacity="0.14"
              :stroke="palette.line"
              stroke-width="0.2"
              stroke-dasharray="1 1"
              rx="0.6"
            />
            <text
              :x="item.box[0] + item.box[2] / 2"
              :y="item.box[1] + item.box[3] / 2 - 0.6"
              :fill="palette.muted"
              font-size="2"
              text-anchor="middle"
            >主图</text>
            <text
              :x="item.box[0] + item.box[2] / 2"
              :y="item.box[1] + item.box[3] / 2 + 2.4"
              :fill="palette.muted"
              font-size="1.8"
              text-anchor="middle"
            >{{ slotSubject(page) }}</text>
          </g>

          <!-- 线 / 面 / 圆 / 印章：颜色只认角色名，与成品同一套配色 -->
          <rect
            v-else-if="item.type === 'rect' || item.type === 'rule'"
            :x="item.box[0]"
            :y="item.box[1]"
            :width="Math.max(item.box[2], 0.06)"
            :height="Math.max(item.box[3], 0.06)"
            :fill="colorOf(item.fill, 'accent')"
            :opacity="item.opacity"
          />
          <circle
            v-else-if="item.type === 'circle'"
            :cx="item.box[0] + item.box[2] / 2"
            :cy="item.box[1] + item.box[3] / 2"
            :r="Math.min(item.box[2], item.box[3]) / 2"
            :fill="colorOf(item.fill, 'accent')"
            :opacity="item.opacity"
          />
          <rect
            v-else-if="item.type === 'seal'"
            :x="item.box[0]"
            :y="item.box[1]"
            :width="item.box[2]"
            :height="item.box[3]"
            rx="0.4"
            :fill="colorOf(item.fill, 'accent')"
            opacity="0.75"
          />

          <!-- 文字：标题写真字（用户要判断像不像），其余按行换成占位条 -->
          <text
            v-else-if="item.type === 'text' && item.text?.role === 'title'"
            :x="item.box[0]"
            :y="item.box[1] + Math.min(textSize(item), item.box[3] * 0.8)"
            :fill="colorOf(item.text.color, 'fg')"
            :font-size="Math.min(textSize(item), item.box[2] * 0.55)"
            font-weight="600"
          >{{ clip(item.text.content || titleOf(page, index), page.archetype === 'cover' ? 16 : 22) }}</text>
          <template v-else-if="item.type === 'text'">
            <rect
              v-for="(bar, b) in barsOf(item)"
              :key="b"
              :x="bar.x"
              :y="bar.y"
              :width="bar.w"
              :height="bar.h"
              :fill="colorOf(item.text?.color, 'fg')"
              opacity="0.12"
              rx="0.3"
            />
          </template>
        </template>

        <!-- 地脚：位置恒定，不随内容浮动 -->
        <line x1="18" :y1="FOOT_Y - 1.6" x2="91" :y2="FOOT_Y - 1.6" :stroke="palette.line" stroke-width="0.12" />
        <text x="9" :y="FOOT_Y + 0.6" :fill="palette.muted" font-size="1.5">{{ clip(titleOf(page, index), 14) }}</text>
        <text x="91" :y="FOOT_Y + 0.6" :fill="palette.muted" font-size="1.5" text-anchor="end">
          {{ pad(index + 1) }} / {{ pad(pages.length) }}
        </text>
      </svg>
      <span class="wire-cap">
        <i>{{ index + 1 }}</i>
        {{ label(page) }}
        <em v-if="slotBadge(page)">{{ slotBadge(page) }}</em>
      </span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { pptThemeById } from '../../config/pptThemes'
import { motifViewBox, type Scene, type SceneElement } from '../../render/scene'
import { fallbackScene } from '../../render/sceneFallback'
import { renderMotif } from '../../render/motifs'
import { ornamentMotif, paperMotif, paletteFor } from '../../render/skin'
import { ARCHETYPE_META, DENSITY_META, type DeckDesign, type PageDesign } from '../../types/design'

const props = withDefaults(
  defineProps<{
    design: DeckDesign
    /** 页标题（来自方案）。线框上写真实标题，用户才判断得出「像不像」。 */
    titles?: string[]
    active?: number
  }>(),
  { titles: () => [], active: 0 },
)

const emit = defineEmits<{ select: [index: number] }>()

/** 与 `scene_fallback.py` 的 `FOOT_Y` 同数。 */
const FOOT_Y = 50.25

const pages = computed(() => props.design.pages ?? [])
const theme = computed(() => pptThemeById(props.design.theme_id))
const palette = computed(() => paletteFor(props.design.theme_id))

const label = (page: PageDesign) => ARCHETYPE_META[page.archetype]?.label ?? '要点'
const titleOf = (page: PageDesign, index: number) => props.titles[index] || label(page)

const figureSlot = (page: PageDesign) => page.slots?.find((item) => item.slot === 'figure' || item.slot === 'evidence')
const slotSubject = (page: PageDesign) => figureSlot(page)?.subject || '通用'

/** 图位上的角标：有槽位就写「图位」，让用户知道这里一定会放东西或明确不放。 */
const slotBadge = (page: PageDesign) => (figureSlot(page) ? '图位' : '')

/** 占位条数按密度上限走，疏密差异在线框上就看得出来。 */
const ROWS_BY_DENSITY: Record<string, number> = { airy: 3, normal: 4, dense: 5 }
const rowsOf = (page: PageDesign) => ROWS_BY_DENSITY[page.density] ?? DENSITY_META[page.density]?.maxBlocks ?? 4

/**
 * 每页的线框场景。
 *
 * 入参只用到「设计单上真实有的东西」：原型、密度、标题、有没有图位、纸与角饰处方。
 * 内容一律用占位——线框的职责是结构，不是内容。
 */
const sceneOf = (page: PageDesign, index: number): Scene => {
  const rows = rowsOf(page)
  const placeholder = Array.from({ length: rows }, () => '占位')
  const columns =
    page.archetype === 'compare' || page.archetype === 'matrix'
      ? [
          { heading: '甲', items: placeholder.slice(0, 3) },
          { heading: '乙', items: placeholder.slice(0, 3) },
        ]
      : undefined
  const blocks =
    page.archetype === 'data'
      ? [{ type: 'figures', value: '0.0', heading: '占位', source: '' }]
      : page.archetype === 'process' || page.archetype === 'agenda'
        ? [{ type: 'steps', items: placeholder.slice(0, 4) }]
        : []
  return fallbackScene({
    archetype: page.archetype,
    title: titleOf(page, index),
    density: page.density,
    blocks,
    bullets: blocks.length ? [] : placeholder,
    columns,
    figureId: figureSlot(page) ? 'wire-figure' : '',
    figureKind: 'asset',
    figureCaption: slotSubject(page),
    paperMotif: paperMotif(props.design.paper),
    ornamentMotif: ornamentMotif(props.design.ornament_set),
  })
}

/** 按 z 排：与 `SceneSurface` 的绘制顺序必须一致，否则线框的叠压关系是假的。 */
const elementsOf = (page: PageDesign, index: number): SceneElement[] =>
  [...sceneOf(page, index).elements].sort((a, b) => a.z - b.z)

const colorOf = (role: string | null | undefined, fallback: string) => {
  const hex = theme.value.hex as unknown as Record<string, string>
  return `#${hex[role ?? ''] ?? hex[fallback]}`
}

const motifMarkup = (item: SceneElement) =>
  renderMotif(item.motif, { palette: palette.value, params: item.params as Record<string, number> })

const textSize = (item: SceneElement) => item.text?.size ?? 2.2

/**
 * 母题按自己的 viewBox 铺进元素框。
 *
 * 角饰画在 0..20，纸底画在 100×56.25；同一个 `translate + scale` 就能都对。
 * 镜像用负 scale 实现，与 `SceneSurface` 的 `flip` 同一套语义（翻面，不是移位）。
 */
const motifTransform = (item: SceneElement) => {
  const [, , vw, vh] = motifViewBox(item.motif).split(' ').map(Number)
  const [x, y, w, h] = item.box
  const sx = (item.flip === 'x' || item.flip === 'xy' ? -1 : 1) * (w / vw)
  const sy = (item.flip === 'y' || item.flip === 'xy' ? -1 : 1) * (h / vh)
  const tx = item.flip === 'x' || item.flip === 'xy' ? x + w : x
  const ty = item.flip === 'y' || item.flip === 'xy' ? y + h : y
  return `translate(${tx} ${ty}) scale(${sx} ${sy})`
}

interface Bar {
  x: number
  y: number
  w: number
  h: number
}

/** 文字元素换成占位条：一行一条，间距固定，最后一行短一点像收尾。 */
const barsOf = (item: SceneElement): Bar[] => {
  const [x, y, w, h] = item.box
  const size = textSize(item)
  const lineH = Math.min(size * 0.62, 1.6)
  const step = Math.max(lineH * 1.7, 1.4)
  const count = Math.max(1, Math.min(Math.floor(h / step) || 1, 4))
  return Array.from({ length: count }, (_, index) => ({
    x: x,
    y: y + index * step,
    w: index === count - 1 && count > 1 ? w * 0.62 : w,
    h: lineH,
  }))
}

const clip = (text: string, limit: number) => (text.length > limit ? `${text.slice(0, limit)}…` : text)
const pad = (value: number) => String(value).padStart(2, '0')
</script>

<style scoped lang="scss">
.wire-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.wire {
  display: grid;
  gap: 4px;
  padding: 0;
  border: 1px solid var(--qh-line);
  border-radius: 6px;
  background: var(--qh-bg);
  cursor: pointer;
  overflow: hidden;
  transition: border-color var(--qh-duration-fast, 120ms) ease, transform var(--qh-duration-fast, 120ms) ease;
}

.wire:hover {
  border-color: var(--qh-accent);
}

.wire.is-active {
  border-color: var(--qh-accent);
  box-shadow: 0 0 0 1px var(--qh-accent);
}

.wire svg {
  display: block;
  width: 100%;
  height: auto;
  font-family: var(--qh-font-reading, serif);
}

.wire-cap {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 5px;
  color: var(--qh-fg-muted);
  font-size: 11px;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wire-cap i {
  flex: none;
  color: var(--qh-accent);
  font-style: normal;
  font-variant-numeric: tabular-nums;
}

.wire-cap em {
  flex: none;
  margin-left: auto;
  padding: 0 4px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--qh-accent) 12%, transparent);
  color: var(--qh-accent);
  font-style: normal;
  font-size: 10px;
}

@media (max-width: 720px) {
  .wire-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
</style>
