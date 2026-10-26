import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = fileURLToPath(new URL('..', import.meta.url))
const viteBin = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))
const baseUrl = 'http://127.0.0.1:5188'
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const server = spawn(process.execPath, [viteBin, '--host', '127.0.0.1', '--port', '5188', '--configLoader', 'runner'], {
  cwd: root,
  env: { ...process.env, VITE_E2E_CAPTURE: 'true' },
  stdio: 'ignore',
  windowsHide: true,
})

try {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    try { if ((await fetch(baseUrl)).ok) break } catch { /* Vite is starting. */ }
    await new Promise(resolve => setTimeout(resolve, 200))
  }

  const browser = await chromium.launch({ headless: true, executablePath: chromePath })
  const errors = []
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 })
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(`${baseUrl}/atlas?e2eForceMock=1`, { waitUntil: 'domcontentloaded' })
  await page.locator('.graph-canvas canvas').waitFor({ state: 'visible' })
  await page.waitForTimeout(1800)

  await page.getByRole('button', { name: '选择节点 麻黄', exact: true }).click()
  await page.locator('.detail-panel h3').waitFor({ state: 'visible', timeout: 4000 })
  assert.equal(await page.locator('.detail-panel h3').innerText(), '麻黄', 'clicking the central herb opens its detail panel')
  await page.locator('.connection-row').first().waitFor({ state: 'visible', timeout: 2000 })
  assert.ok(await page.locator('.connection-row').count() > 0, 'selected nodes list their connected graph relationships')

  await page.locator('.connection-row').filter({ hasText: '麻黄汤' }).first().click()
  assert.equal(await page.locator('.detail-panel h3').innerText(), '麻黄汤', 'connected node links switch the detail panel to that node')

  const countBefore = await page.locator('.graph-counts').innerText()
  const composition = page.getByRole('button', { name: '方药组成' })
  await composition.click()
  assert.equal(await composition.getAttribute('aria-pressed'), 'false', 'relationship filter toggles off')
  const countAfter = await page.locator('.graph-counts').innerText()
  const relations = value => Number(value.match(/(\d+)\s*条关系/)?.[1] || 0)
  assert.ok(relations(countAfter) < relations(countBefore), 'graph counts follow the active filters')
  assert.deepEqual(errors, [], 'graph interactions produce no uncaught browser errors')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(250)
  const mobile = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
    card: document.querySelector('.graph-view')?.getBoundingClientRect().width,
  }))
  assert.equal(mobile.page, mobile.viewport, 'the mobile graph has no horizontal overflow')
  await browser.close()
  console.log('graph interaction browser checks passed')
} finally {
  server.kill()
}
