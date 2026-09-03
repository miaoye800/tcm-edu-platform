import type { AlchemyStep } from './AlchemyStepRail.vue'

export type AlchemyStepEvent = { type: 'step'; stepId: string; label?: string } | { type: 'step_done' | 'status' | 'complete'; stepId?: string }

export const resolveAlchemyStepLabels = (base: readonly AlchemyStep[], events: readonly AlchemyStepEvent[]) => {
  const labels = new Map(base.map((step) => [step.id, step.label]))
  events.forEach((event) => {
    if (event.type === 'step' && event.stepId && event.label?.trim()) labels.set(event.stepId, event.label.trim())
  })
  return base.map((step) => ({ ...step, label: labels.get(step.id) || step.label }))
}
