<template><section class="atlas-view"><GraphView :content="content" :title="title" :loading="loading" :initial-focus-id="focusId" @expand="expand" /></section></template>
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import GraphView from '../components/graph/GraphView.vue'
import { useLearningStore } from '../stores/learning'
import { mockMahuangGraph, useGraphStore } from '../stores/graph'
import type { GraphContent } from '../types/graph'

const route = useRoute()
const learning = useLearningStore()
const graph = useGraphStore()
const content = computed(() => graph.content)
const loading = computed(() => learning.streaming || graph.loading)
const focusId = computed(() => (typeof route.query.focus === 'string' ? route.query.focus : ''))
const centerName = computed(() => content.value.nodes.find(node => node.id === content.value.center_id)?.name)
const title = computed(() => `${centerName.value || learning.activeTopic || '麻黄'} · 关系图谱`)
const isGraph = (value: unknown): value is GraphContent => Boolean(value && typeof value === 'object' && Array.isArray((value as GraphContent).nodes) && Array.isArray((value as GraphContent).edges))

// 换中心节点要重新取子图；只改 center_id 的话，边仍然是旧中心那批。
const expand = (node: GraphContent['nodes'][number]) => { void graph.loadCenter(node.id) }

onMounted(async () => {
  const payload = learning.resources.graph?.payload
  if (isGraph(payload) && payload.nodes.length) { graph.setContent(payload); graph.usingMock = false; return }
  if (await graph.loadDefaultCenter()) return
  graph.setContent(mockMahuangGraph)
})
</script>
<style scoped>.atlas-view{width:min(1500px,100%);margin:0 auto}</style>
