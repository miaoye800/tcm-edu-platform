// End-to-end smoke test for the apprentice story, against a real backend.
//
// Signs in as a guest, plays the whole story: prologue -> identity card -> five
// herb questions -> prescription -> keepsake -> pact -> certificate. Screenshots
// land in deliverables/flow/.
//
//   node scripts/smoke-story.mjs        # backend must be up on :8000
//
// It asserts the things that are easy to regress silently: the prologue never
// shows a line that is not in the cue table, every chapter speaks its real line on
// the second the film speaks it, no question appears before its film has finished,
// and both two-field screens refuse to submit until they are filled.
import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { chromium } from 'playwright'
const PORT = 5213
const BASE = `http://127.0.0.1:${PORT}`
const OUT = 'deliverables/flow'
mkdirSync(OUT, { recursive: true })
const server = spawn(process.platform === 'win32' ? 'npx.cmd' : 'npx',
  ['vite','--host','127.0.0.1','--port',String(PORT),'--strictPort','--configLoader','runner'],
  { cwd: process.cwd(), stdio: 'ignore', shell: process.platform === 'win32' })
const wait = async () => { for (let i=0;i<200;i++){ try { const r = await fetch(BASE); if (r.ok) return true } catch {} await new Promise(r=>setTimeout(r,400)) } return false }
if (!await wait()) { server.kill(); process.exit(1) }
let browser
for (const channel of ['chrome','msedge',undefined]) {
  try { browser = await chromium.launch(channel?{channel}:{channel:'chrome'}); break } catch {}
}
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const bad = []
page.on('response', (r) => { if (r.status() >= 400) bad.push(`${r.status()} ${r.url()}`) })
await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(1200)
await page.locator('text=游客一键体验').first().click()
await page.waitForTimeout(2500)
await page.goto(BASE + '/apprentice', { waitUntil: 'domcontentloaded' })
const pill = (t) => page.locator('.story-choice-pill', { hasText: t }).first()
const shot = (n) => page.screenshot({ path: `${OUT}/${n}.png` })
const failures = []
const expect = (ok, message) => { if (!ok) failures.push(message) }

// 追问是一题一屏：主问题答完，stage 换成同一套排版的下一个题目（右侧选项 + 底部题干），
// 全部答完才落卷。这里按屏幕状态自动答完，直到追问屏消失。
const answerExtras = async (where, limit = 8) => {
  for (let i = 0; i < limit; i++) {
    if ((await page.locator('.scene-extra').count()) === 0) return true
    await page.waitForTimeout(500)
    const field = page.locator('.scene-extra .story-disciple-bubble__field')
    const options = page.locator('.scene-extra .story-choice-stack__options .story-choice-pill')
    if (await field.count()) {
      await field.first().fill(`${where}：按我的实际情况说，平时晚上能练四十分钟。`)
      await page.keyboard.press('Enter')
    } else {
      await options.first().click()
    }
    await page.waitForTimeout(900)
  }
  return (await page.locator('.scene-extra').count()) === 0
}

// Sample the dialogue box until the answer pills appear. Returns every distinct
// line the box rendered, the highest playback second it saw (proof the film was
// allowed to finish), and the second the stage came to rest on once it was over.
//
// `film` is the chapter the sampling belongs to. The answer components only mount
// once their film has ended, so waiting for `.scene-quiz` would start sampling
// after the last line had already gone by; the video's own source is the only
// marker that exists for the whole chapter.
const sampleUntilChoices = async (limitMs, film) => {
  if (film) {
    const enteredAt = Date.now()
    while (Date.now() - enteredAt < limitMs) {
      const src = await page.evaluate(() => document.querySelector('video')?.getAttribute('src') ?? '')
      if (src.endsWith(film)) break
      await page.waitForTimeout(100)
    }
    // One frame of grace so the previous chapter's last line is not sampled.
    await page.waitForTimeout(300)
  }
  const seen = new Set()
  const startedAt = Date.now()
  let clock = 0
  let peak = 0
  while (Date.now() - startedAt < limitMs) {
    const snap = await page.evaluate(() => ({
      t: document.querySelector('video')?.currentTime ?? 0,
      line: document.querySelector('.story-dialogue-box__text')?.textContent?.replace(/▍/g, '').trim() ?? '',
      pills: document.querySelectorAll('.story-choice-pill').length,
    }))
    if (snap.line) seen.add(snap.line)
    peak = Math.max(peak, snap.t)
    if (snap.pills > 0) {
      clock = snap.t
      break
    }
    await page.waitForTimeout(120)
  }
  return { seen, clock, peak }
}

// Every observed string must be a prefix of an allowed line (the box types them
// out one character at a time, so partial renders are expected and fine).
const expectLines = (seen, allowed, where) => {
  for (const line of seen) {
    const ok = allowed.some((expected) => expected.startsWith(line))
    expect(ok, `${where}出现了台词表之外的一句：「${line}」`)
  }
}

// A beat change must never make the box fall back to a line that is not in the
// cue table: the old bug showed "湿滑的青石阶…" for a quarter second at the 4s
// beat, then swapped to the real line, so two lines fought over one beat.
const PROLOGUE_LINES = [
  '鼻间先有陈皮与艾草的微苦。你在一间陌生的中药房醒来。',
  '眼前的景象渐渐远去——你开始想起，自己是如何在雨后的山阶上受伤的。',
  '孩子快醒醒',
  '快醒醒',
  '我观你眼中有向学之光',
  '可愿随我习这岐黄之术',
  '（四张身份牌摆在案上。挑一张最像你走过的路，也可以点「自己说说」。）',
]
const prologue = await sampleUntilChoices(45000)
console.log('identity ready at', prologue.clock, 'peak', prologue.peak)
// section-one has no fade, so the question must arrive on the true last frame.
expect(prologue.peak > 27.5, `序章只播到 ${prologue.peak}s，影片没有播完`)
expect(Math.abs(prologue.clock - 27.77) < 0.3, `序章停在 ${prologue.clock}s，没有停在末帧`)
expectLines(prologue.seen, PROLOGUE_LINES, '序章')
await shot('01-identity')
await pill('临床学徒').click()
// 请笔记完，屏幕上换的是同一套排版的下一个题目，不是另一套界面。
await page.waitForTimeout(900)
expect((await page.locator('.scene-identity').count()) === 0, '答完身份牌后不该再留着上一题的选项')
expect((await page.locator('.scene-extra').count()) === 1, '答完主问题应接着问下一题（追问屏）')
await shot('01b-extra-question')
expect(await answerExtras('身份'), '身份幕的追问答不完，屏幕上一直停着追问')
// 识药：section-two 第 6 秒他才开口问药，影片必须整段播完才出第一题。
const herb = await sampleUntilChoices(60000, 'section-two-1.mp4')
console.log('quiz ready at', herb.clock, 'peak', herb.peak)
expect(herb.peak > 14.4, `识药屏只播到 ${herb.peak}s，影片没有播完`)
expect(Math.abs(herb.clock - 14.6) < 0.3, `识药屏停在 ${herb.clock}s，没有停在末帧`)
expect(herb.seen.has('小友，先识药，再辨性，这几味药你可认得'), '识药屏没有出现「先识药，再辨性」这句台词')
expectLines(herb.seen, ['（老中医把几味药摊在案上，等你凑近些。）', '小友，先识药，再辨性，这几味药你可认得', '你且细看这味药，它最见长的功效是哪一样？'], '识药屏')
for (let q = 1; q <= 5; q++) {
  await page.waitForSelector('.story-choice-pill', { timeout: 60000 })
  await page.waitForTimeout(400)
  if (q === 1) await shot('02-quiz')
  await page.locator('.story-choice-stack__options .story-choice-pill').first().click()
  await page.waitForTimeout(1200)
}
// 五道药认完，识药这一幕还有一道追问（"换到病例里还认不认得出"），
// 同样是一屏一题，答完才落卷。
expect(await answerExtras('识药'), '识药幕的追问没答完，卡在同一屏')
// 抓药：section-three 从 0 秒就在报病例，播完才给「记下病例」。
const counter = await sampleUntilChoices(90000, 'section-three-1.mp4')
const holdStart = counter.clock
console.log('prescription ready at', holdStart, 'peak', counter.peak)
// The film plays to its end (14.9s), then comes to rest on the last readable
// frame before the fade - the answer layer must not sit on a black stage.
expect(counter.peak > 14.7, `抓药屏只播到 ${counter.peak}s，影片没有播完`)
expect(Math.abs(holdStart - 13.8) < 0.35, `抓药屏落在 ${holdStart}s，不是可读帧 13.8s`)
expect(counter.seen.has('照方抓药，剂量与配伍，皆马虎不得'), '抓药屏没有出现「照方抓药…皆马虎不得」这句台词')
expectLines(counter.seen, [
  '病人印堂发黑，有点畏寒',
  '头部发晕，数盲',
  '照方抓药，剂量与配伍，皆马虎不得',
  '（先点「记下病例」，再点「落笔写方」；两样都写好了，「落笔呈上」才会亮。）',
], '抓药屏')
await page.waitForSelector('.scene-prescription', { timeout: 30000 })
// Let the veil finish dissolving before the arrival shot.
await page.waitForTimeout(700)
await shot('03-prescription')
await page.waitForTimeout(1200)
const holdLater = await page.evaluate(() => document.querySelector('video')?.currentTime ?? 0)
expect(Math.abs(holdLater - holdStart) < 0.6, `影片停在 ${holdStart}s，1.2 秒后变成 ${holdLater}s，还在动`)
await shot('03b-prescription-settled')
await pill('记下病例').click()
await page.waitForTimeout(300)
await page.locator('.story-disciple-bubble__field').fill('恶寒发热，无汗，头身疼痛，脉浮紧。')
await shot('04-case-open')
await page.keyboard.press('Enter')
await page.waitForTimeout(400)
await pill('落笔写方').click()
await page.waitForTimeout(300)
await page.locator('.story-disciple-bubble__field').fill('麻黄9g，桂枝6g，杏仁9g，炙甘草6g。')
await page.keyboard.press('Enter')
await page.waitForTimeout(600)
await shot('05-both-filled')
await pill('落笔呈上').click()
await page.waitForTimeout(2500)
await shot('06-after-submit')
expect(bad.length === 0, `请求失败：${bad.slice(0, 4).join(', ')}`)
expect((await page.locator('.scene-prescription').count()) === 0, '呈上之后没有离开抓药屏')

// 4) 药宝：同样要等影片播完才出选项，第 10 秒那句才是正题。
const keepsake = await sampleUntilChoices(60000, 'section-four-1.mp4')
console.log('keepsake ready at', keepsake.clock, 'peak', keepsake.peak)
expect(keepsake.peak > 15.1, `药宝屏只播到 ${keepsake.peak}s，影片没有播完`)
expect(Math.abs(keepsake.clock - 13.9) < 0.35, `药宝屏落在 ${keepsake.clock}s，不是可读帧 13.9s`)
expect(keepsake.seen.has('挑一件趁手的法门带走，日后自有用处'), '药宝屏没有出现「挑一件趁手的法门带走」这句台词')
expectLines(keepsake.seen, ['（架上的药宝不多，各是一路走法。）', '挑一件趁手的法门带走，日后自有用处', '（架上药宝不多。挑一件你愿意常常翻看的带走。）'], '药宝屏')
await page.waitForSelector('.scene-resource .story-choice-pill', { timeout: 30000 })
await page.waitForTimeout(700)
await shot('07-keepsake')
await page.locator('.scene-resource .story-choice-stack__options .story-choice-pill').first().click()
expect(await answerExtras('药宝'), '药宝幕的四道追问没答完，卡在同一屏')

// 5) 学规：两个数字，点开才展开
const rule = await sampleUntilChoices(90000, 'section-five-1.mp4')
const ruleHold = rule.clock
console.log('rule ready at', ruleHold, 'peak', rule.peak)
expect(rule.peak > 9.4, `学规屏只播到 ${rule.peak}s，影片没有播完`)
expect(Math.abs(ruleHold - 8.9) < 0.35, `学规屏落在 ${ruleHold}s，不是可读帧 8.9s`)
expect(rule.seen.has('你且定下每日功课与目标'), '学规屏没有出现「你且定下每日功课与目标」这句台词')
expectLines(rule.seen, ['（灯下摊着一张空白的功课纸。）', '学医贵在有恒', '你且定下每日功课与目标', '（定下每天练多久、先走多少天，两样都填好就能立约。）'], '学规屏')
await page.waitForSelector('.scene-rule', { timeout: 30000 })
await page.waitForTimeout(600)
await shot('08-rule')
const submitPill = page.locator('.scene-rule .story-choice-pill', { hasText: '立下此约' }).first()
expect(await submitPill.isDisabled(), '两样都没填时「立下此约」就该是灰的')
await page.locator('.scene-rule .story-choice-pill', { hasText: '定每日时长' }).first().click()
await page.waitForTimeout(300)
await page.locator('.story-disciple-bubble__field').fill('45')
await shot('09-rule-minutes')
await page.keyboard.press('Enter')
await page.waitForTimeout(400)
await page.locator('.scene-rule .story-choice-pill', { hasText: '定目标周期' }).first().click()
await page.waitForTimeout(300)
await page.locator('.story-disciple-bubble__field').fill('21')
await page.keyboard.press('Enter')
await page.waitForTimeout(500)
await shot('10-rule-filled')
const ruleLabels = await page.evaluate(() => Array.from(document.querySelectorAll('.scene-rule .story-choice-pill')).map((el) => el.textContent.trim()))
console.log('rule pills', JSON.stringify(ruleLabels))
expect(ruleLabels.some((label) => label.includes('45')), '每日时长没写进药丸')
expect(ruleLabels.some((label) => label.includes('21')), '目标周期没写进药丸')
expect(!(await submitPill.isDisabled()), '两样都填好了「立下此约」还是灰的')
await submitPill.click()
await page.waitForTimeout(1200)
// 学规这一幕有四道追问（时间窗 / 断档恢复 / 冲突取舍 / 错题纠正），
// 立约只是主问题，追问答完才真正落卷并进入礼成。
expect(await answerExtras('学规', 10), '学规幕的追问没答完，卡在同一屏')
await page.waitForTimeout(1500)
await shot('11-after-rule')
expect((await page.locator('.scene-rule').count()) === 0, '立约之后没有离开学规屏')
// 收徒礼成：结业卷卡片已经整个删掉，这一拍只剩影片 + 老中医最后一句。
expect((await page.locator('.story-cert').count()) === 0, '收徒礼成那屏还挂着结业卷卡片')
const readBox = () => page.evaluate(() => document.querySelector('.story-dialogue-box__text')?.textContent?.replace(/▍/g, '').trim() ?? '')
expect((await page.locator('.story-dialogue-box').count()) > 0, '收徒礼成那屏没有对话框')
let closing = await readBox()
for (let i = 0; i < 45 && !closing.includes('自会不断生长'); i++) {
  await page.waitForTimeout(300)
  closing = await readBox()
}
console.log('closing line', JSON.stringify(closing))
expect(closing.includes('自会不断生长'), `收徒礼成气泡停在「${closing}」，不是影片最后一句`)
await shot('12-ending')
await browser.close(); server.kill()

if (failures.length) {
  console.error(`\n${failures.length} 项未通过:`)
  for (const line of failures) console.error(` - ${line}`)
  process.exitCode = 1
} else {
  console.log('端到端通过：每段影片都播完才出题、台词按字幕秒数出现、抓药与学规两字段可呈上')
}
