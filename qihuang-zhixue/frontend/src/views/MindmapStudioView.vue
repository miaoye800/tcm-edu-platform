<template>
  <main class="studio" data-qh-theme="light" :class="{ 'chat-collapsed': collapsed }">
    <StudioChatPanel
      :messages="messages"
      :recent="recent"
      :active-id="resourceId"
      :collapsed="collapsed"
      :busy="busy"
      @ask="ask"
      @retry="retry"
      @reset="resetChat"
      @toggle="toggleCollapsed"
      @open="openResource"
      @structure="setStructure"
      @theme="themeId = $event"
    />
    <PlanReview v-if="phase === 'awaiting-plan' && plan" :plan="plan" :busy="planBusy" :error="errorMessage" @revise="revisePlan" @confirm="confirmPlan" @cancel="cancelPlan" />

    <section class="sheet">
      <div class="mat">
        <StudioCanvas
          ref="canvasRef"
          :theme-id="themeId"
          :structure-id="structureId"
          :editing="phase === 'editing'"
          :live="phase === 'generating'"
          @dirty="markDirty"
          @stats="applyStats"
          @node-select="openOnMap"
        />
      </div>

      <!-- 方案分支落地了几支：确认过的结构不能悄悄少一支。 -->
      <p v-if="planCoverage && planCoverage.planned" class="plan-cover">
        <span class="plan-cover-tag">方案分支 {{ planCoverage.matched }}/{{ planCoverage.planned }}</span>
        <span v-if="planCoverage.missing.length" class="plan-cover-miss">未落：{{ planCoverage.missing.join('、') }}</span>
      </p>
      <!-- 层数没做到也要说：用户选了 5 层，画出来 4 层不是「差不多」，是不一致。 -->
      <p v-if="planFit?.note" class="plan-cover plan-depth">
        <span class="plan-cover-miss">{{ planFit.note }}</span>
        <span v-if="planFit.shallow?.length" class="plan-cover-miss">偏浅：{{ planFit.shallow.join('、') }}</span>
      </p>

      <div class="toolbar-slot">
        <StudioToolbar
          :editing="phase === 'editing'"
          :theme-id="themeId"
          :structure-id="structureId"
          :save-status="saveStatus"
          :save-message="saveMessage"
          @add-child="canvasRef?.addChild()"
          @add-sibling="canvasRef?.addSibling()"
          @remove="canvasRef?.removeSelected()"
          @zoom-in="canvasRef?.zoom(1.15)"
          @zoom-out="canvasRef?.zoom(0.87)"
          @fit="canvasRef?.fit()"
          @structure="setStructure"
          @theme="themeId = $event"
          @export-svg="exportSvg"
          @export-png="exportPng"
        />
      </div>

      <div v-if="!hasTree" class="sheet-empty">
        <StateLoading v-if="phase === 'generating'" size="md" />
        <StateError
          v-else-if="phase === 'failed'"
          size="md"
          title="这一炉没炼成"
          :description="errorMessage || '后端没有返回可用的导图，请稍后重试。'"
          action-text="再炼一次"
          @action="retry"
        />
        <StateEmpty
          v-else
          icon="herb"
          title="图纸还空着"
          description="在左边说一个中药学主题，或者点一条建议——底图会边聊边长出来。"
        />
        <!-- 链尾之后的独立分支：插在 v-if/v-else 中间会打断整条链。 -->
        <p v-if="phase === 'generating'" class="sheet-wait">{{ waitLabel }}</p>
      </div>

      <div v-if="saveStatus === 'conflict'" class="conflict" role="alert">
        <p>这份导图在别处被改过（服务端已是第 {{ version }} 版）。</p>
        <div class="conflict-actions">
          <button type="button" @click="reloadFromServer">重新载入</button>
          <button type="button" class="primary" @click="keepMine">保留我的版本</button>
        </div>
      </div>
    </section>

    <!-- 底栏：这一行的作用是把「靠不靠得住」摆到桌面上，而不是藏进提示气泡。 -->
    <footer v-if="hasTree" class="status">
      <span class="status-item">{{ structure.name }}</span>
      <template v-if="intentLabel">
        <span class="sep" aria-hidden="true">·</span>
        <span class="status-item">{{ intentLabel }}</span>
      </template>
      <span class="sep" aria-hidden="true">·</span>
      <span class="status-item">{{ stats.nodes }} 节点</span>
      <span class="sep" aria-hidden="true">·</span>
      <span class="status-item">{{ stats.depth }} 层</span>
      <template v-if="evidence.total">
        <span class="sep" aria-hidden="true">·</span>
        <span class="status-item">教材 {{ evidence.textbook }}%</span>
        <span class="status-item">图谱 {{ evidence.graph }}%</span>
        <span class="status-item" :class="{ warn: evidence.warn }">推断 {{ evidence.model }}%</span>
      </template>
      <span class="status-grow" />
      <!-- 三条提示的优先级：域外 > 结构折了 > 推断偏多。域外最该被看见。
           文案与后端 describe() 对齐：说「本地教材」而不是「中药学教材语料」——
           知识库现在覆盖六门课，再写死中药学会让中基/中诊的用户以为系统认错了。 -->
      <span v-if="degraded" class="status-hint is-degraded">主题不在本地教材内，内容由模型整理</span>
      <span v-else-if="overflowHint" class="status-hint">{{ overflowHint }}</span>
      <span v-else-if="evidence.warn" class="status-hint">推断较多，建议对照教材核对</span>
    </footer>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { StateEmpty, StateError, StateLoading } from '../components/state'
import StudioCanvas from '../components/mindmap/StudioCanvas.vue'
import StudioChatPanel from '../components/mindmap/StudioChatPanel.vue'
import PlanReview from '../components/resources/PlanReview.vue'
import StudioToolbar from '../components/mindmap/StudioToolbar.vue'
import { useMindmapStudio, type StudioStats } from '../composables/useMindmapStudio'
import { useMindmapChat } from '../composables/useMindmapChat'
import { describeOverflow } from '../config/mindmapStructures'
import type { ResourceMindmapNode } from '../types/resource'
import type { MindmapTreeNode } from '../utils/mindmapTree'

interface CanvasApi {
  reset: () => void
  pushNode: (node: ResourceMindmapNode) => void
  finish: () => void
  setTree: (data: unknown) => void
  getTree: () => MindmapTreeNode | null
  zoom: (factor: number) => void
  fit: () => void
  addChild: () => void
  addSibling: () => void
  removeSelected: () => void
  exportSvg: () => Blob | undefined
  exportPng: () => Promise<Blob | null> | undefined
}

const route = useRoute()
const router = useRouter()

/**
 * 计划快照里的叶子点一下就回地图。
 *
 * **只对带 node_id 的节点生效**（也就是从计划导出来的那张图）。普通导图的
 * 节点没有 node_id，点了什么也不该发生——把"点节点"变成"离开页面"是最烦人的
 * 那种交互。
 */
const openOnMap = (nodeId: string) => {
  if (!nodeId) return
  void router.push(`/path?focus=${encodeURIComponent(nodeId)}`)
}
const canvasRef = ref<CanvasApi | null>(null)
const studio = useMindmapStudio(() => canvasRef.value)
const { messages, collapsed, chatPhase, ask, retry, reset: resetChat, toggleCollapsed } = useMindmapChat(studio)

const { phase, plan, planBusy, revisePlan, confirmPlan, cancelPlan, planCoverage, planFit, themeId, structureId, setStructure, structure, saveStatus, saveMessage, stats, evidence, version, errorMessage, recent, resourceId, markDirty, reloadFromServer, keepMine, openResource, restoreTask, refreshRecent, intentLabel, degraded, steps, stepIndex, waitedSeconds } = studio

const hasTree = computed(() => stats.value.nodes > 0)
const busy = computed(() => chatPhase.value === 'submitting' || chatPhase.value === 'streaming')
const applyStats = (value: StudioStats) => { stats.value = value }

/**
 * 生成中的进度文案。
 *
 * 三段递进：刚起炉只说做什么；等过 3 秒开始报秒数（证明还活着）；
 * 超过 25 秒解释为什么慢（整棵树一次成型），把人从「刷新一下试试」里拦下来。
 */
const waitLabel = computed(() => {
  // 后端的工序文案是「进行中」语气，自带省略号（「把要点写进每个分支...」）。
  // 放在「已等 4s」前面时，省略号会让整行读起来像没加载完。
  const label = (steps.value[stepIndex.value] || '起炉').replace(/(\.{2,}|…)$/, '')
  const head = stepIndex.value >= 0
    ? `第 ${stepIndex.value + 1}/${steps.value.length} 步 · ${label}`
    : label
  const waited = waitedSeconds.value
  if (waited < 3) return head
  if (waited < 25) return `${head} · 已等 ${waited}s`
  return `${head} · 已等 ${waited}s，整棵树一次成型，通常要一两分钟，别急着刷新`
})
/** 结构装不下的层被折起来时明说，别让人以为切完结构丢了内容。 */
const overflowHint = computed(() => describeOverflow(structure.value, stats.value.depth, stats.value.folded))

const download = (blob: Blob, extension: string) => {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `导图-${new Date().toISOString().slice(0, 10)}.${extension}`
  anchor.click()
  URL.revokeObjectURL(url)
}

const exportSvg = () => {
  const blob = canvasRef.value?.exportSvg()
  if (blob) download(blob, 'svg')
}

const exportPng = async () => {
  const pending = canvasRef.value?.exportPng()
  if (!pending) return
  const blob = await pending
  if (blob) download(blob, 'png')
}

onMounted(async () => {
  void refreshRecent()
  // 刷新回来先认任务：待确认的方案还在服务端，不该重开一份新的。
  const pending = route.query.task
  if (typeof pending === 'string' && pending) {
    await restoreTask(pending)
    collapsed.value = false
    return
  }
  // 兼容旧链接与「我的资源」里的跳转：/mindmap?resource=<id> 直接打开那一稿。
  const target = route.query.resource
  if (typeof target === 'string' && target) {
    await openResource(target)
    collapsed.value = false
  }
})
</script>

<style scoped lang="scss">
/*
 * 两列一栏：左对话（360，可收到 48）、右画布、底部状态条。
 * 高度精确吃掉 .content-scroll 的上下 padding（22 + 40），页面内只有消息流一个滚动区。
 */
.studio {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) 36px;
  height: calc(100vh - var(--qh-topbar-height) - 62px);
  min-height: 520px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-card);
  background: var(--qh-surface);
  overflow: hidden;
  transition: grid-template-columns 200ms var(--qh-ease);
}

.studio.chat-collapsed {
  grid-template-columns: 48px minmax(0, 1fr);
}

.sheet {
  position: relative;
  min-width: 0;
  /* 卡纸：画布与外框之间那 8px 的沉色垫层。 */
  background: var(--qh-surface-sunken);
}

.mat {
  position: absolute;
  inset: 8px;
}

/* 方案分支落地提示：贴在画布右下角，不参与 .sheet 里其它绝对定位层。 */
.plan-cover {
  position: absolute;
  right: 16px;
  bottom: 16px;
  z-index: 4;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  max-width: calc(100% - 32px);
  margin: 0;
  padding: 5px 10px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-pill);
  background: color-mix(in srgb, var(--qh-surface) 88%, transparent);
  color: var(--qh-fg-muted);
  font-size: var(--qh-text-footnote);
  pointer-events: none;
}

.plan-cover-tag {
  color: var(--qh-celadon-600);
}

.plan-cover-miss {
  color: var(--qh-ochre-600);
}

.toolbar-slot {
  position: absolute;
  top: 16px;
  left: 50%;
  z-index: 4;
  max-width: calc(100% - 24px);
  transform: translateX(-50%);
}

.sheet-empty {
  position: absolute;
  inset: 64px 24px 24px;
  display: grid;
  align-content: center;
  gap: 12px;
  place-items: center;
  pointer-events: none;
}

/* 生成中的进度行：转圈本身不传达任何信息，这一行才传达。 */
.sheet-wait {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.sheet-empty :deep(.state-empty) {
  background: transparent;
}

.conflict {
  position: absolute;
  top: 64px;
  left: 50%;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: var(--qh-space-3);
  max-width: min(560px, calc(100% - 48px));
  padding: 8px 12px;
  border: 1px solid var(--qh-accent-line);
  border-left-width: 2px;
  border-radius: var(--qh-radius-sm);
  background: var(--qh-accent-soft);
  transform: translateX(-50%);
}

.conflict p {
  margin: 0;
  color: var(--qh-accent-strong);
  font-size: var(--qh-text-footnote);
}

.conflict-actions {
  display: flex;
  flex: none;
  gap: 6px;
}

.conflict-actions button {
  padding: 4px 9px;
  border: 1px solid var(--qh-accent-line);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-accent-strong);
  background: var(--qh-surface-raised);
  font-size: var(--qh-text-footnote);
  cursor: pointer;
}

.conflict-actions button.primary {
  color: var(--qh-accent-ink);
  background: var(--qh-accent);
}

/* ---------- 底栏 ---------- */
.status {
  display: flex;
  grid-column: 1 / -1;
  align-items: center;
  gap: 6px;
  padding: 0 14px;
  border-top: 1px solid var(--qh-line);
  background: var(--qh-surface);
  color: var(--qh-fg-muted);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.3px;
}

.status-item {
  white-space: nowrap;
}

/* 推断占比偏高时才变色：常态不喊，异常才喊。 */
.status-item.warn {
  color: #A9714B;
}

.sep {
  color: var(--qh-fg-faint);
}

.status-grow {
  flex: 1;
}

.status-hint {
  color: #A9714B;
  font-size: 10px;
  letter-spacing: 0.2px;
}

/* 域外主题不是「提醒一下」，是「这张图没有本地依据」——该比别的提示更重。 */
.status-hint.is-degraded {
  padding: 1px 6px;
  border-radius: 3px;
  color: #7A4A28;
  background: color-mix(in srgb, #A9714B 12%, transparent);
  font-weight: 600;
}

@media (max-width: 1240px) {
  .studio {
    grid-template-columns: 320px minmax(0, 1fr);
  }
}

@media (max-width: 1020px) {
  .studio {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(220px, 40%) minmax(0, 1fr) 36px;
  }

  /* 小屏时上下堆叠：对话在上，画布在下。 */
  .studio.chat-collapsed {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* 900px 以下 .content-scroll 的 padding 变成 18/34，高度也要跟着改。 */
@media (max-width: 900px) {
  .studio {
    height: calc(100vh - var(--qh-topbar-height) - 52px);
  }

  .toolbar-slot {
    top: 10px;
  }

  .sheet-empty {
    inset: 72px 12px 16px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .studio,
  .toolbar-slot,
  .conflict {
    transition: none;
  }
}
</style>
