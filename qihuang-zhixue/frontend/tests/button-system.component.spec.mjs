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
const chapterBar = read('src/components/story/StoryChapterBar.vue')
const storyView = read('src/views/ApprenticeStoryView.vue')
const prescription = read('src/views/apprentice/PrescriptionScene.vue')
const rule = read('src/views/apprentice/RuleScene.vue')

test('shared button vocabulary uses token-backed primary, secondary, option, and icon controls', () => {
  for (const className of ['.qh-button--primary', '.qh-button--secondary', '.qh-option-button', '.qh-icon-button']) {
    assert(tokens.includes(className), `${className} should be defined in the token style layer`)
  }
  assert(tokens.includes('--qh-control-height: 44px'), 'icon controls should retain a stable 44px hit area')
  assert(onboarding.includes('进入收徒剧情') && onboarding.includes('开始重塑'), 'onboarding should use explicit primary action labels')
  assert(onboarding.includes('qh-option-group'), 'onboarding choice radios should reuse option controls')
})

test('story uses a text-labelled exit control and has no sound or click-to-continue actions', () => {
  assert(chapterBar.includes('退出剧情'), 'exit action should be visibly labelled')
  assert(chapterBar.includes("@click=\"emit('exit')\""), 'the exit control emits instead of navigating itself')
  assert(storyView.includes('@exit="leaveStory"'), 'the container owns the navigation')
  assert(!storyView.includes('开启音效') && !storyView.includes('静音'), 'sound is enabled by default without a toggle')
  assert(!storyView.includes('点击对白继续'), 'dialogue advances with the video, not on click')
  assert(storyView.includes(':muted="false"'), 'story playback defaults to sound on')
})

test('loading and disabled dialogue controls guard against repeat submission', () => {
  assert(dialogue.includes(':loading="pending"'), 'send action should show loading state during a stream')
  assert(dialogue.includes(':disabled="disabled || pending || !draft.trim()"'), 'send action should lock while pending')
  assert(dialogue.includes(':disabled="disabled || pending"'), 'quick replies should lock while pending')
  assert(dialogue.includes('if (props.disabled || props.pending) return'), 'both emission paths should reject a repeated pending submit')
  assert(prescription.includes(':aria-busy="submitting"') && prescription.includes('disabled: !props.canSubmit'), 'the prescription submit pill keeps a stable busy/disabled state')
  assert(rule.includes(':aria-busy="submitting"') && rule.includes('disabled: !props.canSubmit'), 'the study-rule submit pill keeps a stable busy/disabled state')
})

test('unrevealed destiny cards cannot mount an evidence action or reveal card data', () => {
  assert(dialogue.includes('<el-popover v-if="activeIdentityDimension.revealed"'), 'evidence popover must not mount before card reveal')
  assert(dialogue.includes('<div v-if="activeIdentityDimension.revealed" class="tarot-face tarot-front">'), 'front data must stay out of the DOM before reveal')
  assert(dialogue.includes('v-if="!activeIdentityDimension.revealed || isActiveDimensionFlipping"'), 'the neutral card back must be rendered separately')
  const collectionBlock = dialogue.slice(dialogue.indexOf('const collectionGroups'), dialogue.indexOf('const hasDimensionCards'))
  assert(collectionBlock.includes('.filter((dimension) => dimension.revealed)'), 'unrevealed dimensions must stay out of the collection book')
  assert(!collectionBlock.includes('evidenceOnlyGroups'), 'evidence panel data must not bypass reveal privacy')
})

test('reduced-motion handling covers the shared controls and the story scope', () => {
  assert(tokens.includes('@media (prefers-reduced-motion: reduce)'), 'shared controls need a reduced-motion branch')
  assert(tokens.includes('.qh-icon-button'), 'shared reduced-motion branch should cover icon controls')
  assert(/@media \(prefers-reduced-motion: reduce\)[\s\S]*\.story-scope/.test(read('src/styles/story.scss')), 'story motion should collapse for reduced motion')
})
