<template>
  <div class="composer">
    <div class="composer-quick">
      <button v-for="item in quick" :key="item.label" type="button" class="quick-chip" @click="fill(item.prompt)">
        {{ item.label }}
      </button>
    </div>

    <div v-if="selected.length" class="composer-refs">
      <span class="refs-label">引用</span>
      <button v-for="item in selected" :key="item.id" type="button" class="ref-chip" @click="dropRef(item.id)">
        {{ shortTitle(item) }}<span class="ref-x" aria-hidden="true">×</span>
      </button>
    </div>

    <div class="composer-box">
      <textarea
        ref="input"
        v-model="draft"
        class="composer-input"
        :rows="1"
        :disabled="disabled"
        placeholder="说要做什么：做一张解表药的导图 / 给我讲十八反，做 12 页 / 麻黄和桂枝有什么区别"
        aria-label="对小岐说"
        @keydown="onKeydown"
        @input="onInput"
      />
      <button v-if="busy" class="composer-action is-stop" type="button" @click="emit('stop')">停止</button>
      <button v-else class="composer-action" type="button" :disabled="!draft.trim() || disabled" @click="send">发送</button>
    </div>

    <!-- 候选浮层挂在输入框上方，不挤压输入区高度：输入时界面不该跳动。 -->
    <ul v-if="mentionOpen" class="mention" role="listbox" aria-label="引用既有资源">
      <li v-if="!candidates.length" class="mention-empty">
        {{ refsLoading ? '正在找你的资源…' : '没找到匹配的资源；@ 只能引用已经生成过的成品。' }}
      </li>
      <li v-for="item in candidates" :key="item.id">
        <button type="button" class="mention-item" @click="applyMention(item)">
          <span class="mention-kind">{{ kindLabel(item.type) }}</span>
          <span class="mention-title">{{ shortTitle(item) }}</span>
        </button>
      </li>
    </ul>

    <p class="composer-hint">Enter 发送 · Shift+Enter 换行 · 打 @ 可以引用既有资源 · 生成前会先出方案，确认后才动手</p>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

export interface ResourceOption {
  id: string
  title: string
  type: string
}

const props = defineProps<{
  busy?: boolean
  disabled?: boolean
  resources?: ResourceOption[]
  refsLoading?: boolean
}>()

const emit = defineEmits<{ send: [text: string, refs: string[]]; stop: []; lookResources: [] }>()

const draft = ref('')
const input = ref<HTMLTextAreaElement>()
const selected = ref<ResourceOption[]>([])
const mentionOpen = ref(false)
const mentionQuery = ref('')
/** 已触发过一次资源拉取就不再每次 `@` 都发请求。 */
const askedOnce = ref(false)

const quick = [
  { label: '做一张导图', prompt: '做一张解表药的思维导图' },
  { label: '做一份讲稿', prompt: '给规培医师讲十八反，做 12 页' },
  { label: '写一篇文稿', prompt: '写一篇人参的用法与禁忌' },
  { label: '查两味药', prompt: '麻黄和桂枝有什么区别' },
]

const kindLabel = (type: string) =>
  ({ mindmap: '导图', ppt: '幻灯', doc: '文稿', quiz: '试卷', story: '漫剧' }[type] || '资源')

/**
 * 资源标题落库时带了「· 类型」后缀（`麻黄汤证 · 漫剧`）。候选行左侧已经有类型
 * 标签，标题里再带一遍就成了「漫剧 麻黄汤证 · 漫剧」。只掐掉已知的类型后缀，
 * 标题中间本来就有「·」的不动。
 */
const TYPE_SUFFIX = /\s*·\s*(?:思维导图|导图|幻灯|文稿|讲稿|试卷|漫剧|资源)\s*$/

const shortTitle = (item: ResourceOption) => item.title.replace(TYPE_SUFFIX, '').trim() || item.title

const candidates = computed(() => {
  const pool = props.resources || []
  const query = mentionQuery.value.trim()
  const picked = new Set(selected.value.map((item) => item.id))
  return pool
    .filter((item) => !picked.has(item.id))
    .filter((item) => !query || item.title.includes(query))
    .slice(0, 6)
})

const autoGrow = () => {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

const fill = (text: string) => {
  draft.value = text
  mentionOpen.value = false
  void nextTick(() => {
    input.value?.focus()
    autoGrow()
  })
}

/**
 * 从光标位置往前找 `@`。
 *
 * 只在「邮箱 / 账号」的形态下压住候选：`@` 紧跟在 ASCII 字母数字、`.` `-` `_` 之后。
 * 中文用户习惯写完一句话直接打 `@`（前面是全角标点或汉字），那种必须弹。
 */
const detectMention = () => {
  const el = input.value
  if (!el) return
  const caret = el.selectionStart ?? draft.value.length
  const before = draft.value.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at < 0) {
    mentionOpen.value = false
    return
  }
  const prev = before.slice(at - 1, at)
  if (prev && /[A-Za-z0-9._-]/.test(prev)) {
    mentionOpen.value = false
    return
  }
  const query = before.slice(at + 1)
  if (/\s/.test(query)) {
    mentionOpen.value = false
    return
  }
  mentionOpen.value = true
  mentionQuery.value = query
  if (!askedOnce.value) {
    askedOnce.value = true
    emit('lookResources')
  }
}

const onInput = () => {
  detectMention()
  autoGrow()
}

const applyMention = (item: ResourceOption) => {
  const el = input.value
  const caret = el?.selectionStart ?? draft.value.length
  const before = draft.value.slice(0, caret)
  const at = before.lastIndexOf('@')
  const tail = draft.value.slice(caret)
  // 用「」包住标题：@ 被替换掉之后，用户仍看得出这是引用，不是自己打的字。
  draft.value = `${before.slice(0, at)}「${shortTitle(item)}」${tail}`
  if (!selected.value.some((entry) => entry.id === item.id)) selected.value = [...selected.value, item]
  mentionOpen.value = false
  void nextTick(() => {
    input.value?.focus()
    autoGrow()
  })
}

const dropRef = (id: string) => {
  selected.value = selected.value.filter((item) => item.id !== id)
}

const send = () => {
  const text = draft.value.trim()
  if (!text) return
  emit('send', text, selected.value.map((item) => item.id))
  draft.value = ''
  selected.value = []
  mentionOpen.value = false
  void nextTick(autoGrow)
}

const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && mentionOpen.value) {
    event.preventDefault()
    mentionOpen.value = false
    return
  }
  // 候选开着时 Enter 先选第一条：用户打完 @麻黄 回车，期望的是「引用它」而不是「发出去」。
  if (event.key === 'Enter' && mentionOpen.value && candidates.value.length && !event.isComposing) {
    event.preventDefault()
    applyMention(candidates.value[0])
    return
  }
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  send()
}
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;

.composer {
  position: relative;
  display: grid;
  gap: 8px;
  padding-top: 4px;
}

.composer-quick {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.quick-chip {
  padding: 4px 10px;
  border: 1px solid var(--qh-border);
  border-radius: 999px;
  background: transparent;
  color: var(--qh-text-muted);
  cursor: pointer;
  font-size: 12px;
}

.quick-chip:hover {
  border-color: var(--qh-accent-line);
  color: var(--qh-text);
}

.composer-refs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.refs-label {
  color: var(--qh-text-muted);
  font-size: 11px;
}

.ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 220px;
  padding: 3px 8px;
  overflow: hidden;
  border: 1px solid var(--qh-accent-line);
  border-radius: 999px;
  background: var(--qh-accent-soft);
  color: var(--qh-text);
  cursor: pointer;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ref-x {
  color: var(--qh-text-muted);
}

.composer-box {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 8px 8px 8px 14px;
  border: 1px solid var(--qh-border);
  border-radius: 12px;
  background: var(--qh-surface);
}

.composer-box:focus-within {
  border-color: var(--qh-accent-line);
}

.composer-input {
  flex: 1;
  min-height: 24px;
  max-height: 160px;
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: var(--qh-text);
  font-family: var(--qh-font-body);
  font-size: 14px;
  line-height: 1.6;
  resize: none;
}

.composer-input:focus {
  outline: none;
}

.composer-action {
  flex: 0 0 auto;
  min-width: 64px;
  padding: 8px 14px;
  border: 1px solid var(--qh-accent);
  border-radius: 8px;
  background: var(--qh-accent);
  color: var(--qh-accent-ink);
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
}

.composer-action:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.composer-action.is-stop {
  border-color: var(--qh-border);
  background: transparent;
  color: var(--qh-text);
}

.mention {
  position: absolute;
  bottom: calc(100% - 4px);
  left: 0;
  z-index: 6;
  width: min(420px, 100%);
  margin: 0;
  padding: 4px;
  list-style: none;
  border: 1px solid var(--qh-border);
  border-radius: 10px;
  background: var(--qh-surface);
  box-shadow: 0 12px 28px rgb(0 0 0 / 12%);
}

.mention-empty {
  padding: 8px 10px;
  color: var(--qh-text-muted);
  font-size: 12px;
}

.mention-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--qh-text);
  cursor: pointer;
  text-align: left;
}

.mention-item:hover {
  background: var(--qh-accent-soft);
}

.mention-kind {
  flex: 0 0 auto;
  color: var(--qh-text-muted);
  font-size: 11px;
}

.mention-title {
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-hint {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 11px;
}
</style>
