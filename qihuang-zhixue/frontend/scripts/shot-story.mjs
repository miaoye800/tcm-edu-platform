// Visual acceptance for the apprentice story.
//
// Boots a throwaway dev server, captures the two redesigned screens plus the
// component gallery at five breakpoints, and fails when a page scrolls, when
// content escapes the viewport, or when any visible text drops below 12px.
//
//   node scripts/shot-story.mjs            # capture + assert
//   node scripts/shot-story.mjs --keep     # keep the dev server alive
//
// Output: deliverables/story/<name>-<width>x<height>.png

import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { chromium } from 'playwright'

const FRONTEND = fileURLToPath(new URL('..', import.meta.url))
const OUT_DIR = join(FRONTEND, 'deliverables', 'story')
const PORT = Number(process.env.STORY_SHOT_PORT ?? 5199)
const BASE = `http://127.0.0.1:${PORT}`
const MIN_FONT_SIZE = 12

const VIEWPORTS = [
  { width: 1920, height: 1080, label: '2k' },
  { width: 1440, height: 900, label: 'desktop' },
  { width: 1280, height: 800, label: 'laptop' },
  { width: 1024, height: 768, label: 'tablet' },
  { width: 390, height: 844, label: 'mobile' },
]

const SCREENS = [
  { name: 'screen-1-identity', path: '/story-preview?screen=identity' },
  { name: 'screen-1-identity-note', path: '/story-preview?screen=identity&bubble=1' },
  { name: 'screen-2-quiz', path: '/story-preview?screen=quiz' },
  { name: 'screen-2-quiz-note', path: '/story-preview?screen=quiz&bubble=1' },
  { name: 'screen-3-prescription', path: '/story-preview?screen=prescription' },
  { name: 'screen-3-prescription-field', path: '/story-preview?screen=prescription&field=case' },
  { name: 'screen-4-rule', path: '/story-preview?screen=rule' },
  { name: 'screen-4-rule-field', path: '/story-preview?screen=rule&field=minutes' },
  { name: 'story-kit', path: '/story-kit', only: ['desktop'], scrollable: true },
]

const waitForServer = async (deadlineMs = 90_000) => {
  const started = Date.now()
  while (Date.now() - started < deadlineMs) {
    try {
      const response = await fetch(BASE, { method: 'GET' })
      if (response.ok) return true
    } catch {
      // server not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 400))
  }
  return false
}

const measure = () => {
  const root = document.documentElement
  const overflowX = root.scrollWidth - window.innerWidth
  const overflowY = root.scrollHeight - window.innerHeight
  const tiny = []
  for (const element of document.querySelectorAll('body *')) {
    const style = getComputedStyle(element)
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue
    const hasOwnText = Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim().length > 0,
    )
    if (!hasOwnText) continue
    const size = Number.parseFloat(style.fontSize)
    if (size < 12) tiny.push({ text: element.textContent.trim().slice(0, 24), size })
  }
  return { overflowX, overflowY, tiny }
}

const launchBrowser = async () => {
  // Prefer an already-installed Chromium build so the check runs without a
  // multi-hundred-megabyte download; fall back to the bundled browser.
  const channels = [process.env.STORY_SHOT_CHANNEL, 'chrome', 'msedge'].filter(Boolean)
  for (const channel of channels) {
    try {
      return await chromium.launch({ channel })
    } catch {
      // channel not installed, try the next one
    }
  }
  return chromium.launch()
}

const main = async () => {
  mkdirSync(OUT_DIR, { recursive: true })

  const server = spawn(
    process.platform === 'win32' ? 'npx.cmd' : 'npx',
    ['vite', '--host', '127.0.0.1', '--port', String(PORT), '--strictPort', '--configLoader', 'runner'],
    { cwd: FRONTEND, stdio: 'ignore', shell: process.platform === 'win32' },
  )

  const ready = await waitForServer()
  if (!ready) {
    server.kill()
    console.error(`dev server did not start on ${BASE}`)
    process.exit(1)
  }

  const browser = await launchBrowser()
  const failures = []
  let captured = 0

  try {
    for (const screen of SCREENS) {
      for (const viewport of VIEWPORTS) {
        if (screen.only && !screen.only.includes(viewport.label)) continue
        const context = await browser.newContext({ viewport, deviceScaleFactor: 1 })
        const page = await context.newPage()
        await page.goto(`${BASE}${screen.path}`, { waitUntil: 'networkidle' })
        // Let the typewriter finish so the screenshot shows the settled layout.
        // The longest line is ~42 characters at 42ms each, so 2.4s is enough.
        await page.waitForTimeout(2400)

        const name = `${screen.name}-${viewport.width}x${viewport.height}.png`
        await page.screenshot({ path: join(OUT_DIR, name), fullPage: false })
        captured += 1

        const result = await page.evaluate(measure)
        if (result.overflowX > 1) failures.push(`${name}: 横向溢出 ${result.overflowX}px`)
        // Gallery-style pages are meant to scroll; only the game screens must fit
        // inside one viewport.
        if (result.overflowY > 1 && !screen.scrollable) {
          failures.push(`${name}: 纵向溢出 ${result.overflowY}px`)
        }
        for (const item of result.tiny) {
          failures.push(`${name}: 字号 ${item.size}px < ${MIN_FONT_SIZE}px — 「${item.text}」`)
        }

        await context.close()
      }
    }
  } finally {
    await browser.close()
    if (!process.argv.includes('--keep')) server.kill()
  }

  console.log(`captured ${captured} screenshots -> deliverables/story/`)
  if (failures.length) {
    console.error(`\n${failures.length} 项未通过:`)
    for (const line of failures) console.error(` - ${line}`)
    process.exitCode = 1
    return
  }
  console.log('所有断点通过：无溢出、无小于 12px 的文字')
}

await main()
