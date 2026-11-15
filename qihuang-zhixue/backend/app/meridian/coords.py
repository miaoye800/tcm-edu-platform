"""把 362 个穴位钉到皮肤三角面上：坐标 + 重心坐标。

为什么不能只存 xyz
------------------
穴位不是悬在空中的点，它属于"某块皮肤的某处"。换模型、换体型、换 LOD 皮肤时，
xyz 会整体漂移；而 ``(skin_tri, skin_bary)`` 能跟着新几何重算落点。
这条在计划里被列为**不可逆决策**——现在省一次几十行的计算，以后就是 362 个点全错位。

本模块做的事
------------
1. 读上游 ``meridian-surface.json`` 的 670 个点（361 穴 × 左 / 部分 × 右）；
2. 在**我们自己打包的皮肤**（``asset.py`` 产出）上找最近三角面，算重心坐标；
3. 落库到 ``meridian_acupoint``（``x3d/y3d/z3d + skin_tri + skin_bary``）；
4. 量化"点离皮肤多远"，把超过阈值的列出来——**不假装精确**。

关于 ``verified``
-----------------
一律写 0。上游自己声明「未经过逐穴临床校核」，我们也没有做人工校核，
置 1 等于在说"这一穴我核过了"，那是假话。前端按 0 画虚线环，
等真做完了再逐穴置 1（计划里的肺经 11 穴 + 任督 52 穴）。
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.meridian import store
from app.meridian.asset import OUT_DIR, load_surface, read_part, find_part, SKIN_PART, chunk_bytes, load_atlas

#: 点离皮肤超过这个距离就进 review。0.002 m = 2 mm，在 1.72 m 身高上约 0.12%。
#: 下游软化骨盆表面后，该区域的点会离原始皮肤 1–2 cm，这是已知的、可解释的偏差。
DISTANCE_WARN_M = 0.002


def closest_on_triangle(
    p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """点 → 三角形最近点 + 重心坐标（Ericson, Real-Time Collision Detection 5.1.5）。

    返回的重心坐标恒和为 1；落在三角形外时会出现负分量，但那是**面内**最近点的
    正确表示（边上的点会有一项为 0），所以不做截断——截断会让重建位置偏离真实最近点。
    """

    ab = b - a
    ac = c - a
    ap = p - a
    d1 = ab @ ap
    d2 = ac @ ap
    if d1 <= 0 and d2 <= 0:
        return a, np.array([1.0, 0.0, 0.0])
    bp = p - b
    d3 = ab @ bp
    d4 = ac @ bp
    if d3 >= 0 and d4 <= d3:
        return b, np.array([0.0, 1.0, 0.0])
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return a + v * ab, np.array([1 - v, v, 0.0])
    cp = p - c
    d5 = ab @ cp
    d6 = ac @ cp
    if d6 >= 0 and d5 <= d6:
        return c, np.array([0.0, 0.0, 1.0])
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return a + w * ac, np.array([1 - w, 0.0, w])
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return b + w * (c - b), np.array([0.0, 1 - w, w])
    den = 1.0 / (va + vb + vc)
    v = vb * den
    w = vc * den
    return a + ab * v + ac * w, np.array([1 - v - w, v, w])


def skin_geometry() -> tuple[np.ndarray, np.ndarray]:
    """读**我们自己打包的**皮肤，而不是上游 chunk 里那份。

    为什么从 ``data/meridian`` 读而不是回上游取：``skin_tri`` 是我们打包后几何里的
    面索引。一旦有人给皮肤加了简化（哪怕只是合并两个重合顶点），索引整体失效，
    362 个穴位同时错位——而这个错在浏览器里看起来只是"点偏了一点"。
    读同一份文件是唯一能保证"索引属于这份几何"的办法，顺带也不用再下载 33 MB。

    打包文件不存在时退回上游原始 part（此时索引仍一致，因为打包不改编序）。
    """

    packed = OUT_DIR / "skin.bin"
    gz = OUT_DIR / "skin.bin.gz"
    manifest_path = OUT_DIR / "manifest.json"
    if not packed.exists() and gz.exists():
        packed.write_bytes(gzip.decompress(gz.read_bytes()))
    if packed.exists() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        skin = next((a for a in manifest["assets"] if a["name"] == "skin"), None)
        if skin is not None:
            n = int(skin["vertex_count"])
            m = int(skin["triangles"])
            part_count = int(skin.get("part_count") or 0)
            raw = packed.read_bytes()
            # 与 ``asset.pack_bin`` 的布局逐段对齐：pos → 4B 补位 → nrm → 4B 补位
            # → idx → 4B 补位 → part。part 是后加的扩展段，老资产里没有（part_count=0），
            # 所以这里按 manifest 声明的段数算，别写死成"只有三段的时代"。
            off_nrm = n * 12
            off_idx = off_nrm + n * 6
            off_idx += (-off_idx) % 4
            off_part = off_idx + m * 12
            # manifest 里的 ``part_count`` 是**不同分块的数量**，不是数组长度；
            # part 段是逐顶点一个 f32，所以长度按顶点数算。
            need = off_part + (n * 4 if part_count else 0)
            if len(raw) != need:
                raise ValueError(
                    f"skin.bin 体积 {len(raw)} 与 manifest 声明不符（应为 {need}）；"
                    "皮肤几何一改，362 个穴位就全部要重新吸附"
                )
            pos = np.frombuffer(raw[:n * 12], dtype="<f4").reshape(-1, 3)
            idx = np.frombuffer(raw[off_idx:off_part], dtype="<u4").reshape(-1, 3)
            return pos.astype(np.float64), idx

    atlas = load_atlas()
    skin_part = find_part(atlas, SKIN_PART)
    raw_chunk = chunk_bytes(atlas, skin_part.chunk)
    pos, _nrm, idx = read_part(atlas, skin_part, raw_chunk)
    return pos.astype(np.float64), idx


def resolve_point(p: np.ndarray, pos: np.ndarray, idx: np.ndarray) -> tuple[int, np.ndarray, float]:
    """返回 (三角面索引, 重心坐标, 到面的距离)。"""

    verts = pos[idx]
    centroids = verts.mean(axis=1)
    # 先按重心距离粗筛 24 个候选：皮肤 4.4 万面，逐面精算也能跑，但没必要。
    near = np.argsort(((centroids - p) ** 2).sum(axis=1))[:24]
    best: tuple[int, np.ndarray, float] | None = None
    for f in near:
        tri = verts[f]
        cp, bary = closest_on_triangle(p, tri[0], tri[1], tri[2])
        dist = float(np.linalg.norm(cp - p))
        if best is None or dist < best[2]:
            best = (int(f), bary, dist)
    if best is None:
        raise ValueError("皮肤没有三角面")
    return best


def plan(
    db_path: Path | str, surface: dict[str, Any], pos: np.ndarray, idx: np.ndarray
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """算出每个穴位要写的坐标。纯计算，不写库。

    返回 (要更新的行, 需要人工复核的行, 问题列表)。
    """

    conn = store.connect(Path(db_path))
    try:
        rows = conn.execute("SELECT id, code, side FROM meridian_acupoint").fetchall()
    finally:
        conn.close()
    by_id = {str(r[0]): (str(r[1]), str(r[2])) for r in rows}

    updates: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    problems: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for pt in surface["points"]:
        code, _, side = pt["key"].partition(":")
        groups.setdefault(code, []).append({"side": int(side), **pt})

    for code, items in groups.items():
        if code not in by_id:
            problems.append(f"{code}：穴位名录里没有这一穴（上游多出来的？）")
            continue
        # 取 x>0 的那一侧作为代表（约定「左 = +x」），**不按上游的 side 标记取**：
        # 太溪 KI3 的 ``:1`` 实际落在 x=-0.076、``:-1`` 落在 x=+0.075，标记是反的。
        # 按标记取会让这一个穴跑到对侧腿上——图上就是一条经突然横穿身体。
        left = next((x for x in items if float(x["target"]["position"][0]) > 0), None)
        flipped = left is not None and left["side"] == -1
        if left is None:
            left = items[0]  # 中线穴（任督）x 恒为 0，只有一组点
        p = np.asarray(left["target"]["position"], dtype=np.float64)
        tri, bary, dist = resolve_point(p, pos, idx)
        # 存**投影点**而不是上游给的原点。两者在皮肤上重合（中位距离 0.000 mm），
        # 只在骨盆区差 1–2 cm——那是上游把骨盆表面软化过、而我们用的是未软化几何。
        # 如果存原点，xyz 与 (tri, bary) 就对不上：重建出来是投影点，坐标是原始点，
        # 「换模型时靠重心坐标重算」这条保证直接失效。
        surface_point = pos[idx[tri][0]] * bary[0] + pos[idx[tri][1]] * bary[1] + pos[idx[tri][2]] * bary[2]
        # 中线穴（任督）投影到三角面后会偏 0.1–0.5 mm。必须归零：渲染层左右镜像，
        # 偏一点就变成两条相距 1 mm 的线，在近距离特写下看得见。
        if by_id[code][1] == "M":
            surface_point = np.array([0.0, surface_point[1], surface_point[2]])
        entry = {
            "id": code,
            "x3d": round(float(surface_point[0]), 6),
            "y3d": round(float(surface_point[1]), 6),
            "z3d": round(float(surface_point[2]), 6),
            "skin_tri": tri,
            "skin_bary": ",".join(f"{v:.5f}" for v in bary),
            "verified": 0,
        }
        updates.append(entry)
        if flipped:
            review.append(
                {
                    **entry,
                    "distance_mm": round(dist * 1000, 2),
                    "min_bary": round(float(bary.min()), 3),
                    "reason": "上游左右标记与 x 符号相反，已按 x>0 取点",
                }
            )
        elif dist > DISTANCE_WARN_M or float(bary.min()) < -0.05:
            review.append(
                {
                    **entry,
                    "distance_mm": round(dist * 1000, 2),
                    "min_bary": round(float(bary.min()), 3),
                    "reason": "离皮肤超过 2 mm" if dist > DISTANCE_WARN_M else "落在三角面外",
                }
            )

    missing = sorted(set(by_id) - set(groups))
    for code in missing:
        problems.append(f"{code}：上游没有坐标（名录里有，地图上会缺一个光点）")
    return updates, review, problems


def plan_paths(surface: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """26 条上游路径 → 14 条经脉折线。

    上游把十二经的左右各存一条（共 24 条），任督各一条。我们沿用「存一侧」的约定
    （与穴位一致：右侧由渲染层镜像），所以取 x>0 的那条；任督两点 x≈0，取唯一一条。
    **不合并左右的中点**：那会把一条贴着体表的线拉到身体中轴，图上看着像穿过内脏。
    """

    groups: dict[str, list[dict[str, Any]]] = {}
    for path in surface["paths"]:
        groups.setdefault(str(path["id"]), []).append(path)

    out: list[dict[str, Any]] = []
    problems: list[str] = []
    for channel_id, items in groups.items():
        chosen = None
        if len(items) == 1:
            chosen = items[0]
        else:
            # 用第一个顶点的 x 判断左右；GV/CV 两条都是 0 时按顶点多寡取一致的那条
            chosen = max(items, key=lambda p: float(p["vertices"][0]))
        coords = np.asarray(chosen["vertices"], dtype=np.float64).reshape(-1, 3)
        if coords.shape[0] < 2:
            problems.append(f"{channel_id}：路径点不足 2 个")
            continue
        length = float(np.linalg.norm(np.diff(coords, axis=0), axis=1).sum())
        out.append(
            {
                "id": channel_id,
                "points": [[round(float(v), 5) for v in p] for p in coords],
                "length_m": round(length, 4),
            }
        )
    if len(out) != 14:
        problems.append(f"经脉折线 {len(out)} 条，预期 14 条")
    return out, problems


def apply_paths(db_path: Path | str, paths: list[dict[str, Any]]) -> int:
    from app.meridian.asset import GEOMETRY_VERSION

    conn = store.connect(Path(db_path))
    try:
        for row in paths:
            conn.execute(
                "UPDATE meridian_channel SET path_points=?, path_len=?, geometry_version=? WHERE id=?",
                (
                    json.dumps(row["points"], ensure_ascii=False),
                    row["length_m"],
                    GEOMETRY_VERSION,
                    row["id"],
                ),
            )
        conn.commit()
        return len(paths)
    finally:
        conn.close()


def apply(db_path: Path | str, updates: list[dict[str, Any]]) -> int:
    rows = [
        (u["x3d"], u["y3d"], u["z3d"], u["skin_tri"], u["skin_bary"], u["verified"], u["id"])
        for u in updates
    ]
    conn = store.connect(Path(db_path))
    try:
        conn.executemany(
            "UPDATE meridian_acupoint SET x3d=?, y3d=?, z3d=?, skin_tri=?, skin_bary=?, verified=? "
            "WHERE id=?",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def audit(db_path: Path | str = store.DB_PATH) -> dict[str, Any]:
    conn = store.connect(Path(db_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM meridian_acupoint").fetchone()[0]
        placed = conn.execute(
            "SELECT COUNT(*) FROM meridian_acupoint WHERE skin_tri >= 0"
        ).fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM meridian_acupoint WHERE verified = 1"
        ).fetchone()[0]
        bounds = conn.execute(
            "SELECT MIN(y3d), MAX(y3d), MIN(x3d), MAX(x3d) FROM meridian_acupoint WHERE skin_tri >= 0"
        ).fetchone()
    finally:
        conn.close()
    return {
        "total": int(total),
        "placed": int(placed),
        "verified": int(verified),
        "height_m": round(float(bounds[1] - bounds[0]), 3) if bounds and bounds[1] else 0.0,
        "x_range": [round(float(bounds[2]), 3), round(float(bounds[3]), 3)] if bounds else [],
    }


def write_review(rows: list[dict[str, Any]], path: Path) -> Path:
    """``coord_review.csv``：给人工校核用，一行一穴。**只列可疑的，不列全部**。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["id", "x3d", "y3d", "z3d", "skin_tri", "skin_bary", "distance_mm", "min_bary", "reason"]
    lines = [",".join(header)]
    for r in sorted(rows, key=lambda x: -x["distance_mm"]):
        lines.append(
            ",".join(str(r.get(k, "")) for k in header)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="穴位坐标 → 皮肤三角面 + 重心坐标")
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--audit", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db) if args.db else store.DB_PATH
    store.init_db(db)

    if args.audit:
        print(json.dumps(audit(db), ensure_ascii=False))
        return 0

    surface = load_surface()
    pos, idx = skin_geometry()
    print(f"皮肤 {pos.shape[0]} 顶点 / {idx.shape[0]} 面 · 上游坐标点 {len(surface['points'])}")

    updates, review, problems = plan(db, surface, pos, idx)
    print(f"可落位 {len(updates)} 穴 · 需复核 {len(review)} 穴")
    for msg in problems[:10]:
        print("  ·", msg)
    if len(problems) > 10:
        print(f"  …… 另 {len(problems) - 10} 条")
    if review:
        # 复核清单由 ``crosscheck`` 统一产出（它还要并入镜像差、骨度、跳跃段）。
        # 这里只报数，不覆盖那份文件——两份 CSV 互相覆盖过一次，结果只剩 15 行。
        worst = max(r["distance_mm"] for r in review)
        print(f"离面超阈值 {len(review)} 穴（最高偏离 {worst} mm）；完整清单跑 "
              f"`python -m app.meridian.crosscheck`")

    if not args.apply:
        print("预览模式，未写库。加 --apply 生效。")
        return 0
    n = apply(db, updates)
    print(f"已写入 {n} 个穴位坐标")

    paths, path_problems = plan_paths(surface)
    m = apply_paths(db, paths)
    longest = max(paths, key=lambda p: p["length_m"])
    print(
        f"已写入 {m} 条经脉折线（最长 {longest['id']} {longest['length_m']} m）"
    )
    for msg in path_problems:
        print("  ·", msg)
    print(json.dumps(audit(db), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
