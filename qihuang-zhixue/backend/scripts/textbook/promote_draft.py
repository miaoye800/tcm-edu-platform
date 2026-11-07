"""把**已签字**的教材条目生成待发布 JSON（P4.4 入库闸门）。

## 三层隔离，各管一件事

计划要求「防污染四层隔离」，这里是第 2 层和第 4 层：

| 层 | 机制 | 防的是 |
|---|---|---|
| 1 | JSON 是 source of truth，SQLite 是可重建索引 | 索引坏了能重建 |
| 2 | 产物先落 ``data/herbs/_pending/``，签字才 ``publish`` 到 ``data/herbs/`` | 抽取一次改错，442 味药同时进处方审查 |
| 3 | ``load_herbs()`` 只 glob 不递归，``_pending/`` 天然不可见 | 不用改代码就隔离 |
| 4 | 写盘前 ``diff_report``，新增 >50 或修改 >10 必须 ``--force`` | 一次误跑把整库换掉，没人发现 |

第 4 层最容易被忽略，但它拦的是最贵的事故：**一次正则改错，442 味药全变**，
而 ``git diff`` 里是 442 个文件的 2000 行改动，人不会逐行看。
``diff_report`` 把这个 diff 压成一句话：「新增 442、修改 0」，人一眼就知道不对。

## 为什么门槛是 50 / 10

这不是拍脑袋的数字，是这批数据的实测值：正常增量入库是几十条（补一批药），
50 条以上基本只有「整批重抽」这一种情况；修改 10 条以上基本只有
「改了归一表或抽取口径」。这两种都该有人主动确认。

用法::

    python -m scripts.textbook.promote_draft --dry-run
    python -m scripts.textbook.promote_draft
    python -m scripts.textbook.promote_draft --force   # 确认整批变更
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.kb.models import Herb
from app.kg import draft
from app.kg.entities import load_textbook_entries
from scripts.textbook.emit_herbs import build_herb

BACKEND = Path(__file__).resolve().parents[2]
GATED_JSON = BACKEND / "data" / "curated" / "textbook_gated.json"
ALIAS_MAP = BACKEND / "data" / "curated" / "herb_alias_map.json"
HERBS_DIR = BACKEND / "data" / "herbs"
#: 待发布区。``load_herbs()`` 只 glob 不递归，所以这里的东西**不会**被读到。
PENDING_JSON = HERBS_DIR / "_pending" / "textbook_promoted.json"
#: 已发布区。diff 的基线就是它。
PUBLISHED_JSON = HERBS_DIR / "textbook_promoted.json"
OUT_MD = BACKEND.parent / "deliverables" / "diff_report.md"

#: 超过这两个数就必须 --force。
MAX_ADDED = 50
MAX_CHANGED = 10

#: 本流水线自己的产物，不算「已有手写条目」。
OWN_PRODUCTS = ("textbook_promoted.json", "textbook_extracted.json")

#: 参与 diff 的字段。``sources`` 里有页码，页码一改就全是「修改」，
#: 而页码变动是重抽的正常结果，不是数据变了。
DIFF_FIELDS = (
    "category", "nature", "flavor", "toxicity", "meridians",
    "effects", "dosage", "indications", "cautions",
)


def existing_names() -> set[str]:
    """``data/herbs/`` 里**别人写的**条目名。这些一律不碰。

    只 glob 不递归，所以 ``_pending/`` 不会被读进来——这正是第 3 层隔离
    「不用改代码」的含义。
    """

    names: set[str] = set()
    for path in sorted(HERBS_DIR.glob("*.json")):
        if path.name in OWN_PRODUCTS:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        names.update(
            str(item["name"]) for item in items if isinstance(item, dict) and item.get("name")
        )
    return names


def load_published() -> dict[str, dict]:
    """当前已发布的抽取产物，作为 diff 基线。缺文件时返回空。"""

    if not PUBLISHED_JSON.exists():
        return {}
    raw = json.loads(PUBLISHED_JSON.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else [raw]
    return {str(item["name"]): item for item in items if isinstance(item, dict) and item.get("name")}


def _norm(value: object) -> str:
    if isinstance(value, list):
        return "、".join(sorted(str(v) for v in value))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value or "")


def diff_against(base: dict[str, dict], fresh: list[dict]) -> tuple[list[str], list[tuple[str, list[str]]], list[str]]:
    """返回 ``(新增, [(药名, 变了哪些字段)], 消失)``。"""

    added: list[str] = []
    changed: list[tuple[str, list[str]]] = []
    for item in fresh:
        name = str(item["name"])
        old = base.get(name)
        if old is None:
            added.append(name)
            continue
        fields = [
            field for field in DIFF_FIELDS if _norm(old.get(field)) != _norm(item.get(field))
        ]
        if fields:
            changed.append((name, fields))
    fresh_names = {str(item["name"]) for item in fresh}
    removed = sorted(name for name in base if name not in fresh_names)
    return added, changed, removed


def render_diff(
    added: list[str], changed: list[tuple[str, list[str]]], removed: list[str], total: int
) -> str:
    over = []
    if len(added) > MAX_ADDED:
        over.append(f"新增 {len(added)} 条 > {MAX_ADDED}")
    if len(changed) > MAX_CHANGED:
        over.append(f"修改 {len(changed)} 条 > {MAX_CHANGED}")

    lines = [
        "# 入库 diff 报告",
        "",
        f"待发布 {total} 条。对比基线：`data/herbs/textbook_promoted.json`。",
        "",
        f"- 新增 **{len(added)}** 条（阈值 {MAX_ADDED}）",
        f"- 修改 **{len(changed)}** 条（阈值 {MAX_CHANGED}）",
        f"- 消失 **{len(removed)}** 条",
        "",
    ]
    if over:
        lines += [
            "## ⚠ 超过阈值",
            "",
            "　".join(over) + "，必须加 `--force` 才写盘。",
            "",
            "超过阈值通常意味着「整批重抽」或「改了归一表」，不是正常增量。",
            "先确认这是预期的，再 force。",
            "",
        ]
    if changed:
        lines += ["## 字段变化的条目", "", "| 药名 | 变了哪些字段 |", "| --- | --- |"]
        lines += [f"| {name} | {'、'.join(fields)} |" for name, fields in changed[:80]]
        if len(changed) > 80:
            lines.append(f"| … | 另有 {len(changed) - 80} 条 |")
        lines.append("")
    if removed:
        lines += ["## 基线里有、这次没有的条目", ""]
        lines += [f"- {name}" for name in removed[:60]]
        if len(removed) > 60:
            lines.append(f"- …另有 {len(removed) - 60} 条")
        lines.append("")
    if added:
        lines += ["## 新增条目", "", "、".join(added[:200]) + ("…" if len(added) > 200 else ""), ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成待发布药材 JSON")
    parser.add_argument("--dry-run", action="store_true", help="只校验，不写文件")
    parser.add_argument("--force", action="store_true", help="确认整批变更（超过 diff 阈值时）")
    args = parser.parse_args(argv)

    # ---- 门槛一：抽样必须全部看完 ----
    unseen = draft.pending_samples()
    if unseen:
        names = "、".join(item["herb_name"] for item in unseen[:10])
        print(f"拒绝：抽样还有 {len(unseen)} 条没看（{names}…）")
        print("先跑 scripts.textbook.verify_samples，再跑 scripts.textbook.approve_samples")
        return 1

    approved = draft.approved_names()
    if not approved:
        print("拒绝：herb_draft 里没有任何 approved 草稿")
        return 1

    gated = json.loads(GATED_JSON.read_text(encoding="utf-8"))["herbs"]
    entries = load_textbook_entries()
    alias_map = json.loads(ALIAS_MAP.read_text(encoding="utf-8")) if ALIAS_MAP.exists() else {}
    extra_aliases = alias_map.get("extra_aliases") or {}
    have = existing_names()

    fresh: list[dict] = []
    skipped: list[tuple[str, str]] = []
    not_signed: list[str] = []

    for entry in gated:
        name = entry["name"]
        if name in have:
            continue  # 已有手写条目，抽取版按 curated-first 裁掉了
        if name not in approved:
            not_signed.append(name)
            continue
        meta = entries.get(name)
        if not meta or not meta.get("slug"):
            skipped.append((name, "教材词表里没有拼音/ slug，拿不到实体 id"))
            continue

        herb_id = f"herb_{meta['slug']}"
        try:
            candidate = build_herb(
                herb_id, name, meta.get("pinyin", ""), entry,
                {"aliases": extra_aliases.get(name, [])},
            )
            Herb.model_validate(candidate)
        except Exception as exc:  # noqa: BLE001 - 校验失败要如实报出来，不能吞
            skipped.append((name, f"{type(exc).__name__}: {exc}"))
            continue
        fresh.append(candidate)

    base = load_published()
    added, changed, removed = diff_against(base, fresh)
    report = render_diff(added, changed, removed, len(fresh))

    print(f"已签字 {len(approved)}，可发布 {len(fresh)}，未签字跳过 {len(not_signed)}，校验失败 {len(skipped)}")
    print(f"diff: 新增 {len(added)} / 修改 {len(changed)} / 消失 {len(removed)}")
    for name, why in skipped[:20]:
        print(f"   - {name}: {why}")

    over = len(added) > MAX_ADDED or len(changed) > MAX_CHANGED
    if args.dry_run:
        print("\n--dry-run，未写文件。")
        if over:
            print("（真实运行会被 diff 阈值拦下，需要 --force）")
        return 0

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(report, encoding="utf-8")
    print(f"md={OUT_MD}")

    if over and not args.force:
        print(
            f"\n拒绝写盘：新增 {len(added)}（阈值 {MAX_ADDED}）/ 修改 {len(changed)}（阈值 {MAX_CHANGED}）。"
            "\n看一眼 deliverables/diff_report.md，确认无误再加 --force。"
        )
        return 1

    PENDING_JSON.parent.mkdir(parents=True, exist_ok=True)
    PENDING_JSON.write_text(
        json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\njson={PENDING_JSON}")
    print("这是**待发布区**，data/herbs 还没变。确认后跑 scripts.textbook.publish_pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
