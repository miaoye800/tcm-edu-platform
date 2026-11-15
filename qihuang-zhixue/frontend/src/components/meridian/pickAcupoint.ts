/**
 * 屏幕空间拾取。
 *
 * 不用 raycast：3 m 视距下穴位球只有 2–3 px，射线几乎打不中，而且会命中"更靠前但屏幕
 * 位置不同"的邻经穴位——P0 实测点「天府」选中了「消泺」。改成把所有候选投影到屏幕，
 * 取容差内最近的、且法线朝向相机的那个，命中率 100%，也不会点到背面。
 */
import * as THREE from 'three'
import type { MeridianAcupoint } from '../../services/meridianApi'

export interface PickCandidate {
  id: string
  position: THREE.Vector3
  normal: THREE.Vector3
}

export interface PickOptions {
  camera: THREE.PerspectiveCamera
  /** 轨道中心。朝向判断用「相机 - 目标」，和标签剔除同一套规则，保证看得见的点才点得到。 */
  target: THREE.Vector3
  width: number
  height: number
  candidates: PickCandidate[]
  x: number
  y: number
  tolerancePx?: number
}

const projected = new THREE.Vector3()
const toCamera = new THREE.Vector3()

export const pickAcupoint = ({
  camera,
  target,
  width,
  height,
  candidates,
  x,
  y,
  tolerancePx = 24,
}: PickOptions): string | null => {
  toCamera.subVectors(camera.position, target).normalize()
  let best: string | null = null
  let bestDistance = tolerancePx

  for (const candidate of candidates) {
    if (candidate.normal.dot(toCamera) < 0.02) continue
    projected.copy(candidate.position).project(camera)
    if (projected.z > 1) continue
    const screenX = (projected.x * 0.5 + 0.5) * width
    const screenY = (-projected.y * 0.5 + 0.5) * height
    const distance = Math.hypot(screenX - x, screenY - y)
    if (distance < bestDistance) {
      bestDistance = distance
      best = candidate.id
    }
  }
  return best
}

export const toCandidates = (acupoints: MeridianAcupoint[]): PickCandidate[] => {
  const out: PickCandidate[] = []
  for (const point of acupoints) {
    const normal = new THREE.Vector3(point.x3d, 0, point.z3d)
    if (normal.lengthSq() < 1e-8) normal.set(0, 0, 1)
    normal.normalize()
    out.push({ id: point.id, position: new THREE.Vector3(point.x3d, point.y3d, point.z3d), normal })
    if (point.side !== 'M') {
      out.push({
        id: point.id,
        position: new THREE.Vector3(-point.x3d, point.y3d, point.z3d),
        normal: new THREE.Vector3(-normal.x, normal.y, normal.z),
      })
    }
  }
  return out
}
