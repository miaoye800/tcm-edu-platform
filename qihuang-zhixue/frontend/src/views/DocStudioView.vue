<template>
  <main class="studio" data-qh-theme="light">
    <DocTopicPanel :studio="studio" />

    <section class="sheet">
      <header class="sheet-head">
        <div class="sheet-id">
          <p class="sheet-name">{{ sheetName }}</p>
          <p class="sheet-meta">
            <template v-if="blocks.length">{{ docKind || sourceLabel }} · {{ blocks.length }} 块</template>
            <template v-else>审题定调 → 谋篇立序 → 落笔成稿 → 核对引文 → 排版成卷</template>
          </p>
        </div>
        <div class="sheet-actions">
          <button v-if="resourceId" class="action" type="button" @click="openFullPage">打开完整页面</button>
          <button v-if="blocks.length" class="action" type="button" @click="exportMarkdown">导出 Markdown</button>
        </div>
      </header>

      <div class="stage-zone">
        <article v-if="blocks.length" class="document" aria-label="文档预览">
          <h1 v-if="title && source !== 'import'" class="doc-title">{{ title }}</h1>
          <DocBlockView
            v-for="(block, index) in blocks"
            :key="block.id"
            :block="block"
            :index="index"
            :editable="false"
          />
          <p v-if="source === 'import'" class="import-note">导入稿仅本地预览，不入资源库。要入库请用左侧「生成」。</p>
        </article>

        <div v-else class="ghost">
          <StateLoading
            v-if="phase === 'generating'"
            size="md"
            title="炼制中"
            :description="stepHint"
          />
          <StateError
            v-else-if="phase === 'failed'"
            size="md"
            title="这一卷没炼成"
            :description="errorMessage || '后端没有返回可用的文档，请稍后再试。'"
            action-text="再起一稿"
            @action="generate"
          />
          <StateEmpty
            v-else
            icon="herb"
            title="案上还是白纸"
            description="左边定个题点「起稿」，或切到「导入 Markdown」直接粘贴。"
          />
        </div>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { StateEmpty, StateError, StateLoading } from '../components/state'
import DocBlockView from '../components/doc/DocBlockView.vue'
import DocTopicPanel from '../components/doc/DocTopicPanel.vue'
import { useDocStudio } from '../composables/useDocStudio'
import { toMarkdown } from '../utils/docBlocks'

const route = useRoute()
const router = useRouter()
const studio = useDocStudio()
const {
  phase, source, blocks, resourceId, title, docKind, stepIndex, errorMessage,
  loadNodes, generate,
} = studio

const sheetName = computed(() => title.value || '文档工坊')
const sourceLabel = computed(() => (source.value === 'import' ? '导入预览' : source.value === 'resource' ? '已入库' : '生成稿'))
const stepHint = computed(() => {
  const labels = ['审题定调', '谋篇立序', '落笔成稿', '核对引文', '排版成卷']
  return labels[Math.max(0, stepIndex.value)] || '正在磨墨'
})

const openFullPage = () => {
  if (!resourceId.value) return
  void router.push({ name: 'resource-detail', params: { id: resourceId.value } })
}

const exportMarkdown = () => {
  const markdown = toMarkdown(blocks.value)
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${title.value || 'document'}.md`
  anchor.click()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  void loadNodes()
  const target = route.query.resource
  if (typeof target === 'string' && target) void studio.openResource(target)
})
</script>

<style scoped lang="scss">
.studio {
  display: grid;
  grid-template-columns: minmax(288px, 320px) 1fr;
  height: 100vh;
  min-height: 0;
  background: var(--qh-bg);
  color: var(--qh-fg);
  font-family: var(--qh-font-body);
}

.sheet {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: var(--qh-space-3) var(--qh-space-5);
  border-bottom: 1px solid var(--qh-line);
  background: var(--qh-surface);
}

.sheet-id {
  min-width: 0;
}

.sheet-name {
  margin: 0;
  color: var(--qh-fg);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-lead);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sheet-meta {
  margin: 2px 0 0;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
}

.sheet-actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.action {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-sm);
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: var(--qh-text-footnote);
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease), border-color var(--qh-duration-fast) var(--qh-ease);
}

.action:hover {
  color: var(--qh-fg);
  border-color: var(--qh-line-strong);
}

.action:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 2px;
}

.stage-zone {
  flex: 1;
  min-height: 0;
  padding: var(--qh-space-4) var(--qh-space-5) 96px;
  overflow-y: auto;
}

/* 与 DocResourceView 同一张米白纸：纸面承载预览块 */
.document {
  max-width: 760px;
  margin: 0 auto;
  padding: var(--qh-space-6) var(--qh-space-6) var(--qh-space-5);
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-card);
  background: var(--qh-surface);
  box-shadow: var(--qh-shadow-soft);
}

.doc-title {
  margin: 0 0 var(--qh-space-4);
  color: var(--qh-fg);
  font-family: var(--qh-font-reading);
  font-size: 26px;
  font-weight: 600;
}

.import-note {
  margin: var(--qh-space-5) 0 0;
  padding-top: var(--qh-space-3);
  border-top: 1px dashed var(--qh-line);
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
}

.ghost {
  display: grid;
  place-items: center;
  min-height: 60vh;
}

@media (max-width: 960px) {
  .studio {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
    height: auto;
    min-height: 100vh;
  }

  .sheet {
    min-height: 60vh;
  }
}
</style>
