import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
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

const COMPONENTS = [
  'StoryStageFrame',
  'StoryNamePlate',
  'StoryDialogueBox',
  'StoryChoicePill',
  'StoryChoiceStack',
  'StoryDiscipleBubble',
  'StorySpecimenCard',
  'StoryChapterBar',
  'StoryPaperForm',
  'StoryToast',
]

const storyStyles = read('src/styles/story.scss')
const tokens = read('src/styles/tokens.scss')
const indexHtml = read('index.html')

test('every story component exists and declares a script setup block', () => {
  for (const name of COMPONENTS) {
    const path = join(root, 'src', 'components', 'story', `${name}.vue`)
    assert(existsSync(path), `${name}.vue should exist`)
    assert(read(`src/components/story/${name}.vue`).includes('<script setup'), `${name} should use script setup`)
  }
})

test('the six-step type scale is the only font size vocabulary', () => {
  for (const token of ['--qh-text-display: 32px', '--qh-text-title: 24px', '--qh-text-lead: 17px', '--qh-text-ui: 15px', '--qh-text-option: 14px', '--qh-text-footnote: 12px']) {
    assert(tokens.includes(token), `${token} should be declared in tokens.scss`)
  }
})

test('the story palette stays inside its own file', () => {
  const colors = [
    '--story-stage:',
    '--story-stage-rail:',
    '--story-stage-veil:',
    '--story-cream:',
    '--story-cream-soft:',
    '--story-ink:',
    '--story-ink-soft:',
    '--story-ink-faint:',
    '--story-gold:',
    '--story-gold-deep:',
    '--story-cinnabar:',
    '--story-jade:',
    '--story-pill:',
    '--story-pill-text:',
  ]
  for (const token of colors) assert(storyStyles.includes(token), `${token} should be declared in story.scss`)
})

test('story components only reference tokens, never raw colors', () => {
  const raw = /#[0-9a-fA-F]{3,8}\b|rgba?\(/
  for (const name of COMPONENTS) {
    const source = read(`src/components/story/${name}.vue`)
    const style = source.slice(source.indexOf('<style'))
    assert(!raw.test(style), `${name} should not hardcode a color`)
  }
})

test('the story module forbids the retired cheap-material shortcuts', () => {
  for (const name of COMPONENTS) {
    const source = read(`src/components/story/${name}.vue`)
    assert(!source.includes('filter: drop-shadow'), `${name} should not stack drop shadows`)
    assert(!source.includes('clip-path: polygon'), `${name} should not fake torn paper with polygons`)
  }
})

test('screen-reader-only copy is never painted as a second paragraph', () => {
  // Regression: `.story-sr` was used in two components but never defined, so the
  // settled dialogue line rendered twice, once at full opacity under the
  // typewriter line.
  assert(storyStyles.includes('.story-sr'), 'story.scss must define the .story-sr utility')
  const rule = storyStyles.slice(storyStyles.indexOf('.story-sr'))
  assert(rule.includes('position: absolute'), '.story-sr must leave the text flow')
  assert(rule.includes('clip-path: inset(50%)'), '.story-sr must be clipped to nothing')
  for (const name of COMPONENTS) {
    const source = read(`src/components/story/${name}.vue`)
    if (source.includes('class="story-sr"')) {
      assert(storyStyles.includes('.story-sr'), `${name} relies on .story-sr being defined`)
    }
  }
})

test('the floating assistant mascot is gone from the app shell', () => {
  const app = read('src/App.vue')
  assert(!app.includes('MascotBubble'), 'the bottom-right mascot was retired with the 2026-10 visual refresh')
  assert(!existsSync(new URL('../src/components/ui/MascotBubble.vue', import.meta.url)), 'and its component file is deleted, not just unmounted')
})

test('no story surface can print the herb name it is asking about', () => {
  const card = read('src/components/story/StorySpecimenCard.vue')
  assert(!card.includes('name: string'), 'the specimen card has no name prop to leak')
  assert(!card.includes('hint:'), 'nor a nature/flavour hint')
  assert(!read('src/views/StoryKitView.vue').includes('太子参'), 'the gallery demo must not name a herb')
  assert(!read('src/views/StoryPreviewView.vue').includes('${question.herb_name}的代表功效'), 'the preview question must not interpolate the herb name')
})

test('choice pills in one stack share a single width', () => {
  // The options column is sized by its longest label so the menu reads as a
  // block; without the wrapper every pill hugs its own text.
  const stack = read('src/components/story/StoryChoiceStack.vue')
  const pill = read('src/components/story/StoryChoicePill.vue')
  assert(stack.includes('story-choice-stack__options'), 'pills live in a shared-width group')
  assert(stack.includes('width: max-content'), 'the group is measured from the pills only')
  assert(!pill.includes('align-self: flex-end'), 'the pill must not opt out of the group width')
})

test('self-hosted font subsets are shipped and preloaded', () => {
  assert(existsSync(join(root, 'public', 'fonts', 'noto-serif-sc-subset.woff2')), 'serif subset should be committed')
  assert(existsSync(join(root, 'public', 'fonts', 'noto-sans-sc-subset.woff2')), 'sans subset should be committed')
  assert(storyStyles.includes('@font-face'), 'story.scss declares the faces')
  assert(storyStyles.includes('font-display: swap'), 'text should render before the font arrives')
  assert(indexHtml.includes('rel="preload"') && indexHtml.includes('/fonts/noto-serif-sc-subset.woff2'), 'the serif face should preload')
})

test('the dev-only review surfaces are gated behind import.meta.env.DEV', () => {
  const router = read('src/router/index.ts')
  assert(router.includes("import.meta.env.DEV"), 'review routes must not ship to production')
  assert(router.includes("path: '/story-kit'"), 'the component gallery route exists in dev')
  assert(router.includes("path: '/story-preview'"), 'the full-screen preview route exists in dev')
})
