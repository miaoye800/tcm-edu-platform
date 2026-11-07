<template>
  <article class="resource-card" :class="`is-${resource.status}`" :style="{ '--resource-accent': status.accent, '--resource-border': status.border }" @click="emit('open', resource)">
    <div v-if="resource.status === 'generating'" class="shimmer"/>
    <div class="type-icon" :aria-label="resource.type"><svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.6"><path v-if="resource.type === 'doc'" d="M10 12c8-3 13 1 14 4v23c-4-4-9-5-14-3V12Zm28 0c-8-3-13 1-14 4v23c4-4 9-5 14-3V12Z"/><path v-else-if="resource.type === 'graph'" d="M12 14h24M12 34h24M16 14v20m16-20v20M24 14v20"/><path v-else-if="resource.type === 'mindmap'" d="M24 40V23m0 0c-8 0-13-4-14-12m14 12c8 0 13-4 14-12M24 30c-6 0-10 2-12 7m12-7c6 0 10 2 12 7"/><path v-else-if="resource.type === 'quiz'" d="M14 10h20l4 7-4 21H14l-4-21 4-7Zm3 10h14m-7-4v9m-5 8c2-3 8-3 10 0"/><path v-else-if="resource.type === 'ppt'" d="M9 13h30v19H9V13Zm6 6h11m-11 5h18m-18 4h13"/><path v-else d="M12 37V16l12-7 12 7v21M9 39h30M18 19h12m-12 7h12"/></svg></div>
    <div class="copy"><p class="kind">{{ labels[resource.type] }}</p><h3>{{ resource.title }}</h3><p class="knowledge">{{ knowledgePoint }}</p><p class="meta"><time :datetime="resource.created_at">{{ formattedDate }}</time><span>溯源 {{ sourceCount }}</span></p></div>
    <button class="favorite" :class="{ active: resource.favorited }" :aria-label="resource.favorited ? '取消收藏' : '收藏'" @click.stop="emit('favorite', resource)"><svg viewBox="0 0 24 24" fill="currentColor"><path d="m12 20.4-1.3-1.2C5.4 14.4 2 11.3 2 7.5 2 4.4 4.4 2 7.5 2c1.8 0 3.5.8 4.5 2.1C13 2.8 14.7 2 16.5 2 19.6 2 22 4.4 22 7.5c0 3.8-3.4 6.9-8.7 11.7L12 20.4Z"/></svg></button>
  </article>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { resourceStatusStyles, resourceTypeLabels as labels } from '../../config/immersive'
import type { ResourceItem } from '../../types/resource'
const props = defineProps<{ resource: ResourceItem }>(); const emit = defineEmits<{ open: [ResourceItem]; favorite: [ResourceItem] }>()
const sourceCount = computed(() => props.resource.sources?.length || 0)
const status = computed(() => resourceStatusStyles[props.resource.status])
// 「自由讲题」生成时 node_id 是哨兵值 general，直接上屏会在中文界面里露出一串英文。
const FREE_TOPIC_NODE = 'general'
const knowledgePoint = computed(() => {
  const explicit = props.resource.meta?.knowledge_point
  if (explicit) return String(explicit)
  const nodeId = String(props.resource.node_id || '')
  return nodeId && nodeId !== FREE_TOPIC_NODE ? nodeId : '自由讲题'
})
const formattedDate = computed(() => new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(props.resource.created_at)))
</script>
<style scoped lang="scss">
.resource-card{position:relative;display:grid;grid-template-columns:42px minmax(0,1fr) 30px;gap:var(--qh-space-3);align-items:start;min-height:128px;padding:var(--qh-space-4);border:1px solid var(--resource-border);border-radius:var(--qh-radius-stage);background:color-mix(in srgb,var(--qh-color-night-soft) 68%,transparent);overflow:hidden;cursor:pointer;transition:border-color .18s ease,transform .18s ease,background .18s ease}.resource-card:hover{border-color:var(--resource-accent);background:color-mix(in srgb,var(--qh-color-jade) 12%,var(--qh-color-night-soft));transform:translateY(-3px)}.type-icon{display:grid;width:42px;height:42px;place-items:center;border:1px solid color-mix(in srgb,var(--resource-accent) 45%,transparent);border-radius:50%;color:var(--resource-accent);background:color-mix(in srgb,var(--qh-color-jade) 8%,transparent)}.type-icon svg{width:27px;height:27px}.kind,.knowledge,.meta{margin:0}.kind{color:var(--resource-accent);font-size:11px;font-weight:800;letter-spacing:1px}.copy h3{margin:4px 0;color:var(--qh-text);font-family:var(--qh-font-display);font-size:15px;line-height:1.4}.knowledge{overflow:hidden;color:var(--qh-text-muted);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.meta{display:flex;gap:var(--qh-space-2);margin-top:7px;color:var(--qh-text-muted);font-size:11px}.favorite{display:grid;width:30px;height:30px;padding:7px;place-items:center;border:0;border-radius:50%;color:var(--qh-text-muted);background:transparent;cursor:pointer}.favorite:hover,.favorite.active{color:var(--qh-color-gold);background:color-mix(in srgb,var(--qh-color-gold) 10%,transparent)}.favorite.active svg{animation:favorite-pop .32s ease-out}.favorite svg{width:16px;height:16px}.shimmer{position:absolute;inset:0;background:linear-gradient(105deg,transparent 30%,color-mix(in srgb,var(--qh-color-cream) 9%,transparent) 48%,transparent 64%);transform:translateX(-100%);animation:shimmer 2.1s infinite}@keyframes shimmer{to{transform:translateX(100%)}}@keyframes favorite-pop{50%{transform:scale(1.32)}}@media(prefers-reduced-motion:reduce){.shimmer,.favorite.active svg{animation:none}}
</style>
