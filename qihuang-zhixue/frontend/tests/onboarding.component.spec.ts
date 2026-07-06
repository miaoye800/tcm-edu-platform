type OnboardingComponentCase = {
  name: string
  setup: string
  action: string
  expected: string
}

export const onboardingComponentCases: OnboardingComponentCase[] = [
  {
    name: 'first login without profile redirects to onboarding',
    setup: 'Mock an authenticated user, GET /api/profile returns all zero confidence, and no local onboarding state exists.',
    action: 'Navigate to /dashboard.',
    expected: 'Router lands on /onboarding with redirect=/dashboard.',
  },
  {
    name: 'skip is non blocking and records passive mode',
    setup: 'Mock an authenticated user on /onboarding.',
    action: 'Click "先随便逛逛 / 跳过".',
    expected: 'Calls session start(passive), answer(passive skip event), finish, stores skipped state, then enters the original page.',
  },
  {
    name: 'guided flow advances through session steps',
    setup: 'Mock session/start returning an identity_quiz or diagnostic_quiz step.',
    action: 'Select required answers and click 下一步.',
    expected: 'Calls /api/profile/session/answer with the current step_id and answer payload.',
  },
  {
    name: 'finish refreshes profile and renders result cards',
    setup: 'Mock answer returning finished=true and finish succeeding.',
    action: 'Complete the last step.',
    expected: 'Calls finish, then GET /api/profile and /api/profile/narrative; radar, dossier text, and resource cards are visible.',
  },
  {
    name: 'low confidence profile shows a gentle profile hint',
    setup: 'Mock /profile with average confidence below 0.5.',
    action: 'Open the disciple profile page.',
    expected: 'Shows "当前多为待取证维度，去构建更准" without blocking the page.',
  },
]
