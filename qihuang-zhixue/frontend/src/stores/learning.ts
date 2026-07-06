import { defineStore } from 'pinia'
import { readLearningPath, readReviews, streamResources, submitQuiz } from '../services/learningApi'
import type {
  PathResponse,
  QuizSubmitItem,
  QuizSubmitResponse,
  Recommendation,
  ResourceEnvelope,
  ResourceType,
  ReviewsResponse,
  SkillNode,
} from '../types/learning'
import { MarkdownStreamBuffer } from '../utils/markdownStream'

const defaultTypes: ResourceType[] = ['explain', 'graph', 'mindmap', 'quiz', 'reading']

const resourceLabel: Record<ResourceType, string> = {
  explain: '讲解',
  graph: '知识图谱',
  mindmap: '思维导图',
  quiz: '题库',
  reading: '拓展阅读',
}

export const useLearningStore = defineStore('learning', {
  state: () => ({
    activeTopic: '麻黄',
    nodeLabel: '待命中',
    streamStableMarkdown: '',
    streamPendingMarkdown: '',
    resources: {} as Partial<Record<ResourceType, ResourceEnvelope>>,
    streamTrace: [] as Array<{ node: string; summary?: string; label?: string }>,
    streaming: false,
    loadingPath: false,
    loadingReviews: false,
    error: '',
    path: null as PathResponse | null,
    reviews: null as ReviewsResponse | null,
    lastSubmit: null as QuizSubmitResponse | null,
  }),
  getters: {
    skillTree: (state): SkillNode[] => state.path?.skill_tree ?? [],
    recommendations: (state): Recommendation[] => state.path?.recommendations ?? [],
    resourceList: (state): ResourceEnvelope[] => defaultTypes.flatMap((type) => (state.resources[type] ? [state.resources[type]] : [])),
    hasResources: (state): boolean => Object.keys(state.resources).length > 0,
  },
  actions: {
    async loadPath() {
      this.loadingPath = true
      this.error = ''
      try {
        this.path = await readLearningPath()
        this.reviews = { user_id: this.path.user_id, ...this.path.reviews }
      } catch (error) {
        this.error = error instanceof Error ? error.message : '学习路径加载失败'
      } finally {
        this.loadingPath = false
      }
    },
    async loadReviews() {
      this.loadingReviews = true
      this.error = ''
      try {
        this.reviews = await readReviews()
      } catch (error) {
        this.error = error instanceof Error ? error.message : '复习计划加载失败'
      } finally {
        this.loadingReviews = false
      }
    },
    async generateResources(topic: string, types: ResourceType[] = defaultTypes) {
      const buffer = new MarkdownStreamBuffer()
      this.activeTopic = topic.trim() || this.activeTopic
      this.nodeLabel = '准备生成'
      this.streamStableMarkdown = ''
      this.streamPendingMarkdown = ''
      this.resources = {}
      this.streamTrace = []
      this.streaming = true
      this.error = ''

      try {
        await streamResources(
          {
            topic: this.activeTopic,
            types,
          },
          {
            nodeStart: (payload) => {
              this.nodeLabel = payload.label || payload.node
              this.streamTrace.push(payload)
            },
            token: (payload) => {
              const next = buffer.push(payload.text)
              this.streamStableMarkdown = next.stable
              this.streamPendingMarkdown = next.pending
            },
            resourceDone: ({ resource }) => {
              this.resources = {
                ...this.resources,
                [resource.type]: resource,
              }
            },
            done: () => {
              const finalText = buffer.flush()
              this.streamStableMarkdown = finalText.stable
              this.streamPendingMarkdown = finalText.pending
              this.nodeLabel = '生成完成'
            },
          },
        )
      } catch (error) {
        this.error = error instanceof Error ? error.message : '资源生成失败'
      } finally {
        this.streaming = false
      }
    },
    async answerQuiz(items: QuizSubmitItem[]) {
      this.error = ''
      try {
        this.lastSubmit = await submitQuiz(items)
        await Promise.all([this.loadPath(), this.loadReviews()])
      } catch (error) {
        this.error = error instanceof Error ? error.message : '题目提交失败'
      }
    },
    labelFor(type: ResourceType) {
      return resourceLabel[type]
    },
  },
})
