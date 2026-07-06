<template>
  <div class="markdown-block">
    <div v-if="html" class="markdown-body" v-html="html" />
    <p v-if="pending" class="stream-pending">{{ pending }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const props = defineProps<{
  source: string
  pending?: string
}>()

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

const html = computed(() => md.render(props.source || ''))
</script>

<style scoped lang="scss">
.markdown-block {
  min-width: 0;
}

.markdown-body {
  color: rgba(238, 245, 239, 0.88);
  font-size: 15px;
  line-height: 1.85;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 18px 0 10px;
  color: var(--qh-text);
  font-family: var(--qh-font-display);
  font-weight: 900;
}

.markdown-body :deep(h1) {
  font-size: 24px;
}

.markdown-body :deep(h2) {
  font-size: 20px;
}

.markdown-body :deep(h3) {
  font-size: 17px;
}

.markdown-body :deep(p),
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0 0 12px;
}

.markdown-body :deep(li + li) {
  margin-top: 6px;
}

.markdown-body :deep(code) {
  padding: 2px 6px;
  border-radius: 8px;
  color: var(--qh-color-gold);
  background: rgba(238, 245, 239, 0.08);
}

.markdown-body :deep(blockquote) {
  margin: 12px 0;
  padding: 8px 12px;
  border-left: 3px solid var(--qh-color-jade);
  color: var(--qh-text-muted);
  background: rgba(143, 208, 173, 0.07);
}

.stream-pending {
  display: inline;
  margin: 0;
  color: rgba(238, 245, 239, 0.62);
  line-height: 1.85;
}

.stream-pending::after {
  display: inline-block;
  width: 8px;
  height: 1.1em;
  margin-left: 4px;
  vertical-align: -0.18em;
  background: var(--qh-color-jade);
  animation: live 1.1s ease-in-out infinite;
  content: "";
}
</style>
