<template>
  <section class="resource-mindmap">
    <header>
      <div>
        <p class="eyebrow">Mindmap</p>
        <strong>{{ payload.root }}</strong>
      </div>
      <el-button size="small" :icon="FullScreen" @click="fit">居中</el-button>
    </header>
    <svg ref="svgRef" class="mindmap-svg" />
  </section>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { FullScreen } from '@element-plus/icons-vue'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'
import type { MindmapPayload } from '../../types/learning'

const props = defineProps<{
  payload: MindmapPayload
}>()

const svgRef = ref<SVGSVGElement | null>(null)
const transformer = new Transformer()
let markmap: Markmap | null = null
let resizeObserver: ResizeObserver | null = null

const render = async () => {
  if (!svgRef.value) return
  const { root } = transformer.transform(props.payload.markdown)
  if (!markmap) {
    markmap = Markmap.create(
      svgRef.value,
      {
        autoFit: true,
        duration: 280,
        color: () => '#8fd0ad',
        maxWidth: 320,
      },
      root,
    )
  } else {
    await markmap.setData(root)
  }
  fit()
}

const fit = () => {
  void nextTick(() => markmap?.fit())
}

onMounted(() => {
  void render()
  if (svgRef.value) {
    resizeObserver = new ResizeObserver(fit)
    resizeObserver.observe(svgRef.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  markmap = null
})

watch(
  () => props.payload.markdown,
  () => void render(),
)
</script>

<style scoped lang="scss">
.resource-mindmap {
  display: grid;
  min-height: 430px;
  grid-template-rows: auto minmax(320px, 1fr);
  gap: 12px;
}

header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.eyebrow {
  margin: 0 0 5px;
  color: var(--qh-color-jade);
  font-size: 12px;
  font-weight: 900;
}

strong {
  font-family: var(--qh-font-display);
  font-size: 18px;
  font-weight: 900;
}

.mindmap-svg {
  width: 100%;
  min-height: 320px;
  border: 1px solid var(--qh-line);
  border-radius: 18px;
  background: var(--qh-line);
}

:deep(.markmap-link) {
  stroke: var(--qh-accent-line);
}

:deep(.markmap-node > circle) {
  fill: var(--qh-surface);
  stroke: #e7c274;
}

:deep(.markmap-node text) {
  fill: #eef5ef;
  font-family: var(--qh-font-body);
}
</style>
