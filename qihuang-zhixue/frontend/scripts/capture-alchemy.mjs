import { mkdirSync, writeFileSync } from 'node:fs'
import { spawn } from 'node:child_process'

const out = new URL('../deliverables/alchemy-demo/v2/', import.meta.url).pathname.replace(/^\//, '')
mkdirSync(`${out}/variants`, { recursive: true })
const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const child = spawn(chrome, ['--headless=new', '--remote-debugging-port=9232', `--user-data-dir=${out}/chrome-profile`, 'about:blank'], { windowsHide: true })
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms))
await sleep(1400)
const page = await (await fetch('http://127.0.0.1:9232/json/new?http://127.0.0.1:4179/alchemy-demo', { method: 'PUT' })).json()
const ws = new WebSocket(page.webSocketDebuggerUrl)
const command = (id, method, params = {}) => new Promise(resolve => { const listener = event => { const message = JSON.parse(event.data); if (message.id === id) { ws.removeEventListener('message', listener); resolve(message.result) } }; ws.addEventListener('message', listener); ws.send(JSON.stringify({ id, method, params })) })
await new Promise(resolve => { ws.onopen = resolve })
await command(1, 'Emulation.setDeviceMetricsOverride', { width: 1440, height: 980, deviceScaleFactor: 1, mobile: false })
let id = 10
const evaluate = expression => command(id++, 'Runtime.evaluate', { expression })
const shot = async name => { const image = await command(id++, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); writeFileSync(`${out}/${name}.png`, Buffer.from(image.data, 'base64')) }
const click = text => evaluate(`Array.from(document.querySelectorAll('button')).find(el=>el.textContent.includes('${text}'))?.click()`)
await sleep(900); await shot('heat-gentle')
await sleep(6200); await shot('heat-fierce')
await sleep(6100); await shot('heat-complete')
await sleep(3600); await shot('elixir-card')
await click('重播'); await sleep(14500); await click('点击跳至终态'); await sleep(250); await shot('elixir-flash'); await sleep(1200); await shot('elixir-rise')
await click('模拟断线'); await sleep(350); await shot('state-offline'); await click('模拟失败'); await sleep(350); await shot('state-failed')
await evaluate(`document.querySelector('input[value="paper"]')?.click()`); await sleep(900); const paper = await command(id++, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); writeFileSync(`${out}/variants/paper-full.png`, Buffer.from(paper.data, 'base64'))
await evaluate(`document.querySelector('input[value="night"]')?.click()`); await sleep(900); const night = await command(id++, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); writeFileSync(`${out}/variants/night-full.png`, Buffer.from(night.data, 'base64'))
ws.close(); child.kill()
