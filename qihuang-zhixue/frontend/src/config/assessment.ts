export const assessmentVisual = {
  quickTransitionMs: 250,
  wrongFlashMs: 180,
  sealMs: 300,
  timerWarningSeconds: 3,
  timerDangerPulseSeconds: 0.72,
  comboRevealMs: 220,
  seal: { durationMs: 520, overshoot: 1.18, blurPx: 5, spreadMs: 360 },
  paper: { textureOpacity: 0.032, lineColor: 'var(--qh-color-paper-line)', ink: 'var(--qh-color-paper-ink)' },
  paperTextureOpacity: 0.032,
  passReturnQuery: 'assessment_passed',
} as const
