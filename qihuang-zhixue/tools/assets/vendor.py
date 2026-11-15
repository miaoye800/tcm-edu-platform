#!/usr/bin/env python
"""套组入库：把下载来的插画 zip 归一化、打标、写成可入库的条目。

与 ``local.py`` 的分工刻意分开：

- ``local.py`` 处理**仓库里逐张挑好的**图，元数据写在 ``local.json`` 里，是人填的。
- 本脚本处理**整包下载的套组**，元数据只能由套组本身 + 逐张看图推出，是算出来的。

```bash
python tools/assets/vendor.py              # 抽取 + 归一化 + 打标 + 写 local.generated.json
python tools/assets/vendor.py --relabel    # 强制重新打标（默认命中文档就跳过，省钱）
python tools/assets/vendor.py --emit-only  # 只重写条目，不碰图
```

打标调用大模型看图，所以**这一步需要网络**；模型连不上时退化为套组名 + 序号，
并把 ``review.note`` 标成「未逐张看图」，不假装过了目。

产物 ``inventory.json`` 是可重跑的中间态：图被重新归一化不会丢已打好的标签，
因为标签按 ``asset_id`` 存在这一层。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from normalize import normalize  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PUBLIC = ROOT / "frontend" / "public"
OUT_DIR = PUBLIC / "assets" / "pictorial"
META = HERE / "heritage.json"
INVENTORY = HERE / "inventory.json"
GENERATED = HERE / "local.generated.json"

#: 单张预算，与 ``audit.py`` 的 plate 项一致。超了在抽取阶段就退让，别等到 CI 报红。
PLATE_BUDGET = 200 * 1024

#: 打标用的提示词。要求少而具体——泛泛的「器皿」在检索时无法与「双耳陶罐」区分。
LABEL_PROMPT = (
    "你在为中医药教学课件挑选复古插画。看图后只输出一个 JSON 对象，不要解释：\n"
    '{"subject": "画面主体，2-8 个汉字的具体名词", '
    '"alt": "一句话说明画面内容，不超过 30 字", '
    '"tags": ["3 到 5 个检索标签，含风格与题材"]}\n'
    "subject 要具体到能与其他图区分（写「双耳陶罐」而不是「器物」，写「杭白菊」而不是「花」）。"
)


def _load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    dotenv = ROOT / "backend" / ".env"
    if not dotenv.exists():
        return env
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if match:
            env[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return env


def _bytes_to_data_png(payload: bytes, max_edge: int = 640) -> str:
    """缩到 640 再发：打标只需要看清画的是什么，原图 3000px 纯属浪费。"""

    from PIL import Image

    image = Image.open(io.BytesIO(payload)).convert("RGBA")
    paper = Image.new("RGBA", image.size, (250, 247, 240, 255))
    flattened = Image.alpha_composite(paper, image).convert("RGB")
    flattened.thumbnail((max_edge, max_edge))
    buffer = io.BytesIO()
    flattened.save(buffer, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def label_one(payload: bytes, *, env: dict[str, str]) -> dict | None:
    """让模型看一眼，返回 subject/alt/tags。任何失败都返回 None，由调用方兜底。"""

    key = env.get("DEEPSEEK_API_KEY", "")
    if not key:
        return None
    base = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    body = {
        "model": env.get("DEEPSEEK_MODEL_FLASH", "deepseek-flash"),
        "stream": False,
        "thinking": {"type": "disabled"},
        "temperature": 0.2,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": LABEL_PROMPT},
                    {"type": "image_url", "image_url": {"url": _bytes_to_data_png(payload)}},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read())
        text = str(data["choices"][0]["message"].get("content") or "")
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"    ! 打标失败：{type(exc).__name__}", file=sys.stderr)
        return None

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    subject = str(parsed.get("subject") or "").strip()[:12]
    if not subject:
        return None
    tags = [str(t).strip()[:8] for t in (parsed.get("tags") or []) if str(t).strip()][:5]
    return {"subject": subject, "alt": str(parsed.get("alt") or "").strip()[:40], "tags": tags}


def sources_for(set_name: str) -> list[tuple[str, bytes]]:
    """优先用已解压的目录，其次才读 zip。返回 [(原始文件名, 字节)]。"""

    inbox = HERE / "inbox" / "heritage" / set_name
    if inbox.exists():
        files = sorted(p for p in inbox.rglob("*.png"))
        if files:
            return [(p.name, p.read_bytes()) for p in files]
    archive = HERE / f"{set_name}.zip"
    if not archive.exists():
        return []
    with zipfile.ZipFile(archive) as bundle:
        # 套组里有 PDF（许可与说明）和 PNG（素材），只取 PNG，且按名字排序保证序号稳定。
        names = sorted(n for n in bundle.namelist() if n.lower().endswith(".png"))
        return [(Path(n).name, bundle.read(n)) for n in names]


def main() -> int:
    parser = argparse.ArgumentParser(description="Heritage 套组入库")
    parser.add_argument("--relabel", action="store_true", help="强制重新打标")
    parser.add_argument("--emit-only", action="store_true", help="只重写条目")
    parser.add_argument("--only", default="", help="只处理名字含该子串的套组，便于先试一套")
    args = parser.parse_args()

    meta = _load(META, {})
    sets = meta.get("sets") or {}
    if args.only:
        sets = {name: cfg for name, cfg in sets.items() if args.only in name}
    if not sets:
        print(f"× {META} 没有 sets", file=sys.stderr)
        return 1

    inventory = _load(INVENTORY, {"items": []})
    known = {item["asset_id"]: item for item in inventory["items"]}
    env = _env()
    treat = meta.get("treatment", "vintage-keep")
    long_edge = int(meta.get("long_edge", 1100))
    quality = int(meta.get("quality", 80))
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    items: list[dict] = list(known.values()) if args.only else []
    if args.only:
        # 只处理一套时，别的套组的结果要原样留着，否则 inventory 会被截断。
        items = [item for item in known.values() if item.get("set") not in sets]
    unlabeled = 0
    for set_name, config in sets.items():
        slug = config["slug"]
        aspect = config.get("aspect", "portrait")
        pairs = sources_for(set_name)
        if not pairs:
            print(f"[跳过] {set_name}：既没有 inbox 目录也没有 zip")
            continue
        print(f"[{set_name}] {len(pairs)} 张")

        for index, (origin, payload) in enumerate(pairs, start=1):
            asset_id = f"plate-{slug}-{index:02d}"
            target = OUT_DIR / f"{asset_id}.webp"
            previous = known.get(asset_id) or {}

            if not args.emit_only:
                size = _normalize_under_budget(
                    _materialize(origin, payload), target, recipe=treat, aspect=aspect,
                    long_edge=long_edge, quality=quality, budget=PLATE_BUDGET,
                )
            else:
                from PIL import Image

                with Image.open(target) as opened:
                    size = {"w": opened.width, "h": opened.height, "bytes": target.stat().st_size}

            label = previous.get("label")
            if args.relabel or not label:
                label = label_one(payload, env=env)
                if label is None:
                    unlabeled += 1
                    label = {"subject": f"{set_name} 第{index}幅", "alt": "", "tags": []}
                else:
                    print(f"    {asset_id}: {label['subject']} — {label['alt'][:24]}")

            items.append(
                {
                    "asset_id": asset_id,
                    "set": set_name,
                    "origin": origin,
                    "file": str(target.relative_to(PUBLIC)).replace("\\", "/"),
                    "w": size["w"],
                    "h": size["h"],
                    "bytes": size["bytes"],
                    "label": label,
                    "aspect": aspect,
                    "tags": list(config.get("tags") or []),
                }
            )

    INVENTORY.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    entries = []
    for item in items:
        subject = item["label"]["subject"]
        entries.append(
            {
                "source": item["file"].replace("assets/pictorial/", "frontend/public/assets/pictorial/"),
                "id": item["asset_id"],
                "kind": "plate",
                "slot_class": "decorative",
                "subjects": [subject],
                "tags": sorted({*item["tags"], *item["label"].get("tags", [])}),
                "recipe": treat,
                "aspect": item["aspect"],
                "license": meta["license"],
                "attribution": meta["attribution"],
                "source_url": meta["source_url"],
                "alt": item["label"].get("alt", ""),
                "review": {
                    "reviewer": "agent-review",
                    "date": today,
                    "note": (
                        f"Heritage 套组「{item['set']}」自动归一化入库；主体名由图生模型看图给出，待人眼复核。"
                        "套组许可为 CC0，但附加条款禁止把素材文件再配布，因此不提供素材下载入口。"
                    ),
                },
            }
        )
    GENERATED.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    total = sum(item["bytes"] for item in items)
    print(f"\n条目 {len(entries)} 条 → {GENERATED.relative_to(ROOT)}")
    print(f"图片总体积 {total / 1024 / 1024:.1f}MB（单张均 {total / max(1, len(items)) / 1024:.0f}KB）")
    if unlabeled:
        print(f"⚠ {unlabeled} 张未打标，已用套组名占位——这些图检索时只能靠套组标签命中。")
    return 0


def _normalize_under_budget(source: Path, target: Path, *, recipe: str, aspect: str,
                             long_edge: int, quality: int, budget: int) -> dict:
    """归一化到体积预算之内。彩绘精细件在 1100 长边下会到 280KB，超了 plate 的 200KB。

    逐级退让而不是一刀切降全局：线稿本来只有 50KB，不必陪着一起挨刀。
    """

    edge, q = long_edge, quality
    for _ in range(3):
        size = normalize(source, target, recipe=recipe, aspect=aspect, long_edge=edge, quality=q)
        if size["bytes"] <= budget:
            return size
        edge = int(edge * 0.82)
        q = max(66, q - 5)
    return size


def _materialize(origin: str, payload: bytes) -> Path:
    """normalize() 收的是路径，这里把字节落到临时目录。用完即弃，不污染 inbox。

    文件名带内容哈希前缀：八套素材里每一张都叫 ``Element 1.png``，只用原名会互相覆盖。
    """

    import hashlib

    staging = HERE / ".staging"
    staging.mkdir(exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()[:10]
    path = staging / f"{digest}-{Path(origin).name}"
    path.write_bytes(payload)
    return path


if __name__ == "__main__":
    raise SystemExit(main())
