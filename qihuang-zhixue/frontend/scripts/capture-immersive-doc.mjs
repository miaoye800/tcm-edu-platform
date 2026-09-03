import { mkdirSync, writeFileSync } from 'node:fs'
import { spawn } from 'node:child_process'

const out = new URL('../deliverables/immersive-doc/', import.meta.url).pathname.replace(/^\//, '')
mkdirSync(out, { recursive: true })
const session = await (await fetch('http://127.0.0.1:8000/api/auth/guest', { method: 'POST' })).json()
const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const child = spawn(chrome, ['--headless=new', '--remote-debugging-port=9237', `--user-data-dir=${out}/chrome-profile`, 'about:blank'], { windowsHide: true })
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
await wait(1200)
const target = await (await fetch('http://127.0.0.1:9237/json/new?about:blank', { method: 'PUT' })).json()
const ws = new WebSocket(target.webSocketDebuggerUrl); await new Promise(resolve => { ws.onopen = resolve })
let id = 0
const command = (method, params = {}) => new Promise((resolve, reject) => { const current = ++id; const listener = event => { const message = JSON.parse(event.data); if (message.id !== current) return; ws.removeEventListener('message', listener); message.error ? reject(new Error(message.error.message)) : resolve(message.result) }; ws.addEventListener('message', listener); ws.send(JSON.stringify({ id: current, method, params })) })
const evalPage = expression => command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
const shot = async name => { const image = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); writeFileSync(`${out}/${name}.png`, Buffer.from(image.data, 'base64')) }
const nav = async url => { await command('Page.navigate', { url }); await wait(1100) }
await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false })
await command('Page.addScriptToEvaluateOnNewDocument', { source: `localStorage.setItem('qihuang-e2e-force-mock','1');sessionStorage.setItem('qihuang-e2e-force-mock','1');localStorage.setItem('qihuang-auth-v1',${JSON.stringify(JSON.stringify(session))})` })
await nav('http://127.0.0.1:5174/resources/mock-doc?task=demo&e2eForceMock=1')
await wait(680); await shot('document-generating-mid')
await wait(1400); await shot('document-complete')
await evalPage("document.querySelector('.citation-mark')?.click()")
await wait(180); await shot('citation-panel')
await evalPage("document.querySelector('.alchemy-notes summary')?.click()")
await wait(180); await shot('alchemy-notes')
await nav('http://127.0.0.1:5174/alchemy-demo')
await wait(15300); await shot('elixir-card')
ws.close(); child.kill()
