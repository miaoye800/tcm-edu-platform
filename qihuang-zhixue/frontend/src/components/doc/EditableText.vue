<template>
  <component
    :is="tag"
    ref="surface"
    class="doc-editable"
    :class="{ 'is-empty': isEmpty, 'is-readonly': !editable }"
    :contenteditable="editable ? 'true' : 'false'"
    :data-placeholder="placeholder"
    spellcheck="false"
    @input="onInput"
    @keydown="onKeydown"
    @paste="onPaste"
    @focus="onFocus"
    @blur="onBlur"
    @compositionstart="onCompositionStart"
    @compositionend="onCompositionEnd"
  />
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { parseInline } from '../../utils/inlineMarkdown'

/**
 * 单块可编辑文本区。
 *
 * 这是整个改造里最容易出问题的组件，原因只有一个：中文输入法。
 * 因此三条硬规则：
 *  1. composition 期间绝不回写 DOM（否则候选词会被打断 / 丢字）
 *  2. 编辑中（focus）绝不因为 modelValue 变化而重设 innerHTML（否则光标跳到行首）
 *  3. 失焦时才做一次「DOM → Markdown」的最终序列化
 */
const props = withDefaults(defineProps<{
  modelValue: string
  tag?: string
  placeholder?: string
  editable?: boolean
  /** 单行块：Enter 交给父级新建块，Shift+Enter 才换行 */
  singleLine?: boolean
}>(), {
  tag: 'div',
  placeholder: '输入 / 唤起命令',
  editable: true,
  singleLine: true,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'enter', payload: { shift: boolean; before: string; after: string }): void
  (e: 'backspace-empty'): void
  (e: 'focus'): void
  (e: 'blur'): void
  (e: 'slash', payload: { before: string }): void
}>()

const surface = ref<HTMLElement | null>(null)
const composing = ref(false)
const focused = ref(false)

const escapeHtml = (text: string): string =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

const toHtml = (text: string): string =>
  parseInline(text || '').map(segment => {
    switch (segment.kind) {
      case 'strong': return `<strong>${escapeHtml(segment.text)}</strong>`
      case 'em': return `<em>${escapeHtml(segment.text)}</em>`
      case 'del': return `<del>${escapeHtml(segment.text)}</del>`
      case 'code': return `<code>${escapeHtml(segment.text)}</code>`
      case 'link': return `<a href="${escapeHtml(segment.href || '')}" target="_blank" rel="noopener">${escapeHtml(segment.text)}</a>`
      case 'citation':
        return `<span class="doc-citation" data-citation="${segment.index}" contenteditable="false">[${Number(segment.index) + 1}]</span>`
      default: return escapeHtml(segment.text)
    }
  }).join('')

/** DOM → 内联 Markdown。与 parseInline 构成往返。 */
const serialize = (node: Node): string => {
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || ''
  if (node.nodeType !== Node.ELEMENT_NODE) return ''
  const element = node as HTMLElement
  const inner = Array.from(element.childNodes).map(serialize).join('')
  switch (element.tagName) {
    case 'STRONG': case 'B': return inner ? `**${inner}**` : ''
    case 'EM': case 'I': return inner ? `*${inner}*` : ''
    case 'DEL': case 'S': case 'STRIKE': return inner ? `~~${inner}~~` : ''
    case 'CODE': return inner ? `\`${inner}\`` : ''
    case 'A': return `[${inner}](${element.getAttribute('href') || ''})`
    case 'BR': return '\n'
    case 'DIV': case 'P': return inner + (inner ? '\n' : '')
    case 'SPAN':
      if (element.dataset.citation !== undefined) return `[${element.dataset.citation}]`
      return inner
    default: return inner
  }
}

const readModel = (): string => {
  const element = surface.value
  if (!element) return ''
  const raw = Array.from(element.childNodes).map(serialize).join('')
  return raw.replace(/\u00a0/g, ' ').replace(/\n+$/, '')
}

const isEmpty = computed(() => !props.modelValue)

const onInput = () => {
  if (composing.value) return
  emit('update:modelValue', readModel())
}

/** 光标是否在块首 / 块尾，供 Backspace 与方向键跨块使用 */
const caretAtStart = (): boolean => {
  const selection = window.getSelection()
  if (!selection || !selection.rangeCount) return true
  const range = selection.getRangeAt(0).cloneRange()
  const probe = document.createRange()
  probe.selectNodeContents(surface.value as HTMLElement)
  probe.setEnd(range.startContainer, range.startOffset)
  return probe.toString().length === 0
}

const caretAtEnd = (): boolean => {
  const selection = window.getSelection()
  if (!selection || !selection.rangeCount) return true
  const range = selection.getRangeAt(0).cloneRange()
  const probe = document.createRange()
  probe.selectNodeContents(surface.value as HTMLElement)
  probe.setStart(range.endContainer, range.endOffset)
  return probe.toString().length === 0
}

const splitAtCaret = (): { before: string; after: string } => {
  const selection = window.getSelection()
  const element = surface.value
  if (!selection || !selection.rangeCount || !element) return { before: readModel(), after: '' }
  const range = selection.getRangeAt(0)
  const tail = range.cloneRange()
  tail.selectNodeContents(element)
  tail.setStart(range.endContainer, range.endOffset)
  const after = Array.from(tail.cloneContents().childNodes).map(serialize).join('')
  const head = range.cloneRange()
  head.selectNodeContents(element)
  head.setEnd(range.endContainer, range.endOffset)
  const before = Array.from(head.cloneContents().childNodes).map(serialize).join('')
  return { before: before.replace(/\n+$/, ''), after: after.replace(/^\n+/, '') }
}

const onKeydown = (event: KeyboardEvent) => {
  if (composing.value) return
  if (!props.editable) return

  if (event.key === 'Enter') {
    if (props.singleLine && !event.shiftKey) {
      event.preventDefault()
      emit('enter', { shift: false, ...splitAtCaret() })
      return
    }
    if (props.singleLine && event.shiftKey) {
      event.preventDefault()
      document.execCommand('insertLineBreak')
      emit('update:modelValue', readModel())
      return
    }
    return
  }

  if (event.key === 'Backspace' && caretAtStart() && !readModel().trim()) {
    event.preventDefault()
    emit('backspace-empty')
    return
  }

  // 斜杠只在段首/空白文本时唤起，避免正文里出现 "/" 就弹菜单
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    const model = readModel()
    if (!model.trim() || caretAtStart()) emit('slash', { before: '' })
  }
}

const onPaste = (event: ClipboardEvent) => {
  if (!props.editable) return
  // 一律按纯文本粘贴：保留用户内容，但不把外部 HTML 灌进 contenteditable
  const text = event.clipboardData?.getData('text/plain') ?? ''
  if (!text) return
  event.preventDefault()
  document.execCommand('insertText', false, props.singleLine ? text.replace(/\s*\n\s*/g, ' ') : text)
  emit('update:modelValue', readModel())
}

const onFocus = () => { focused.value = true; emit('focus') }

const onBlur = () => {
  focused.value = false
  emit('update:modelValue', readModel())
  emit('blur')
}

const onCompositionStart = () => { composing.value = true }
const onCompositionEnd = () => {
  composing.value = false
  emit('update:modelValue', readModel())
}

const sync = (text: string) => {
  const element = surface.value
  if (!element) return
  if (element.innerHTML !== toHtml(text)) element.innerHTML = toHtml(text)
}

watch(() => props.modelValue, value => {
  if (focused.value || composing.value) return
  sync(value)
})

onMounted(() => { sync(props.modelValue) })

const focus = async (position: 'start' | 'end' = 'end') => {
  await nextTick()
  const element = surface.value
  if (!element) return
  element.focus()
  const range = document.createRange()
  range.selectNodeContents(element)
  range.collapse(position === 'start')
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
}

defineExpose({ focus, caretAtStart, caretAtEnd, readModel, sync })
</script>

<style scoped lang="scss">
.doc-editable {
  min-height: 1.5em;
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  caret-color: var(--qh-fg);
}

.doc-editable.is-empty::before {
  color: var(--qh-fg-faint);
  content: attr(data-placeholder);
  pointer-events: none;
}

.doc-editable.is-readonly {
  cursor: default;
}

.doc-editable :deep(strong) {
  color: var(--qh-fg);
  font-weight: 600;
}

.doc-editable :deep(em) {
  font-style: italic;
}

.doc-editable :deep(del) {
  color: var(--qh-fg-muted);
  text-decoration: line-through;
}

.doc-editable :deep(code) {
  padding: 2px 5px;
  border-radius: 3px;
  color: var(--qh-accent);
  background: color-mix(in srgb, var(--qh-accent) 9%, transparent);
  font-family: "SFMono-Regular", "Cascadia Code", Consolas, "Liberation Mono", monospace;
  font-size: 0.9em;
}

.doc-editable :deep(a) {
  color: var(--qh-accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.doc-editable :deep(.doc-citation) {
  margin: 0 1px;
  padding: 0 3px;
  border-radius: 3px;
  color: var(--qh-accent);
  background: color-mix(in srgb, var(--qh-accent) 10%, transparent);
  font-size: 0.76em;
  vertical-align: 0.1em;
  cursor: pointer;
  user-select: none;
}
</style>
