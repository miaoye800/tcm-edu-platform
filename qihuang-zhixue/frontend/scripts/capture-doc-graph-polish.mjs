import { mkdirSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = fileURLToPath(new URL('..', import.meta.url))
const round = process.argv.find(value => value.startsWith('--round='))?.slice(8) || 'round-1'
const output = fileURLToPath(new URL(`../deliverables/stage4/doc-graph/${round}/`, import.meta.url))
const baseUrl = 'http://127.0.0.1:5186'
const viteBin = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

const server = spawn(process.execPath, [viteBin, '--host', '127.0.0.1', '--port', '5186', '--configLoader', 'runner'], { cwd: root, env: { ...process.env, VITE_E2E_CAPTURE: 'true' }, stdio: 'pipe', windowsHide: true })
try {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try { if ((await fetch(baseUrl)).ok) break } catch { /* Vite is starting. */ }
    await sleep(200)
  }
  mkdirSync(output, { recursive: true })
  const browser = await chromium.launch({ headless: true, executablePath: chromePath })
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 })
  await page.goto(`${baseUrl}/resources/mock-doc?task=demo&e2eForceMock=1`, { waitUntil: 'networkidle' })
  await sleep(3200)
  await page.screenshot({ path: `${output}/document-full.png`, fullPage: true, animations: 'disabled' })
  await page.locator('.alchemy-notes summary').click()
  await page.locator('.alchemy-notes').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${output}/alchemy-notes-open.png`, fullPage: true, animations: 'disabled' })
  await page.locator('.citation-mark').click()
  await page.screenshot({ path: `${output}/source-open.png`, fullPage: true, animations: 'disabled' })
  await page.goto(`${baseUrl}/atlas?e2eForceMock=1`, { waitUntil: 'networkidle' })
  await sleep(4800)
  await page.screenshot({ path: `${output}/graph-page.png`, fullPage: true, animations: 'disabled' })
  await browser.close()
  console.log(output)
} finally {
  server.kill()
}
