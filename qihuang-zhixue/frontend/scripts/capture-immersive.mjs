import { mkdirSync, writeFileSync } from 'node:fs'
import { spawn } from 'node:child_process'

const out = new URL('../deliverables/immersive/', import.meta.url).pathname.replace(/^\//, '')
mkdirSync(out, { recursive: true })
const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const child = spawn(chrome, ['--headless=new', '--remote-debugging-port=9232', `--user-data-dir=${out}/chrome-profile-v2`, 'about:blank'], { windowsHide: true })
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
await wait(1800)
const target = await (await fetch('http://127.0.0.1:9232/json/new?about:blank', { method: 'PUT' })).json()
const ws = new WebSocket(target.webSocketDebuggerUrl); await new Promise((resolve) => { ws.onopen = resolve })
let id = 0
const command = (method, params = {}) => new Promise((resolve, reject) => { const current = ++id; const listener = (event) => { const data = JSON.parse(event.data); if (data.id !== current) return; ws.removeEventListener('message', listener); if (data.error) reject(new Error(data.error.message)); else resolve(data.result) }; ws.addEventListener('message', listener); ws.send(JSON.stringify({ id: current, method, params })) })
const evaluate = async (expression) => (await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result.value
const waitFor = async (selector, timeout = 5000) => { const deadline = Date.now() + timeout; while (Date.now() < deadline) { if (await evaluate(`Boolean(document.querySelector(${JSON.stringify(selector)}))`)) return; await wait(120) } throw new Error(`Timed out waiting for ${selector}`) }
const assertPage = async (route, sentinel) => { const state = await evaluate(`({ path: location.pathname, force: sessionStorage.getItem('qihuang-e2e-force-mock') || localStorage.getItem('qihuang-e2e-force-mock'), found: Boolean(document.querySelector(${JSON.stringify(sentinel)})) })`); if (state.path !== route || state.force !== '1' || !state.found) throw new Error(`Screenshot assertion failed: ${JSON.stringify(state)}`) }
const shot = async (name, route, sentinel) => { await assertPage(route, sentinel); const image = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); writeFileSync(`${out}/${name}.png`, Buffer.from(image.data, 'base64')) }
await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false })
await command('Page.addScriptToEvaluateOnNewDocument', { source: "localStorage.setItem('qihuang-e2e-force-mock','1');sessionStorage.setItem('qihuang-e2e-force-mock','1')" })
await command('Page.navigate', { url: 'http://127.0.0.1:5174/login?e2eForceMock=1' }); await wait(700)
await evaluate("fetch('/api/auth/guest',{method:'POST'}).then(r=>r.json()).then(s=>{localStorage.setItem('qihuang-auth-v1',JSON.stringify(s));location.href='/path?e2eForceMock=1'})")
await wait(2200)
console.log('first navigation', await evaluate("({ pathname: window.location.pathname, forceMock: sessionStorage.getItem('qihuang-e2e-force-mock') || localStorage.getItem('qihuang-e2e-force-mock') })"))
await waitFor('[data-node-id]')
await evaluate("document.querySelector('[data-node-id]')?.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:300,clientY:300}))")
await waitFor('.immersive-layer')
await wait(1000); await shot('immersive-opening', '/path', '.immersive-layer')
await evaluate("document.querySelector('.chips button')?.click()")
await waitFor('.recommendation-grid')
await wait(2600); await shot('immersive-recommendations', '/path', '.recommendation-grid')
await evaluate("document.querySelector('.confirm')?.click()")
await waitFor('.progress'); await shot('immersive-confirm-progress', '/path', '.progress')
await evaluate("location.href='/resources?e2eForceMock=1'"); await waitFor('.resources-view .grid'); await shot('resources-library', '/resources', '.resources-view .grid')
await evaluate("const input=document.querySelector('.resources-view header input');input.value='__empty__';input.dispatchEvent(new Event('input',{bubbles:true}))"); await wait(600); await shot('resources-empty', '/resources', '.resources-view .empty svg')
ws.close(); child.kill()
