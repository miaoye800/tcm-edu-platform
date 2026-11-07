<template>
  <aside class="panel">
    <header class="panel-head">
      <p class="kicker">制稿 · 文档</p>
      <h2>定一个题，出一卷</h2>
    </header>

    <div class="panel-body">
      <section class="block">
        <div class="chips" role="group" aria-label="来源">
          <button class="chip" type="button" :class="{ active: tab === 'generate' }" @click="tab = 'generate'">生成</button>
          <button class="chip" type="button" :class="{ active: tab === 'import' }" @click="tab = 'import'">导入 Markdown</button>
        </div>
      </section>

      <template v-if="tab === 'generate'">
        <section class="block">
          <label class="block-label" for="doc-free-topic">你想写什么</label>
          <input
            id="doc-free-topic"
            v-model="freeTopic"
            class="input"
            type="text"
            maxlength="60"
            placeholder="如：我想学人参，给我制订一份计划"
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

        <section v-if="phase !== 'idle'" class="block">
          <div class="block-head">
            <span class="block-label">工序</span>
            <span class="count">{{ blocks.length }} 块</span>
          </div>
          <ol class="steps">
            <li v-for="item in stepStates" :key="item.id" :class="`is-${item.state}`">
              <i>{{ item.short }}</i>
              <span>{{ item.label }}</span>
            </li>
          </ol>
        </section>
      </template>

      <template v-else>
        <section class="block">
          <label class="block-label" for="doc-import-source">粘贴 Markdown</label>
          <textarea
            id="doc-import-source"
            v-model="importText"
            class="input import-source"
            rows="12"
            placeholder="支持 Notion 导出的 markdown：<aside> 提示块、> 图标 callout、嵌套列表（折叠块）、表格与代码。"
          />
          <p class="hint">导入稿仅本地预览，不入资源库；选中节点起稿后生成的才会入库。</p>
          <button class="ink-button ghost-ink" type="button" :disabled="!importText.trim()" @click="importMarkdown(importText)">
            渲染预览
          </button>
        </section>
      </template>
    </div>

    <footer class="panel-foot">
      <p v-if="phase === 'failed'" class="error">{{ errorMessage }}</p>
      <button v-if="tab === 'generate'" class="ink-button" type="button" :disabled="!canGenerate" @click="generate">
        <span v-if="phase === 'generating'">炼制中…</span>
        <span v-else-if="phase === 'ready' || phase === 'failed'">再起一稿</span>
        <span v-else>起稿</span>
      </button>
      <p v-if="tab === 'generate'" class="foot-note">{{ footNote }}</p>
    </footer>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { useDocStudio } from '../../composables/useDocStudio'

const props = defineProps<{ studio: ReturnType<typeof useDocStudio> }>()
const {
  keyword, freeTopic, filteredNodes, allNodes, nodesLoading, selectedNode,
  stepStates, phase, blocks, canGenerate, errorMessage,
  pickNode, pickFreeTopic, generate, importMarkdown,
} = props.studio

const tab = ref<'generate' | 'import'>('generate')
const importText = ref('')

const footNote = computed(() => {
  if (phase.value === 'generating') return '五道工序正在跑，先别动'
  return selectedNode.value ? '按节点生成' : freeTopic.value.trim() ? '自由讲题' : '先选节点或写讲题'
})
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

.import-source {
  resize: vertical;
  min-height: 200px;
  line-height: 1.55;
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
}

.hint {
  margin: 6px 0 0;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
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

.ghost-ink {
  margin-top: var(--qh-space-2);
  letter-spacing: 2px;
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
