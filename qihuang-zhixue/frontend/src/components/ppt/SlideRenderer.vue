<template>
  <!--
    有场景就走场景渲染器：模型的几何说了算。
    没有场景（旧稿、兜底失败、还没走到设计步）就走旧渲染器：至少不白屏。
  -->
  <SceneSurface
    v-if="scene"
    :scene="scene"
    :theme-id="themeId"
    :font-pair-id="fontPairId"
    :index="index"
    :total="total"
    :deck-title="deckTitle"
    :slide-id="slide.id"
    :assets="assets"
    :palette="design?.palette"
    :hide-foot="hideFoot"
  />
  <SlideSurface
    v-else
    :slide="slide"
    :theme-id="themeId"
    :index="index"
    :total="total"
    :deck-title="deckTitle"
    :paper="design?.paper_motif || design?.paper"
    :ornament="design?.ornament_motif || design?.ornament_set"
    :interactive="interactive"
    @update="$emit('update', $event)"
    @update-column="$emit('update-column', $event)"
    @add-bullet="$emit('add-bullet')"
    @remove-bullet="$emit('remove-bullet', $event)"
  />
</template>

<script setup lang="ts">
/**
 * 渲染调度器。
 *
 * 存在的唯一理由是**切换期**：旧渲染器按 `archetype` 分支画十个版式，新渲染器按
 * `Scene` 画九个基元。两者要并存一段时间，因为旧稿没有 `scene`，而重新生成整份稿
 * 才能补上——不能因为换了渲染器就让用户手里的稿子打不开。
 *
 * 调度点放在这里而不是散在三个调用方：`DeckStage`（舞台）、`SlideFilmstrip`（胶片条）、
 * `PptDeckLab`（调试页）都读同一份判断，才不会出现「舞台是新版、缩略图是旧版」。
 */
import { computed } from 'vue'
import SceneSurface from './SceneSurface.vue'
import SlideSurface from './SlideSurface.vue'
import { layoutOf } from '../../utils/slideBlocks'
import { normalizeScene } from '../../render/scene'
import { assetMapOf } from '../../render/assetSrc'
import type { SlideNode } from '../../types/ppt'
import type { DeckDesign } from '../../types/design'

const props = withDefaults(
  defineProps<{
    slide: SlideNode
    themeId: string
    index?: number
    total?: number
    deckTitle?: string
    design?: DeckDesign | null
    fontPairId?: string
    interactive?: boolean
  }>(),
  { index: 0, total: 1, deckTitle: '', design: null, fontPairId: 'song-kai', interactive: false },
)

defineEmits<{
  update: [patch: Partial<SlideNode>]
  'update-column': [payload: { index: number; patch: { heading?: string; items?: string[] } }]
  'add-bullet': []
  'remove-bullet': [index: number]
}>()

const scene = computed(() => (props.slide.scene ? normalizeScene(props.slide.scene) : null))

/**
 * 这一页的素材地址表。
 *
 * 远程素材（馆藏图、生成图）的地址只存在于 `asset_refs` 里，场景里存的是 id——
 * 场景是几何，不该混进一串 URL。两边的接合点就是这张表。
 */
const assets = computed(() => assetMapOf(props.slide.asset_refs))

/** 封面与章节页不压页码：页码是「册子」的东西，封面是封面。 */
const hideFoot = computed(() => ['cover', 'section'].includes(layoutOf(props.slide)))
</script>
