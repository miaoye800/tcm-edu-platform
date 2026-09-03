<template>
  <main class="mindmap-resource" :class="`theme-${theme.id}`" :style="theme.cssVars">
    <header class="toolbar">
      <div v-if="showReason" class="reason"><span>小岐建议</span>{{ content.choice_reason }}<button title="关闭建议" aria-label="关闭建议" @click="showReason = false">×</button></div>
      <div class="tools"><button title="继续展开" :disabled="!selectedId || expanding" @click="expandSelected">继续展开</button><button title="导出 SVG" @click="exportSvg">SVG</button><button title="导出 PNG" @click="exportPng">PNG</button></div>
    </header>
    <nav class="theme-picker" aria-label="导图模板"><button v-for="item in mindmapThemes" :key="item.id" class="theme-option" :class="{ active: item.id === theme.id }" @click="selectTheme(item.id)"><i :style="{ '--a': item.palette[0], '--b': item.palette[1] }" />{{ item.name }}</button></nav>
    <section ref="host" class="map-host"><div v-if="expanding" class="branch-loading" :style="loadingPosition">正在续展</div></section>
  </main>
</template>

<script setup lang="ts">
import MindElixir, { SIDE, type MindElixirData, type Theme } from 'mind-elixir'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { mindmapThemes } from '../config/mindmapThemes'
import { expandMindmap, patchMindmapTemplate } from '../services/resourceApi'

type Node = { id: string; topic: string; children?: Node[]; meta?: { source_indexes?: number[] }; direction?: 0 | 1 }
type Content = { template_id: string; choice_reason: string; mindmap_data: { nodeData: Node }; sources?: unknown[] }
const props = defineProps<{ resourceId: string; initialContent: Content; usingMock?: boolean; topic?: string }>()
const emit = defineEmits<{ changed: [content: Content] }>()
const content = ref<Content>(structuredClone(props.initialContent)); const host = ref<HTMLElement | null>(null); const selectedId = ref(''); const expanding = ref(false); const showReason = ref(true)
let elixir: any = null
const theme = computed(() => mindmapThemes.find(item => item.id === content.value.template_id) ?? mindmapThemes.find(item => item.id === 'warm-gold')!)
const loadingPosition = computed(() => ({ left: '50%', top: '50%' }))
const elixirTheme = (): Theme => ({ name: theme.value.id, type: theme.value.id === 'warm-gold' || theme.value.id === 'neon-night' || theme.value.id === 'star-chart' ? 'dark' : 'light', palette: theme.value.palette, cssVar: { '--main-color': theme.value.cssVars['--mm-primary'], '--main-bgcolor': theme.value.cssVars['--mm-surface'], '--color': theme.value.cssVars['--mm-text'], '--bgcolor': theme.value.cssVars['--mm-bg'], '--root-bgcolor': theme.value.cssVars['--mm-surface'], '--root-color': theme.value.cssVars['--mm-text'], '--root-border-color': theme.value.cssVars['--mm-primary'], '--main-border': `1px solid ${theme.value.cssVars['--mm-node-border']}`, '--root-radius': theme.value.nodeStyle.radius, '--main-radius': theme.value.nodeStyle.radius, '--selected': theme.value.cssVars['--mm-secondary'], '--map-padding': '64px' } })
const render = async () => { await nextTick(); if (!host.value) return; elixir?.destroy(); host.value.replaceChildren(); elixir = new MindElixir({ el: host.value, direction: SIDE, editable: false, contextMenu: false, toolBar: false, keypress: false, theme: elixirTheme(), scaleMin: .4, scaleMax: 1.5 }); elixir.init(content.value.mindmap_data as MindElixirData); elixir.bus.addListener('selectNode', (node: Node) => { selectedId.value = node.id }); setTimeout(() => elixir?.toCenter(), 0) }
const selectTheme = async (id: string) => { if (id === content.value.template_id) return; content.value.template_id = id; emit('changed', content.value); await render(); if (!props.usingMock) await patchMindmapTemplate(props.resourceId, id) }
const findPath = (node: Node, id: string, path: string[] = []): string[] | null => { const next = [...path, node.id]; if (node.id === id) return next; for (const child of node.children ?? []) { const found = findPath(child, id, next); if (found) return found }; return null }
const expandSelected = async () => { const path = findPath(content.value.mindmap_data.nodeData, selectedId.value); if (!path) return; expanding.value = true; try { const result = props.usingMock ? await mockExpand(path) : await expandMindmap(props.resourceId, path); content.value.mindmap_data = result.mindmap_data as Content['mindmap_data']; emit('changed', content.value); await render() } finally { expanding.value = false } }
const mockExpand = async (path: string[]) => { const data = structuredClone(content.value.mindmap_data); let node = data.nodeData; for (const id of path.slice(1)) node = node.children?.find(child => child.id === id)!; node.children = [...(node.children ?? []), { id: `${node.id}-more`, topic: '延伸辨析', meta: { source_indexes: [0] } }]; return { node_path: path, added_nodes: node.children, mindmap_data: data, idempotent: false } }
const download = (blob: Blob, extension: string) => { const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${props.topic || content.value.mindmap_data.nodeData.topic}-${theme.value.name}.${extension}`; anchor.click(); URL.revokeObjectURL(url) }
const exportSvg = () => { const svg = host.value?.querySelector('svg'); if (svg) download(new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' }), 'svg') }
const exportPng = () => { const svg = host.value?.querySelector('svg'); if (!svg) return; const image = new Image(); const raw = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' }); image.onload = () => { const canvas = document.createElement('canvas'); canvas.width = image.width || 1600; canvas.height = image.height || 900; canvas.getContext('2d')?.drawImage(image, 0, 0); canvas.toBlob(blob => blob && download(blob, 'png'), 'image/png') }; image.src = URL.createObjectURL(raw) }
onMounted(render); onBeforeUnmount(() => elixir?.destroy()); watch(() => props.initialContent, value => { content.value = structuredClone(value); void render() }, { deep: true })
</script>

<style scoped lang="scss">
.mindmap-resource{min-height:calc(100vh - 150px);padding:18px;color:var(--mm-text);background:var(--mm-bg)}.toolbar,.theme-picker{display:flex;align-items:center;justify-content:space-between;gap:10px;max-width:1320px;margin:0 auto 12px}.reason{display:flex;align-items:center;gap:8px;font-size:13px}.reason span{padding:3px 7px;border-radius:4px;color:var(--mm-bg);background:var(--mm-accent);font-weight:800}.reason button,.tools button,.theme-option{border:1px solid color-mix(in srgb,var(--mm-text) 18%,transparent);border-radius:4px;color:var(--mm-text);background:var(--mm-surface);cursor:pointer}.reason button{border:0;background:none;font-size:18px}.tools{display:flex;gap:7px}.tools button{padding:7px 10px}.theme-picker{justify-content:start;overflow:auto}.theme-option{display:flex;align-items:center;gap:6px;white-space:nowrap;padding:7px 9px;font-size:12px}.theme-option.active{border-color:var(--mm-primary);box-shadow:0 0 0 1px var(--mm-glow)}.theme-option i{width:18px;height:13px;background:linear-gradient(135deg,var(--a) 50%,var(--b) 50%)}.map-host{position:relative;height:680px;max-width:1320px;margin:auto;overflow:hidden;border:1px solid color-mix(in srgb,var(--mm-text) 18%,transparent);border-radius:6px;background:var(--mm-bg)}.map-host :deep(.map-container){background:var(--mm-bg)}.map-host :deep(me-tpc){max-width:220px;white-space:normal;line-height:1.42}.branch-loading{position:absolute;z-index:4;padding:7px 10px;border-radius:4px;color:var(--mm-text);background:var(--mm-surface);box-shadow:0 0 0 6px var(--mm-glow);animation:breathe 1.2s ease-in-out infinite}@keyframes breathe{50%{opacity:.5;transform:scale(1.05)}}@media(max-width:680px){.toolbar{align-items:start;flex-direction:column}.map-host{height:560px}.mindmap-resource{padding:10px}}
</style>
