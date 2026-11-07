"""从教材数据生成**真题**，替掉 `app/quiz/seed.py` 的占位题。

## 占位题为什么必须换掉

`seed.py` 的题干是「围绕知识点「麻黄」进行复习时，下列哪项是本题对应的核心知识点？」，
选项是**其他知识点名**（性味归经 / 升降浮沉 / 配伍禁忌）。那些名字在图谱里
**没有实体**，所以：

- 错题归因解析不到「你把哪味药搞混了」，只能报 `unknown`；
- 干扰项本身没有任何医学含义，做对了也不代表会了。

## 真题从哪来

只从教材已经抽好的字段出题，**不调模型、不编内容**：

| 题型 | 依据 | 唯一性前提 |
|---|---|---|
| 功效→药 | 教材【功效】原文 | 全库只有这一味药是这套功效 |
| 主治→药 | 教材【应用】的证名 | 全库只有这一味药列这个证 |
| 配伍禁忌 | 十八反 / 十九畏 边 | 干扰项与该药**不**相反 |

唯一性前提是硬条件，不是保险。不满足就不出这道题——**出了就有两个正确答案**，
那是错题，不是难题。实测（162 味药、442 个教材条目）：功效题 54 道、主治题 153 道、
配伍禁忌 12 道，合计 **219 道，覆盖 55 味药**。被跳过的 36 个主治是「两味以上的药
都列了这个证」（例如「风寒感冒」），问「哪味药主治X」就有多个答案。

## 干扰项怎么选

优先挑**教材明文要求鉴别**的药（`同类鉴别` 边），其次同节、同章。这样学生选错时，
归因能指着一条真实的教材鉴别句说「你把这两味搞混了」，而不是只能说「不知道」。

## 选项为什么必须是药名

这是本次的核心约束：错题归因要把「选了哪一项」解析成图谱实体。选项是「性味归经」
这种知识点名，解析不出来，归因链就断在第一步。

用法（在 backend 下）::

    python scripts/gen_questions.py --dry-run
    python scripts/gen_questions.py --db-path .data/qihuang.db --purge-placeholder
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.profile.store import DB_PATH, connect
from app.quiz.storage import init_quiz_db, upsert_exam_point, upsert_question

HERBS_DIR = BACKEND_ROOT / "data" / "herbs"
ENTRIES_PATH = BACKEND_ROOT / "data" / "curated" / "textbook_entries.json"

KIND_EFFECT = "effect_to_herb"
KIND_INDICATION = "indication_to_herb"
KIND_FORBIDDEN = "forbidden_pair"

#: 教材里表示配伍禁忌的关系类型。十八反、十九畏原文都在 `data/curated` 里校验过。
FORBIDDEN_RELATIONS = ("十八反", "十九畏")

#: 题目的来源前缀。
#:
#: 这批题是**依据教材自编**的，不是历年真题——`quiz.schemas.QuizQuestion` 里
#: `real` 级别要求「可核实的年份 + 出处」，我们没有年份，标成 `real` 就是假声明，
#: 会被校验器当场拒掉（`real questions require a verified year and source`）。
#: 标 `original` 是如实的：题是我们出的，依据逐条可回溯到教材页码。
#: `app/quiz/service.py::_source_level` 认得这个前缀。
SOURCE_PREFIX = "教材自编："

#: 占位题的来源前缀。`seed.py` 和 `build_question_bank.py` 的 `original` 分支
#: 都写这个前缀——它的含义就是「照着知识点名编的题」，没有教材依据。
#: 与上面的 `SOURCE_PREFIX` 必须能区分开，否则清理会误删真题。
PLACEHOLDER_SOURCE = "original: knowledge_node:"


def load_herbs() -> dict[str, dict[str, Any]]:
    """读全部药材 JSON，同名取字段更全的那份。"""

    merged: dict[str, dict[str, Any]] = {}
    for path in sorted(HERBS_DIR.glob("*.json")):
        for herb in json.loads(path.read_text(encoding="utf-8")):
            name = str(herb.get("name") or "").strip()
            if not name:
                continue
            current = merged.get(name)
            if current is None or len(json.dumps(herb, ensure_ascii=False)) > len(
                json.dumps(current, ensure_ascii=False)
            ):
                merged[name] = herb
    return merged


def load_entries() -> dict[str, dict[str, Any]]:
    """教材条目索引：药名 → 章号 / 节 / 页码 / **原文功效**。

    题干引的是 ``fields['功效']`` 的**原始字符串**，不是把归一后的 ``effects``
    列表拼起来。归一过程会把「；」改成「，」、把「生用：」拆成独立项——拼出来的
    句子和教材对不上，那就成了「看起来像原文的转述」。要引就引原文。
    """

    payload = json.loads(ENTRIES_PATH.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for entry in payload.get("entries") or []:
        name = str(entry.get("name") or "")
        if not name:
            continue
        fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
        result[name] = {
            "chapter": entry.get("chapter") or "",
            "chapter_no": entry.get("chapter_no") or "",
            "section": entry.get("section") or "",
            "page": entry.get("page"),
            "effects_text": str(fields.get("功效") or "").strip(),
            "application_text": str(fields.get("应用") or "").strip(),
        }
    return result


def _stable_suffix(text: str, size: int = 10) -> str:
    """稳定短哈希。不用 ``hash()``——它每个进程随机化，重跑会造出重复题目。"""

    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:size]


def _point_names(db_path: Path) -> set[str]:
    with connect(db_path) as conn:
        return {str(row[0]) for row in conn.execute("SELECT name FROM knowledge_node WHERE level = 'point'")}


def node_index(db_path: Path) -> dict[str, str]:
    """药名 → 知识点节点 id。

    先按名字精确匹配；对不上再退回**唯一前缀**（``紫苏叶`` 的节点在 seed 里叫
    ``紫苏``）。**不做相似度匹配**——那会把两味药并成一条。

    前缀只在唯一时采纳：``贝母`` 同时是 ``川贝母``、``浙贝母`` 的前缀，两个候选就
    不认，宁可这味药没有节点，也不能挂到错的那味药上。
    """

    with connect(db_path) as conn:
        points = {
            str(row[1]): str(row[0])
            for row in conn.execute("SELECT id, name FROM knowledge_node WHERE level = 'point'")
        }
    result = dict(points)
    for herb_name in load_herbs():
        if herb_name in result:
            continue
        candidates = [node_id for node_name, node_id in points.items() if herb_name.startswith(node_name)]
        if len(candidates) == 1:
            result[herb_name] = candidates[0]
    return result


def textbook_graph(
    db_path: Path,
) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], dict[str, str]]:
    """取三样图谱事实：鉴别对、禁忌对（带关系类型）、实体 id → 药名。"""

    with connect(db_path) as conn:
        # 图谱还没建（全新库）时表都不在。这时没有鉴别边、没有禁忌边，只能出
        # 「功效→药 / 主治→药」两类——不能因为缺表就整个脚本炸掉。
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "kg_entity" not in tables or "kg_edge" not in tables:
            return {}, {}, {}
        entity_names = {
            str(row[0]): str(row[1])
            for row in conn.execute("SELECT id, name FROM kg_entity WHERE kind = 'herb'")
        }
        differentiation: dict[str, set[str]] = defaultdict(set)
        forbidden: dict[str, dict[str, str]] = defaultdict(dict)
        for source, target, relation in conn.execute(
            "SELECT source_id, target_id, relation_type FROM kg_edge"
        ):
            left, right = entity_names.get(str(source)), entity_names.get(str(target))
            if not left or not right:
                continue
            if relation == "同类鉴别":
                differentiation[left].add(right)
                differentiation[right].add(left)
            elif relation in FORBIDDEN_RELATIONS:
                forbidden[left][right] = str(relation)
                forbidden[right][left] = str(relation)
    return differentiation, forbidden, entity_names


def _option_text(herb: dict[str, Any]) -> str:
    return str(herb.get("name") or "")


def _citation(entry: dict[str, Any], name: str) -> str:
    parts = ["《中药学》(钟赣生)"]
    if entry.get("chapter_no"):
        parts.append(str(entry["chapter_no"]))
    if entry.get("section"):
        parts.append(str(entry["section"]))
    parts.append(name)
    text = "·".join(parts)
    if entry.get("page"):
        text += f"·p{entry['page']}"
    return text


def _effects(herb: dict[str, Any]) -> list[str]:
    return [str(item) for item in (herb.get("effects") or []) if item]


def _textbook_syndromes(herb: dict[str, Any]) -> list[dict[str, str]]:
    return [
        item
        for item in (herb.get("indications") or [])
        if item.get("origin") == "textbook" and item.get("syndrome")
    ]


def _pick_options(
    target: str,
    *,
    valid: list[str],
    differentiation: dict[str, set[str]],
    same_section: dict[str, set[str]],
    same_chapter: dict[str, set[str]],
    count: int = 3,
) -> list[str]:
    """挑干扰项。顺序：教材鉴别对 → 同节 → 同章 → 其他。

    ``valid`` 是**已经通过正确性过滤**的候选（不含正确答案、不含会构成第二正确答案
    的药）。过滤在外面做，这里只按优先级排序并取前 ``count`` 个。

    **不能整表洗牌再取前几个**——那会把优先级抹平。实测过一次：人参的干扰项里
    出现了石膏，因为 162 味药一起洗，补气药那批被洗散了。取前 N 之后再由调用方
    洗牌，既保证干扰项来自最近的邻居，又不让正确答案总落在 A。
    """

    pool = set(valid)
    pool.discard(target)
    ordered: list[str] = []
    for group in (
        differentiation.get(target, set()),
        same_section.get(target, set()),
        same_chapter.get(target, set()),
    ):
        for name in sorted(group & pool):
            if name not in ordered:
                ordered.append(name)
    for name in sorted(pool):
        if name not in ordered:
            ordered.append(name)
    return ordered[:count]


def _build_effect_questions(
    herbs: dict[str, dict[str, Any]],
    nodes: dict[str, str],
    entries: dict[str, dict[str, Any]],
    differentiation: dict[str, set[str]],
    same_section: dict[str, set[str]],
    same_chapter: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    skipped: Counter[str] = Counter()

    def effects_text(name: str) -> str:
        """教材【功效】原文。取不到才退回归一后的拼接。"""

        raw = str(entries.get(name, {}).get("effects_text") or "").strip()
        return raw or "，".join(_effects(herbs.get(name, {})))

    signature: dict[str, list[str]] = defaultdict(list)
    for name in herbs:
        text = effects_text(name)
        if text:
            signature[text].append(name)

    questions: list[dict[str, Any]] = []
    for name, herb in herbs.items():
        text = effects_text(name)
        if not text:
            continue
        if name not in nodes:
            continue
        if len(signature[text]) != 1:
            # 两味药功能一模一样，问「哪味药的功能是X」就有两个正确答案。
            skipped["effect_not_unique"] += 1
            continue
        quote = text.rstrip("。")
        distractors = _pick_options(
            name,
            valid=[other for other in herbs if effects_text(other) != text],
            differentiation=differentiation,
            same_section=same_section,
            same_chapter=same_chapter,
        )
        if len(distractors) < 3:
            skipped["not_enough_distractors"] += 1
            continue
        options = [name, *distractors]
        random.Random(f"order:{KIND_EFFECT}:{name}").shuffle(options)
        keys = ["A", "B", "C", "D"]
        wrong_lines = [
            f"{other}：{effects_text(other).rstrip('。') or '教材未载'}" for other in distractors
        ]
        analysis = (
            f"教材【功效】原文：「{text}」"
            f"其余选项的功能：{'；'.join(wrong_lines)}。"
            f"出处：{_citation(entries.get(name, {}), name)}"
        )
        questions.append(
            {
                "kind": KIND_EFFECT,
                "herb": name,
                "node_id": nodes[name],
                "question_id": f"rq_tb_eff_{_stable_suffix(name)}",
                "stem": f"下列哪味药的功能是「{quote}」？",
                "options": [
                    {"key": keys[index], "text": _option_text(herbs[item])}
                    for index, item in enumerate(options)
                ],
                "answer": [keys[options.index(name)]],
                "analysis": analysis,
                "source": f"{SOURCE_PREFIX}{_citation(entries.get(name, {}), name)}",
            }
        )
    return questions, skipped


def _build_indication_questions(
    herbs: dict[str, dict[str, Any]],
    nodes: dict[str, str],
    entries: dict[str, dict[str, Any]],
    differentiation: dict[str, set[str]],
    same_section: dict[str, set[str]],
    same_chapter: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    skipped: Counter[str] = Counter()
    owners: dict[str, list[str]] = defaultdict(list)
    for name, herb in herbs.items():
        for item in _textbook_syndromes(herb):
            owners[item["syndrome"]].append(name)

    def shares_syndrome(herb_name: str, syndrome: str) -> bool:
        """干扰项只要**以任何形式**载有同一证候就排除。

        精确相等不够：「风寒感冒」和「风寒感冒夹湿」是两个字符串，临床上是一回事。
        双向子串都算冲突。人工归纳的证名（``origin=manual``，如「脾气虚证」）也一起
        比——它粒度更粗，但确实是同一个证，放进来照样会让题目出现两个正确答案。
        宁可少一个干扰项，也不能出一道有两个正确答案的题。
        """

        for item in herbs.get(herb_name, {}).get("indications") or []:
            other = str(item.get("syndrome") or "")
            if other and (other == syndrome or other in syndrome or syndrome in other):
                return True
        return False

    questions: list[dict[str, Any]] = []
    for name, herb in herbs.items():
        if name not in nodes:
            continue
        for item in _textbook_syndromes(herb):
            syndrome = str(item["syndrome"])
            if len(owners[syndrome]) != 1:
                skipped["syndrome_not_unique"] += 1
                continue
            distractors = _pick_options(
                name,
                valid=[other for other in herbs if other != name and not shares_syndrome(other, syndrome)],
                differentiation=differentiation,
                same_section=same_section,
                same_chapter=same_chapter,
            )
            if len(distractors) < 3:
                skipped["not_enough_distractors"] += 1
                continue
            options = [name, *distractors]
            random.Random(f"order:{KIND_INDICATION}:{name}:{syndrome}").shuffle(options)
            keys = ["A", "B", "C", "D"]
            description = str(item.get("description") or "").strip()
            analysis = (
                f"教材【应用】列「{syndrome}」：{description}"
                f"该药功能为「{'，'.join(_effects(herb))}」。"
                f"其余选项教材未列此主治。"
                f"出处：{_citation(entries.get(name, {}), name)}"
            )
            questions.append(
                {
                    "kind": KIND_INDICATION,
                    "herb": name,
                    "node_id": nodes[name],
                    "question_id": f"rq_tb_ind_{_stable_suffix(name + '|' + syndrome)}",
                    "stem": f"下列哪味药主治「{syndrome}」？",
                    "options": [
                        {"key": keys[index], "text": _option_text(herbs[item])}
                        for index, item in enumerate(options)
                    ],
                    "answer": [keys[options.index(name)]],
                    "analysis": analysis,
                    "source": f"{SOURCE_PREFIX}{_citation(entries.get(name, {}), name)}",
                }
            )
    return questions, skipped


def _build_forbidden_questions(
    herbs: dict[str, dict[str, Any]],
    nodes: dict[str, str],
    entries: dict[str, dict[str, Any]],
    forbidden: dict[str, dict[str, str]],
    differentiation: dict[str, set[str]],
    same_section: dict[str, set[str]],
    same_chapter: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """配伍禁忌题：一味药只出一道。

    甘草有甘遂、大戟、海藻、芫花四个反药。若按「每对一道」出，就会得到四道
    **题干完全相同、答案不同**的题——`real_question` 的唯一键是
    `(exam_point_id, stem)`，它们会互相覆盖，最后只剩一条，答案还取决于写入顺序。
    所以每题只拿一个反药当正确答案，**其余反药一律不进干扰项**：只要选项里出现
    第二个反药，这题就有两个正确答案。
    """

    skipped: Counter[str] = Counter()
    questions: list[dict[str, Any]] = []
    for name in sorted(forbidden):
        if name not in nodes:
            continue
        partners = [item for item in sorted(forbidden[name]) if item in herbs]
        if not partners:
            skipped["partner_without_monograph"] += 1
            continue
        partner = partners[0]
        relation = forbidden[name][partner]
        distractors = _pick_options(
            name,
            valid=[other for other in herbs if other != name and other not in forbidden[name]],
            differentiation=differentiation,
            same_section=same_section,
            same_chapter=same_chapter,
        )
        if len(distractors) < 3:
            skipped["not_enough_distractors"] += 1
            continue
        options = [partner, *distractors]
        random.Random(f"order:{KIND_FORBIDDEN}:{name}").shuffle(options)
        keys = ["A", "B", "C", "D"]
        analysis = (
            f"教材配伍禁忌：「{name}」与「{partner}」属{relation}，不宜同用。"
            f"其余选项与「{name}」无禁忌记载。"
            f"出处：{_citation(entries.get(name, {}), name)}"
        )
        questions.append(
            {
                "kind": KIND_FORBIDDEN,
                "herb": name,
                "node_id": nodes[name],
                "question_id": f"rq_tb_forb_{_stable_suffix(name)}",
                "stem": f"下列哪味药与「{name}」同用属于配伍禁忌？",
                "options": [
                    {"key": keys[index], "text": _option_text(herbs[item])}
                    for index, item in enumerate(options)
                ],
                "answer": [keys[options.index(partner)]],
                "analysis": analysis,
                "source": f"{SOURCE_PREFIX}{_citation(entries.get(name, {}), name)}",
            }
        )
    return questions, skipped


def build(db_path: Path = DB_PATH) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    herbs = load_herbs()
    entries = load_entries()
    nodes = node_index(db_path)
    point_names = _point_names(db_path)
    differentiation, forbidden, _ = textbook_graph(db_path)

    # 药材 JSON 里混着 9 条十八反/十九畏的**写法**（乌头、牙硝、官桂、砒霜…）：
    # `chapter` 为空，`aliases` 指向教材正名。它们是给关系解析用的桩，不是独立药材。
    #
    # 留在出题池里会造出「答案不唯一」的题——实测 29 道题的选项里 `川乌` 和 `乌头`
    # 同时出现，二者是同一味药，选哪个都对；`牙硝` 与 `芒硝` 同理。唯一性前提一破，
    # 题就是错的，判卷会判学生错。
    alias_of = {
        name: str((herb.get("aliases") or [""])[0])
        for name, herb in herbs.items()
        if name not in entries and not herb.get("chapter") and herb.get("aliases")
    }
    not_in_textbook = sorted(name for name in herbs if name not in entries)
    # 出题池只收**教材立过条目的**药：题干引的原文、出处页码都来自条目，
    # 没条目的药连「出处」都写不出来。
    herbs = {name: herb for name, herb in herbs.items() if name in entries}

    # 图谱里的名字先翻译成正名，否则 `甘草—大戟` 这种边在池子里找不到 `大戟`
    # （池子里只有教材条目 `京大戟`），题就白丢了。
    differentiation = {
        alias_of.get(name, name): {alias_of.get(peer, peer) for peer in peers}
        for name, peers in differentiation.items()
    }
    forbidden = {
        alias_of.get(name, name): {
            alias_of.get(peer, peer): relation for peer, relation in partners.items()
        }
        for name, partners in forbidden.items()
    }

    same_section: dict[str, set[str]] = defaultdict(set)
    same_chapter: dict[str, set[str]] = defaultdict(set)
    for name, herb in herbs.items():
        for other, peer in herbs.items():
            if other == name:
                continue
            if herb.get("subcategory") and herb.get("subcategory") == peer.get("subcategory"):
                same_section[name].add(other)
            if herb.get("category") and herb.get("category") == peer.get("category"):
                same_chapter[name].add(other)

    questions: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for builder in (
        _build_effect_questions,
        _build_indication_questions,
    ):
        built, more = builder(herbs, nodes, entries, differentiation, same_section, same_chapter)
        questions.extend(built)
        skipped.update(more)
    built, more = _build_forbidden_questions(
        herbs, nodes, entries, forbidden, differentiation, same_section, same_chapter
    )
    questions.extend(built)
    skipped.update(more)

    stats = {
        "herbs": len(herbs),
        "herbs_with_node": len([name for name in herbs if name in nodes]),
        # 没有知识点节点的药出不了题：`exam_point.node_id` 有外键，挂不上。
        "herbs_without_node": len([name for name in herbs if name not in nodes]),
        # 不进池子的别名桩，列出来可核对。
        "herbs_not_in_textbook": not_in_textbook,
        # 靠唯一前缀挂到旧节点名上的药（`紫苏叶` → 节点 `紫苏`）。列出来可核对。
        "alias_adopted": {
            herb: node for herb, node in nodes.items() if herb not in point_names
        },
        "questions": len(questions),
        "by_kind": dict(Counter(item["kind"] for item in questions)),
        "distinct_herbs": len({item["herb"] for item in questions}),
        # 题目主体不在 153 味池子里的，只有 `贝母`：十八反原文写「贝母」，教材把它
        # 拆成川贝母、浙贝母两条，没有「贝母」这个条目。题本身是对的（答案川乌），
        # 但出处只能写到书名，缺章节页码。列出来，别让它藏在统计里。
        "subjects_not_in_textbook": sorted({item["herb"] for item in questions} - set(herbs)),
        "skipped": dict(skipped),
    }
    return questions, stats


def purge_placeholders(db_path: Path) -> int:
    """删掉 `seed.py` 造的占位题。

    判据是 `source` 前缀，不是题干。占位题有三种模板（单选 / 简答 / 病例），
    按题干里的某一句话删只能删掉三分之一——实测过：按「核心知识点」那句删，
    36 道只走了 12 道，剩 24 道简答和病例题留着，答案就是药名本身，照样是废题。

    `original: knowledge_node:` 的含义就是「照着知识点名编的、没有教材依据的题」，
    按它删不会误伤教材题（教材题 source 以「教材：」开头）。
    """

    with connect(db_path) as conn:
        ids = [
            str(row[0])
            for row in conn.execute(
                "SELECT id FROM real_question WHERE source LIKE ?", (f"{PLACEHOLDER_SOURCE}%",)
            )
        ]
        for question_id in ids:
            conn.execute("DELETE FROM real_question WHERE id = ?", (question_id,))
        # 只清同一来源的考点，不按「没有题目」清——那会连带删掉别处建的、
        # 只是暂时没题目的考点。
        conn.execute(
            "DELETE FROM exam_point WHERE source LIKE ? "
            "AND id NOT IN (SELECT DISTINCT exam_point_id FROM real_question)",
            (f"{PLACEHOLDER_SOURCE}%",),
        )
        conn.commit()
    return len(ids)


def persist(questions: list[dict[str, Any]], db_path: Path) -> int:
    init_quiz_db(db_path)
    with connect(db_path) as conn:
        frequencies = {
            str(row[0]): float(row[1])
            for row in conn.execute("SELECT id, exam_frequency FROM knowledge_node")
        }
    written = 0
    for item in questions:
        point_id = upsert_exam_point(
            node_id=item["node_id"],
            name=item["herb"],
            exam_years=[],
            frequency=frequencies.get(item["node_id"], 0.6),
            source=item["source"],
            db_path=db_path,
        )
        upsert_question(
            {
                "id": item["question_id"],
                "exam_point_id": point_id,
                "stem": item["stem"],
                "options": item["options"],
                "answer": item["answer"],
                "analysis": item["analysis"],
                "year": None,
                "source": item["source"],
                "question_type": "single",
            },
            db_path,
        )
        written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="用教材数据生成真题")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--purge-placeholder", action="store_true")
    args = parser.parse_args()

    questions, stats = build(args.db_path)
    if args.dry_run:
        print(json.dumps({**stats, "dry_run": True}, ensure_ascii=False, indent=2))
        for sample in questions[:3]:
            print(json.dumps(sample, ensure_ascii=False, indent=2)[:1200])
        return
    purged = purge_placeholders(args.db_path) if args.purge_placeholder else 0
    written = persist(questions, args.db_path)
    print(
        json.dumps(
            {**stats, "purged_placeholders": purged, "written": written},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
