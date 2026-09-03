import { mkdirSync, writeFileSync } from 'node:fs'
import { spawn } from 'node:child_process'

const out = new URL('../deliverables/mindmap-themes/', import.meta.url).pathname.replace(/^\//, '')
mkdirSync(out, { recursive: true })
const themes = ['ink-scroll', 'warm-gold', 'neon-night', 'herb-garden', 'fishbone-contrast', 'timeline-process', 'star-chart', 'hand-scroll']
const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const port = 9238
const child = spawn(chrome, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${out}/chrome-profile`, 'about:blank'], { windowsHide: true })
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
for (let attempt = 0; attempt < 20; attempt += 1) {
  try {
    await fetch(`http://127.0.0.1:${port}/json/version`)
    break
  } catch {
    if (attempt === 19) throw new Error('Chrome remote debugging did not become available')
    await wait(500)
  }
}

for (const [index, theme] of themes.entries()) {
  const page = await (await fetch(`http://127.0.0.1:${port}/json/new`, { method: 'PUT' })).json()
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve) => { ws.onopen = resolve })
  let id = 0
  const command = (method, params = {}) => new Promise((resolve) => {
    const currentId = ++id
    const listener = (event) => { const message = JSON.parse(event.data); if (message.id === currentId) { ws.removeEventListener('message', listener); resolve(message.result) } }
    ws.addEventListener('message', listener)
    ws.send(JSON.stringify({ id: currentId, method, params }))
  })
  await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false })
  await command('Page.navigate', { url: `http://127.0.0.1:5179/mindmap-themes?theme=${theme}` })
  await wait(index === 0 ? 1700 : 850)
  const body = await command('Runtime.evaluate', { expression: 'document.body.innerText' })
  if (!body?.result?.value?.includes('麻黄')) throw new Error(`Mindmap page did not mount for ${theme}`)
  const image = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
  writeFileSync(`${out}/${String(index + 1).padStart(2, '0')}-${theme}.png`, Buffer.from(image.data, 'base64'))
  ws.close()
}
child.kill()
