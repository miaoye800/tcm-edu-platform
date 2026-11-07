"""P4.5 关系发现：从教材【应用】的散文里召回配伍候选，过 R001–R011 闸门。

## 为什么从【应用】里挖

现有 T2 的 217 条「配伍」边全部来自人工整理的 ``data/herbs/*.json``
``combinations`` 字段——只有 76 味精编药有。剩下 366 味药在教材里明明写着
「每与羌活、苍术、川芎等祛风湿、止痛药同用」，这些句子一条都没变成边。

统计：442 味药里 367 味、1208 处这样的句子。这是 P4.5 要补的缺口。

## 只从【应用】召回，不从【使用注意】召回

【使用注意】写的是禁忌：「不宜与芒硝、玄明粉同用」。那是**配伍禁忌**，
不是配伍。这类约束已经由人工维护在 ``incompatibility.json``（T1 十八反/
十九畏/妊娠禁忌），再去【使用注意】里挖一遍只会把「不能一起用」建成
「一起用」的边——方向反了。

## 只召回不算数

召回 4000 多条候选，能过闸门的只是一部分。过不了的写进
``kg_edge_staging`` 并附原因（``status='rejected'``），不进 ``kg_edge``。
**被拒绝的候选也留档**——将来有人问「为什么不建这条边」，答案是现成的。

用法::

    python -m scripts.textbook.relations --dry-run   # 只统计，不写库
    python -m scripts.textbook.relations             # 写 staging + kg_edge

注意顺序：``build_kg`` 会清空 ``kg_edge`` 重建，所以**先跑 build_kg，再跑这里**。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import BACKEND_ROOT
from app.kg.constants import tier_of
from app.kg.rules import DESCRIBE, RelationCandidate, evaluate
from app.kg.store import KgEdge, upsert_edges
from app.profile.store import DB_PATH

GATED_JSON = BACKEND / "data" / "curated" / "textbook_gated.json"
INCOMPATIBILITY = BACKEND / "data" / "curated" / "incompatibility.json"
OUT_MD = BACKEND_ROOT.parent / "deliverables" / "relations.md"

#: 一句话一句。教材用「。」「；」断句。
_SENTENCE = re.compile(r"[^。；;]+[。；;]?")

#: 「常与 X、Y、Z 等……同用」。捕获组只取列举部分。
#:
#: 前面的「常/每/多/均/可」是可选的——教材有的写「每与」，有的直接写「与」。
#: 结尾的「等药/等品」要吃掉，否则「川芎等祛风湿药」整串会被当成一个药名。
_PAIR = re.compile(
    r"(?:常|每|多|均|可)?(?:与|配用?|同用|配伍)"
    r"([^。；;]{2,40}?)"
    r"(?:等(?:药|品)?)?"
    r"(?:同用|配伍|合用|相须为用|相使为用|为用|同煎|研末)"
)

#: 药名前面偶尔带的动词。「以荆芥」「用薄荷」。
_LEAD = re.compile(r"^(?:以|用|加|入|取)")

#: 描述性短语的特征：以「药」结尾、含「的」、含「其他」——都不是药名。
_NOISE = re.compile(r"(?:药$|^其他|的|诸药|之类)")


def load_index(db_path: Path) -> tuple[dict[str, str], set[str], dict[str, str]]:
    """``(名字→实体id, 有结构化条目的药名, id→正名)``。"""

    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT id, name FROM kg_entity WHERE kind='herb'").fetchall()
        aliases = conn.execute(
            "SELECT alias, entity_id FROM kg_alias WHERE entity_id LIKE 'herb_%'"
        ).fetchall()

    by_name = {str(name): str(eid) for eid, name in rows}
    canonical = {str(eid): str(name) for eid, name in rows}
    for alias, eid in aliases:
        by_name.setdefault(str(alias), str(eid))

    have_monograph: set[str] = set()
    for path in sorted((BACKEND / "data" / "herbs").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        have_monograph.update(
            str(item["name"]) for item in items if isinstance(item, dict) and item.get("name")
        )
    return by_name, have_monograph, canonical


def load_t1_pairs() -> set[frozenset[str]]:
    """十八反 / 十九畏的药对（无序）。用来做 R010。"""

    if not INCOMPATIBILITY.exists():
        return set()
    payload = json.loads(INCOMPATIBILITY.read_text(encoding="utf-8"))
    pairs: set[frozenset[str]] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            # 形如 {"a": "川乌", "b": "半夏"} 或 {"pair": ["川乌","半夏"]}
            a, b = node.get("a") or node.get("source"), node.get("b") or node.get("target")
            if isinstance(a, str) and isinstance(b, str):
                pairs.add(frozenset({a, b}))
            pair = node.get("pair")
            if isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair):
                pairs.add(frozenset({str(pair[0]), str(pair[1])}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return pairs


def recall(name: str, entry: dict, by_name: dict[str, str]) -> list[RelationCandidate]:
    """从一味药的【应用】里召回候选。**不做判断**，判断交给闸门。"""

    text = (entry.get("quotes") or {}).get("应用", "")
    if not text:
        return []

    found: list[RelationCandidate] = []
    for sentence in _SENTENCE.findall(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        pairs: list[tuple[str, str]] = []
        for match in _PAIR.finditer(sentence):
            for token in re.split(r"[、，,]", match.group(1)):
                token = _LEAD.sub("", token.split("等")[0].strip()).strip()
                if len(token) < 2 or _NOISE.search(token):
                    continue
                pairs.append((token, match.group(0)))
        if not pairs:
            continue
        # 七情类别只在**一句里只有一对**时才敢标。一句列三味药又说「相须」，
        # 无法确定是跟哪一味相须，标上去就是臆测。原文没写七情词就是「配伍」。
        qiqing = next(
            (word for word in ("相须", "相使", "相畏", "相杀", "相恶") if word in sentence), ""
        )
        relation = (qiqing or "配伍") if len(pairs) == 1 else "配伍"
        for token, _ in pairs:
            found.append(
                RelationCandidate(
                    source_name=name,
                    target_name=token,
                    source_quote=text,
                    quote=sentence,
                    field="应用",
                    page=entry.get("page"),
                    relation_type=relation,
                )
            )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P4.5 教材配伍关系召回 + 闸门")
    parser.add_argument("--db", default=None, help="SQLite 路径，默认 QIHUANG_DB_PATH")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    entries = json.loads(GATED_JSON.read_text(encoding="utf-8"))["herbs"]
    by_name, have_monograph, canonical = load_index(db_path)
    t1_pairs = load_t1_pairs()

    with closing(sqlite3.connect(db_path)) as conn:
        existing = {
            (str(a), str(b), str(c))
            for a, b, c in conn.execute(
                "SELECT source_id, target_id, relation_type FROM kg_edge"
            )
        }

    candidates: list[RelationCandidate] = []
    for entry in entries:
        candidates.extend(recall(entry["name"], entry, by_name))

    # 每味药这次召回了几条——R011 要用
    per_source: Counter[str] = Counter(item.source_name for item in candidates)

    seen_pairs: set[tuple[str, str, str]] = set()
    staged: list[dict] = []
    approved: list[KgEdge] = []
    code_counter: Counter[str] = Counter()

    for item in candidates:
        item.source_id = by_name.get(item.source_name)
        item.target_id = by_name.get(item.target_name)
        # 有结构化条目要看**正名**：候选里写的是「杏仁」，正名是「苦杏仁」，
        # 拿别名去比对 data/herbs 的名字会一律判成「没有条目」。
        target_canonical = canonical.get(str(item.target_id), item.target_name)
        item.target_has_monograph = target_canonical in have_monograph
        item.partner_count = per_source[item.source_name]
        item.t1_conflict = (
            item.source_id is not None
            and item.target_id is not None
            and frozenset({item.source_name, item.target_name}) in t1_pairs
        )
        item.already_exists = (
            item.source_id,
            item.target_id,
            item.relation_type,
        ) in existing

        verdict = evaluate(item)
        for hit in verdict.hits:
            if hit.status != "pass":
                code_counter[hit.code] += 1

        # 去重键要和表上的唯一约束一致（source,target,type,label）——
        # 只按药对去重的话，同一对药既被召成「配伍」又被召成「相须」时会
        # 两条都留下，插 staging 直接撞 UNIQUE 约束。
        #
        # 解析不到的目标药没有 id，用名字占位——否则所有「解析不到」的候选
        # 会挤成同一个键，只剩一条留档，其余为什么被拒就查不到了。
        target_key = str(item.target_id) if item.target_id else f"?{item.target_name}"
        pair_key = (str(item.source_id), target_key, item.relation_type)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        record = {
            "source_id": item.source_id,
            "target_id": item.target_id,
            "relation_type": item.relation_type,
            "tier": int(tier_of(item.relation_type)),
            "label": "",
            "evidence": [
                {
                    "quote": item.quote,
                    "field": item.field,
                    "page": item.page,
                    "book": "《中药学》新世纪第四版（钟赣生主编）",
                }
            ],
            "confidence": 0.6,
            "derived": 1,
            "status": "approved" if verdict.status == "pass" else "rejected",
            "meta": {
                "source_name": item.source_name,
                "target_name": item.target_name,
                "gate_status": verdict.status,
                "gate_detail": verdict.describe(),
                "recall": "textbook.app",
            },
        }
        staged.append(record)

        if record["status"] == "approved":
            approved.append(
                KgEdge(
                    source_id=str(item.source_id),
                    target_id=str(item.target_id),
                    relation_type=item.relation_type,
                    tier=int(tier_of(item.relation_type)),
                    label="",
                    evidence=record["evidence"],
                    confidence=0.6,
                    derived=1,
                    status="approved",
                    meta={"source_name": item.source_name, "recall": "textbook.app"},
                )
            )

    verdicts = Counter(record["status"] for record in staged)
    print(f"候选 {len(candidates)} 条")
    print(f"过闸门 {verdicts.get('approved', 0)}，拒绝 {verdicts.get('rejected', 0)}")
    print(f"闸门触发：{dict(sorted(code_counter.items()))}")

    if args.dry_run:
        print("\n--dry-run：未写库")
        return 0

    # staging 表两端都有外键，解析不到的候选**插不进去**。这类候选本身
    # 也不是「被否掉的关系」，而是「没召回成功」——只在报告里留样本，
    # 不占库里的位置。
    storable = [
        record for record in staged if record["source_id"] and record["target_id"]
    ]

    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DELETE FROM kg_edge_staging")
        conn.executemany(
            """
            INSERT INTO kg_edge_staging(source_id, target_id, relation_type, tier, label,
                                        evidence_json, confidence, derived, status,
                                        created_at, meta_json)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?)
            """,
            [
                (
                    record["source_id"],
                    record["target_id"],
                    record["relation_type"],
                    record["tier"],
                    record["label"],
                    json.dumps(record["evidence"], ensure_ascii=False),
                    record["confidence"],
                    record["derived"],
                    record["status"],
                    json.dumps(record["meta"], ensure_ascii=False),
                )
                for record in storable
            ],
        )
        conn.commit()

    written = upsert_edges(approved, db_path)
    print(f"staging 写入 {len(storable)} 条（{len(staged) - len(storable)} 条两端解析不全，只进报告）")
    print(f"kg_edge 新增/更新 {written} 条")
    print(f"db={db_path}")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(
        render_report(staged, verdicts, code_counter, per_source), encoding="utf-8"
    )
    print(f"md={OUT_MD}")
    return 0


def render_report(
    staged: list[dict],
    verdicts: Counter[str],
    code_counter: Counter[str],
    per_source: Counter[str],
) -> str:
    """一页说明：召回了什么、闸门挡了什么、为什么挡。"""

    lines = [
        "# P4.5 教材配伍关系召回报告",
        "",
        "来源：《中药学》(钟赣生·新世纪第四版) 【应用】字段",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 候选 | {len(staged)} |",
        f"| 过闸门 | {verdicts.get('approved', 0)} |",
        f"| 拒绝 | {verdicts.get('rejected', 0)} |",
        "",
        "## 闸门触发",
        "",
        "| 编号 | 查什么 | 触发次数 |",
        "| --- | --- | --- |",
    ]
    for code, label in DESCRIBE.items():
        lines.append(f"| {code} | {label} | {code_counter.get(code, 0)} |")

    lines += ["", "## 拒绝样本（每类最多 5 条）", ""]
    by_code: dict[str, list[dict]] = defaultdict(list)
    for record in staged:
        if record["status"] == "approved":
            continue
        detail = str(record["meta"].get("gate_detail") or "")
        code = detail.split()[0] if detail else "?"
        by_code[code].append(record)
    for code in DESCRIBE:
        samples = by_code.get(code) or []
        if not samples:
            continue
        lines.append(f"**{code}** {DESCRIBE[code]}")
        for record in samples[:5]:
            meta = record["meta"]
            lines.append(
                f"- {meta.get('source_name')} → {meta.get('target_name')}："
                f"{meta.get('gate_detail')}"
            )
        lines.append("")

    lines += ["## 召回最多的 15 味", "", "| 药材 | 候选数 |", "| --- | --- |"]
    for name, count in per_source.most_common(15):
        lines.append(f"| {name} | {count} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
