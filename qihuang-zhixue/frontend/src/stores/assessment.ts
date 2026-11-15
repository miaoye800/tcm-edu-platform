import { defineStore } from 'pinia'
import { answerAssessment, finishAssessment, startAssessment } from '../services/assessmentApi'
import type { AssessmentFinishResponse, AssessmentGate, AssessmentGrade, AssessmentPhase, AssessmentQuestion, AssessmentStartResponse } from '../types/assessment'

const fallback = (nodeId: string): AssessmentStartResponse => ({ assessment_id: `mock-${Date.now()}`, strictness: '标准', threshold: 70, gates: [
  { id: 'quick', name: '快问快答', recommended_seconds: 45, questions: [{ id: 'mock-q1', stem: '麻黄发汗解表时，最须辨明的表证特点是？', options: [{ key: 'A', text: '表实无汗' }, { key: 'B', text: '阴虚盗汗' }, { key: 'C', text: '里热汗多' }], question_type: 'single', exam_point_id: nodeId, source_level: 'real', source_detail: { year: 2023, source: '中药学真题' } }] },
  { id: 'case', name: '病例情境', recommended_seconds: 180, questions: [{ id: 'mock-case', stem: '患者恶寒发热、无汗喘咳。请选择最合适的辨证与用药思路。', options: [{ key: 'A', text: '风寒表实，麻黄宣肺平喘' }, { key: 'B', text: '阴虚内热，滋阴降火' }, { key: 'C', text: '湿热中阻，清热利湿' }], question_type: 'case', exam_point_id: nodeId, source_level: 'adapted', source_detail: { adapted_from_question_id: 'case-01', changes: '病例措辞改编' } }] },
] })

export const useAssessmentStore = defineStore('assessment', {
  state: () => ({ phase: 'idle' as AssessmentPhase, nodeId: '', session: null as AssessmentStartResponse | null, gateIndex: 0, questionIndex: 0, combo: 0, answers: {} as Record<string, AssessmentGrade>, result: null as AssessmentFinishResponse | null, error: '', usingMock: false }),
  getters: {
    currentGate(state): AssessmentGate | undefined { return state.session?.gates[state.gateIndex] },
    currentQuestion(): AssessmentQuestion | undefined { return this.currentGate?.questions[this.questionIndex] },
    elapsedCount(state) { return Object.keys(state.answers).length },
  },
  actions: {
    async begin(nodeId: string) { this.$reset(); this.nodeId = nodeId; this.phase = 'loading'; const forceMock = window.sessionStorage.getItem('qihuang-e2e-force-mock') === '1' || window.localStorage.getItem('qihuang-e2e-force-mock') === '1'; try { this.session = forceMock ? fallback(nodeId) : await startAssessment(nodeId); this.usingMock = forceMock } catch { this.session = fallback(nodeId); this.usingMock = true } this.phase = 'answering' },
    async submit(answer: string | string[], elapsed_seconds = 0) { const question = this.currentQuestion; if (!question || !this.session || this.phase !== 'answering') return; this.phase = 'transitioning'; try { const grade = this.usingMock ? { question_id: question.id, correct: (Array.isArray(answer) ? answer[0] : answer) === 'A', score: (Array.isArray(answer) ? answer[0] : answer) === 'A' ? 100 : 0, missing_points: [], comment: '', analysis: '' } : await answerAssessment({ assessment_id: this.session.assessment_id, question_id: question.id, answer, elapsed_seconds }); this.answers[question.id] = grade; this.combo = grade.correct ? this.combo + 1 : 0; return grade } catch (error) { this.error = error instanceof Error ? error.message : '小岐没接住这一答，重试即可。'; this.phase = 'answering'; return } },
    next() { const gate = this.currentGate; if (!gate) return; if (this.questionIndex + 1 < gate.questions.length) this.questionIndex += 1; else if (this.gateIndex + 1 < (this.session?.gates.length || 0)) { this.gateIndex += 1; this.questionIndex = 0 } else { void this.finish(); return } this.phase = 'answering' },
    async finish() { if (!this.session) return; this.phase = 'finishing'; try { this.result = this.usingMock ? { assessment_id: this.session.assessment_id, node_id: this.nodeId, kc_node_id: this.nodeId, score: Math.round(Object.values(this.answers).reduce((sum, item) => sum + item.score, 0) / Math.max(1, this.elapsedCount)), gate_scores: {}, threshold: this.session.threshold, passed: Object.values(this.answers).every(item => item.correct), weak_exam_point_ids: [], remediation: null } : await finishAssessment(this.session.assessment_id); this.phase = 'result' } catch (error) { this.error = error instanceof Error ? error.message : '小岐还在核卷，请再试一次。'; this.phase = 'error' } },
  },
})
