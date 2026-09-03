import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { PNG } from 'pngjs'
import pixelmatch from 'pixelmatch'

const root = fileURLToPath(new URL('..', import.meta.url))
const label = process.argv.find((value) => value.startsWith('--label='))?.slice(8) || 'after'
const compareLabel = process.argv.find((value) => value.startsWith('--compare='))?.slice(10)
const mobile = process.argv.includes('--mobile')
const output = new URL(`../deliverables/stage4/baseline/${label}/`, import.meta.url)
const compareOutput = new URL(`../deliverables/stage4/baseline/compare-${compareLabel}-to-${label}/`, import.meta.url)
const baseUrl = 'http://127.0.0.1:5184'
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const viteBin = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))
const pixelTolerance = 10
const routes = [
  ['01-login', '/login'],
  ['02-dashboard', '/dashboard'],
  ['03-profile', '/profile'],
  ['04-meridian-map', '/path'],
  ['05-immersive-entry', '/alchemy-demo'],
  ['06-resources', '/resources'],
  ['07-atlas', '/atlas'],
  ['08-document', '/resources/mock-doc'],
  ['09-mindmap-themes', '/mindmap-themes'],
]

if (process.argv.includes('--include-lab')) routes.push(['10-stage4-lab', '/stage4-lab'])

const waitForServer = async () => {
  // The first Tailwind JIT scan can make a cold Vite start take about 20 seconds.
  for (let attempt = 0; attempt < 150; attempt += 1) {
    try {
      if ((await fetch(baseUrl)).ok) return
    } catch {
      // Vite is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error('Timed out waiting for the isolated Vite capture server.')
}

const server = spawn(process.execPath, [viteBin, '--host', '127.0.0.1', '--port', '5184', '--configLoader', 'runner'], {
  cwd: root,
  env: { ...process.env, VITE_E2E_CAPTURE: 'true' },
  stdio: 'pipe',
  windowsHide: true,
})

try {
  mkdirSync(output, { recursive: true })
  await waitForServer()
  const browser = await chromium.launch({ headless: true, executablePath: chromePath })
  const page = await browser.newPage({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 960 }, deviceScaleFactor: 1 })
  await page.emulateMedia({ reducedMotion: 'reduce' })

  for (const [name, route] of routes) {
    await page.goto(`${baseUrl}${route}?e2eForceMock=1`, { waitUntil: 'networkidle' })
    // G6 settles its canvas layout asynchronously; use its established capture delay.
    await page.waitForTimeout(route === '/atlas' ? 4800 : 1200)
    await page.screenshot({ path: fileURLToPath(new URL(`${name}.png`, output)), fullPage: true, animations: 'disabled' })
  }

  await browser.close()

  const summary = { label, routes: routes.map(([name, route]) => ({ name, route })), compare: null }
  if (compareLabel) {
    mkdirSync(compareOutput, { recursive: true })
    const compared = []
    for (const [name] of routes) {
      const previous = fileURLToPath(new URL(`../deliverables/stage4/baseline/${compareLabel}/${name}.png`, import.meta.url))
      const current = fileURLToPath(new URL(`${name}.png`, output))
      if (!existsSync(previous) || !existsSync(current)) {
        compared.push({ name, status: 'missing' })
        continue
      }
      const before = PNG.sync.read(readFileSync(previous))
      const after = PNG.sync.read(readFileSync(current))
      if (before.width !== after.width || before.height !== after.height) {
        compared.push({ name, status: 'dimension-mismatch', before: [before.width, before.height], after: [after.width, after.height] })
        continue
      }
      const diff = new PNG({ width: before.width, height: before.height })
      const pixels = pixelmatch(before.data, after.data, diff.data, before.width, before.height, { threshold: 0.1 })
      if (pixels > pixelTolerance) writeFileSync(fileURLToPath(new URL(`${name}.diff.png`, compareOutput)), PNG.sync.write(diff))
      compared.push({ name, status: pixels > pixelTolerance ? 'different' : pixels ? 'within-tolerance' : 'identical', pixels, pixelTolerance })
    }
    summary.compare = compared
    writeFileSync(fileURLToPath(new URL('comparison.json', compareOutput)), `${JSON.stringify(summary, null, 2)}\n`)
  }
  writeFileSync(fileURLToPath(new URL('manifest.json', output)), `${JSON.stringify(summary, null, 2)}\n`)
  console.log(JSON.stringify(summary, null, 2))
} finally {
  server.kill()
}
