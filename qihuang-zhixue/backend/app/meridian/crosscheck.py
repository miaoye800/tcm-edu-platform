"""坐标到底准不准：三类可量化的校验，产出 ``coord_review.csv``。

为什么不用「和另一个 3D 模型逐穴比欧氏距离」
--------------------------------------------
计划里原本写的是与 ``zmtom18988tmd/chinese-acupuncture-point`` 比对。实际去看过之后
不采用，原因有两条，都是硬原因：

1. 那个仓库只提供一个 **41 MB 的 .blend**（Blender 2.92 工程），穴位是立方体对象，
   坐标在 .blend 的对象变换里。要读它就得装 Blender 或者手写 .blend 解析器。
2. 更重要：**两个不同体型模型的绝对坐标不可比**。同一个人体取穴，1.72 m 和 1.80 m
   两个模型的"足三里"xyz 差好几厘米，比出来的差异全是体型差，不是定位差。

所以改成三条**与体型无关**的校验：

* **左右镜像**：同一穴位左右两点应关于 x=0 对称。这条最硬——它不依赖任何标准，
  也不依赖体型，偏差就是错误。
* **骨度分寸**：教材规定的"肘横纹到腕横纹 12 寸"这类比例。把两端穴位的距离除以
  寸数，反推"1 寸是多少毫米"，各条规则的结果应彼此接近。用**相对**一致性判断，
  避免不同版本身高差异。
* **沿经单调**：同一条经上相邻穴位的间距不应出现离群值（差了 10 倍的间距多半是
  某个点吸附到了别处）。

三条都不需要第二个模型，也不需要"标准答案坐标"，但能把真实错误挑出来。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from app.meridian import coords, store

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "meridian" / "coord_review.csv"

#: 骨度分寸规则：``(起点穴, 终点穴, 寸数, 依据)``。
#: 只收**两端都是固定体表标志**的分段——"腋前纹头"这种没有对应穴位的分段不能用
#: 穴位对来算，硬算等于自己编一个参考点。
BONE_CUN: tuple[tuple[str, str, float, str], ...] = (
    ("CV8", "CV2", 5, "脐中至耻骨联合上缘 5 寸"),
    ("PC3", "PC7", 12, "肘横纹至腕横纹 12 寸（心包经）"),
    ("LI11", "LI5", 12, "肘横纹至腕横纹 12 寸（大肠经）"),
    ("LU5", "LU9", 12, "肘横纹至腕横纹 12 寸（肺经）"),
    ("HT3", "HT7", 12, "肘横纹至腕横纹 12 寸（心经）"),
    ("SP9", "SP5", 13, "胫骨内侧髁下方至内踝尖 13 寸"),
    ("ST35", "ST41", 16, "膝中至外踝尖 16 寸"),
    ("GB34", "GB40", 16, "腘横纹至外踝尖 16 寸"),
    ("BL40", "BL60", 16, "腘横纹至外踝尖 16 寸"),
    ("GV24", "GV20", 4.5, "前发际上 0.5 寸至百会 5 寸，计 4.5 寸"),
)

#: 左右镜像偏差阈值（米）。5 mm 在 1.72 m 身高上约 0.3%，超过就值得看一眼：
#: 骨盆区被软化过，本来就可能有 1 cm 级的差异。
SYMMETRY_WARN_M = 0.005
#: 各条骨度规则的「1 寸」长度偏离中位数的比例阈值。
#: 取 40% 而不是 15%：骨度是沿体表折线量的，我们量的是两点直线，腕肘这种弯曲段
#: 的直线/弧线比与腹部差很多（实测 21 mm vs 29 mm，差 37%，但两条都**没有错**）。
#: 这个检查只能抓量级错误（把 12 寸算成 3 寸），不能当精度尺用——所以阈值放宽，
#: 免得一屏全是"⚠"而没人看。
CUN_SPREAD_WARN = 0.40
#: 同经相邻穴位的绝对间距上限。人体相邻两穴极少超过 25 cm；超过的多半是
#: 经脉本身的跳跃段（如膀胱经从腘窝上到背部），不是坐标错，但 t 参数若当成
#: 连续会出问题（流光会"瞬移"），所以仍然列出来让人看一眼。
STEP_WARN_M = 0.25


def _load_points(db_path: Path | str) -> dict[str, tuple[float, float, float]]:
    conn = store.connect(Path(db_path))
    try:
        rows = conn.execute(
            "SELECT id, x3d, y3d, z3d FROM meridian_acupoint WHERE skin_tri >= 0"
        ).fetchall()
    finally:
        conn.close()
    return {str(r[0]): (float(r[1]), float(r[2]), float(r[3])) for r in rows}


def bone_cun_report(points: dict[str, tuple[float, float, float]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """反推每寸长度，比较彼此一致性。"""

    rows: list[dict[str, Any]] = []
    per_cun: list[tuple[str, float]] = []
    for a, b, cun, why in BONE_CUN:
        if a not in points or b not in points:
            rows.append({"id": f"{a}-{b}", "check": "bone_cun", "value": "", "note": f"缺坐标：{why}"})
            continue
        dist = math.dist(points[a], points[b])
        per = dist / cun
        per_cun.append((f"{a}-{b}", per))
        rows.append(
            {
                "id": f"{a}-{b}",
                "check": "bone_cun",
                "value": round(per * 1000, 2),
                "note": f"{why}｜{dist*100:0.1f} cm",
            }
        )
    if not per_cun:
        return rows, {}
    median = float(np.median([v for _, v in per_cun]))
    spread = []
    for key, per in per_cun:
        dev = (per - median) / median
        if abs(dev) > CUN_SPREAD_WARN:
            spread.append({"rule": key, "per_cun_mm": round(per * 1000, 1), "deviation": round(dev, 3)})
            rows.append(
                {
                    "id": key,
                    "check": "bone_cun_spread",
                    "value": round(dev * 100, 1),
                    "note": f"每寸 {per*1000:.1f} mm，偏离中位数 {dev*100:+.0f}%",
                }
            )
    return rows, {
        "per_cun_mm": round(median * 1000, 2),
        "rules": len(per_cun),
        "outliers": spread,
    }


def symmetry_report(surface: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """同一穴位左右两点应关于 x=0 镜像。不依赖体型，偏差就是错。"""

    groups: dict[str, dict[int, np.ndarray]] = {}
    for pt in surface["points"]:
        code, _, side = pt["key"].partition(":")
        if not side:
            continue
        groups.setdefault(code, {})[int(side)] = np.asarray(pt["target"]["position"], dtype=np.float64)

    rows: list[dict[str, Any]] = []
    devs: list[float] = []
    paired = 0
    for code, sides in groups.items():
        if 1 not in sides or -1 not in sides:
            continue
        paired += 1
        left, right = sides[1], sides[-1]
        mirrored = np.array([-right[0], right[1], right[2]])
        dev = float(np.linalg.norm(left - mirrored))
        devs.append(dev)
        # 太溪的左右标记是反的，镜像差会接近两倍 x——单独在 coords 里已纠正，
        # 这里仍然报出来，因为"上游数据有错"这件事本身要留痕。
        if dev > SYMMETRY_WARN_M:
            hint = "，足踝区吸附不稳，建议人工校核" if dev > 0.02 else ""
            rows.append(
                {
                    "id": code,
                    "check": "symmetry",
                    "value": round(dev * 1000, 1),
                    "note": (
                        f"左右镜像差 {dev*1000:.1f} mm"
                        f"（左 x={left[0]:+.3f} y={left[1]:.3f} / 右 x={right[0]:+.3f} y={right[1]:.3f}）{hint}"
                    ),
                }
            )
    return rows, {
        "paired": paired,
        "median_mm": round(float(np.median(devs)) * 1000, 2) if devs else 0.0,
        "max_mm": round(float(np.max(devs)) * 1000, 2) if devs else 0.0,
        "over_threshold": len(rows),
    }


def monotonic_report(
    points: dict[str, tuple[float, float, float]], db_path: Path | str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """同一条经上相邻穴位的间距不应突变。"""

    conn = store.connect(Path(db_path))
    try:
        rows = conn.execute(
            "SELECT channel_id, id, name, t FROM meridian_acupoint ORDER BY channel_id, t"
        ).fetchall()
    finally:
        conn.close()

    by_channel: dict[str, list[tuple[str, str, float]]] = {}
    for ch, nid, name, t in rows:
        by_channel.setdefault(str(ch), []).append((str(nid), str(name), float(t)))

    out: list[dict[str, Any]] = []
    worst: list[dict[str, Any]] = []
    total = 0
    for ch, seq in by_channel.items():
        steps: list[tuple[str, str, float]] = []
        for (a, an, _), (b, bn, _) in zip(seq, seq[1:]):
            if a not in points or b not in points:
                continue
            steps.append((a, b, math.dist(points[a], points[b])))
        if len(steps) < 3:
            continue
        total += len(steps)
        median = float(np.median([s[2] for s in steps]))
        for a, b, dist in steps:
            if dist > STEP_WARN_M:
                worst.append({"channel": ch, "pair": f"{a}-{b}", "mm": round(dist * 1000, 1)})
                out.append(
                    {
                        "id": f"{a}-{b}",
                        "check": "path_step",
                        "value": round(dist * 1000, 1),
                        "note": (
                            f"{ch} 相邻间距 {dist*1000:.1f} mm（该经中位 {median*1000:.0f} mm）；"
                            "多为经脉跳跃段，若 t 连续则该段 t 参数需复核"
                        ),
                    }
                )
    return out, {"steps": total, "outliers": len(out), "worst": sorted(worst, key=lambda x: -x["mm"])[:5]}


def surface_report(surface: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """复用 coords 的离面距离：这里只是把它并进同一份复核清单。"""

    pos, idx = coords.skin_geometry()
    rows: list[dict[str, Any]] = []
    dists: list[float] = []
    # 与 coords.plan 用同一套取点规则（x>0 侧），否则这里报的距离会与落库的坐标不对应
    groups: dict[str, list[dict[str, Any]]] = {}
    for pt in surface["points"]:
        code, _, _side = pt["key"].partition(":")
        groups.setdefault(code, []).append(pt)
    for code, items in groups.items():
        pt = next((x for x in items if float(x["target"]["position"][0]) > 0), items[0])
        p = np.asarray(pt["target"]["position"], dtype=np.float64)
        _tri, _bary, dist = coords.resolve_point(p, pos, idx)
        dists.append(dist)
        if dist > coords.DISTANCE_WARN_M:
            rows.append(
                {
                    "id": code,
                    "check": "surface",
                    "value": round(dist * 1000, 2),
                    "note": "离皮肤超过 2 mm（骨盆区曾被软化，属已知偏差）",
                }
            )
    return rows, {
        "points": len(dists),
        "median_mm": round(float(np.median(dists)) * 1000, 3) if dists else 0.0,
        "max_mm": round(float(np.max(dists)) * 1000, 2) if dists else 0.0,
        "over_threshold": len(rows),
    }


def write_csv(rows: list[dict[str, Any]], path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["id", "check", "value", "note"]
    lines = [",".join(header)]
    order = {"symmetry": 0, "surface": 1, "path_step": 2, "bone_cun": 3, "bone_cun_spread": 4}
    for r in sorted(rows, key=lambda x: (order.get(str(x["check"]), 9), -float(x["value"] or 0))):
        note = str(r.get("note", "")).replace(",", "，")
        lines.append(",".join([str(r["id"]), str(r["check"]), str(r["value"]), note]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(db_path: Path | str = store.DB_PATH) -> dict[str, Any]:
    from app.meridian.asset import load_surface

    surface = load_surface()
    points = _load_points(db_path)
    body, cun_stats = bone_cun_report(points)
    sym, sym_stats = symmetry_report(surface)
    mono, mono_stats = monotonic_report(points, db_path)
    surf, surf_stats = surface_report(surface)
    rows = sym + surf + mono + body
    write_csv(rows)
    return {
        "points": len(points),
        "bone_cun": cun_stats,
        "symmetry": sym_stats,
        "monotonic": mono_stats,
        "surface": surf_stats,
        "review_rows": len(rows),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="坐标校验：左右镜像 / 骨度分寸 / 沿经间距")
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)
    db = Path(args.db) if args.db else store.DB_PATH
    store.init_db(db)
    result = run(db)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"已落位穴位 {result['points']} 个")
    cun = result["bone_cun"]
    print(f"骨度分寸：中位每寸 {cun.get('per_cun_mm', 0)} mm（{cun.get('rules', 0)} 条规则）")
    for o in cun.get("outliers", []):
        print(f"  ⚠ {o['rule']} 每寸 {o['per_cun_mm']} mm（偏离 {o['deviation']*100:+.0f}%）")
    sym = result["symmetry"]
    print(
        f"左右镜像：{sym['paired']} 对，中位 {sym['median_mm']} mm，"
        f"最大 {sym['max_mm']} mm，超阈值 {sym['over_threshold']} 个"
    )
    mono = result["monotonic"]
    print(f"沿经间距：{mono['steps']} 段，超过 250 mm 的 {mono['outliers']} 段")
    for w in mono["worst"]:
        print(f"  ⚠ {w['channel']} {w['pair']} {w['mm']} mm")
    surf = result["surface"]
    print(
        f"离皮肤距离：中位 {surf['median_mm']} mm，最大 {surf['max_mm']} mm，"
        f"超阈值 {surf['over_threshold']} 个"
    )
    print(f"\n复核清单 {result['review_rows']} 行 → {OUT}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
