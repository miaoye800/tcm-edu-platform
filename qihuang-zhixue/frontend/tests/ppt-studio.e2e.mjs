/**
 * 幻灯工坊端到端验收。
 *
 * 跑之前需要后端在 127.0.0.1:8000（且**没有**模型 key，这样走确定性模板，
 * 结果可复现）。脚本自己拉起 Vite，用游客身份进 /ppt，把六件事走一遍：
 *
 *   1. 一键出稿：空态 → 起稿 → 一页页翻开到 12 页
 *   2. 先定大纲：谋篇后停流 → 改标题 → 确认 → 续跑
 *   3. 编辑自动保存：改一条要点 → 印章落「已钤」→ 刷新后仍在
 *   4. 导出 .pptx：真的下载，且压缩包里的页数与舞台一致
 *   5. 皮肤：墨夜 / 青瓷 各截一张，确认纸面颜色真的换了
 *   6. 刷新恢复：?resource=<id> 直接回到那一稿
 */

import assert from 'node:assert/strict'
import { mkdirSync, readFileSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import JSZip from 'jszip'

const root = fileURLToPath(new URL('..', import.meta.url))
const viteBin = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))
const baseUrl = 'http://127.0.0.1:5188'
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const shots = process.env.PPT_SHOTS || 'D:\\Agent\\tcm-edu-platform\\.tmpshots'
mkdirSync(shots, { recursive: true })

const server = spawn(process.execPath, [viteBin, '--host', '127.0.0.1', '--port', '5188', '--configLoader', 'runner'], {
  cwd: root,
  stdio: 'ignore',
  windowsHide: true,
})

const waitFor = async (predicate, timeout = 30000) => {
  const started = Date.now()
  while (Date.now() - started < timeout) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error('等待超时')
}

let browser
try {
  await waitFor(async () => {
    try {
      return (await fetch(baseUrl)).ok
    } catch {
      return false
    }
  })

  browser = await chromium.launch({ headless: true, executablePath: chromePath })
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1, acceptDownloads: true })

  // 游客身份 + 跳过画像引导：这两步是路由守卫的前置条件，必须先落到 localStorage。
  const session = await context.request.post(`${baseUrl}/api/auth/guest`, { data: {} })
  assert.ok(session.ok(), '游客登录接口可用')
  const auth = await session.json()
  await context.addInitScript((payload) => {
    window.localStorage.setItem('qihuang-auth-v1', JSON.stringify(payload))
    window.localStorage.setItem(
      `qihuang-onboarding:${payload.user.user_id}`,
      JSON.stringify({ status: 'skipped', mode: 'quickcard', updatedAt: new Date().toISOString() }),
    )
  }, auth)

  const errors = []
  const page = await context.newPage()
  page.on('pageerror', (error) => errors.push(error.message))

  /* —— 1. 空态与一键出稿 —— */
  await page.goto(`${baseUrl}/ppt`, { waitUntil: 'domcontentloaded' })
  await page.locator('.ghost').waitFor({ state: 'visible', timeout: 15000 })
  assert.match(await page.locator('.ghost').innerText(), /白纸/, '空态说清下一步')
  assert.equal(await page.locator('.strip .thumb').count(), 0, '空态没有胶片条')
  await page.screenshot({ path: `${shots}/01-empty.png` })

  await page.locator('#ppt-free-topic').fill('麻黄')
  await page.locator('.ink-button').click()
  await page.waitForFunction(() => document.querySelectorAll('.strip .thumb').length >= 12, null, { timeout: 60000 })
  assert.equal(await page.locator('.strip .thumb').count(), 12, '12 页全部进胶片条')
  await page.waitForTimeout(600)
  const coverTitle = page.locator('.stage .cover-title input').first()
  assert.match(await coverTitle.inputValue(), /麻黄/, '封面写的是讲题')
  await page.screenshot({ path: `${shots}/02-deck-paper.png` })

  /* —— 3. 编辑 → 自动保存 → 刷新仍在 —— */
  await page.locator('.strip .thumb').nth(1).click()
  const bullet = page.locator('.stage input[aria-label="要点 1"]').first()
  await bullet.waitFor({ state: 'visible', timeout: 8000 })
  const marked = '发汗解表（已改）'
  await bullet.fill(marked)
  await page.waitForFunction(() => (document.querySelector('.seal-text')?.textContent || '').includes('已钤'), null, { timeout: 15000 })
  await page.screenshot({ path: `${shots}/03-edited-sealed.png` })

  /* —— 4. 导出 .pptx —— */
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 60000 }),
    page.getByRole('button', { name: 'PPTX' }).click(),
  ])
  const pptxPath = `${shots}\\deck.pptx`
  await download.saveAs(pptxPath)
  const zip = await JSZip.loadAsync(readFileSync(pptxPath))
  const slidesInFile = Object.keys(zip.files).filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
  const notesInFile = Object.keys(zip.files).filter((name) => /^ppt\/notesSlides\/notesSlide\d+\.xml$/.test(name))
  assert.equal(slidesInFile.length, 12, '导出的 pptx 页数与舞台一致')
  assert.ok(notesInFile.length >= 1, '讲者备注写进了 notesSlides')
  const firstSlide = await zip.file('ppt/slides/slide1.xml').async('string')
  assert.match(firstSlide, /麻黄/, '首屏文字真的落进 XML')

  /* —— 5. 皮肤 —— */
  await page.getByRole('button', { name: '墨夜' }).click()
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${shots}/04-theme-ink.png` })
  const inkBg = await page.locator('.stage').evaluate((node) => getComputedStyle(node).getPropertyValue('--ppt-bg').trim())
  assert.equal(inkBg, '#151719', '墨夜真的换了纸色')
  await page.getByRole('button', { name: '青瓷' }).click()
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${shots}/05-theme-celadon.png` })

  /* —— 6. 刷新恢复（走资源库入口：?resource=<id>） —— */
  const listed = await context.request.get(`${baseUrl}/api/resources?type=ppt&limit=5`, {
    headers: { Authorization: `Bearer ${auth.token}` },
  })
  assert.ok(listed.ok(), '资源库列表可读')
  const newest = (await listed.json()).items[0]
  assert.ok(newest?.id, '刚生成的讲稿已进资源库')
  await page.goto(`${baseUrl}/ppt?resource=${newest.id}`, { waitUntil: 'domcontentloaded' })
  await page.locator('.strip .thumb').first().waitFor({ state: 'visible', timeout: 20000 })
  await page.locator('.strip .thumb').nth(1).click()
  const reloaded = page.locator('.stage input[aria-label="要点 1"]').first()
  await reloaded.waitFor({ state: 'visible', timeout: 8000 })
  assert.equal(await reloaded.inputValue(), marked, '改过的要点刷新后还在（已落库）')
  await page.screenshot({ path: `${shots}/06-reloaded.png` })

  /* —— 7. 资源库：幻灯是独立一类，点开回到工坊 —— */
  await page.goto(`${baseUrl}/resources`, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '幻灯', exact: true }).click()
  await page.locator('.resource-card').first().waitFor({ state: 'visible', timeout: 15000 })
  assert.match(await page.locator('.resource-card').first().innerText(), /讲稿/, '资源库里认得出讲稿')
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${shots}/09-library.png` })
  await page.locator('.resource-card').first().click()
  await page.waitForURL(/\/ppt\?resource=/, { timeout: 20000 })
  await page.locator('.stage .surface').first().waitFor({ state: 'visible', timeout: 20000 })

  /* —— 2. 先定大纲（放在最后，避免影响上面那份稿） —— */
  await page.goto(`${baseUrl}/ppt`, { waitUntil: 'domcontentloaded' })
  await page.locator('#ppt-free-topic').fill('桂枝汤方解')
  await page.getByRole('button', { name: '先定大纲' }).click()
  await page.locator('.ink-button').click()
  await page.locator('.confirm').waitFor({ state: 'visible', timeout: 40000 })
  const cards = await page.locator('.confirm .card').count()
  assert.ok(cards >= 3, `大纲至少三页，实际 ${cards}`)
  await page.locator('.confirm .card-title').first().fill('桂枝汤 · 方义总纲')
  await page.screenshot({ path: `${shots}/07-outline.png` })
  await page.locator('.confirm .primary').click()
  // 大纲几页就等几页落进胶片条：截屏要的是铺完的成稿，不是生长到一半的样子。
  await page.waitForFunction((want) => document.querySelectorAll('.strip .thumb').length >= want, cards, { timeout: 60000 })
  await page.waitForTimeout(500)
  assert.equal(await page.locator('.confirm').count(), 0, '确认后大纲面板收起')
  assert.match(
    await page.locator('.stage .cover-title input').first().inputValue(),
    /桂枝汤 · 方义总纲/,
    '改过的标题进了成稿',
  )
  await page.screenshot({ path: `${shots}/08-outline-deck.png` })

  /* —— 侧栏入口 —— */
  const rail = await page.locator('.app-sidebar, aside, nav').first().innerText()
  assert.match(rail, /幻灯工坊/, '侧栏有幻灯工坊入口')

  assert.deepEqual(errors, [], `页面不应有运行时错误：${errors.join(' | ')}`)
  console.log('ppt studio e2e passed · screenshots:', shots)
} finally {
  await browser?.close()
  server.kill()
}
