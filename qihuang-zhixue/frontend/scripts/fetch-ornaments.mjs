// 装饰素材包：从 Openclipart（公有领域 / CC0）下载中式装饰资产并登记进 registry。
// 人工跑一次：node scripts/fetch-ornaments.mjs
// 幂等：已登记的 id 跳过；重跑只补缺。
//
// 为什么是 Openclipart：Wikimedia 在当前网络不可达、freepd 关站、MET 接口 410。
// Openclipart 全站 CC0/公有领域，SVG 矢量（potrace 描摹为主），正是角花/边框/
// 底纹需要的形态。质量靠**策展**：下面这张 manifest 是人工挑过的，不是搜出来
// 什么都收。跑完请人眼复核 assets/ornaments/ 里的每一张，再把 review.reviewer
// 改成真人。
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync, existsSync, statSync } from 'node:fs'
import { resolve } from 'node:path'

const FRONTEND = resolve(import.meta.dirname, '..')
const OUT_DIR = resolve(FRONTEND, 'public/assets/ornaments')
const REGISTRY = resolve(FRONTEND, 'public/assets/registry.json')

/** 策展清单。kind: ornament（角饰/边框/横带）| ground-texture（底纹）。 */
const MANIFEST = [
  // —— 角花：四角的点睛装饰 ——
  { id: 'ornament-corner-antique', oc: 333685, kind: 'ornament', subjects: ['古典角花'], tags: ['角花', '角饰', '古典', '卷草'] },
  { id: 'ornament-corner-nouveau', oc: 322454, kind: 'ornament', subjects: ['藤蔓角花'], tags: ['角花', '角饰', '藤蔓', '卷草'] },
  { id: 'ornament-corner-seamless-01', oc: 282429, kind: 'ornament', subjects: ['对称角花'], tags: ['角花', '角饰', '对称'] },
  { id: 'ornament-corner-seamless-02', oc: 282430, kind: 'ornament', subjects: ['对称角花'], tags: ['角花', '角饰', '对称'] },
  { id: 'ornament-corner-seamless-03', oc: 282431, kind: 'ornament', subjects: ['对称角花'], tags: ['角花', '角饰', '对称'] },
  { id: 'ornament-corner-seamless-04', oc: 282432, kind: 'ornament', subjects: ['对称角花'], tags: ['角花', '角饰', '对称'] },
  // —— 横带 / 边框：眉标、页脚、框线 ——
  { id: 'ornament-band-greekkey', oc: 287501, kind: 'ornament', subjects: ['回纹横带'], tags: ['回纹', '横带', '边框', '几何'] },
  { id: 'ornament-band-flowers', oc: 244030, kind: 'ornament', subjects: ['花草边带'], tags: ['花边', '横带', '花草'] },
  { id: 'ornament-band-leafy', oc: 181552, kind: 'ornament', subjects: ['枝叶边带'], tags: ['花边', '横带', '枝叶'] },
  { id: 'ornament-border-divider', oc: 242627, kind: 'ornament', subjects: ['分隔饰线'], tags: ['分隔', '横带', '饰线'] },
  { id: 'ornament-border-frame-01', oc: 222491, kind: 'ornament', subjects: ['装饰框'], tags: ['边框', '框饰'] },
  { id: 'ornament-border-frame-02', oc: 249894, kind: 'ornament', subjects: ['装饰框'], tags: ['边框', '框饰', '花草'] },
  { id: 'ornament-greek-frame-01', oc: 303831, kind: 'ornament', subjects: ['回纹框'], tags: ['回纹', '框饰', '几何'] },
  { id: 'ornament-greek-frame-02', oc: 303923, kind: 'ornament', subjects: ['回纹框'], tags: ['回纹', '框饰', '几何'] },
  // —— 底纹：整页铺的纹样 ——
  { id: 'ground-papercut-01', oc: 190487, kind: 'ground-texture', subjects: ['剪纸底纹'], tags: ['剪纸', '纹样', '底纹', '民间'] },
  { id: 'ground-papercut-02', oc: 190490, kind: 'ground-texture', subjects: ['剪纸底纹'], tags: ['剪纸', '纹样', '底纹', '民间'] },
  { id: 'ground-papercut-03', oc: 190493, kind: 'ground-texture', subjects: ['剪纸底纹'], tags: ['剪纸', '纹样', '底纹', '民间'] },
  { id: 'ground-fret-tileable', oc: 216132, kind: 'ground-texture', subjects: ['回纹底纹'], tags: ['回纹', '底纹', '方连', '几何'] },
  { id: 'ground-sayagata-01', oc: 290454, kind: 'ground-texture', subjects: ['卍字底纹'], tags: ['卍字', '纱绫', '底纹', '几何'] },
  { id: 'ground-sayagata-02', oc: 290456, kind: 'ground-texture', subjects: ['卍字底纹'], tags: ['卍字', '纱绫', '底纹', '几何'] },
  { id: 'ground-folk-01', oc: 182872, kind: 'ground-texture', subjects: ['民间纹样'], tags: ['民间', '纹样', '底纹'] },
  { id: 'ground-folk-02', oc: 263060, kind: 'ground-texture', subjects: ['冰裂底纹'], tags: ['冰裂纹', '纹样', '底纹'] },
  { id: 'ground-chinese-01', oc: 48271, kind: 'ground-texture', subjects: ['中式纹样'], tags: ['纹样', '底纹', '几何'] },
  { id: 'ground-chinese-02', oc: 48301, kind: 'ground-texture', subjects: ['中式纹样'], tags: ['纹样', '底纹', '几何'] },
  { id: 'ground-chinese-03', oc: 48283, kind: 'ground-texture', subjects: ['中式纹样'], tags: ['纹样', '底纹', '斜纹', '几何'] },
]

const slugOf = (id) => id

const svgSize = (text) => {
  const viewBox = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(text)
  if (viewBox) return { w: Math.round(Number(viewBox[1])), h: Math.round(Number(viewBox[2])) }
  const w = /width="([\d.]+)/.exec(text)
  const h = /height="([\d.]+)/.exec(text)
  return { w: w ? Math.round(Number(w[1])) : 0, h: h ? Math.round(Number(h[1])) : 0 }
}

mkdirSync(OUT_DIR, { recursive: true })
const registry = JSON.parse(readFileSync(REGISTRY, 'utf8'))
const known = new Set(registry.assets.map((item) => item.id))
const today = new Date().toISOString().slice(0, 10)

let added = 0
for (const item of MANIFEST) {
  if (known.has(item.id)) {
    console.log(`skip ${item.id} (already registered)`)
    continue
  }
  const file = resolve(OUT_DIR, `${item.id}.svg`)
  if (!existsSync(file)) {
    const url = `https://openclipart.org/download/${item.oc}/${slugOf(item.id)}.svg`
    const response = await fetch(url, { headers: { 'User-Agent': 'QihuangEduBot/1.0 (asset curation)' } })
    if (!response.ok) {
      console.log(`MISS ${item.id}: HTTP ${response.status}`)
      continue
    }
    const text = await response.text()
    if (!text.includes('<svg')) {
      console.log(`MISS ${item.id}: not an svg`)
      continue
    }
    writeFileSync(file, text, 'utf8')
  }
  const text = readFileSync(file, 'utf8')
  const size = svgSize(text)
  const bytes = statSync(file).size
  const sha256 = createHash('sha256').update(text).digest('hex')
  registry.assets.push({
    id: item.id,
    kind: item.kind,
    slot_class: 'decorative',
    subjects: item.subjects,
    herb_ids: [],
    tags: item.tags,
    file: `assets/ornaments/${item.id}.svg`,
    w: size.w,
    h: size.h,
    bytes,
    sha256,
    license: 'CC0',
    source_url: `https://openclipart.org/detail/${item.oc}`,
    attribution: 'Openclipart（公有领域贡献）',
    treatment: '',
    added_at: new Date().toISOString(),
    review: {
      reviewer: 'agent-review',
      date: today,
      note: '装饰素材包：人工策展自 Openclipart CC0。待人眼复核观感与降噪后把 reviewer 改成真人。',
    },
  })
  known.add(item.id)
  added += 1
  console.log(`added ${item.id} (${size.w}x${size.h}, ${(bytes / 1024).toFixed(0)}KB)`)
}

if (added) writeFileSync(REGISTRY, JSON.stringify(registry, null, 2) + '\n', 'utf8')
console.log(`done: +${added}, total assets ${registry.assets.length}`)
