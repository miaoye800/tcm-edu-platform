export type AssessmentGateId = 'quick' | 'case' | 'optional'
export type AssessmentPhase = 'idle' | 'loading' | 'answering' | 'transitioning' | 'finishing' | 'result' | 'error'
export type SourceLevel = 'real' | 'adapted' | 'original'

export interface AssessmentQuestion {
  id: string
  stem: string
  options: Array<{ key: string; text: string }>
  question_type: 'single' | 'multi' | 'case' | 'short'
  exam_point_id: string
  source_level: SourceLevel
  source_detail: Record<string, unknown>
}
export interface AssessmentGate { id: AssessmentGateId; name: string; recommended_seconds: number; questions: AssessmentQuestion[] }
export interface AssessmentStartResponse { assessment_id: string; gates: AssessmentGate[]; strictness: string; threshold: number }
export interface AssessmentGrade { question_id: string; correct: boolean; score: number; missing_points: string[]; comment: string; analysis: string; remediation?: Record<string, unknown> | null }
export interface AssessmentFinishResponse { assessment_id: string; node_id: string; score: number; gate_scores: Record<string, number>; threshold: number; passed: boolean; weak_exam_point_ids: string[]; remediation: { entry: string; node_id: string } | null }
export interface AssessmentAnswerRequest { assessment_id: string; question_id: string; answer: string | string[]; elapsed_seconds?: number }
