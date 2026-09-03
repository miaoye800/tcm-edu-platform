import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = fileURLToPath(new URL('..', import.meta.url))
const viteBin = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))
const baseUrl = 'http://127.0.0.1:5187'
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const server = spawn(process.execPath, [viteBin, '--host', '127.0.0.1', '--port', '5187', '--configLoader', 'runner'], { cwd: root, env: { ...process.env, VITE_E2E_CAPTURE: 'true' }, stdio: 'pipe', windowsHide: true })

try {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try { if ((await fetch(baseUrl)).ok) break } catch { /* Vite is starting. */ }
    await sleep(200)
  }
  const browser = await chromium.launch({ headless: true, executablePath: chromePath })
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } })
  await page.goto(`${baseUrl}/resources/mock-doc?task=demo&e2eForceMock=1`, { waitUntil: 'networkidle' })
  await sleep(3200)
  assert.equal(await page.locator('.block.is-revealing').count(), 0, 'completed stream leaves no animation class')
  await page.locator('.alchemy-notes summary').click()
  assert.equal(await page.locator('.alchemy-notes').evaluate(element => element.open), true, 'alchemy notes expand')
  for (const [label, path] of [['去练一练', '/quiz'], ['看图谱', '/atlas'], ['回脉络图', '/path']]) {
    await page.goto(`${baseUrl}/resources/mock-doc?e2eForceMock=1`, { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: label }).click()
    await page.waitForURL(`${baseUrl}${path}`)
  }
  await browser.close()
  console.log('document polish browser checks passed')
} finally {
  server.kill()
}
