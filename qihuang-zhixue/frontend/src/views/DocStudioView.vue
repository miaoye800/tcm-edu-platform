<template>
  <main class="studio" data-qh-theme="light">
    <DocTopicPanel :studio="studio" />
    <PlanReview v-if="phase === 'awaiting-plan' && plan" :plan="plan" :busy="planBusy" :error="errorMessage" @revise="revisePlan" @confirm="confirmPlan" @cancel="cancelPlan" />

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
            @update="toggleCheck"
          />
          <p v-if="source === 'import'" class="import-note">导入稿仅本地预览，不入资源库。要入库请用左侧「生成」。</p>
        </article>

        <!-- 逐节依据：整篇只标一个「有依据」等于没标，得说清哪节靠教材、哪节是模型补充。 -->
        <aside v-if="blocks.length && (sectionSources.length || docCheck)" class="verify-strip" aria-label="逐节依据与核对">
          <p class="verify-head">
            <span v-if="docCheck" class="verify-tag">方案章节 {{ docCheck.matched }}/{{ docCheck.planned }}</span>
            <span v-if="docCheck?.words" class="verify-tag">
              {{ docCheck.words }} 字<template v-if="docCheck.expected_words"> / 目标 {{ docCheck.expected_words }}</template>
            </span>
            <span v-if="docCheck?.missing.length" class="warn">未落：{{ docCheck.missing.join('、') }}</span>
            <!-- 整节都是大白话也算「没写到位」：排版是内容的一部分，得让用户看见。 -->
            <span v-if="docCheck?.dense?.length" class="warn">整节纯文字：{{ docCheck.dense.join('、') }}</span>
            <span v-if="docCheck?.devices_missing?.length" class="warn">缺{{ missingDeviceText }}</span>
            <span v-if="docCheck?.bad_citations" class="warn">{{ docCheck.bad_citations }} 处引文编号无效</span>
          </p>
          <ul v-if="sectionSources.length">
            <li v-for="section in sectionSources" :key="section.heading" :data-tier="section.source">
              <span class="tier">{{ section.source === 'knowledge_base' ? '教材依据' : '模型补充' }}</span>
              <span class="heading">{{ section.heading }}</span>
              <span v-if="section.citations" class="cites">{{ section.citations }} 处引用</span>
            </li>
          </ul>
        </aside>

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
import PlanReview from '../components/resources/PlanReview.vue'
import { useDocStudio } from '../composables/useDocStudio'
import { toMarkdown } from '../utils/docBlocks'

const route = useRoute()
const router = useRouter()
const studio = useDocStudio()
const {
  phase, source, blocks, resourceId, title, docKind, stepIndex, errorMessage, plan, planBusy,
  loadNodes, generate, revisePlan, confirmPlan, cancelPlan, sectionSources, docCheck, restoreTask, toggleCheck,
} = studio

const sheetName = computed(() => title.value || '文档工坊')
const sourceLabel = computed(() => (source.value === 'import' ? '导入预览' : source.value === 'resource' ? '已入库' : '生成稿'))

/** 排版要素说人话。方案里写的是 table / checklist，用户看到的该是「对照表 / 行动清单」。 */
const DEVICE_LABELS: Record<string, string> = {
  table: '对照表',
  checklist: '行动清单',
  steps: '编号步骤',
  timeline: '时间线',
  callout: '重点提示',
  summary: '速记小结',
  glossary: '术语表',
}
const missingDeviceText = computed(() =>
  (docCheck.value?.devices_missing ?? []).map(device => DEVICE_LABELS[device] ?? device).join('、'))
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
  // 刷新回来先认任务：待确认的方案还在服务端，不该重开一份新的。
  const pending = route.query.task
  if (typeof pending === 'string' && pending) { void restoreTask(pending); return }
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

/* 逐节依据条：跟在稿纸下方，与纸同宽。 */
.verify-strip {
  max-width: 760px;
  margin: 12px auto 0;
  padding: 10px 14px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-card);
  background: color-mix(in srgb, var(--qh-surface) 60%, transparent);
  color: var(--qh-fg-muted);
  font-size: var(--qh-text-footnote);
}

.verify-head {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 0;
}

.verify-tag {
  padding: 1px 6px;
  border-radius: var(--qh-radius-pill);
  background: color-mix(in srgb, var(--qh-celadon-500) 20%, transparent);
  color: var(--qh-celadon-600);
}

.warn {
  color: var(--qh-ochre-600);
}

.verify-strip ul {
  display: grid;
  gap: 4px;
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}

.verify-strip li {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.verify-strip .tier {
  flex: none;
  padding: 0 5px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--qh-celadon-500) 16%, transparent);
  color: var(--qh-celadon-600);
  font-size: 11px;
}

/* 模型补充要看得出来：不伪装成教材事实。 */
.verify-strip li[data-tier='model'] .tier {
  background: color-mix(in srgb, var(--qh-ochre-500) 18%, transparent);
  color: var(--qh-ochre-600);
}

.verify-strip .heading {
  overflow: hidden;
  color: var(--qh-fg);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.verify-strip .cites {
  flex: none;
  color: var(--qh-fg-faint);
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
