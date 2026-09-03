<template><section class="atlas-view"><GraphView :content="content" :title="`${learning.activeTopic || '麻黄'} · 全局图谱`" :loading="learning.streaming" :initial-focus-id="focusId" @expand="expand" /></section></template>
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import GraphView from '../components/graph/GraphView.vue'
import { useLearningStore } from '../stores/learning'
import { mockMahuangGraph, useGraphStore } from '../stores/graph'
import type { GraphContent } from '../types/graph'
const route = useRoute(); const learning = useLearningStore(); const graph = useGraphStore(); const content = computed(() => graph.content); const focusId = computed(() => typeof route.query.focus === 'string' ? route.query.focus : '')
const isGraph = (value: unknown): value is GraphContent => Boolean(value && typeof value === 'object' && Array.isArray((value as GraphContent).nodes) && Array.isArray((value as GraphContent).edges))
const expand = (node: GraphContent['nodes'][number]) => graph.setContent({ ...content.value, center_id: node.id })
onMounted(() => { const payload = learning.resources.graph?.payload; graph.setContent(isGraph(payload) ? payload : mockMahuangGraph) })
</script>
<style scoped>.atlas-view{width:min(1500px,100%);margin:0 auto}</style>
