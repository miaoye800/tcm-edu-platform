/**
 * 体表中性化（P10）：把男性外生殖器那一块几何**换成中性曲面**，并把落在这块上的穴位贴回去。
 *
 * ## 为什么不是"加条短裤"
 * 任脉下腹三穴（曲骨 0.879 / 中极 0.917 / 关元 0.947）正好在布料会盖住的位置；
 * 遮挡等于用展示规范换教学内容。这里只动几何本身：不新增图层、不挡任何穴位。
 *
 * ## 为什么不是"平滑一下"
 * 第一版用的是受限拉普拉斯（盒选种子 + 环带衰减 + 只重算受影响法线）。实测失败：
 * 拉普拉斯是**扩散**算子，它只会把突起摊成更矮更宽的突起——z 峰值从 0.106 m 只降到
 * 0.093 m（1.3 cm），侧面轮廓仍然一眼可辨是生殖器，而周围腹股沟被一起熨平了。
 * 突起是低频大尺度形状，不是噪声，扩散治不了它。
 *
 * ## 现在的做法：边界固定的调和填充
 * 窗口内的顶点解 Δp = 0，**窗口边界环一个字节不动**。等价于在窗口上绷一张膜：
 * 窗口内被换成边界值的光滑延拓，突起整体消失；边界与周围原曲面严格连续，不会有台阶。
 * 实测 60 次 Gauss–Seidel（超松弛 1.8）即收敛到 0.55 mm 以内，这里取 96 次留余量。
 *
 * ## 为什么不重投影经脉折线
 * 量过了：窗口里 36 个折线点（ST/LR/CV）到新曲面的偏差最大 2.6 mm，仍在描边宽度内。
 * 更重要的是任脉那一段本来就走在会阴侧（z≈0.037），比生殖器突起靠后 6 cm——
 * 它是被突起挡住，而不是画在突起上，抹平后只会从"被挡住"变成"露出来"。
 * 穴位不同：会阴（CV1）的三角形就在被改动的顶点上，必须跟着体表走，见 `reproject`。
 */
import * as THREE from 'three'
import atlasConfig from '../../config/meridian-atlas.json'
import { loadGeometry, type MeridianAcupoint, type MeridianAsset } from '../../services/meridianApi'

const NEUTRAL = atlasConfig.anatomy.neutral_zone

export interface NeutralSurface {
  geometry: THREE.BufferGeometry
  /** 穴位 id → 中性化后体表上的坐标。只有被改到的穴位才在表里，其余用原始坐标。 */
  offsets: Map<string, THREE.Vector3>
}

/**
 * 零长度法线是所有光照异常的根源：`MeshStandardMaterial` 拿到 (0,0,0) 做 `normalize`
 * 得到 NaN，裸渲染时只黑掉自己那几个面，一接 bloom 就被加性混合摊成**整屏透明**。
 * 打包端 `vertex_normals()` 已经修了根，这里再兜一层：坏数据不该在渲染时才爆。
 */
const sanitizeNormals = (normals: Int16Array) => {
  for (let i = 0; i < normals.length; i += 3) {
    if (normals[i] === 0 && normals[i + 1] === 0 && normals[i + 2] === 0) {
      normals[i + 1] = 32767 // 朝上：对躯干类形体是最不突兀的默认值
    }
  }
}

const toGeometry = (packed: NonNullable<Awaited<ReturnType<typeof loadGeometry>>>) => {
  const geometry = new THREE.BufferGeometry()
  // `packed` 是 `loadGeometry` 的缓存，被多个消费者共享；下面要就地改写位置，
  // 所以这里先拷一份。否则同一个资产被再次构建时会把抹平叠加两次。
  const positions = packed.positions.slice()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  // i16 + normalized：磁盘上省一半内存，但读进来必须声明 normalized，
  // 否则法线长度是 32767，光照直接炸白。
  sanitizeNormals(packed.normals)
  geometry.setAttribute('normal', new THREE.BufferAttribute(packed.normals, 3, true))
  geometry.setIndex(new THREE.BufferAttribute(packed.indices, 1))
  geometry.computeBoundingSphere()
  return geometry
}

const neighboursOf = (count: number, index: ArrayLike<number>): number[][] => {
  const neighbours: number[][] = Array.from({ length: count }, () => [])
  for (let i = 0; i < index.length; i += 3) {
    const a = index[i]
    const b = index[i + 1]
    const c = index[i + 2]
    neighbours[a].push(b, c)
    neighbours[b].push(a, c)
    neighbours[c].push(a, b)
  }
  return neighbours
}

const inWindow = (positions: Float32Array, v: number) => {
  const { box } = NEUTRAL
  return (
    Math.abs(positions[v * 3]) <= box.x &&
    positions[v * 3 + 1] >= box.y_min &&
    positions[v * 3 + 1] <= box.y_max &&
    positions[v * 3 + 2] >= box.z_min
  )
}

/**
 * 窗口内解 Δp = 0：逐点向一环邻域均值松弛（Gauss–Seidel + 超松弛）。
 * 边界环（窗口内、但有一环邻居在窗口外）**保持不动**，正是它把膜绷住。
 * 返回被改动的顶点下标（法线重算与穴位回贴都以它为准）。
 *
 * 窗口掩码在迭代开始前算一次就固定：如果边迭代边重算，顶点把邻居拽进窗口后
 * 掩码会自己长大，窗口就再也收不住了。
 */
const relaxToMembrane = (positions: Float32Array, neighbours: number[][], count: number) => {
  const inside = new Uint8Array(count)
  for (let v = 0; v < count; v += 1) if (inWindow(positions, v)) inside[v] = 1

  const inner: number[] = []
  for (let v = 0; v < count; v += 1) {
    if (!inside[v]) continue
    let onEdge = false
    for (const n of neighbours[v]) {
      if (!inside[n]) {
        onEdge = true
        break
      }
    }
    if (!onEdge) inner.push(v)
  }
  for (let step = 0; step < NEUTRAL.iterations; step += 1) {
    for (const v of inner) {
      const list = neighbours[v]
      if (!list.length) continue
      let sx = 0
      let sy = 0
      let sz = 0
      for (const n of list) {
        sx += positions[n * 3]
        sy += positions[n * 3 + 1]
        sz += positions[n * 3 + 2]
      }
      const k = NEUTRAL.relax / list.length
      positions[v * 3] += (sx - positions[v * 3] * list.length) * k
      positions[v * 3 + 1] += (sy - positions[v * 3 + 1] * list.length) * k
      positions[v * 3 + 2] += (sz - positions[v * 3 + 2] * list.length) * k
    }
  }
  return inner
}

/**
 * 位置动了、法线不动，光照上会留一块「平但亮」的异常面，比形状本身更扎眼。
 * 只重算被改动顶点所在三角形：其余顶点的朝向没变，重算反而会把打包端做好的
 * 平滑着色打乱。
 *
 * ⚠ 法线是 **i16 + normalized**。这里必须乘 32767 再取整——直接写 [-1,1] 的浮点
 * 会被 Int16Array 截断成 0，而零长度法线正是「整屏透明」的根（见 sanitizeNormals）。
 */
const rebuildNormals = (geometry: THREE.BufferGeometry, moved: number[]) => {
  const position = geometry.getAttribute('position') as THREE.BufferAttribute
  const index = geometry.getIndex()
  const normal = geometry.getAttribute('normal') as THREE.BufferAttribute
  if (!index || !normal) return
  const positions = position.array as Float32Array
  const normals = normal.array as Int16Array
  const idx = index.array as ArrayLike<number>
  const touched = new Set(moved)
  const acc = new Map<number, THREE.Vector3>()
  const a = new THREE.Vector3()
  const b = new THREE.Vector3()
  const c = new THREE.Vector3()
  const e1 = new THREE.Vector3()
  const e2 = new THREE.Vector3()
  for (let i = 0; i < idx.length; i += 3) {
    const ia = idx[i]
    const ib = idx[i + 1]
    const ic = idx[i + 2]
    if (!touched.has(ia) && !touched.has(ib) && !touched.has(ic)) continue
    a.fromArray(positions, ia * 3)
    b.fromArray(positions, ib * 3)
    c.fromArray(positions, ic * 3)
    e1.subVectors(b, a)
    e2.subVectors(c, a)
    const face = e1.clone().cross(e2) // 未归一化：面积大的面权重自然大
    for (const v of [ia, ib, ic]) {
      const item = acc.get(v)
      if (item) item.add(face)
      else acc.set(v, face.clone())
    }
  }
  for (const [v, sum] of acc) {
    const len = sum.length()
    if (len < 1e-6) continue // 退化三角形：NaN 进了帧缓冲会被 bloom 摊满整屏
    normals[v * 3] = Math.round((sum.x / len) * 32767)
    normals[v * 3 + 1] = Math.round((sum.y / len) * 32767)
    normals[v * 3 + 2] = Math.round((sum.z / len) * 32767)
  }
  normal.needsUpdate = true
  position.needsUpdate = true
}

/** 点在三角形上的重心坐标（先投到三角形平面，允许点略微离面）。 */
const barycentric = (a: THREE.Vector3, b: THREE.Vector3, c: THREE.Vector3, p: THREE.Vector3) => {
  const v0 = b.clone().sub(a)
  const v1 = c.clone().sub(a)
  const v2 = p.clone().sub(a)
  const d00 = v0.dot(v0)
  const d01 = v0.dot(v1)
  const d11 = v1.dot(v1)
  const d20 = v2.dot(v0)
  const d21 = v2.dot(v1)
  const den = d00 * d11 - d01 * d01
  if (Math.abs(den) < 1e-20) return [1, 0, 0] as const
  const v = (d11 * d20 - d01 * d21) / den
  const w = (d00 * d21 - d01 * d20) / den
  return [1 - v - w, v, w] as const
}

/**
 * 穴位回贴。导出的 `skin_tri` 是**原始**几何上的三角形下标，所以重心坐标要用
 * 改动前的坐标算，再套到改动后的三个顶点上——这样穴位严格沿体表走，不会浮在皮外，
 * 也不会陷进体内。实测受影响的只有会阴（CV1），位移 9.5 mm；其余骨盆区穴位
 * （曲骨 / 急脉 / 冲门 / 长强）的三角形落在边界环上，本来就没动。
 */
const reproject = (
  acupoints: MeridianAcupoint[],
  original: Float32Array,
  current: Float32Array,
  index: ArrayLike<number>,
  moved: number[],
) => {
  const offsets = new Map<string, THREE.Vector3>()
  const touched = new Set(moved)
  const a = new THREE.Vector3()
  const b = new THREE.Vector3()
  const c = new THREE.Vector3()
  const point = new THREE.Vector3()
  for (const item of acupoints) {
    const base = item.skin_tri * 3
    const ia = index[base]
    const ib = index[base + 1]
    const ic = index[base + 2]
    if (!touched.has(ia) && !touched.has(ib) && !touched.has(ic)) continue
    a.fromArray(original, ia * 3)
    b.fromArray(original, ib * 3)
    c.fromArray(original, ic * 3)
    point.set(item.x3d, item.y3d, item.z3d)
    const [wa, wb, wc] = barycentric(a, b, c, point)
    const next = new THREE.Vector3()
      .addScaledVector(new THREE.Vector3().fromArray(current, ia * 3), wa)
      .addScaledVector(new THREE.Vector3().fromArray(current, ib * 3), wb)
      .addScaledVector(new THREE.Vector3().fromArray(current, ic * 3), wc)
    offsets.set(item.id, next)
  }
  return offsets
}

const build = async (asset: MeridianAsset, acupoints: MeridianAcupoint[]): Promise<NeutralSurface> => {
  const geometry = toGeometry(await loadGeometry(asset))
  const position = geometry.getAttribute('position') as THREE.BufferAttribute
  const index = geometry.getIndex()
  if (!NEUTRAL.enabled || !index) return { geometry, offsets: new Map() }

  const positions = position.array as Float32Array
  const count = position.count
  // 重心坐标要在**改动前**的坐标上算，先留一份副本（9 万个数，一次性开销）
  const original = positions.slice()
  const neighbours = neighboursOf(count, index.array as ArrayLike<number>)
  const moved = relaxToMembrane(positions, neighbours, count)
  if (!moved.length) return { geometry, offsets: new Map() }

  rebuildNormals(geometry, moved)
  geometry.computeBoundingSphere()
  return { geometry, offsets: reproject(acupoints, original, positions, index.array as ArrayLike<number>, moved) }
}

/**
 * 皮肤只有一份（约 1 MB），但有两个消费者：BodyMesh 要它画皮，AcupointNodes 要穴位偏移。
 * 按资产 sha256 去重，两个组件各自 `await` 同一个 Promise，变形只跑一次。
 */
let cached: { key: string; task: Promise<NeutralSurface> } | null = null

export const neutralSurface = (asset: MeridianAsset, acupoints: MeridianAcupoint[]): Promise<NeutralSurface> => {
  const key = `${asset.sha256}:${acupoints.length}`
  if (cached?.key === key) return cached.task
  const task = build(asset, acupoints)
  cached = { key, task }
  return task
}

export const clearNeutralSurface = () => {
  cached = null
}
