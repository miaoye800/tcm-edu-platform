import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const output = new URL('../deliverables/stage4/meridian/', import.meta.url)
const baseUrl = process.env.MERIDIAN_CAPTURE_URL || 'http://127.0.0.1:5187'
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const mode = process.argv[2] || 'all'
mkdirSync(output, { recursive: true })

const browser = await chromium.launch({ headless: true, executablePath: chromePath, args: ['--disable-frame-rate-limit', '--disable-gpu-vsync'] })
const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 })
const page = await context.newPage()
const url = (query = '') => `${baseUrl}/path?e2eForceMock=1${query}`
const outputPath = (name) => fileURLToPath(new URL(name, output))
const waitForMap = async () => {
  await page.waitForSelector('.map-stage')
  await page.waitForTimeout(900)
}
const metrics = { viewport: [1440, 960], frames: {} }

try {
  if (mode === 'full' || mode === 'all') {
    await page.goto(url(), { waitUntil: 'domcontentloaded' })
    await waitForMap()
    await page.screenshot({ path: outputPath('full-map.png'), fullPage: true })
    console.log('captured full map')
  }

  if (mode === 'current' || mode === 'all') {
    await page.goto(url(), { waitUntil: 'domcontentloaded' })
    await waitForMap()
    const current = page.locator('[data-node-id="d01"]')
    const currentCore = page.locator('[data-node-id="d01"] .node-core')
    const currentBox = await currentCore.boundingBox()
    if (!currentBox) throw new Error('Current node is not visible.')
    await page.mouse.move(currentBox.x + 13, currentBox.y + 13)
    await page.waitForTimeout(120)
    await page.screenshot({ path: outputPath('current-node-hover.png'), clip: { x: Math.max(0, currentBox.x - 125), y: Math.max(0, currentBox.y - 115), width: 300, height: 250 } })
    console.log('captured current node')
  }

  if (mode === 'flow' || mode === 'all') {
    await page.goto(url('&assessment_passed=general'), { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.flow-orb')
    await page.waitForTimeout(620)
    await page.screenshot({ path: outputPath('lighting-flow-midframe.png'), fullPage: true })
    console.log('captured lighting flow')
  }

  if (mode === 'transition' || mode === 'all') {
    await page.goto(url(), { waitUntil: 'domcontentloaded' })
    await waitForMap()
    const transitionNode = page.locator('[data-node-id="d01"]')
    await transitionNode.click({ position: { x: 13, y: 13 }, force: true, noWaitAfter: true })
    await page.waitForTimeout(190)
    await page.screenshot({ path: outputPath('entry-transition-midframe.png'), fullPage: true })
    console.log('captured entry transition')
  }

  if (mode === 'metrics' || mode === 'all') {
    await page.goto(url(), { waitUntil: 'domcontentloaded' })
    await waitForMap()
    metrics.frames.normal = await page.evaluate(async () => {
    const stamps = []
    await new Promise((resolve) => {
      const step = (time) => { stamps.push(time); if (stamps.length >= 121) resolve(); else requestAnimationFrame(step) }
      requestAnimationFrame(step)
    })
    const deltas = stamps.slice(1).map((time, index) => time - stamps[index])
    const mean = deltas.reduce((sum, value) => sum + value, 0) / deltas.length
    return { samples: deltas.length, meanFrameMs: Number(mean.toFixed(2)), estimatedFps: Number((1000 / mean).toFixed(1)) }
    })

    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.reload({ waitUntil: 'domcontentloaded' })
    await waitForMap()
    metrics.frames.reducedMotion = await page.evaluate(() => {
    const particle = getComputedStyle(document.querySelector('.rise-particles'))
    const current = document.querySelector('[data-visual-state="current"] .node-halo')
    const currentStyle = current ? getComputedStyle(current) : null
    return { particleDisplay: particle.display, nodeAnimationName: currentStyle?.animationName || '', nodeAnimationDuration: currentStyle?.animationDuration || '' }
    })
    await page.screenshot({ path: outputPath('reduced-motion-static.png'), fullPage: true })
    console.log('captured reduced motion')
    writeFileSync(outputPath('performance.json'), `${JSON.stringify(metrics, null, 2)}\n`)
  }
} finally {
  await browser.close()
}

console.log(JSON.stringify(metrics, null, 2))
