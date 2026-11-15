#!/usr/bin/env python
"""素材采集：按**人工维护的 URL 清单**逐条下载、归一化、写登记表。

不做爬虫、不做站内搜索抓取。输入是 ``tools/assets/sources.json`` 里的一条条
人工挑过的记录——速率、版权、可复现三者都只在人工清单下可控。

    python tools/assets/ingest.py --dry-run      # 只看会做什么
    python tools/assets/ingest.py                # 真下载

三条规矩：

1. 只下载 ``sources.json`` 里列出的 URL，**不接受命令行传 URL**——否则它就退化成爬虫。
2. 每条都要在清单里写好 ``license`` 与 ``source_url``（原图页面，不是直链）。
   缺 license 的条目直接跳过并报错，不猜。
3. 写完 ``sources.lock``（url + 抓取时间 + sha256），别人能复现同一批文件。

``review`` 字段留空：未人工过目的图不得进库，这是流程而不是建议。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from normalize import ASPECTS, RECIPES, normalize  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"
REGISTRY = PUBLIC / "assets" / "registry.json"
SOURCES = Path(__file__).resolve().parent / "sources.json"
LOCK = Path(__file__).resolve().parent / "sources.lock"
CACHE = Path(__file__).resolve().parent / ".cache"

#: 只收这几种；HTML 页面误当图片下载是最常见的错误。
ALLOWED_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def load_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(url: str, target: Path, *, retries: int = 1) -> str:
    """下载到本地缓存，返回 sha256。失败重试一次，再失败就抛——不静默跳过。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "qihuang-asset-ingest/1.0 (dev)"})
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - 只取人工清单里的 URL
                payload = response.read()
            target.write_bytes(payload)
            return hashlib.sha256(payload).hexdigest()
        except Exception as exc:  # noqa: BLE001 - 网络错误种类多，统一重试一次
            last = exc
            time.sleep(1.5)
    raise RuntimeError(f"下载失败 {url}: {last}")


def main() -> int:
    parser = argparse.ArgumentParser(description="按清单采集素材")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sources = load_json(SOURCES, {"items": []}).get("items") or []
    if not sources:
        print("sources.json 里没有条目。先人工填清单，再跑这个脚本。")
        return 0

    registry = load_json(REGISTRY, {"version": 1, "assets": []})
    known = {str(item.get("id") or "") for item in registry.get("assets") or []}
    lock = load_json(LOCK, {"items": []})
    added = 0

    for index, item in enumerate(sources):
        url = str(item.get("url") or "")
        license_id = str(item.get("license") or "")
        source_url = str(item.get("source_url") or "")
        asset_id = str(item.get("id") or "")
        suffix = Path(url).suffix.lower()
        if not url or not license_id or not source_url or not asset_id:
            print(f"[跳过] 第 {index + 1} 条缺 url/license/source_url/id，不猜")
            continue
        if suffix not in ALLOWED_SUFFIX:
            print(f"[跳过] {asset_id}：{suffix or '无扩展名'} 不是图片")
            continue
        if asset_id in known:
            print(f"[跳过] {asset_id} 已登记")
            continue
        recipe = str(item.get("recipe") or "plate-wash")
        if recipe not in RECIPES:
            print(f"[跳过] {asset_id}：未知配方 {recipe}")
            continue
        aspect = str(item.get("aspect") or "portrait")
        if aspect not in ASPECTS:
            print(f"[跳过] {asset_id}：未知比例 {aspect}")
            continue

        if args.dry_run:
            print(f"[计划] {asset_id} ← {url}（{recipe}/{aspect}）")
            continue

        raw = CACHE / f"{asset_id}{suffix}"
        digest = fetch(url, raw)
        output = PUBLIC / "assets" / "pictorial" / f"{asset_id}.webp"
        info = normalize(raw, output, recipe=recipe, aspect=aspect)
        final_digest = hashlib.sha256(output.read_bytes()).hexdigest()
        registry.setdefault("assets", []).append(
            {
                "id": asset_id,
                "kind": str(item.get("kind") or "plate"),
                "slot_class": str(item.get("slot_class") or "decorative"),
                "subjects": list(item.get("subjects") or []),
                "herb_ids": list(item.get("herb_ids") or []),
                "tags": list(item.get("tags") or []),
                "file": f"assets/pictorial/{asset_id}.webp",
                "w": info["w"],
                "h": info["h"],
                "bytes": info["bytes"],
                "sha256": final_digest,
                "license": license_id,
                "source_url": source_url,
                "attribution": str(item.get("attribution") or ""),
                "treatment": recipe,
                "added_at": datetime.now(UTC).isoformat(),
                # 空 review = 还没人看过。audit 不拦这一条，但流程上必须先过目。
                "review": {},
            }
        )
        lock.setdefault("items", []).append(
            {"id": asset_id, "url": url, "fetched_at": datetime.now(UTC).isoformat(), "sha256": digest, "license": license_id}
        )
        REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        added += 1
        print(f"[完成] {asset_id} → {output.relative_to(ROOT)}（{info['w']}×{info['h']}，{info['bytes'] // 1024}KB）")

    if not args.dry_run:
        print(f"新增 {added} 条。下一步：人工过目后填 review，再跑 python tools/assets/audit.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
