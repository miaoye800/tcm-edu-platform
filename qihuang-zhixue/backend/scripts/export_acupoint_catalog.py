"""从 P0 原型资产导出一份**只含名录**的穴位表，供 P1 造图使用。

为什么单独导一份而不是让后端直接读 ``docs/proto/assets/``：

- ``docs/proto`` 是一次性技术验证的产物，随时可能被删；后端不能依赖它。
- P1 只需要「哪条经有哪些穴、顺序如何」，**不需要 3D 坐标**。坐标与模型版本强绑定
  （换模型 = 全部重新吸附），必须等 P2 把几何冻死之后再写进库。
  现在就带坐标，等于把"几何父级"这条不可逆决策提前用掉了。

用法::

    cd backend && ./.venv/Scripts/python.exe scripts/export_acupoint_catalog.py
"""

from __future__ import annotations

import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND.parent / "docs" / "proto" / "assets" / "teaching-points.json"
DST = BACKEND / "app" / "meridian" / "data" / "acupoints.json"

#: 十二经流注顺序 + 任督。这个顺序决定地图上"学完一章走下一章"往哪走，
#: 不能按字母排（那会变成 BL→CV→GB 这种乱跳）。
CHANNELS: list[tuple[str, str, str, str]] = [
    ("LU", "手太阴肺经", "front", "meridian"),
    ("LI", "手阳明大肠经", "side", "meridian"),
    ("ST", "足阳明胃经", "front", "meridian"),
    ("SP", "足太阴脾经", "front", "meridian"),
    ("HT", "手少阴心经", "front", "meridian"),
    ("SI", "手太阳小肠经", "back", "meridian"),
    ("BL", "足太阳膀胱经", "back", "meridian"),
    ("KI", "足少阴肾经", "front", "meridian"),
    ("PC", "手厥阴心包经", "front", "meridian"),
    ("TE", "手少阳三焦经", "side", "meridian"),
    ("GB", "足少阳胆经", "side", "meridian"),
    ("LR", "足厥阴肝经", "front", "meridian"),
    ("GV", "督脉", "back", "meridian"),
    ("CV", "任脉", "front", "meridian"),
]


def _sort_key(p: dict) -> tuple[int, str]:
    """按穴号排序，穴号后的字母后缀参与二级比较（`GV24` < `GV24+` < `GV25`）。"""

    digits = "".join(ch for ch in p["code"] if ch.isdigit())
    suffix = "".join(ch for ch in p["code"] if not ch.isdigit())
    return (int(digits or 0), suffix)


def main() -> None:
    points = json.loads(SRC.read_text(encoding="utf-8"))
    by_channel: dict[str, list[dict]] = {cid: [] for cid, *_ in CHANNELS}

    for p in points:
        cid = p["meridianId"]
        if cid not in by_channel:
            raise SystemExit(f"未知经脉 {cid}（{p['code']}）")
        by_channel[cid].append(p)

    out_points: list[dict] = []
    for cid, title, view_bias, kind in CHANNELS:
        arr = sorted(by_channel[cid], key=_sort_key)
        n = len(arr)
        for i, p in enumerate(arr):
            # code 里可能有非数字后缀：`GV24+` 是印堂（经外奇穴挂在督脉下，用 `+` 标记）。
            # 早先按「只留数字」重排，GV24 与 GV24+ 会撞成同一个 code，直接触发唯一键冲突。
            raw = p["code"]
            rest = raw[len(cid):] if raw.startswith(cid) else raw
            # 首尾各留一点余量：通经连线以穴位为端点，t=0/1 会让首末穴顶在经线的绝对端点
            t = round((i + 1) / (n + 1), 6)
            out_points.append(
                {
                    "id": raw,
                    "code": f"{cid} {rest}",
                    "name": p["name"],
                    "pinyin": p.get("pinyin", ""),
                    "channel_id": cid,
                    "side": "M" if p.get("midline") else "L",
                    "t": t,
                }
            )

    payload = {
        "geometry_version": "",  # P2 冻结模型后再填
        "note": "名录来自 docs/proto/assets/teaching-points.json，不含 3D 坐标；坐标由 P2 写入。",
        "channels": [
            {"id": cid, "abbr": cid, "title": title, "view_bias": vb, "kind": kind, "sort_hint": i}
            for i, (cid, title, vb, kind) in enumerate(CHANNELS)
        ],
        "acupoints": out_points,
    }
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    counts = {cid: len(v) for cid, v in by_channel.items()}
    print(f"写入 {DST.relative_to(BACKEND)}")
    print(f"  经脉 {len(CHANNELS)} 条 / 穴位 {len(out_points)} 个")
    print("  按经:", counts)


if __name__ == "__main__":
    main()
