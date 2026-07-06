<template>
  <section class="resource-graph" :class="{ 'is-full': full }">
    <header class="graph-toolbar">
      <div>
        <p class="eyebrow">Knowledge Graph</p>
        <strong>{{ title }}</strong>
      </div>
      <div class="graph-actions">
        <el-checkbox-group v-model="activeRelations" size="small">
          <el-checkbox-button v-for="relation in relations" :key="relation" :label="relation">
            {{ relation }}
          </el-checkbox-button>
        </el-checkbox-group>
        <el-button size="small" :icon="Refresh" @click="clearPin">取消固定</el-button>
      </div>
    </header>

    <div ref="chartRef" class="graph-canvas" />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import type { EChartsType } from 'echarts'
import type { GraphPayload } from '../../types/learning'

const props = withDefaults(
  defineProps<{
    payload: GraphPayload
    title?: string
    full?: boolean
  }>(),
  {
    title: '中药知识图谱',
    full: false,
  },
)

const chartRef = ref<HTMLDivElement | null>(null)
const activeRelations = ref<string[]>([])
const pinnedNode = ref('')
let chart: EChartsType | null = null
let resizeObserver: ResizeObserver | null = null

const relations = computed(() => Array.from(new Set(props.payload.links.map((link) => link.relation))))
const visibleLinks = computed(() => {
  if (!activeRelations.value.length) return props.payload.links
  return props.payload.links.filter((link) => activeRelations.value.includes(link.relation))
})
const visibleNodeIds = computed(() => {
  const ids = new Set<string>()
  visibleLinks.value.forEach((link) => {
    ids.add(link.source)
    ids.add(link.target)
  })
  props.payload.nodes.forEach((node) => {
    if (!props.payload.links.length || ids.has(node.id)) ids.add(node.id)
  })
  return ids
})
const visibleNodes = computed(() =>
  props.payload.nodes
    .filter((node) => visibleNodeIds.value.has(node.id))
    .map((node) => ({
      ...node,
      label: {
        show: true,
        color: '#eef5ef',
        fontWeight: 800,
      },
      itemStyle:
        node.id === pinnedNode.value
          ? {
              color: '#e7c274',
              borderColor: '#eef5ef',
              borderWidth: 3,
            }
          : undefined,
    })),
)

const renderGraph = () => {
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value, undefined, { renderer: 'canvas' })
    chart.on('click', (params) => {
      if (params.dataType !== 'node') return
      const id = String((params.data as { id?: string }).id ?? '')
      pinnedNode.value = pinnedNode.value === id ? '' : id
      void nextTick(applyPin)
    })
  }

  chart.setOption(
    {
      backgroundColor: 'transparent',
      color: ['#8fd0ad', '#9fb6d6', '#e7c274'],
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(17,29,32,0.94)',
        borderColor: 'rgba(143,208,173,0.24)',
        textStyle: { color: '#eef5ef' },
        formatter: (params: { dataType?: string; data?: unknown }) => {
          const data = params.data as { name?: string; relation?: string; evidence?: string } | undefined
          if (params.dataType === 'edge') return `${data?.relation ?? '关系'}<br/>${data?.evidence ?? ''}`
          return data?.name ?? ''
        },
      },
      legend: {
        show: true,
        bottom: 0,
        textStyle: { color: 'rgba(238,245,239,0.7)' },
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          top: 6,
          bottom: 36,
          categories: [
            { name: 'herb' },
            { name: 'syndrome' },
            { name: 'formula' },
          ],
          data: visibleNodes.value,
          links: visibleLinks.value.map((link) => ({
            ...link,
            label: { show: true, formatter: link.relation, color: 'rgba(238,245,239,0.72)' },
          })),
          force: {
            repulsion: props.full ? 280 : 180,
            edgeLength: props.full ? 128 : 98,
            gravity: 0.08,
          },
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: 8,
          lineStyle: {
            color: 'source',
            width: 1.4,
            opacity: 0.62,
            curveness: 0.08,
          },
          emphasis: {
            focus: 'adjacency',
            lineStyle: { width: 3, opacity: 0.95 },
          },
        },
      ],
    },
    true,
  )
  applyPin()
}

const applyPin = () => {
  if (!chart) return
  chart.dispatchAction({ type: 'downplay', seriesIndex: 0 })
  if (!pinnedNode.value) return
  const index = visibleNodes.value.findIndex((node) => node.id === pinnedNode.value)
  if (index >= 0) {
    chart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: index })
  }
}

const clearPin = () => {
  pinnedNode.value = ''
  applyPin()
}

onMounted(() => {
  renderGraph()
  if (chartRef.value) {
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(chartRef.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})

watch(
  () => [props.payload, activeRelations.value, pinnedNode.value],
  () => void nextTick(renderGraph),
  { deep: true },
)

watch(
  relations,
  (next) => {
    activeRelations.value = next
  },
  { immediate: true },
)
</script>

<style scoped lang="scss">
@use "../../styles/tokens.scss" as *;

.resource-graph {
  display: grid;
  min-height: 430px;
  grid-template-rows: auto minmax(320px, 1fr);
  gap: 12px;
}

.resource-graph.is-full {
  min-height: calc(100vh - 170px);
}

.graph-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
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

.graph-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.graph-canvas {
  width: 100%;
  min-height: 320px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 18px;
  background: rgba(238, 245, 239, 0.035);
}

.is-full .graph-canvas {
  min-height: calc(100vh - 245px);
}

@media (max-width: 760px) {
  .graph-toolbar {
    display: grid;
  }

  .graph-actions {
    justify-content: flex-start;
  }
}
</style>
