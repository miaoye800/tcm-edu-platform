"""把 BodyParts3D 的原生资产裁成「一张图能加载得动」的自有资产。

为什么必须裁
------------
上游 `JICio/tcm-3d <https://github.com/JICio/tcm-3d>`_ 是 15 个 chunk × 2.2 MB(gz) ≈ 33 MB，
按 ``import`` 全量加载，首屏在移动端直接废（P0 实测：835k 面 + bloom 在纯 CPU 光栅化下 3–5 fps）。
而穴位只吸附在**皮肤**一块（``FJ2810``，22 902 顶点 / 44 744 面）上，
所以首屏只需要皮肤，其余系统按需。

两个许可必须分开记
------------------
* **代码与穴位坐标是 MIT**（© JIC contributors）。坐标是他们人工标定并吸附到
  BodyParts3D 表面的，不因为参照了 CC BY 模型就变成 CC BY。
* **``public/models/**`` 是 CC BY 4.0**（© DBCLS BodyParts3D 4.0），**不得按 MIT 再许可**。
  我们做了改动（只抽皮肤、重新打包、肌肉做体素简化），所以 ATTRIBUTION 里必须写明改动。

坐标是"工程种子"，不是临床校核过的定位——这一点在上游 NOTICE 里也写着。
本模块只处理几何，不声称精度；精度边界在 ``crosscheck.py`` 里量化。

用法::

    python -m app.meridian.asset --status          # 看资产齐不齐、多大
    python -m app.meridian.asset --build           # 下载（若缺）→ 裁剪 → 打包
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

#: 上游提交。**必须钉死 commit**：换成 main 之后几何一变，362 个穴位全部要重新吸附。
SOURCE_COMMIT = "e8b9ad1c17e67a25e94406100e3fb5c296c24f7a"
SOURCE_REPO = "JICio/tcm-3d"
RAW = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_COMMIT}"

#: 首屏只加载皮肤；肌肉层是"解剖写实"方案的第二层，按需加载。
SKIN_PART = "FJ2810"

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "data" / "sources" / "tcm3d"
OUT_DIR = ROOT / "data" / "meridian"

#: 几何版本 = 上游 commit + 我们的打包参数。换模型 = 全部重新吸附，所以版本必须落库。
GEOMETRY_VERSION = f"bodyparts3d4@tcm3d-{SOURCE_COMMIT[:8]}-skin1"

LICENSES: tuple[dict[str, str], ...] = (
    {
        "asset": "coordinates",
        "license": "MIT",
        "holder": "© JIC contributors",
        "note": "穴位体表坐标由 JIC 人工标定并吸附到 BodyParts3D 表面；未经过逐穴临床校核。",
    },
    {
        "asset": "geometry",
        "license": "CC BY 4.0",
        "holder": "© The Database Center for Life Science (BodyParts3D 4.0)",
        "url": "https://creativecommons.org/licenses/by/4.0/",
        "changes": "抽取 integumentary 系统（FJ2810）单独打包；肌肉层做体素简化；坐标轴与单位沿用上游（米 / Y 轴向上）。",
    },
)

ATTRIBUTION = (
    "人体几何来自 BodyParts3D 4.0（© DBCLS，CC BY 4.0），"
    f"经由 {SOURCE_REPO}@{SOURCE_COMMIT[:8]} 适配与吸附；"
    "本项目仅抽取皮肤单件并重新打包，肌肉层经体素简化。"
    "穴位坐标为 JIC contributors（MIT）人工标定，未经临床校核。"
)


@dataclass(frozen=True)
class Part:
    id: str
    name: str
    system: str
    chunk: int
    positions: int
    normals: int
    indices: int
    vertex_count: int
    index_count: int
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]]


def _fetch(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    print(f"  下载 {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - 固定白名单 URL
        data = resp.read()
    dest.write_bytes(data)
    return dest


def load_atlas(path: Path | None = None) -> dict[str, Any]:
    p = _fetch(f"{RAW}/public/models/atlas.json", path or (SOURCE_DIR / "atlas.json"))
    return json.loads(p.read_text(encoding="utf-8"))


def load_surface(path: Path | None = None) -> dict[str, Any]:
    p = _fetch(f"{RAW}/app/meridian-surface.json", path or (SOURCE_DIR / "meridian-surface.json"))
    return json.loads(p.read_text(encoding="utf-8"))


def find_part(atlas: dict[str, Any], part_id: str) -> Part:
    for raw in atlas["parts"]:
        if raw["id"] == part_id:
            return Part(
                id=raw["id"],
                name=raw.get("name", ""),
                system=raw.get("system", ""),
                chunk=int(raw["chunk"]),
                positions=int(raw["positions"]),
                normals=int(raw["normals"]),
                indices=int(raw["indices"]),
                vertex_count=int(raw["vertexCount"]),
                index_count=int(raw["indexCount"]),
                bounds=(tuple(raw["bounds"][0]), tuple(raw["bounds"][1])),  # type: ignore[arg-type]
            )
    raise KeyError(f"atlas 里没有 {part_id!r}；上游换过模型？先核对 SOURCE_COMMIT")


def chunk_bytes(atlas: dict[str, Any], chunk: int) -> bytes:
    chunk_meta = atlas["chunks"][chunk]
    name = Path(chunk_meta["url"]).name
    gz = _fetch(f"{RAW}/public/models/{name}.gz", SOURCE_DIR / "chunks" / f"{name}.gz")
    raw = gzip.decompress(gz.read_bytes())
    if len(raw) != int(chunk_meta["bytes"]):
        raise ValueError(
            f"{name} 解压后 {len(raw)} 字节，atlas 声明 {chunk_meta['bytes']}；"
            "上游可能重新生成过资产，先核对 commit"
        )
    return raw


def read_part(atlas: dict[str, Any], part: Part, raw: bytes | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """按上游布局取值：``f32 positions`` / ``i16 normals`` / ``u32 indices``，各自 4 字节对齐。"""

    if raw is None:
        raw = chunk_bytes(atlas, part.chunk)
    buf = np.frombuffer(raw, dtype=np.uint8)

    def take(offset: int, count: int) -> np.ndarray:
        end = offset + count
        if end > buf.size:
            raise ValueError(f"{part.id}: 偏移 {offset}+{count} 超出 chunk（{buf.size}）")
        return np.frombuffer(buf[offset:end].tobytes(), dtype=np.uint8)

    pos = take(part.positions, part.vertex_count * 12).view("<f4").reshape(-1, 3)
    nrm = take(part.normals, part.vertex_count * 6).view("<i2").reshape(-1, 3)
    idx = take(part.indices, part.index_count * 4).view("<u4").reshape(-1, 3)
    if idx.max(initial=0) >= part.vertex_count:
        raise ValueError(f"{part.id}: 索引越界（max={int(idx.max())} ≥ {part.vertex_count}）")
    return pos.astype(np.float32), nrm.astype(np.int16), idx.astype(np.uint32)


# --------------------------------------------------------------------------- 打包


def part_hash(part_id: str) -> float:
    """把 part id 映射到 ``[0, 1)``，作为"这块肌肉"的稳定指纹。

    P0 的验尸结论：BodyParts3D 没有 UV，肌理只能程序化；而"一条条肌肉"的观感
    来自**逐块明度微差**，不是噪声。合并后一旦丢了分块标识，整层就塌成一坨肉色。
    这里把指纹算在服务端写进二进制，前端不必再拿 part 名字表对一遍。
    """

    h = 2166136261
    for ch in part_id:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return (h % 100000) / 100000.0


def pack_bin(
    pos: np.ndarray,
    nrm: np.ndarray,
    idx: np.ndarray,
    part: np.ndarray | None = None,
) -> bytes:
    """与上游同构的二进制：前端可以不换加载器直接读。

    末尾追加可选的 ``part``（每顶点 f32，值域 ``[0, 1)``）。这是**向后兼容的扩展**：
    不带 part 时字节流与上游完全一致，老测试照旧逐字节比对。
    """

    out = bytearray()
    out += np.ascontiguousarray(pos, dtype="<f4").tobytes()
    while len(out) % 4:
        out.append(0)
    out += np.ascontiguousarray(nrm, dtype="<i2").tobytes()
    while len(out) % 4:
        out.append(0)
    out += np.ascontiguousarray(idx, dtype="<u4").tobytes()
    if part is not None:
        while len(out) % 4:
            out.append(0)
        out += np.ascontiguousarray(part, dtype="<f4").tobytes()
    return bytes(out)


def vertex_normals(pos: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """简化后法线必须重算——原法线属于被合并掉的顶点，留着会让光照斑驳。

    **零长度法线必须给一个方向，不能原样留 (0,0,0)**。体素合并后会出现三类
    法线互相抵消的顶点（薄片、尖角、孤立面），这些顶点若保留零法线，
    前端 `normalize` 会算出 NaN：裸渲染只是那几个三角面黑掉，一旦叠上 bloom，
    NaN 会顺着半浮点模糊链加性摊满整帧，表现为整屏透明（实测踩过）。
    这里用「由形体中心指向该点」当兜底方向，比随便给个 (0,1,0) 更贴形。
    """

    tri = pos[idx]
    face = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    acc = np.zeros_like(pos, dtype=np.float64)
    for k in range(3):
        np.add.at(acc, idx[:, k], face)
    norm = np.linalg.norm(acc, axis=1)
    degenerate = norm < 1e-12
    norm[degenerate] = 1.0
    unit = acc / norm[:, None]
    if degenerate.any():
        outward = pos[degenerate] - pos.mean(axis=0, keepdims=True)
        out_norm = np.linalg.norm(outward, axis=1)
        out_norm[out_norm < 1e-12] = 1.0
        fallback = outward / out_norm[:, None]
        fallback[out_norm < 1e-12] = (0.0, 1.0, 0.0)
        unit[degenerate] = fallback
    return np.clip(np.round(unit * 32767), -32767, 32767).astype(np.int16)



def voxel_cluster(pos: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """体素聚类：返回（聚类后的顶点, 原顶点 → 新顶点 的映射）。

    把 ``inverse`` 交出去，是为了让"分块标识"能跟着顶点一起搬家——
    合并顶点时 part 也要投票，否则简化完就分不清哪块肌肉是哪块了。
    """

    if cell <= 0:
        return pos, np.arange(pos.shape[0], dtype=np.int64)
    keys = np.floor(pos.astype(np.float64) / cell).astype(np.int64)
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    counts = np.bincount(inverse)
    acc = np.zeros((counts.size, 3), dtype=np.float64)
    for axis in range(3):
        acc[:, axis] = np.bincount(inverse, weights=pos[:, axis].astype(np.float64))
    return (acc / counts[:, None]).astype(np.float32), inverse.astype(np.int64)


def voxel_simplify(
    pos: np.ndarray, idx: np.ndarray, cell: float
) -> tuple[np.ndarray, np.ndarray]:
    """体素聚类减面。**只能用在穴位不落的层**（肌肉 / 骨骼）。

    皮肤绝不能用：362 个穴位存的是皮肤三角面索引（``skin_tri``），
    合并顶点会让索引整体失效，等于把全部坐标作废。
    """

    new_pos, inverse = voxel_cluster(pos, cell)
    new_idx = inverse[idx].astype(np.uint32)
    keep = (
        (new_idx[:, 0] != new_idx[:, 1])
        & (new_idx[:, 1] != new_idx[:, 2])
        & (new_idx[:, 0] != new_idx[:, 2])
    )
    return new_pos, new_idx[keep]


def carry_part(part_index: np.ndarray, inverse: np.ndarray) -> np.ndarray:
    """把逐顶点的 part 序号搬到聚类后的顶点上。

    取该体素里**第一个**原顶点所属的 part，而不是做权重投票：7.5 mm 体素下
    相邻肌肉本来就不会挤进同一格，投票只是在为不存在的情况付复杂度。
    """

    n_clusters = int(inverse.max()) + 1
    first = np.full(n_clusters, -1, dtype=np.int64)
    # 倒着写一遍：同一个簇反复被覆盖，最后落下的是**最小**的那个下标。
    first[inverse[::-1]] = np.arange(inverse.shape[0] - 1, -1, -1, dtype=np.int64)
    return part_index[first]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_asset(
    name: str,
    pos: np.ndarray,
    nrm: np.ndarray,
    idx: np.ndarray,
    out_dir: Path = OUT_DIR,
    *,
    part: np.ndarray | None = None,
    part_ids: Iterable[str] = (),
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = pack_bin(pos, nrm, idx, part)
    gz = gzip.compress(payload, compresslevel=9, mtime=0)
    (out_dir / f"{name}.bin").write_bytes(payload)
    (out_dir / f"{name}.bin.gz").write_bytes(gz)
    record: dict[str, Any] = {
        "name": name,
        "bytes": len(payload),
        "gzip_bytes": len(gz),
        "sha256": _sha256(payload),
        "vertex_count": int(pos.shape[0]),
        "triangles": int(idx.shape[0]),
        "attributes": ["position", "normal", "index"] + (["part"] if part is not None else []),
        # 前端按"三个数组紧挨着、各自 4 字节对齐"推算偏移；part 段把偏移显式写出来，
        # 省得哪天 alignment 改了一个字节，前后端各算一套。
        "part_offset": (len(payload) - int(np.asarray(part, dtype="<f4").nbytes)) if part is not None else None,
        "part_count": len(list(part_ids)) if part is not None else 0,
        "url": f"/api/meridian/asset/{name}.bin.gz",
    }
    return record


def merge_parts(
    atlas: dict[str, Any], parts: Iterable[Part], raw_by_chunk: dict[int, bytes]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """合并同系统部件，并给每个顶点盖上 part 序号。

    序号（而不是指纹）是为了让简化后再映射成指纹——顶点一合并，指纹没法投票，
    序号可以。
    """

    pos_all: list[np.ndarray] = []
    nrm_all: list[np.ndarray] = []
    idx_all: list[np.ndarray] = []
    part_all: list[np.ndarray] = []
    base = 0
    for order, part in enumerate(parts):
        pos, nrm, idx = read_part(atlas, part, raw_by_chunk.get(part.chunk))
        pos_all.append(pos)
        nrm_all.append(nrm)
        idx_all.append(idx + base)
        part_all.append(np.full(pos.shape[0], order, dtype=np.int64))
        base += pos.shape[0]
    if not pos_all:
        raise ValueError("没有可合并的 part")
    return (
        np.concatenate(pos_all).astype(np.float32),
        np.concatenate(nrm_all).astype(np.int16),
        np.concatenate(idx_all).astype(np.uint32),
        np.concatenate(part_all),
    )


# --------------------------------------------------------------------------- 构建


def build(
    out_dir: Path = OUT_DIR,
    *,
    with_muscle: bool = True,
    muscle_cell: float = 0.0075,
    with_skeleton: bool = True,
    bone_cell: float = 0.004,
) -> dict[str, Any]:
    """产出皮肤（原样）+ 肌肉（体素简化）+ 骨骼（可选）+ manifest。"""

    atlas = load_atlas()
    surface = load_surface()
    parts = [find_part(atlas, SKIN_PART)]
    if with_muscle:
        parts += _system_parts(atlas, "muscular")
    if with_skeleton:
        parts += _system_parts(atlas, "skeletal")

    raw_by_chunk: dict[int, bytes] = {}
    for p in parts:
        if p.chunk not in raw_by_chunk:
            raw_by_chunk[p.chunk] = chunk_bytes(atlas, p.chunk)

    assets: list[dict[str, Any]] = []

    skin = parts[0]
    skin_pos, skin_nrm, skin_idx = read_part(atlas, skin, raw_by_chunk[skin.chunk])
    assets.append(
        write_asset(
            "skin",
            skin_pos,
            skin_nrm,
            skin_idx,
            out_dir,
            # 皮肤只有一个部件，指纹仍是满值域：前端两种层走同一套着色器，不分叉。
            part=np.full(skin_pos.shape[0], part_hash(skin.id), dtype=np.float32),
            part_ids=[skin.id],
        )
    )
    print(
        f"  皮肤 {skin_pos.shape[0]} 顶点 / {skin_idx.shape[0]} 面 → "
        f"{assets[-1]['gzip_bytes'] / 1e6:.2f} MB(gz)"
    )

    if with_muscle:
        muscle_parts = _system_parts(atlas, "muscular")
        m_pos, m_nrm, m_idx, m_part = merge_parts(atlas, muscle_parts, raw_by_chunk)
        s_pos, inverse = voxel_cluster(m_pos, muscle_cell)
        s_idx = inverse[m_idx].astype(np.uint32)
        keep = (
            (s_idx[:, 0] != s_idx[:, 1])
            & (s_idx[:, 1] != s_idx[:, 2])
            & (s_idx[:, 0] != s_idx[:, 2])
        )
        s_idx = s_idx[keep]
        s_nrm = vertex_normals(s_pos, s_idx)
        hashes = np.array([part_hash(p.id) for p in muscle_parts], dtype=np.float32)
        s_part = hashes[carry_part(m_part, inverse)]
        assets.append(
            write_asset(
                "muscle", s_pos, s_nrm, s_idx, out_dir,
                part=s_part, part_ids=[p.id for p in muscle_parts],
            )
        )
        print(
            f"  肌肉 {m_idx.shape[0]} → {s_idx.shape[0]} 面（体素 {muscle_cell*1000:.1f} mm）"
            f" → {assets[-1]['gzip_bytes'] / 1e6:.2f} MB(gz)"
        )

    if with_skeleton:
        bone_parts = _system_parts(atlas, "skeletal")
        b_pos, _b_nrm, b_idx, b_part = merge_parts(atlas, bone_parts, raw_by_chunk)
        # 骨骼比肌肉细得多，7.5 mm 会把它磨成一根根棍子；4 mm 是保住骨面起伏的下限。
        s_pos, inverse = voxel_cluster(b_pos, bone_cell)
        s_idx = inverse[b_idx].astype(np.uint32)
        keep = (
            (s_idx[:, 0] != s_idx[:, 1])
            & (s_idx[:, 1] != s_idx[:, 2])
            & (s_idx[:, 0] != s_idx[:, 2])
        )
        s_idx = s_idx[keep]
        s_nrm = vertex_normals(s_pos, s_idx)
        hashes = np.array([part_hash(p.id) for p in bone_parts], dtype=np.float32)
        s_part = hashes[carry_part(b_part, inverse)]
        assets.append(
            write_asset(
                "skeleton", s_pos, s_nrm, s_idx, out_dir,
                part=s_part, part_ids=[p.id for p in bone_parts],
            )
        )
        print(
            f"  骨骼 {b_idx.shape[0]} → {s_idx.shape[0]} 面（体素 {bone_cell*1000:.1f} mm）"
            f" → {assets[-1]['gzip_bytes'] / 1e6:.2f} MB(gz)"
        )

    manifest = {
        "geometry_version": GEOMETRY_VERSION,
        "source": {
            "repo": SOURCE_REPO,
            "commit": SOURCE_COMMIT,
            "atlas_url": f"{RAW}/public/models/atlas.json",
            "surface_url": f"{RAW}/app/meridian-surface.json",
        },
        "licenses": list(LICENSES),
        "attribution": ATTRIBUTION,
        "assets": assets,
        "points": len(surface["points"]),
        "paths": len(surface["paths"]),
        "first_screen_bytes": assets[0]["gzip_bytes"],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _system_parts(atlas: dict[str, Any], system: str) -> list[Part]:
    return [
        find_part(atlas, raw["id"])
        for raw in atlas["parts"]
        if raw.get("system") == system
    ]


def status(out_dir: Path = OUT_DIR) -> int:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        print("还没有资产。跑 `python -m app.meridian.asset --build`。")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    total = 0
    for asset in manifest["assets"]:
        total += int(asset["gzip_bytes"])
        print(
            f"{asset['name']:<8} {asset['triangles']:>8} 面  "
            f"{asset['gzip_bytes']/1e6:6.2f} MB(gz)  {asset['url']}"
        )
    print(
        f"{'首屏':<8} {manifest['assets'][0]['triangles']:>8} 面  "
        f"{manifest['first_screen_bytes']/1e6:6.2f} MB(gz)  "
        f"（门禁 3.00 MB）{'✅' if manifest['first_screen_bytes'] <= 3_000_000 else '❌'}"
    )
    print(f"合计 {total/1e6:.2f} MB(gz) · {manifest['geometry_version']}")
    print(f"坐标点 {manifest['points']} · 经脉路径 {manifest['paths']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="BodyParts3D 资产裁剪与打包")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-muscle", action="store_true", help="只打包皮肤（最小资产）")
    ap.add_argument("--no-skeleton", action="store_true", help="不打包骨骼层")
    ap.add_argument("--muscle-cell", type=float, default=0.0075, help="肌肉体素边长（米）")
    ap.add_argument("--bone-cell", type=float, default=0.004, help="骨骼体素边长（米）")
    args = ap.parse_args(argv)

    if args.status or not args.build:
        return status()
    manifest = build(
        with_muscle=not args.no_muscle,
        muscle_cell=args.muscle_cell,
        with_skeleton=not args.no_skeleton,
        bone_cell=args.bone_cell,
    )
    print(json.dumps({k: manifest[k] for k in ("geometry_version", "first_screen_bytes")}, ensure_ascii=False))
    return status()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
