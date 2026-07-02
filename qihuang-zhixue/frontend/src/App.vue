<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

type Source = {
  name: string
  type: string
  ref: string
}

type SseEvent = {
  event: string
  data: Record<string, unknown>
}

const nodeLabels: Record<string, string> = {
  router: '正在判断问题类型',
  rag_agent: '正在检索中药知识库',
  explain_agent: '正在生成讲解',
  profile_agent: '正在更新学习画像',
}

const question = ref('麻黄的功效是什么？')
const answer = ref('')
const sources = ref<Source[]>([])
const currentNode = ref('等待提问')
const loading = ref(false)
const finished = ref(false)

const renderedAnswer = computed(() => {
  const sourceMarkdown = sources.value.length
    ? `\n\n### 来源\n${sources.value
        .map((source, index) => `${index + 1}. ${source.name}：${source.ref}`)
        .join('\n')}`
    : ''

  return renderMarkdown(`${answer.value}${sourceMarkdown}`)
})

const askQuestion = async (): Promise<void> => {
  const text = question.value.trim()
  if (!text) {
    ElMessage.warning('请输入问题')
    return
  }

  loading.value = true
  finished.value = false
  answer.value = ''
  sources.value = []
  currentNode.value = '正在连接后端'

  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ question: text }),
    })

    if (!response.ok || !response.body) {
      throw new Error(`后端响应异常：${response.status}`)
    }

    await readSseStream(response.body)
  } catch (error) {
    const message = error instanceof Error ? error.message : '流式问答失败'
    ElMessage.error(message)
    currentNode.value = '请求失败'
  } finally {
    loading.value = false
  }
}

const readSseStream = async (body: ReadableStream<Uint8Array>): Promise<void> => {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }

    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split(/\r?\n\r?\n/)
    buffer = parts.pop() ?? ''

    for (const part of parts) {
      const event = parseSseEvent(part)
      if (event) {
        handleSseEvent(event)
      }
    }
  }

  buffer += decoder.decode()
  const tail = parseSseEvent(buffer)
  if (tail) {
    handleSseEvent(tail)
  }
}

const parseSseEvent = (raw: string): SseEvent | null => {
  const lines = raw.split(/\r?\n/).filter(Boolean)
  const eventLine = lines.find((line) => line.startsWith('event:'))
  const dataLines = lines.filter((line) => line.startsWith('data:'))

  if (!eventLine || dataLines.length === 0) {
    return null
  }

  const event = eventLine.slice('event:'.length).trim()
  const dataText = dataLines.map((line) => line.slice('data:'.length).trim()).join('\n')

  return {
    event,
    data: JSON.parse(dataText) as Record<string, unknown>,
  }
}

const handleSseEvent = (item: SseEvent): void => {
  if (item.event === 'node_start') {
    const node = String(item.data.node ?? '')
    currentNode.value = nodeLabels[node] ?? `正在运行 ${node}`
    return
  }

  if (item.event === 'token') {
    answer.value += String(item.data.text ?? '')
    return
  }

  if (item.event === 'done') {
    sources.value = Array.isArray(item.data.sources) ? (item.data.sources as Source[]) : []
    currentNode.value = '回答完成'
    finished.value = true
  }
}

const renderMarkdown = (markdown: string): string => {
  const lines = markdown.split('\n')
  const html: string[] = []
  let inList = false
  let inCode = false
  let codeLines: string[] = []

  const closeList = (): void => {
    if (inList) {
      html.push('</ul>')
      inList = false
    }
  }

  for (const line of lines) {
    if (line.startsWith('```')) {
      closeList()
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = []
        inCode = false
      } else {
        inCode = true
      }
      continue
    }

    if (inCode) {
      codeLines.push(line)
      continue
    }

    if (line.startsWith('### ')) {
      closeList()
      html.push(`<h3>${renderInline(line.slice(4))}</h3>`)
      continue
    }

    if (/^\d+\.\s+/.test(line)) {
      closeList()
      html.push(`<p class="source-line">${renderInline(line)}</p>`)
      continue
    }

    if (line.startsWith('- ')) {
      if (!inList) {
        html.push('<ul>')
        inList = true
      }
      html.push(`<li>${renderInline(line.slice(2))}</li>`)
      continue
    }

    if (!line.trim()) {
      closeList()
      continue
    }

    closeList()
    html.push(`<p>${renderInline(line)}</p>`)
  }

  closeList()
  return html.join('')
}

const renderInline = (text: string): string =>
  escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>')

const escapeHtml = (text: string): string =>
  text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
</script>

<template>
  <main class="page-shell">
    <section class="chat-workspace">
      <header class="topbar">
        <div>
          <p class="eyebrow">中医学个性化学习多智能体系统</p>
          <h1>岐黄智学</h1>
        </div>
        <el-tag class="node-tag" :type="finished ? 'success' : 'warning'" effect="light">
          {{ currentNode }}
        </el-tag>
      </header>

      <section class="chat-panel" aria-label="流式问答">
        <div class="message user-message">
          <span class="message-role">学生</span>
          <el-input
            v-model="question"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 5 }"
            resize="none"
            placeholder="请输入中药学问题"
            :disabled="loading"
            @keydown.ctrl.enter.prevent="askQuestion"
          />
          <el-button type="primary" :loading="loading" @click="askQuestion">发送</el-button>
        </div>

        <div class="message assistant-message">
          <span class="message-role">智能体</span>
          <div v-if="answer || loading" class="markdown-body" v-html="renderedAnswer" />
          <el-empty v-else description="输入问题后开始流式问答" :image-size="90" />
        </div>
      </section>
    </section>
  </main>
</template>
