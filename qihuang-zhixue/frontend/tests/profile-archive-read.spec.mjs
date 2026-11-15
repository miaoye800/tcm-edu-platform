import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'

const root = new URL('../src/', import.meta.url)
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

const api = read('../src/services/profileApi.ts')
const store = read('../src/stores/profile.ts')
const storage = read('../src/services/onboardingStorage.ts')
const onboarding = read('../src/views/OnboardingView.vue')
const guard = read('../src/router/index.ts')

// 1) 画像档案是整页主数据，不能给几百毫秒的预算：一次冷启动就够超时。
const timeout = /const PROFILE_TIMEOUT_MS = ([\d_]+)/.exec(api)
assert(timeout, 'profile api must declare an explicit read timeout')
const timeoutMs = Number(timeout[1].replaceAll('_', ''))
assert(timeoutMs >= 5_000, `profile read timeout is too tight: ${timeoutMs}ms`)
assert(!api.includes('PROFILE_TIMEOUT_MS = 800'), 'the 800ms profile read budget must not come back')

// 2) 读失败绝不能被说成"已切换到本地兜底数据"。用户会以为证据丢了。
const walk = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const target = new URL(name, dir)
    return statSync(target).isDirectory() ? walk(new URL(`${name}/`, dir)) : [target]
  })
const offenders = walk(root)
  .filter((file) => /\.(ts|vue)$/.test(file.pathname))
  .filter((file) => readFileSync(file, 'utf8').includes('已切换为本地兜底数据'))
assert.equal(offenders.length, 0, `no surface may claim it fell back to local data: ${offenders.map((f) => f.pathname)}`)

// 3) store 的失败分支不许把真实画像换成空白占位。
assert(store.includes('loadFailed'), 'profile store must expose a dedicated load-failure flag')
const mockAssignments = store.match(/this\.usingMock = true/g) ?? []
assert.equal(mockAssignments.length, 1, 'usingMock may only be set by the explicit e2e flag')
const catchBlock = store.slice(store.indexOf('} catch (error) {'), store.indexOf('} finally {'))
assert(catchBlock.length > 0, 'loadProfile must keep an explicit catch block')
assert(!catchBlock.includes('this.resetNarrativeState(this.activeUserId)\n        this.usingMock'), 'a failed read must not reset the real profile into placeholder data')
assert(store.includes('loadFailed = true'), 'a failed read must be recorded, not disguised')

// 4) 路由守卫不能把"没读到"当成"还没画画像"，否则一次网络抖动就把人抓进入口。
assert(guard.includes('profile.loadFailed'), 'route guard must skip the onboarding invite when the profile read failed')

// 5) 进行中的会话要熬过浏览器重启；历史会话由后端列出。
assert(
  storage.includes('window.localStorage.getItem(activeSessionKeyFor'),
  'the active profile session must survive a browser restart',
)
assert(!storage.includes('window.sessionStorage.getItem(activeSessionKeyFor'), 'the active profile session must not live in sessionStorage')
assert(api.includes('listProfileSessions') && api.includes('/api/profile/sessions'), 'session history must come from the backend')
assert(onboarding.includes('listProfileSessions'), 'onboarding entry must list past sessions')
assert(onboarding.includes('openHistorySession') && onboarding.includes('historyItems'), 'a listed session must be reopenable')
assert(onboarding.includes('回到入口'), 'a reviewed session needs a way back to the entry')

// 6) 对话页自己要能看到历史，不必先退回入口；空会话（开过一局但一句没说）
//    不能拿来"恢复"，否则用户点重塑画像就被丢进一片空白。
assert(onboarding.includes('dialogue-history'), 'the dialogue page must list past sessions inline')
assert(
  !onboarding.includes('class="history-card"') && !onboarding.includes('class="history-list"'),
  'the entry page must not repeat the history list that the dialogue page already owns',
)
assert(onboarding.includes("item.message_count > 0"), 'history must not list sessions with no messages at all')
assert(
  onboarding.includes("(restored.chat_history ?? []).length"),
  'restoring a session with no messages must fall back to the entry instead of an empty chat',
)
assert(onboarding.includes('void loadHistory()'), 'the dialogue page loads history even when it resumes a session')

// 7) 回看历史时要直接把已翻开的牌摆上台面，而不是只留一个"藏牌匣 · 1"。
const board = read('../src/components/profile/DestinyCardBoard.vue')
assert(board.includes('lastRevealedSignature'), 'the board must reopen revealed cards when the session changes')

// 8) 档案分数只有一个入口：分类维度只有 radar_score，前端两处都要认。
const math = read('../src/utils/profileMath.ts')
assert(math.includes('dimensionScore'), 'the archive must resolve a dimension score from score ?? radar_score')
assert(math.includes('dimension?.score ?? dimension?.radar_score'), 'category dimensions must fall back to radar_score')
assert(!math.includes("return '派生值'"), 'the archive must not label a real score as a "derived value"')

console.log('ok - profile archive read path and session history contracts')
