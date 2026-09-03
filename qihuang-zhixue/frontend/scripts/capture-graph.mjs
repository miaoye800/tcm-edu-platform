import { mkdirSync, writeFileSync } from 'node:fs'
import { spawn } from 'node:child_process'
const out = new URL('../deliverables/graph/', import.meta.url).pathname.replace(/^\//, ''); mkdirSync(out, { recursive: true })
const child = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', ['--headless=new', '--remote-debugging-port=9238', `--user-data-dir=${out}/chrome-profile`, 'about:blank'], { windowsHide: true })
await new Promise(resolve => setTimeout(resolve, 1600)); const page = await (await fetch('http://127.0.0.1:9238/json/new?http://127.0.0.1:5178/login', { method: 'PUT' })).json(); const ws = new WebSocket(page.webSocketDebuggerUrl)
let seq = 0; const command = (method, params = {}) => new Promise(resolve => { const id = ++seq; const listener = event => { const msg = JSON.parse(event.data); if (msg.id === id) { ws.removeEventListener('message', listener); resolve(msg.result) } }; ws.addEventListener('message', listener); ws.send(JSON.stringify({ id, method, params })) }); await new Promise(resolve => { ws.onopen = resolve })
await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false }); await command('Page.navigate', { url: 'http://127.0.0.1:5178/atlas' }); await new Promise(resolve => setTimeout(resolve, 4800))
const snap = async name => { const image = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); writeFileSync(`${out}/${name}.png`, Buffer.from(image.data, 'base64')) }
await snap('01-global'); await command('Page.navigate', { url: 'http://127.0.0.1:5178/atlas?focus=herb-mahuang' }); await new Promise(resolve => setTimeout(resolve, 1000)); await snap('02-focus')
await command('Runtime.evaluate', { expression: "document.querySelector('.detail-panel details')?.setAttribute('open','')" }); await snap('03-detail'); await command('Runtime.evaluate', { expression: "document.querySelector('.edge-labels button')?.click()" }); await new Promise(resolve => setTimeout(resolve, 250)); await snap('04-edge-source')
ws.close(); child.kill()
