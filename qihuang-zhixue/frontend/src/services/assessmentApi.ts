import { requestJson } from './request'
import type { AssessmentAnswerRequest, AssessmentFinishResponse, AssessmentGrade, AssessmentStartResponse } from '../types/assessment'

const json = (body: unknown) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const startAssessment = (node_id: string) => requestJson<AssessmentStartResponse>('/api/assessment/start', json({ node_id }), { networkMessage: '小岐正在整理试卷，稍候再试一次。' })
export const answerAssessment = (body: AssessmentAnswerRequest) => requestJson<AssessmentGrade>('/api/assessment/answer', json(body), { networkMessage: '这一招没送到，小岐已替你留住题面，重试即可。' })
export const finishAssessment = (assessment_id: string) => requestJson<AssessmentFinishResponse>('/api/assessment/finish', json({ assessment_id }), { networkMessage: '小岐正在放榜，稍后再试一次。' })
export const gradePractice = (question_id: string, answer: string | string[], elapsed_seconds?: number) => requestJson<AssessmentGrade>('/api/quiz/grade', json({ question_id, answer, elapsed_seconds }))
