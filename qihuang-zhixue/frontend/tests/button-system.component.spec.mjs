import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const root = process.cwd()
const read = (path) => readFileSync(join(root, path), 'utf8')
const test = (name, fn) => {
  try {
    fn()
    console.log(`ok - ${name}`)
  } catch (error) {
    console.error(`not ok - ${name}`)
    throw error
  }
}

const tokens = read('src/styles/tokens.scss')
const onboarding = read('src/views/OnboardingView.vue')
const dialogue = read('src/components/profile/ProfileDialogue.vue')
const story = read('src/views/ApprenticeStoryView.vue')

test('shared button vocabulary uses token-backed primary, secondary, option, and icon controls', () => {
  for (const className of ['.qh-button--primary', '.qh-button--secondary', '.qh-option-button', '.qh-icon-button']) {
    assert(tokens.includes(className), `${className} should be defined in the token style layer`)
  }
  assert(tokens.includes('--qh-control-height: 44px'), 'icon controls should retain a stable 44px hit area')
  assert(onboarding.includes('进入收徒剧情') && onboarding.includes('开始重塑'), 'onboarding should use explicit primary action labels')
  assert(onboarding.includes('qh-option-group'), 'onboarding choice radios should reuse option controls')
})

test('story icon buttons expose accurate labels and a tooltip without replacing text actions', () => {
  assert(story.includes('aria-label="退出收徒剧情"'), 'exit action should have an accessible name')
  assert(story.includes("muted ? '开启音效' : '静音'"), 'sound action should announce its stateful purpose')
  assert(story.includes('<el-tooltip'), 'icon-only story controls should provide tooltips')
  assert(story.includes('<Close />') && story.includes('<Microphone'), 'story controls should use the established icon library')
  assert(story.includes("scene.canSkip ? '继续' : '进入问答'"), 'the primary narrative action should remain text-labelled')
})

test('loading and disabled dialogue controls guard against repeat submission', () => {
  assert(dialogue.includes(':loading="pending"'), 'send action should show loading state during a stream')
  assert(dialogue.includes(':disabled="disabled || pending || !draft.trim()"'), 'send action should lock while pending')
  assert(dialogue.includes(':disabled="disabled || pending"'), 'quick replies should lock while pending')
  assert(dialogue.includes('if (props.disabled || props.pending) return'), 'both emission paths should reject a repeated pending submit')
  assert(story.includes(':aria-busy="submitting"') && story.includes(':disabled="!canSubmit"'), 'story submit should retain a stable busy/disabled state')
})

test('unrevealed destiny cards cannot mount an evidence action or reveal card data', () => {
  assert(dialogue.includes('<el-popover v-if="activeIdentityDimension.revealed"'), 'evidence popover must not mount before card reveal')
  assert(dialogue.includes('<div v-if="activeIdentityDimension.revealed" class="tarot-face tarot-front">'), 'front data must stay out of the DOM before reveal')
  assert(dialogue.includes('v-if="!activeIdentityDimension.revealed || isActiveDimensionFlipping"'), 'the neutral card back must be rendered separately')
  const collectionBlock = dialogue.slice(dialogue.indexOf('const collectionGroups'), dialogue.indexOf('const hasDimensionCards'))
  assert(collectionBlock.includes('.filter((dimension) => dimension.revealed)'), 'unrevealed dimensions must stay out of the collection book')
  assert(!collectionBlock.includes('evidenceOnlyGroups'), 'evidence panel data must not bypass reveal privacy')
})

test('reduced-motion button branch remains present', () => {
  assert(tokens.includes('@media (prefers-reduced-motion: reduce)'), 'shared controls need a reduced-motion branch')
  assert(tokens.includes('.qh-icon-button'), 'shared reduced-motion branch should cover icon controls')
  assert(story.includes('.story-controls .qh-icon-button { transition: none; }'), 'story control motion should be removed for reduced motion')
})
