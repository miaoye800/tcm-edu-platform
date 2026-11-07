/**
 * 逐页截屏：现起一份讲稿，把每一页翻过去各存一张图。
 *
 * 只用于人工视觉复核，不进 `npm test`。用法：
 *   node tests/ppt-shots.mjs 麻黄 [输出目录] [主题]
 */

import { mkdirSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const topic = process.argv[2] || '麻黄'
const outDir = process.argv[3] || 'D:\\Agent\\tcm-edu-platform\\.tmpshots\\pages'
const theme = process.argv[4] || ''
mkdirSync(outDir, { recursive: true })

const root = fileURLToPath(new URL('..', import.meta.url))
const viteBin = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))
const baseUrl = 'http://127.0.0.1:5189'
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const server = spawn(process.execPath, [viteBin, '--host', '127.0.0.1', '--port', '5189', '--configLoader', 'runner'], {
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
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 })
  const session = await context.request.post(`${baseUrl}/api/auth/guest`, { data: {} })
  const auth = await session.json()
  await context.addInitScript((payload) => {
    window.localStorage.setItem('qihuang-auth-v1', JSON.stringify(payload))
    window.localStorage.setItem(
      `qihuang-onboarding:${payload.user.user_id}`,
      JSON.stringify({ status: 'skipped', mode: 'quickcard', updatedAt: new Date().toISOString() }),
    )
  }, auth)

  const page = await context.newPage()
  await page.goto(`${baseUrl}/ppt`, { waitUntil: 'domcontentloaded' })
  await page.locator('#ppt-free-topic').waitFor({ state: 'visible', timeout: 20000 })
  await page.locator('#ppt-free-topic').fill(topic)
  await page.locator('.ink-button').click()
  await page.waitForFunction(() => document.querySelectorAll('.strip .thumb').length >= 12, null, { timeout: 90000 })
  await page.waitForTimeout(600)
  if (theme) {
    await page.getByRole('button', { name: theme }).click()
    await page.waitForTimeout(300)
  }

  const total = await page.locator('.strip .thumb').count()
  for (let index = 0; index < total; index += 1) {
    await page.locator('.strip .thumb').nth(index).click()
    await page.waitForTimeout(320)
    await page.locator('.stage-wrap').screenshot({ path: `${outDir}\\page-${String(index + 1).padStart(2, '0')}.png` })
  }
  console.log('shots:', total, '→', outDir)
} finally {
  await browser?.close()
  server.kill()
}
