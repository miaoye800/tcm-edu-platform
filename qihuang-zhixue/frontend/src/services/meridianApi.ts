/**
 * 经脉图数据层：目录（atlas）/ 经脉折线（paths）/ 几何二进制（asset）/ 单点正文（point）。
 *
 * 四个接口刻意分开，因为它们的**体积差两个数量级**：目录 100 KB、折线 200 KB、
 * 皮肤 0.6 MB、肌肉 1.4 MB。首屏只要目录 + 皮肤；肌肉和骨骼等用户要看解剖层再拉。
 */
import { requestJson, requestResponse } from './request'

export interface MeridianAsset {
  name: string
  bytes: number
  gzip_bytes: number
  sha256: string
  vertex_count: number
  triangles: number
  attributes?: string[]
  part_offset?: number | null
  part_count?: number
  url: string
}

export interface MeridianPrecision {
  verified: number
  total: number
  statement: string
  review_url: string
}

export interface MeridianAcupoint {
  id: string
  code: string
  name: string
  pinyin: string
  channel_id: string
  side: 'L' | 'R' | 'M'
  t: number
  x3d: number
  y3d: number
  z3d: number
  verified: number
  skin_tri: number
  tier: number
}

export interface MeridianChannel {
  id: string
  abbr: string
  title: string
  kind: 'meridian' | 'extra'
  view_bias: 'front' | 'back' | 'side'
  sort_hint: number
  path_len: number | null
  geometry_version: string
}

export interface MeridianTick {
  id: string
  name: string
}

export interface MeridianSection {
  id: string
  layer_id: string
  chapter_key: string
  title: string
  acupoint_id: string
  slot_ord: number
  point_count: number
  ord: number
  chapter_no: number
  book_chapter_no: number
  chapter_title: string
  part: string
  section_no: number
  virtual: number
  points: MeridianTick[]
}

export interface MeridianLayer {
  id: string
  title: string
  kind: 'container' | 'point_native'
  color_token: string
  ord: number
  visible_default: number
  authoring_meta?: Record<string, unknown>
  sections: MeridianSection[]
}

export interface MeridianAtlas {
  geometry_version: string
  height_m: number
  attribution: string
  licenses: Array<Record<string, string>>
  assets: MeridianAsset[]
  first_screen_bytes: number
  channels: MeridianChannel[]
  acupoints: MeridianAcupoint[]
  layers: Array<Pick<MeridianLayer, 'id' | 'title' | 'kind' | 'color_token' | 'ord' | 'visible_default'>>
  layer: MeridianLayer | null
  precision: MeridianPrecision
}

export interface MeridianPathData {
  id: string
  title: string
  length_m: number | null
  points: number[][]
}

export interface MeridianPointDetail {
  id: string
  name: string
  level: string
  parent_id: string | null
  description: string
  exam_frequency: number | null
  content: Record<string, unknown>
  content_source: Record<string, unknown>
  sections: Array<Record<string, unknown>>
}

export interface PackedGeometry {
  positions: Float32Array
  normals: Int16Array
  indices: Uint32Array
  part: Float32Array | null
}

const atlasCache = new Map<string, MeridianAtlas>()
const assetCache = new Map<string, PackedGeometry>()
let pathCache: MeridianPathData[] | null = null

export const fetchAtlas = async (layer = 'herb'): Promise<MeridianAtlas> => {
  const cached = atlasCache.get(layer)
  if (cached) return cached
  const atlas = await requestJson<MeridianAtlas>(`/api/meridian/atlas?layer=${encodeURIComponent(layer)}`)
  atlasCache.set(layer, atlas)
  return atlas
}

export const fetchPaths = async (): Promise<MeridianPathData[]> => {
  if (pathCache) return pathCache
  const payload = await requestJson<{ channels: MeridianPathData[] }>('/api/meridian/paths')
  pathCache = payload.channels
  return pathCache
}

export const fetchPointDetail = (nodeId: string) =>
  requestJson<MeridianPointDetail>(`/api/meridian/point/${encodeURIComponent(nodeId)}`, undefined, {
    skipAuthRedirect: true,
  })

/**
 * 按打包布局解出四个数组。
 *
 * 布局是「pos(f32×3) → 4 字节对齐 → nrm(i16×3 归一化) → 对齐 → idx(u32) → 对齐 → part(f32)」。
 * 法线是 **i16 归一化**而不是 f32——按 f32 读会让整块偏移 5 KB，读出来是乱码而且不报错。
 */
export const parsePackedGeometry = (buffer: ArrayBuffer, asset: MeridianAsset): PackedGeometry => {
  const vertices = asset.vertex_count
  const triangles = asset.triangles
  const posBytes = vertices * 12
  const nrmOffset = posBytes + (4 - (posBytes % 4)) % 4
  const nrmBytes = vertices * 6
  const idxOffset = nrmOffset + nrmBytes + ((4 - (nrmBytes % 4)) % 4)
  const idxBytes = triangles * 12
  const partOffset =
    typeof asset.part_offset === 'number'
      ? asset.part_offset
      : idxOffset + idxBytes + ((4 - (idxBytes % 4)) % 4)

  const need = idxOffset + idxBytes
  if (buffer.byteLength < need) {
    throw new Error(`几何资产不完整：${asset.name} 只有 ${buffer.byteLength} 字节，至少要 ${need}`)
  }

  return {
    positions: new Float32Array(buffer, 0, vertices * 3),
    normals: new Int16Array(buffer, nrmOffset, vertices * 3),
    indices: new Uint32Array(buffer, idxOffset, triangles * 3),
    part:
      buffer.byteLength >= partOffset + vertices * 4
        ? new Float32Array(buffer, partOffset, vertices)
        : null,
  }
}

export const loadGeometry = async (asset: MeridianAsset): Promise<PackedGeometry> => {
  const cached = assetCache.get(asset.name)
  if (cached) return cached
  // 服务端对 .gz 直接下发 Content-Encoding: gzip，浏览器在传输层解掉，
  // 这里拿到的就是裸二进制——不用 DecompressionStream，也不用多拷一次。
  const response = await requestResponse(asset.url, undefined, { timeoutMs: 30_000 })
  if (!response.ok) throw new Error(`加载 ${asset.name} 失败：HTTP ${response.status}`)
  const buffer = await response.arrayBuffer()
  const geometry = parsePackedGeometry(buffer, asset)
  assetCache.set(asset.name, geometry)
  return geometry
}

export const assetOf = (atlas: MeridianAtlas, name: string): MeridianAsset | undefined =>
  atlas.assets.find((item) => item.name === name)

export const clearMeridianCache = () => {
  atlasCache.clear()
  assetCache.clear()
  pathCache = null
}
