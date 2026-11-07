<template>
  <aside class="chat" :class="{ 'is-collapsed': collapsed }">
    <!-- 收起态：只留一条窄脊，画布拿回全部宽度 -->
    <button v-if="collapsed" class="spine" type="button" title="展开对话" aria-label="展开对话" @click="$emit('toggle')">
      <el-icon><ChatDotRound /></el-icon>
      <span v-if="unread" class="spine-dot" aria-hidden="true" />
    </button>

    <template v-else>
      <header class="chat-head">
        <div class="actions">
          <button type="button" class="ghost" @click="$emit('reset')">新对话</button>
          <button type="button" class="ghost" :class="{ on: historyOpen }" @click="historyOpen = !historyOpen">历史</button>
        </div>
        <button type="button" class="ghost icon" title="收起对话" aria-label="收起对话" @click="$emit('toggle')">
          <el-icon><Fold /></el-icon>
        </button>
      </header>

      <div ref="scroller" class="stream">
        <!-- 冷启动：四条都落在中药学语料范围内，点了就有货 -->
        <div v-if="!messages.length" class="cold">
          <p class="cold-lead">说一个中药学主题，边聊边出图。</p>
          <div class="chips">
            <button v-for="item in CHAT_SUGGESTIONS" :key="item" type="button" class="suggest" @click="submit(item)">
              {{ item }}
            </button>
          </div>
        </div>

        <template v-for="message in messages" :key="message.id">
          <p v-if="message.role === 'user'" class="bubble">{{ message.text }}</p>

          <div v-else-if="message.kind === 'progress'" class="progress">
            <i class="pulse" aria-hidden="true" />
            <span>{{ message.text }}</span>
          </div>

          <div v-else-if="message.kind === 'error'" class="error">
            <p>{{ message.text }}</p>
            <button type="button" class="retry" @click="$emit('retry')">重试</button>
          </div>

          <ChatCompleteCard
            v-else-if="message.kind === 'result'"
            :title="message.title"
            :nodes="message.nodes"
            :depth="message.depth"
            :inferred="message.inferred"
            :structure-id="message.structureId"
            :theme-id="message.themeId"
            :note="message.note"
            :degraded="message.degraded"
            @structure="$emit('structure', $event)"
            @theme="$emit('theme', $event)"
          />

          <p v-else class="note">{{ message.text }}</p>
        </template>
      </div>

      <!-- 历史抽屉：只列已有的导图，点一条载入画布 -->
      <transition name="slide">
        <section v-if="historyOpen" class="history" aria-label="历史导图">
          <header>
            <span>历史导图<span v-if="recent.length" class="history-count">{{ recent.length }}</span></span>
            <button type="button" class="ghost" @click="historyOpen = false">关闭</button>
          </header>
          <p v-if="!recent.length" class="history-empty">还没有存下来的导图。</p>
          <button
            v-for="item in recent"
            :key="item.id"
            type="button"
            class="history-row"
            :class="{ current: item.id === activeId }"
            :title="item.title"
            @click="open(item.id)"
          >
            <span class="history-title">{{ item.title }}</span>
            <span class="history-time">{{ shortDate(item.created_at) }}</span>
          </button>
        </section>
      </transition>

      <footer class="composer">
        <div class="box" :class="{ 'is-busy': busy }">
          <textarea
            v-model="draft"
            rows="1"
            maxlength="200"
            :placeholder="PLACEHOLDER"
            :disabled="busy"
            aria-label="导图主题"
            @keydown.enter.exact.prevent="submit()"
            @input="autoGrow"
          />
          <div class="box-foot">
            <button type="button" class="plus" title="添加附件（暂未开放）" aria-label="添加附件" disabled>
              <el-icon><Plus /></el-icon>
            </button>
            <button
              type="button"
              class="send"
              :disabled="!draft.trim() || busy"
              title="生成导图"
              aria-label="生成导图"
              @click="submit()"
            >
              <el-icon><Promotion /></el-icon>
            </button>
          </div>
        </div>
        <p class="fineprint">生成约 10–20 秒 · 可在右侧切换结构与配色</p>
      </footer>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { ChatDotRound, Fold, Plus, Promotion } from '@element-plus/icons-vue'
import { computed, nextTick, ref, watch } from 'vue'
import type { ResourceItem } from '../../types/resource'
import type { StructureId } from '../../config/mindmapStructures'
import { CHAT_SUGGESTIONS, type ChatMessage } from '../../composables/useMindmapChat'
import ChatCompleteCard from './ChatCompleteCard.vue'

const PLACEHOLDER = '说个中药学主题，或直接问：麻黄和桂枝有什么区别？'

const props = defineProps<{
  messages: ChatMessage[]
  recent: ResourceItem[]
  activeId?: string
  collapsed: boolean
  busy: boolean
}>()

const emit = defineEmits<{
  ask: [text: string]
  retry: []
  reset: []
  toggle: []
  open: [id: string]
  structure: [id: StructureId]
  theme: [id: string]
}>()

const draft = ref('')
const historyOpen = ref(false)
const scroller = ref<HTMLElement | null>(null)
const unread = computed(() => props.messages.length > 0 && props.collapsed)

const submit = (text?: string) => {
  const value = (text ?? draft.value).trim()
  if (!value || props.busy) return
  draft.value = ''
  emit('ask', value)
}

const autoGrow = (event: Event) => {
  const el = event.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 120)}px`
}

const open = (id: string) => {
  historyOpen.value = false
  emit('open', id)
}

const shortDate = (value: string) => {
  if (!value) return ''
  return new Date(value).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

// 新消息进来时贴底，但只在用户本来就在底部时才滚——否则会打断回看。
watch(() => props.messages.length, async () => {
  const el = scroller.value
  if (!el) return
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
  await nextTick()
  if (nearBottom) el.scrollTop = el.scrollHeight
})
</script>

<style scoped lang="scss">
.chat {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  border-right: 1px solid var(--qh-line);
  background: var(--qh-surface);
}

/* ---------- 收起态 ---------- */
.spine {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  border: 0;
  color: var(--qh-fg-muted);
  background: var(--qh-surface);
  cursor: pointer;

  &:hover { color: var(--qh-fg); background: var(--qh-surface-hover); }
}

.spine-dot {
  position: absolute;
  top: 14px;
  right: 12px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--qh-accent);
}

/* ---------- 头部 ---------- */
.chat-head {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  height: 44px;
  padding: 0 10px 0 12px;
  border-bottom: 1px solid var(--qh-line);
}

.actions {
  display: flex;
  gap: 2px;
}

.ghost {
  padding: 4px 8px;
  border: 1px solid transparent;
  border-radius: 999px;
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease), background-color var(--qh-duration-fast) var(--qh-ease);

  &:hover { color: var(--qh-fg); background: var(--qh-surface-hover); }
  &.on { color: var(--qh-fg); border-color: var(--qh-line); background: var(--qh-surface-sunken); }
  &.icon { display: grid; place-items: center; width: 26px; height: 26px; padding: 0; }
}

/* ---------- 消息流 ---------- */
.stream {
  flex: 1;
  min-height: 0;
  padding: 14px 14px 8px;
  overflow-y: auto;
  scrollbar-width: thin;
}

.cold {
  padding: 18px 2px;
}

.cold-lead {
  margin: 0 0 12px;
  color: var(--qh-fg-soft);
  font-size: 13px;
  line-height: 1.7;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.suggest {
  padding: 6px 10px;
  border: 1px solid var(--qh-line);
  border-radius: 999px;
  color: var(--qh-fg-soft);
  background: var(--qh-surface-raised);
  font-size: 12px;
  cursor: pointer;
  transition: border-color var(--qh-duration-fast) var(--qh-ease), color var(--qh-duration-fast) var(--qh-ease);

  &:hover { border-color: var(--qh-accent-line); color: var(--qh-fg); }
  &:focus-visible { outline: 2px solid var(--qh-accent-line); outline-offset: 1px; }
}

/* 用户气泡右对齐，米灰底——与参考图的对话语气一致。 */
.bubble {
  width: fit-content;
  max-width: 86%;
  margin: 0 0 12px auto;
  padding: 8px 12px;
  border-radius: 14px 14px 4px 14px;
  color: #3A2F1E;
  background: #F1EDE4;
  font-size: 13px;
  line-height: 1.65;
  word-break: break-word;
}

.progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  color: var(--qh-fg-muted);
  font-size: 12px;
}

.pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--qh-accent);
  animation: breathe 1.1s ease-in-out infinite;
}

.note {
  margin: 0 0 10px;
  padding: 8px 10px;
  border-left: 2px solid var(--qh-line-strong);
  color: var(--qh-fg-soft);
  font-size: 12px;
  line-height: 1.65;
}

.error {
  margin-bottom: 10px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, #B4543A 30%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, #B4543A 6%, transparent);

  p { margin: 0 0 6px; color: #8C3F2B; font-size: 12px; line-height: 1.6; }
}

.retry {
  padding: 3px 9px;
  border: 1px solid color-mix(in srgb, #B4543A 34%, transparent);
  border-radius: 999px;
  color: #8C3F2B;
  background: transparent;
  font-size: 11px;
  cursor: pointer;
}

/* ---------- 历史抽屉 ---------- */
.history {
  position: absolute;
  top: 44px;
  left: 0;
  right: 0;
  z-index: 6;
  /* 30 条也能翻：抽屉自己滚，不把消息流撑高。 */
  max-height: 68%;
  padding: 8px 10px 10px;
  overflow-y: auto;
  overscroll-behavior: contain;
  border-bottom: 1px solid var(--qh-line);
  background: var(--qh-surface-raised);
  box-shadow: var(--qh-shadow-2, 0 8px 24px rgb(0 0 0 / 8%));

  header {
    position: sticky;
    top: -8px;
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 2px;
    background: var(--qh-surface-raised);
    color: var(--qh-fg-muted);
    font-size: 11px;
    letter-spacing: 0.6px;
  }
}

.history-count {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 999px;
  color: var(--qh-fg-muted);
  background: var(--qh-surface-sunken);
  font-size: 10px;
  letter-spacing: 0;
}

.history-empty {
  margin: 8px 2px;
  color: var(--qh-fg-muted);
  font-size: 12px;
}

.history-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  cursor: pointer;

  &:hover { background: var(--qh-surface-hover); }
  &.current .history-title { color: var(--qh-accent-strong); }
}

.history-title {
  overflow: hidden;
  color: var(--qh-fg);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-time {
  flex: none;
  color: var(--qh-fg-muted);
  font-size: 11px;
}

.slide-enter-active,
.slide-leave-active { transition: opacity 160ms var(--qh-ease), transform 160ms var(--qh-ease); }
.slide-enter-from,
.slide-leave-to { opacity: 0; transform: translateY(-6px); }

/* ---------- 输入框 ---------- */
.composer {
  flex: none;
  padding: 8px 12px 10px;
}

.box {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 8px 6px 12px;
  border: 1px solid #E6DFD1;
  border-radius: 16px;
  background: #FBF8F2;
  transition: border-color var(--qh-duration-fast) var(--qh-ease), box-shadow var(--qh-duration-fast) var(--qh-ease);

  &:focus-within {
    border-color: var(--qh-accent-line);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--qh-accent) 12%, transparent);
  }

  &.is-busy { opacity: 0.72; }
}

textarea {
  width: 100%;
  min-height: 40px;
  max-height: 120px;
  padding: 2px 0;
  border: 0;
  outline: none;
  resize: none;
  color: var(--qh-fg);
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;

  &::placeholder { color: var(--qh-fg-muted); }
}

.box-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.plus {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 50%;
  color: var(--qh-fg-faint);
  background: transparent;
  cursor: not-allowed;
}

.send {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 50%;
  color: var(--qh-accent-ink);
  background: var(--qh-accent);
  cursor: pointer;
  transition: opacity var(--qh-duration-fast) var(--qh-ease), transform var(--qh-duration-fast) var(--qh-ease);

  &:hover:not(:disabled) { transform: translateY(-1px); }
  &:disabled { opacity: 0.36; cursor: not-allowed; }
  &:focus-visible { outline: 2px solid var(--qh-accent-line); outline-offset: 2px; }
}

.fineprint {
  margin: 6px 2px 0;
  color: var(--qh-fg-faint);
  font-size: 10px;
  letter-spacing: 0.2px;
}

@keyframes breathe {
  50% { opacity: 0.36; transform: scale(0.82); }
}

@media (prefers-reduced-motion: reduce) {
  .pulse { animation: none; }
  .slide-enter-active,
  .slide-leave-active { transition: none; }
}
</style>
