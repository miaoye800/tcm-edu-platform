<template>
  <DocResourceView v-if="type === 'doc' || type === 'story' || type === 'loading'" />
  <GraphResourceView v-else-if="type === 'graph'" :resource-id="String(route.params.id)" />
  <QuizResourceView v-else-if="type === 'quiz'" />
  <main v-else class="phase-three"><p>资源查看</p><h1>{{ title }}</h1><span>阶段3上线</span><button @click="router.push({ name: 'resources' })">返回资源库</button></main>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DocResourceView from './DocResourceView.vue'
import GraphResourceView from './GraphResourceView.vue'
import QuizResourceView from './QuizResourceView.vue'
import { getResource } from '../services/resourceApi'
import type { ResourceType } from '../types/resource'
const route = useRoute()
const router = useRouter()
const type = ref<ResourceType | 'loading'>('loading')
const title = ref('')
onMounted(async () => {
  try {
    const resource = await getResource(String(route.params.id))
    // 幻灯与导图都没有「只读查看」这一态：它们天生要改、要导、要换结构配色，
    // 所以直接送进各自的工坊打开那一稿。只读视图（MindmapResourceView 等）已删除。
    if (resource.type === 'ppt') {
      await router.replace({ name: 'ppt-studio', query: { resource: resource.id } })
      return
    }
    if (resource.type === 'mindmap') {
      await router.replace({ name: 'mindmap-studio', query: { resource: resource.id } })
      return
    }
    type.value = resource.type
    title.value = resource.title
  } catch {
    type.value = 'doc'
  }
})
</script>
<style scoped>.phase-three{display:grid;min-height:70vh;place-items:center;align-content:center;gap:12px;color:var(--qh-text);text-align:center}.phase-three p{margin:0;color:var(--qh-color-jade);font-size:12px;letter-spacing:1px}.phase-three h1{margin:0;font-family:var(--qh-font-display)}.phase-three span{color:var(--qh-text-muted)}.phase-three button{padding:9px 14px;border:1px solid var(--qh-border);border-radius:4px;color:var(--qh-text);background:transparent;cursor:pointer}</style>
