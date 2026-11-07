<template>
  <section class="confirm" role="dialog" aria-label="确认大纲">
    <header class="confirm-head">
      <p class="kicker">谋篇 · 待确认</p>
      <h2>{{ deckTitle || '这一稿的大纲' }}</h2>
      <p class="lead">改页序、改标题、改要点都可以。确认后才开始逐页敷演。</p>
    </header>

    <div class="confirm-body">
      <article v-for="(item, index) in outline" :key="item.id" class="card">
        <div class="card-row">
          <span class="no">{{ String(index + 1).padStart(2, '0') }}</span>
          <input
            class="card-title"
            :value="item.title"
            :maxlength="SLIDE_LIMITS.title"
            :aria-label="`第 ${index + 1} 页标题`"
            @input="$emit('rename', { index, value: ($event.target as HTMLInputElement).value })"
          />
          <div class="card-tools">
            <button type="button" class="tool" aria-label="上移" :disabled="index === 0" @click="$emit('move', { from: index, to: index - 1 })">↑</button>
            <button
              type="button"
              class="tool"
              aria-label="下移"
              :disabled="index >= outline.length - 1"
              @click="$emit('move', { from: index, to: index + 1 })"
            >
              ↓
            </button>
            <button type="button" class="tool danger" aria-label="删掉这一页" :disabled="outline.length <= 1" @click="$emit('remove', index)">
              ×
            </button>
          </div>
        </div>
        <textarea
          class="card-points"
          rows="2"
          :value="item.points.join('\n')"
          :aria-label="`第 ${index + 1} 页要点`"
          placeholder="每行一条要点，留空表示这一页只有标题"
          @input="$emit('points', { index, value: ($event.target as HTMLTextAreaElement).value })"
        />
      </article>
    </div>

    <footer class="confirm-foot">
      <p v-if="error" class="error">{{ error }}</p>
      <button class="primary" type="button" :disabled="!outline.length || busy" @click="$emit('confirm')">
        {{ busy ? '敷演中…' : `确认这 ${outline.length} 页，开始敷演` }}
      </button>
      <p class="foot-note">确认后大纲会存进这次任务，刷新页面也不会丢。</p>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { SLIDE_LIMITS } from '../../types/ppt'
import type { OutlineSlide } from '../../types/ppt'

defineProps<{ outline: OutlineSlide[]; deckTitle: string; error: string; busy: boolean }>()

defineEmits<{
  rename: [payload: { index: number; value: string }]
  points: [payload: { index: number; value: string }]
  remove: [index: number]
  move: [payload: { from: number; to: number }]
  confirm: []
}>()
</script>

<style scoped lang="scss">
/* 从右侧滑入的一叠稿纸：盖上舞台，但左边仍能看到工序进度。 */
.confirm {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 6;
  display: flex;
  flex-direction: column;
  width: min(420px, 100%);
  border-left: 1px solid var(--qh-line);
  background: var(--qh-surface);
  box-shadow: var(--qh-shadow-3);
  animation: confirm-in var(--qh-duration-md) var(--qh-ease);
}

.confirm-head {
  padding: var(--qh-space-5) var(--qh-space-5) var(--qh-space-3);
  border-bottom: 1px solid var(--qh-line);
}

.kicker {
  margin: 0 0 6px;
  color: var(--qh-accent);
  font-size: var(--qh-text-footnote);
  letter-spacing: 2px;
}

.confirm-head h2 {
  margin: 0;
  color: var(--qh-fg);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-lead);
  font-weight: 600;
  line-height: 1.4;
}

.lead {
  margin: 8px 0 0;
  color: var(--qh-fg-muted);
  font-size: var(--qh-text-footnote);
  line-height: 1.6;
}

.confirm-body {
  flex: 1;
  min-height: 0;
  padding: var(--qh-space-3) var(--qh-space-4);
  overflow-y: auto;
}

.card {
  padding: 10px 10px 8px;
  margin-bottom: 8px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-sm);
  background: var(--qh-surface-sunken);
  transition: border-color var(--qh-duration-fast) var(--qh-ease);
}

.card:focus-within {
  border-color: var(--qh-accent-line);
}

.card-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.no {
  flex: none;
  color: var(--qh-fg-faint);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-footnote);
  font-variant-numeric: tabular-nums;
}

.card-title {
  flex: 1;
  min-width: 0;
  padding: 3px 4px;
  border: 0;
  border-radius: var(--qh-radius-xs);
  color: var(--qh-fg);
  background: transparent;
  font-family: inherit;
  font-size: var(--qh-text-option);
  outline: none;
}

.card-title:focus-visible {
  background: var(--qh-surface-raised);
  box-shadow: inset 0 -1px 0 var(--qh-accent);
}

.card-tools {
  display: flex;
  flex: none;
  gap: 2px;
  opacity: 0;
  transition: opacity var(--qh-duration-fast) var(--qh-ease);
}

.card:hover .card-tools,
.card:focus-within .card-tools {
  opacity: 1;
}

.tool {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: var(--qh-radius-xs);
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.tool:hover:not(:disabled) {
  color: var(--qh-fg);
  background: var(--qh-surface-hover);
}

.tool:disabled {
  color: var(--qh-fg-faint);
  cursor: not-allowed;
}

.tool.danger:hover:not(:disabled) {
  color: var(--qh-accent);
}

.tool:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 1px;
}

.card-points {
  width: 100%;
  margin-top: 6px;
  padding: 6px 8px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-xs);
  color: var(--qh-fg-soft);
  background: var(--qh-surface);
  font-family: inherit;
  font-size: var(--qh-text-footnote);
  line-height: 1.7;
  resize: vertical;
}

.card-points::placeholder {
  color: var(--qh-fg-faint);
}

.card-points:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 1px;
}

.confirm-foot {
  padding: var(--qh-space-3) var(--qh-space-4) var(--qh-space-4);
  border-top: 1px solid var(--qh-line);
}

.primary {
  width: 100%;
  height: var(--qh-control-height);
  border: 1px solid var(--qh-accent);
  border-radius: var(--qh-radius-sm);
  color: var(--qh-accent-ink);
  background: var(--qh-accent);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-ui);
  letter-spacing: 2px;
  cursor: pointer;
  transition: opacity var(--qh-duration-fast) var(--qh-ease);
}

.primary:hover:not(:disabled) {
  opacity: 0.9;
}

.primary:disabled {
  border-color: var(--qh-line);
  color: var(--qh-fg-faint);
  background: var(--qh-surface-sunken);
  cursor: not-allowed;
}

.primary:focus-visible {
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

@keyframes confirm-in {
  from {
    opacity: 0;
    transform: translateX(24px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .confirm {
    animation: none;
  }
}
</style>
