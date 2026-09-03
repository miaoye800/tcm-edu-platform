import { mkdirSync, writeFileSync } from 'node:fs'

const out = new URL('../deliverables/meridian-map/', import.meta.url).pathname.replace(/^\//, '')
mkdirSync(out, { recursive: true })
const chromium = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const profile = `${out}/chrome-profile`
const child = (await import('node:child_process')).spawn(chromium, ['--headless=new', '--remote-debugging-port=9223', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true })
await new Promise((resolve) => setTimeout(resolve, 1800))
const page = await (await fetch('http://127.0.0.1:9223/json/new?http://127.0.0.1:5174/path', { method: 'PUT' })).json()
const ws = new WebSocket(page.webSocketDebuggerUrl)
const command = (id, method, params = {}) => new Promise((resolve) => { const listener = (event) => { const message = JSON.parse(event.data); if (message.id === id) { ws.removeEventListener('message', listener); resolve(message.result) } }; ws.addEventListener('message', listener); ws.send(JSON.stringify({ id, method, params })) })
await new Promise((resolve) => { ws.onopen = resolve })
await command(0, 'Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false })
await command(1, 'Runtime.evaluate', { expression: "localStorage.setItem('qihuang-auth-v1', JSON.stringify({token:'demo',user:{user_id:'demo',username:'demo',display_name:'Demo',is_guest:true,created_at:''}}));location.href='/path'" })
await new Promise((resolve) => setTimeout(resolve, 2800))
const image = await command(2, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
writeFileSync(`${out}/full-map.png`, Buffer.from(image.data, 'base64'))
ws.close()
child.kill()
