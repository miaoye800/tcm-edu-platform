import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const reducer = read('../src/components/profile/profileDialogueStreamState.ts')
const dialogue = read('../src/components/profile/ProfileDialogue.vue')
const profile = read('../src/views/ProfileView.vue')

// The reducer consumes only the backend's one-shot reveal flag. It never
// manufactures a reveal from claim status, confidence, or a refreshed state.
assert(reducer.includes('playedRevealKeys'), 'identity card animation must be deduplicated per session and dimension')
assert(reducer.includes('dimension.revealed_now'), 'only the backend reveal event may start a flip')
assert(dialogue.includes("'is-veiled'"), 'pending cards should render a veiled state')
assert(dialogue.includes('尚待实证'), 'veiled cards should explain why they are obscured')
assert(dialogue.includes('@keyframes unveil-card'), 'verification should reveal the card with a CSS animation')

assert(profile.includes('画像变迁录'), 'profile page should render the profile change log')
assert(profile.includes('previousChanges') && profile.includes('nextChanges'), 'change log should support paging')
assert(profile.includes('师父信任度') && profile.includes('readProfileTrust'), 'trust component should use the trust endpoint')

assert(profile.includes('noKeyFallbackDimensions.value.length'), 'no-key narrative source should use the existing provenance-derived dimension list')
assert(!profile.includes('noKeyRuleDimensions.value.length'), 'profile narrative source must not reference an undefined computed value')

assert(dialogue.includes('proposeProfileGoal') && dialogue.includes('confirmProfileGoal'), 'dialogue convergence should support goal confirmation')
assert(dialogue.includes('重新说') && dialogue.includes('确认'), 'goal confirmation needs both confirm and restate paths')

// 审计面板只是开发期观察窗，已随需求下线；页面里不能再有它的挂载点。
assert(!existsSync(new URL('../src/components/profile/ProfileDeveloperAudit.vue', import.meta.url)), 'developer audit component must be removed')
assert(!dialogue.includes('ProfileDeveloperAudit'), 'dialogue must not mount the removed audit panel')

console.log('ok - profile enhancement component contracts')
