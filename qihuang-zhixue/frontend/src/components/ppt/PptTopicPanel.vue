<template>
  <aside class="panel">
    <header class="panel-head">
      <p class="kicker">制稿 · 讲题</p>
      <h2>定一个题，起一稿</h2>
    </header>

    <div class="panel-body">
      <section class="block">
        <label class="block-label" for="ppt-free-topic">讲题</label>
        <input
          id="ppt-free-topic"
          v-model="freeTopic"
          class="input"
          type="text"
          maxlength="60"
          placeholder="如：麻黄 / 桂枝汤方解 / 十八反速记"
          @input="pickFreeTopic"
        />
        <p class="hint">留空则按下面选中的知识节点生成。</p>
      </section>

      <section class="block">
        <div class="block-head">
          <span class="block-label">知识节点</span>
          <span class="count">{{ allNodes.length }} 个</span>
        </div>
        <input v-model="keyword" class="input" type="search" placeholder="按名字筛选" />
        <div class="node-list" role="listbox" aria-label="知识节点">
          <p v-if="nodesLoading" class="node-empty">正在取科目表…</p>
          <button
            v-for="row in filteredNodes"
            :key="row.node.id"
            class="node"
            type="button"
            role="option"
            :aria-selected="selectedNode?.id === row.node.id"
            :class="{ active: selectedNode?.id === row.node.id }"
            @click="pickNode(row.node)"
          >
            <span class="node-name">{{ row.node.name }}</span>
            <span class="node-trail">{{ row.node.level === 'point' ? '药材' : row.node.level === 'subtype' ? '分类' : '章节' }}</span>
          </button>
          <p v-if="!nodesLoading && !filteredNodes.length" class="node-empty">没有匹配的节点，改用上面的讲题。</p>
        </div>
      </section>

      <section class="block">
        <span class="block-label">章程</span>
        <div class="chips" role="group" aria-label="页数">
          <button v-for="value in PAGE_CHOICES" :key="`p-${value}`" class="chip" type="button" :class="{ active: pageCount === value }" @click="pageCount = value">
            {{ value }} 页
          </button>
        </div>
        <div class="chips" role="group" aria-label="生成节奏">
          <button v-for="item in PPT_MODES" :key="item.value" class="chip" type="button" :class="{ active: mode === item.value }" :title="item.hint" @click="mode = item.value">
            {{ item.label }}
          </button>
        </div>
        <div class="chips" role="group" aria-label="受众">
          <button v-for="item in AUDIENCE_CHOICES" :key="item.value" class="chip" type="button" :class="{ active: audience === item.value }" @click="audience = item.value">
            {{ item.label }}
          </button>
        </div>
      </section>

      <section v-if="phase !== 'idle'" class="block">
        <div class="block-head">
          <span class="block-label">工序</span>
          <span class="count">{{ stats.slides }} 页</span>
        </div>
        <ol class="steps">
          <li v-for="item in stepStates" :key="item.id" :class="`is-${item.state}`">
            <i>{{ item.short }}</i>
            <span>{{ item.label }}</span>
          </li>
        </ol>
      </section>

      <section v-if="currentSlide" class="block">
        <div class="block-head">
          <span class="block-label">当前页</span>
          <span class="count">{{ slideLayoutLabel(currentSlide) }} · {{ current + 1 }}/{{ stats.slides }}</span>
        </div>
        <div class="page-actions">
          <button class="mini" type="button" :disabled="current === 0" @click="moveSlide(current, current - 1)">前移</button>
          <button class="mini" type="button" :disabled="current >= stats.slides - 1" @click="moveSlide(current, current + 1)">后移</button>
          <button class="mini danger" type="button" :disabled="stats.slides <= 1" @click="removeSlide(current)">删页</button>
        </div>
        <label class="block-label note-label" :for="`ppt-note-${currentSlide.id}`">讲者备注</label>
        <textarea
          :id="`ppt-note-${currentSlide.id}`"
          class="input note"
          rows="3"
          maxlength="200"
          placeholder="这一页要强调什么、学生常错在哪"
          :value="currentSlide.note"
          @input="updateSlide(current, { note: ($event.target as HTMLTextAreaElement).value })"
        />
      </section>

      <section v-if="recent.length" class="block">
        <span class="block-label">最近讲稿</span>
        <button v-for="item in recent" :key="item.id" class="recent" type="button" @click="openResource(item.id)">
          <span class="recent-title">{{ item.title }}</span>
          <span class="recent-time">{{ shortDate(item.created_at) }}</span>
        </button>
      </section>
    </div>

    <footer class="panel-foot">
      <p v-if="phase === 'failed'" class="error">{{ errorMessage }}</p>
      <button class="ink-button" type="button" :disabled="!canGenerate" @click="generate">
        <span v-if="phase === 'generating'">炼制中…</span>
        <span v-else-if="phase === 'awaiting-outline'">重新起稿</span>
        <span v-else-if="phase === 'failed'">再起一稿</span>
        <span v-else-if="phase === 'editing'">再起一稿</span>
        <span v-else>起稿</span>
      </button>
      <p class="foot-note">{{ footNote }}</p>
    </footer>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { AUDIENCE_CHOICES, PAGE_CHOICES, PPT_MODES } from '../../config/pptStudio'
import type { usePptStudio } from '../../composables/usePptStudio'
import { slideLayoutLabel } from '../../types/ppt'

const props = defineProps<{ studio: ReturnType<typeof usePptStudio> }>()
const {
  keyword,
  freeTopic,
  pageCount,
  mode,
  audience,
  filteredNodes,
  allNodes,
  nodesLoading,
  selectedNode,
  recent,
  stepStates,
  phase,
  stats,
  canGenerate,
  current,
  currentSlide,
  errorMessage,
  pickNode,
  pickFreeTopic,
  generate,
  openResource,
  updateSlide,
  moveSlide,
  removeSlide,
} = props.studio

const footNote = computed(() => {
  if (phase.value === 'generating') return '四道工序正在跑，先别动'
  if (phase.value === 'awaiting-outline') return '改完大纲点「确认」，继续敷演'
  if (phase.value === 'editing') return '改动会自动存盘'
  return selectedNode.value ? '按节点生成' : freeTopic.value.trim() ? '自由讲题' : '先选节点或写讲题'
})

const shortDate = (value: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getMonth() + 1}/${date.getDate()}`
}
</script>

<style scoped lang="scss">
.panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--qh-line);
  background: var(--qh-surface);
}

.panel-head {
  padding: var(--qh-space-5) var(--qh-space-4) var(--qh-space-3);
  border-bottom: 1px solid var(--qh-line);
}

.kicker {
  margin: 0 0 6px;
  color: var(--qh-accent);
  font-size: var(--qh-text-footnote);
  letter-spacing: 2px;
}

.panel-head h2 {
  margin: 0;
  color: var(--qh-fg);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-lead);
  font-weight: 600;
  letter-spacing: 1px;
}

.panel-body {
  flex: 1;
  min-height: 0;
  padding: var(--qh-space-3) var(--qh-space-4);
  overflow-y: auto;
}

.block {
  padding: var(--qh-space-3) 0;
  border-bottom: 1px dashed var(--qh-line);
}

.block:last-child {
  border-bottom: 0;
}

.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.block-label {
  display: block;
  margin-bottom: var(--qh-space-2);
  color: var(--qh-fg-muted);
  font-size: var(--qh-text-footnote);
  letter-spacing: 1.5px;
}

.count {
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
}

.input {
  width: 100%;
  padding: 9px 10px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-sm);
  color: var(--qh-fg);
  background: var(--qh-surface-sunken);
  font-family: inherit;
  font-size: var(--qh-text-option);
  transition: border-color var(--qh-duration-fast) var(--qh-ease);
}

.input::placeholder {
  color: var(--qh-fg-faint);
}

.input:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 1px;
}

.note {
  resize: vertical;
  min-height: 64px;
  line-height: 1.5;
}

.note-label {
  margin-top: var(--qh-space-3);
}

.hint {
  margin: 6px 0 0;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
}

.node-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 196px;
  margin-top: var(--qh-space-2);
  overflow-y: auto;
}

.node-empty {
  margin: var(--qh-space-2) 0;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
}

/* 抽屉被拉开一截：位移代替描边，选中不再是「加一个框」。 */
.node {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 36px;
  padding: 0 10px 0 12px;
  border: 0;
  border-radius: var(--qh-radius-sm);
  color: var(--qh-fg-soft);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: transform var(--qh-duration-fast) var(--qh-ease), background-color var(--qh-duration-fast) var(--qh-ease);
}

.node::before {
  position: absolute;
  left: 3px;
  width: 3px;
  height: 16px;
  border-radius: 2px;
  background: var(--qh-accent);
  content: '';
  transform: scaleY(0);
  transform-origin: center;
  transition: transform var(--qh-duration-fast) var(--qh-ease);
}

.node:hover {
  background: var(--qh-surface-hover);
}

.node.active {
  background: var(--qh-accent-soft);
  color: var(--qh-fg);
  transform: translateX(4px);
}

.node.active::before {
  transform: scaleY(1);
}

.node:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: -1px;
}

.node-name {
  overflow: hidden;
  font-size: var(--qh-text-option);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-trail {
  flex: none;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.chips:last-child {
  margin-bottom: 0;
}

.chip {
  padding: 5px 10px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: var(--qh-text-footnote);
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease), border-color var(--qh-duration-fast) var(--qh-ease);
}

.chip:hover {
  color: var(--qh-fg);
}

.chip.active {
  border-color: var(--qh-accent-line);
  color: var(--qh-accent);
  background: var(--qh-accent-soft);
}

.chip:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 1px;
}

.steps {
  display: flex;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.steps li {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding-top: 7px;
  border-top: 1px solid var(--qh-line);
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
  transition: color var(--qh-duration-md) var(--qh-ease), border-color var(--qh-duration-md) var(--qh-ease);
}

.steps li i {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border: 1px solid currentColor;
  border-radius: 2px;
  font-family: var(--qh-font-reading);
  font-style: normal;
  font-size: 11px;
}

.steps li.is-done {
  border-top-color: var(--qh-positive);
  color: var(--qh-positive);
}

.steps li.is-active {
  border-top-color: var(--qh-accent);
  color: var(--qh-accent);
}

.steps li.is-active i {
  animation: step-pulse 1.4s var(--qh-ease) infinite;
}

.page-actions {
  display: flex;
  gap: 6px;
  margin-bottom: var(--qh-space-2);
}

.mini {
  flex: 1;
  height: 28px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-sm);
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: var(--qh-text-footnote);
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease), border-color var(--qh-duration-fast) var(--qh-ease);
}

.mini:hover:not(:disabled) {
  color: var(--qh-fg);
  border-color: var(--qh-line-strong);
}

.mini:disabled {
  color: var(--qh-fg-faint);
  cursor: not-allowed;
}

.mini.danger:hover:not(:disabled) {
  color: var(--qh-accent);
  border-color: var(--qh-accent-line);
}

.recent {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 8px;
  border: 0;
  border-radius: var(--qh-radius-sm);
  color: var(--qh-fg-muted);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.recent:hover {
  color: var(--qh-fg);
  background: var(--qh-surface-hover);
}

.recent-title {
  overflow: hidden;
  font-size: var(--qh-text-footnote);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent-time {
  flex: none;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
}

.panel-foot {
  padding: var(--qh-space-3) var(--qh-space-4) var(--qh-space-4);
  border-top: 1px solid var(--qh-line);
  background: var(--qh-surface);
}

.ink-button {
  width: 100%;
  height: var(--qh-control-height);
  border: 1px solid var(--qh-accent);
  border-radius: var(--qh-radius-sm);
  color: var(--qh-accent-ink);
  background: var(--qh-accent);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-ui);
  letter-spacing: 3px;
  cursor: pointer;
  transition: opacity var(--qh-duration-fast) var(--qh-ease);
}

.ink-button:hover:not(:disabled) {
  opacity: 0.9;
}

.ink-button:disabled {
  border-color: var(--qh-line);
  color: var(--qh-fg-faint);
  background: var(--qh-surface-sunken);
  cursor: not-allowed;
}

.ink-button:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 2px;
}

.foot-note {
  margin: 8px 0 0;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
  text-align: center;
}

.error {
  margin: 0 0 8px;
  padding: 7px 9px;
  border-left: 2px solid var(--qh-accent);
  color: var(--qh-accent-strong);
  background: var(--qh-accent-soft);
  font-size: var(--qh-text-footnote);
}

@keyframes step-pulse {
  50% {
    opacity: 0.35;
  }
}

@media (prefers-reduced-motion: reduce) {
  .node,
  .node::before,
  .steps li i {
    transition: none;
    animation: none;
  }
}
</style>
