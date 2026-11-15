#!/usr/bin/env python
"""本地原图入库：把**仓库里已有**的原图归一化、算指纹、写进登记表。

为什么另起一个而不是扩展 ``ingest.py``：那个脚本的第一条规矩是「只下载
sources.json 里的 URL，不接受命令行传 URL」，为的是不退化爬虫。而这里处理的
图根本不在网上——它已经在 ``frontend/public/story/herbs/`` 里躺着了，缺的只是
元数据与归一化。两件事的风险面完全不同，混在一个脚本里会把「不抓网」这条硬约束
磨钝。

    python tools/assets/local.py --dry-run    # 只打印会做什么
    python tools/assets/local.py              # 真跑

脚本不写元数据：``local.json`` 里的每条都必须是人工填好的（许可、来源页、主题词）。
脚本只负责三件确定的事——跑算子、算 sha256、合并进 ``registry.json``。

``review`` 允许先由 agent 填，但 note 里必须写明「待人眼复核」，不能假装已经过目。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from normalize import ASPECTS, RECIPES, normalize  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"
REGISTRY = PUBLIC / "assets" / "registry.json"
ENTRIES = Path(__file__).resolve().parent / "local.json"
OUT_DIR = PUBLIC / "assets" / "pictorial"

#: 与 audit.py 的 MAX_BYTES 一致。这里先卡一道，免得写完才在校验里报错。
MAX_BYTES = {"plate": 200 * 1024, "photo": 400 * 1024, "motif": 80 * 1024, "generated": 400 * 1024}

REQUIRED = ("source", "id", "kind", "slot_class", "subjects", "recipe", "aspect", "license", "source_url", "review")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="本地原图入库")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--entries",
        default=str(ENTRIES),
        help="条目文件。整包套组走 vendor.py 生成的 local.generated.json，手挑的单张仍用 local.json。",
    )
    args = parser.parse_args()

    entries_path = Path(args.entries)
    entries = _load(entries_path).get("entries", [])
    registry = _load(REGISTRY) if REGISTRY.exists() else {"version": 1, "assets": []}
    by_id = {asset["id"]: asset for asset in registry.get("assets", [])}

    problems: list[str] = []
    for entry in entries:
        missing = [key for key in REQUIRED if not entry.get(key)]
        if missing:
            problems.append(f"{entry.get('id', '?')}: 缺字段 {', '.join(missing)}")
            continue
        if entry["recipe"] not in RECIPES:
            problems.append(f"{entry['id']}: 未知配方 {entry['recipe']}")
            continue
        if entry["aspect"] not in ASPECTS:
            problems.append(f"{entry['id']}: 未知比例 {entry['aspect']}")
            continue

        source = ROOT / entry["source"]
        if not source.exists():
            problems.append(f"{entry['id']}: 原图不存在（{entry['source']}）")
            continue

        target = OUT_DIR / f"{entry['id']}.webp"
        if args.dry_run:
            print(f"[dry-run] {source.relative_to(ROOT)} → {target.relative_to(ROOT)}")
            continue

        if source.resolve() == target.resolve():
            # 已经是成品：vendor.py 那一类整包套组在抽取阶段就跑过算子了。
            # 这里不再归一化一次——二次压缩会把透明边缘磨掉。
            from PIL import Image

            with Image.open(target) as opened:
                size = {"w": opened.width, "h": opened.height, "bytes": target.stat().st_size}
        else:
            size = normalize(source, target, recipe=entry["recipe"], aspect=entry["aspect"], long_edge=1600)
        if size["bytes"] > MAX_BYTES.get(entry["kind"], 400 * 1024):
            problems.append(f"{entry['id']}: 归一化后 {size['bytes'] // 1024}KB 超预算")
            continue

        # 同一个 id 重跑时整条覆盖：元数据以 local.json 为准，指纹与尺寸以文件为准。
        by_id[entry["id"]] = {
            "id": entry["id"],
            "kind": entry["kind"],
            "slot_class": entry["slot_class"],
            "subjects": list(entry["subjects"]),
            "herb_ids": list(entry.get("herb_ids", [])),
            "tags": list(entry.get("tags", [])),
            "file": str(target.relative_to(PUBLIC)).replace("\\", "/"),
            "w": size["w"],
            "h": size["h"],
            "bytes": size["bytes"],
            "sha256": _sha256(target),
            "license": entry["license"],
            "source_url": entry["source_url"],
            "attribution": entry.get("attribution", ""),
            "treatment": entry["recipe"],
            "added_at": entry.get("added_at") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "review": entry["review"],
        }
        print(f"{entry['id']}: {size['w']}×{size['h']} {size['bytes'] // 1024}KB")

    if problems:
        print("", file=sys.stderr)
        for problem in problems:
            print(f"✗ {problem}", file=sys.stderr)
        return 1
    if args.dry_run:
        return 0

    registry["assets"] = sorted(by_id.values(), key=lambda asset: asset["id"])
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n登记表：{len(registry['assets'])} 条 → {REGISTRY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
