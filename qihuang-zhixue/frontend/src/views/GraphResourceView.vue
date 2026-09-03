<template><main class="graph-resource"><GraphView :content="content" :title="title" :loading="loading" :error="loadError" @expand="expand" @retry="load" /></main></template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import GraphView from '../components/graph/GraphView.vue'
import { useGraphStore, mockMahuangGraph } from '../stores/graph'
import { getResource } from '../services/resourceApi'
import type { GraphContent } from '../types/graph'
const props = defineProps<{ resourceId?: string; content?: GraphContent; title?: string }>(); const route = useRoute(); const store = useGraphStore(); const title = ref(props.title || '资源图谱'); const content = computed(() => props.content || store.content); const loading = ref(false); const loadError = ref(false)
const isGraph = (value: unknown): value is GraphContent => Boolean(value && typeof value === 'object' && Array.isArray((value as GraphContent).nodes) && Array.isArray((value as GraphContent).edges))
const load = async () => { if (props.content) { store.setContent(props.content); return }; loading.value = true; loadError.value = false; try { const resource = await getResource(props.resourceId || String(route.params.id)); title.value = resource.title; store.setContent(isGraph(resource.content) ? resource.content : mockMahuangGraph) } catch { store.setContent(mockMahuangGraph); loadError.value = true } finally { loading.value = false } }
onMounted(load)
const expand = (node: GraphContent['nodes'][number]) => store.setContent({ ...content.value, center_id: node.id })
</script>
<style scoped>.graph-resource{width:min(1500px,100%);margin:0 auto}</style>
