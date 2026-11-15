"""结构原型 → 导图骨架。

原型是「这类题的正确答案长什么样」的固化。三件事靠它：

1. **兜底不空壳**。没有模型（无 key、超时、返回空）时，这里必须凭真实素材
   长出一张可读的图。「待补充资料」这种节点一张都不该出现。
2. **给模型的槽位**。谋篇立骨那一步不交给模型编——分类题的骨架就是教材
   目录，辨别题的骨架就是对比维度。让人机都按同一个骨架填，图才稳定。
3. **依据分级**。每个节点自带 ``evidence``：教材/图谱/推断。前端角标与底栏
   三档占比都数它。

九种原型：monograph / taxonomy / comparison / reverse / filter / relation /
syndrome / exam / concept。另有 ``overview``——整门课或整章，骨架直接取教材目录。
"""

from __future__ import annotations

from typing import Any

from .draft import MAX_TOPIC, clean_topic, question_title
from .evidence import (
    EVIDENCE_GRAPH,
    EVIDENCE_MODEL,
    EVIDENCE_TEXTBOOK,
    EvidencePack,
    docs_for_names,
)
from .schemas import MindElixirData, MindmapMeta, MindmapNode

#: 单个一级分支下最多挂几个叶子。与 MindmapNode.children 的 12 上限留一档余量。
MAX_LEAVES = 10
#: 一级分支上限。再多就不是「一眼看懂」，而是另一棵树。
MAX_BRANCHES = 8


def build_archetype_tree(pack: EvidencePack) -> MindElixirData:
    """按原型长出整棵树。纯确定性，不调用模型。"""

    builder = _BUILDERS.get(pack.intent.archetype, _concept)
    root_topic = _title_of(pack)
    branches = builder(pack)
    if not branches:
        branches = _concept(pack)
    children = [node for node in branches if node is not None][:MAX_BRANCHES]
    # 根节点也要有依据档位，否则底栏三档占比加起来永远少一个。
    # 域外主题根节点就是提问本身，那也只能算推断。
    root_evidence = EVIDENCE_MODEL if pack.degraded else EVIDENCE_TEXTBOOK
    return MindElixirData(
        nodeData=MindmapNode(
            id="root",
            topic=_clip(root_topic),
            meta=MindmapMeta(evidence=root_evidence),
            children=children,
        )
    )


def _title_of(pack: EvidencePack) -> str:
    """根节点的字。

    不能直接抄提问——「风寒表证用什么药」当根节点，图就变成了一张问答截图，
    而不是一张知识图。根节点应该是**被问的那个东西**：
    单味药是药名，分类题是类名，鉴别题是「A与B的鉴别」。
    """

    intent = pack.intent
    anchors = intent.anchors
    if intent.kind == "monograph" and pack.herb_names:
        return pack.herb_names[0]
    if intent.kind == "comparison" and len(pack.herb_names) >= 2:
        return f"{pack.herb_names[0]}与{pack.herb_names[1]}鉴别"
    if intent.kind == "taxonomy" and (anchors.categories or anchors.subcategories):
        return (anchors.categories or anchors.subcategories)[0]
    if intent.kind == "syndrome" and pack.syndromes:
        return str(pack.syndromes[0].get("name") or anchors.syndromes[0])
    if intent.kind == "relation":
        if anchors.relations:
            return anchors.relations[0]
        if len(anchors.herbs) >= 2:
            return f"{anchors.herbs[0]}配{anchors.herbs[1]}"
    if intent.kind == "reverse" and anchors.effects:
        return f"能{anchors.effects[0]}的药"
    # 域外主题没有正名可用，但也**不能直接抄提问**：根节点写
    # 「做一个智能体的简介的思维导图」，等于图上没有标题。
    # ``question_title`` 只剥掉「请/帮我/做一个/…的思维导图」这类外壳。
    return question_title(intent.topic) or clean_topic(intent.topic) or "未命名主题"


# ---------------------------------------------------------------------------
# 各原型
# ---------------------------------------------------------------------------


def _monograph(pack: EvidencePack) -> list[MindmapNode]:
    doc = pack.herbs[0] if pack.herbs else None
    if doc is None:
        return _concept(pack)
    slots: list[tuple[str, list[str]]] = [
        ("性味归经", [_nature_line(doc)]),
        ("功效", list(doc["effects"])),
        ("主治", [item["syndrome"] for item in doc["indications"]]),
        ("配伍应用", [f"{item.get('purpose', '')}".strip() for item in doc["combinations"]]),
        ("相似药鉴别", [f"{item.get('compare_name', '')}：{item.get('key_points', '')}" for item in doc["differentiation"]]),
        ("用量与注意", [str(doc["dosage"].get("text", "")), *doc["cautions"]]),
    ]
    branches: list[MindmapNode] = []
    for index, (label, items) in enumerate(slots):
        leaves = _leaves([item for item in items if _meaningful(item)], index, EVIDENCE_TEXTBOOK)
        if leaves:
            branches.append(_branch(label, index, leaves))
    return branches


def _taxonomy(pack: EvidencePack) -> list[MindmapNode]:
    if not pack.nodes:
        return _concept(pack)
    root_id = pack.nodes[0]["id"]
    children = [row for row in pack.nodes if row["parent_id"] == root_id]
    # 只有亚类/领域能当一级分支。点的子节点还是点（教材有些类只有一层），
    # 那就别硬造分支——直接把药名铺开，比多一层空壳好看。
    containers = [row for row in children if row["level"] in ("domain", "subtype")]
    branches: list[MindmapNode] = []
    if containers:
        for index, row in enumerate(containers[:MAX_BRANCHES]):
            points = [child["name"] for child in pack.nodes if child["parent_id"] == row["id"]][:MAX_LEAVES]
            leaves = _leaves(points, index, EVIDENCE_TEXTBOOK)
            branches.append(_branch(row["name"], index, leaves))
        # 只有两个亚类的章节（解表药就是）单看是两根光杆。
        # 补一条「亚类药性对比」：每个亚类取一味代表药，摆出它的性味与功效——
        # 这不是凑数，学生记分类靠的正是这条对照（辛温解表 vs 辛凉解表）。
        if len(branches) < 3:
            contrast = _subtype_contrast(pack, containers)
            if contrast:
                branches.append(_branch("亚类药性对比", len(branches), contrast))
        return branches
    points = [row["name"] for row in children][:MAX_LEAVES]
    return [_branch("下辖药物", 0, _leaves(points, 0, EVIDENCE_TEXTBOOK))] if points else _concept(pack)


def _subtype_contrast(pack: EvidencePack, containers: list[dict[str, str]]) -> list[MindmapNode]:
    lines: list[str] = []
    for row in containers[:MAX_LEAVES]:
        points = [child["name"] for child in pack.nodes if child["parent_id"] == row["id"]]
        docs = docs_for_names(tuple(points[:2]))
        if not docs:
            continue
        doc = docs[0]
        line = f"{row['name']}：{_nature_line(doc)}"
        if doc["effects"]:
            line += "·" + "、".join(doc["effects"][:2])
        lines.append(line)
    return _leaves(lines, 90, EVIDENCE_TEXTBOOK)


def _comparison(pack: EvidencePack) -> list[MindmapNode]:
    if len(pack.herbs) < 2:
        return _monograph(pack)
    left, right = pack.herbs[0], pack.herbs[1]
    shared = [effect for effect in left["effects"] if effect in set(right["effects"])]
    branches: list[MindmapNode] = []
    common = []
    if left["category"] == right["category"]:
        common.append(f"同属{left['category']}")
    if shared:
        common.append(f"均可{'、'.join(shared[:3])}")
    common_leaves = _leaves(common, 0, EVIDENCE_TEXTBOOK)
    if common_leaves:
        branches.append(_branch("共同点", 0, common_leaves))
    dimensions: list[tuple[str, list[str]]] = [
        ("性味归经", [
            f"{left['name']}：{_nature_line(left)}",
            f"{right['name']}：{_nature_line(right)}",
        ]),
        ("功效侧重", [
            f"{left['name']}：{'、'.join(left['effects'][:3])}",
            f"{right['name']}：{'、'.join(right['effects'][:3])}",
        ]),
        ("主治差异", [
            f"{left['name']}：{'、'.join(item['syndrome'] for item in left['indications'][:2])}",
            f"{right['name']}：{'、'.join(item['syndrome'] for item in right['indications'][:2])}",
        ]),
        ("配伍与用量", [
            f"{left['name']}：{left['dosage'].get('text', '')}",
            f"{right['name']}：{right['dosage'].get('text', '')}",
        ]),
    ]
    # 禁忌本来就在素材包的槽位里（``branch_slots`` 有「使用注意」），
    # 但这里漏了一直没出：对比两味药，能不能一起吃恰恰是禁忌在回答。
    cautions = [
        f"{doc['name']}：{'、'.join(doc['cautions'][:3])}"
        for doc in (left, right)
        if doc["cautions"] and any(_meaningful(item) for item in doc["cautions"][:3])
    ]
    if cautions:
        dimensions.append(("使用禁忌", cautions))
    for index, (label, items) in enumerate(dimensions, start=1):
        leaves = _leaves(items, index, EVIDENCE_TEXTBOOK)
        if leaves:
            branches.append(_branch(label, index, leaves))
    key_points = [f"{item['compare_name']}：{item['key_points']}" for item in left["differentiation"] if item.get("compare_name") in (left["name"], right["name"])]
    if key_points:
        branches.append(_branch("鉴别要点", len(branches), _leaves(key_points, len(branches), EVIDENCE_TEXTBOOK)))
    return branches


def _reverse(pack: EvidencePack) -> list[MindmapNode]:
    if pack.groups:
        branches: list[MindmapNode] = []
        for index, (label, names) in enumerate(pack.groups.items()):
            if index >= MAX_BRANCHES:
                break
            leaves = _leaves(list(names), index, EVIDENCE_TEXTBOOK)
            if leaves:
                branches.append(_branch(label, index, leaves))
        if branches:
            return branches
    branches = []
    effects = pack.effects or pack.branch_slots
    for index, effect in enumerate(effects[:MAX_BRANCHES]):
        names = [doc["name"] for doc in pack.herbs if any(effect in item or item in effect for item in doc["effects"])]
        leaves = _leaves(names, index, EVIDENCE_TEXTBOOK)
        if leaves:
            branches.append(_branch(effect, index, leaves))
    if branches:
        return branches
    # 功效词在药材主表里对不上时，退回「按类分组」——总比空图强。
    return _by_category(pack)


def _filter(pack: EvidencePack) -> list[MindmapNode]:
    if pack.groups:
        branches: list[MindmapNode] = []
        for index, (label, names) in enumerate(pack.groups.items()):
            if index >= MAX_BRANCHES:
                break
            leaves = _leaves(list(names), index, EVIDENCE_TEXTBOOK)
            if leaves:
                branches.append(_branch(label, index, leaves))
        if branches:
            return branches
    return _by_category(pack) or _concept(pack)


def _by_category(pack: EvidencePack) -> list[MindmapNode]:
    groups: dict[str, list[str]] = {}
    for doc in pack.herbs:
        line = f"{doc['name']}·{doc['nature']}" + (f"·{'、'.join(doc['meridians'])}经" if doc["meridians"] else "")
        groups.setdefault(doc["category"] or "未分类", []).append(line)
    branches: list[MindmapNode] = []
    for index, (label, items) in enumerate(groups.items()):
        if index >= MAX_BRANCHES:
            break
        branches.append(_branch(label, index, _leaves(items, index, EVIDENCE_TEXTBOOK)))
    return branches


def _relation(pack: EvidencePack) -> list[MindmapNode]:
    if not pack.edges:
        return _pairing(pack)
    groups: dict[str, list[str]] = {}
    for edge in pack.edges:
        groups.setdefault(edge["source"], []).append(edge["target"])
    branches: list[MindmapNode] = []
    for index, (source, targets) in enumerate(groups.items()):
        if index >= MAX_BRANCHES:
            break
        leaves = [MindmapNode(id=f"b{index}-{position}", topic=_clip(target), meta=MindmapMeta(evidence=EVIDENCE_GRAPH)) for position, target in enumerate(dict.fromkeys(targets))][:MAX_LEAVES]
        branches.append(_branch(source, index, leaves, evidence=EVIDENCE_GRAPH))
    # 一对一的配伍关系只有两条边，图会瘦成一根线。把两味药各自的配伍条目
    # 与功效补上——「为什么这样配」本来就在药材条目里。
    if len(branches) < 3:
        for extra in _pairing(pack):
            branches.append(extra.model_copy(update={"id": f"b{len(branches)}"}))
    return branches


def _pairing(pack: EvidencePack) -> list[MindmapNode]:
    """图谱里没有这条关系时的退路：按两味药各自的配伍条目组织。

    「桂枝配白芍」在教材里是散在两条【配伍】里的，不是一条边。
    把这些条目对齐摆出来，仍然是原文，不是编的。
    """

    if len(pack.herbs) < 2:
        return _concept(pack)
    left, right = pack.herbs[0], pack.herbs[1]
    names = {left["name"], right["name"]}
    basis: list[str] = []
    for doc in (left, right):
        for combo in doc["combinations"]:
            partners = set(combo.get("with_names") or [])
            if partners & names or any(name in str(combo.get("purpose", "")) for name in names):
                basis.append(f"{doc['name']}：{combo.get('purpose', '')}")
    branches: list[MindmapNode] = []
    if basis:
        branches.append(_branch("同用依据", 0, _leaves(basis, 0, EVIDENCE_TEXTBOOK)))
    branches.append(_branch("各自功效", 1, _leaves([f"{doc['name']}：{'、'.join(doc['effects'][:3])}" for doc in (left, right)], 1, EVIDENCE_TEXTBOOK)))
    cautions = [f"{doc['name']}：{doc['cautions'][0]}" for doc in (left, right) if doc["cautions"]]
    if cautions:
        branches.append(_branch("使用注意", 2, _leaves(cautions, 2, EVIDENCE_TEXTBOOK)))
    return branches


def _syndrome(pack: EvidencePack) -> list[MindmapNode]:
    if not pack.syndromes:
        return _concept(pack)
    item = pack.syndromes[0]
    branches: list[MindmapNode] = []
    notes = [f"{note['herb']}：{note['text']}" for note in item.get("herb_notes") or []]
    slots: list[tuple[str, list[str], str]] = [
        ("主治原文", notes, EVIDENCE_TEXTBOOK),
        # 证候条目自带的药往往只有一味（它是从单条主治反推的），
        # 要和素材包里按药证链补齐的药合起来，否则「用什么药」只答得出一个。
        ("常用药", list(dict.fromkeys([*(item.get("related_herbs") or []), *pack.herb_names])) or pack.herb_names, EVIDENCE_TEXTBOOK),
        ("代表方", list(pack.formulas), EVIDENCE_TEXTBOOK),
        ("主症", list(item.get("symptoms") or []) + ([item.get("tongue", "")] if item.get("tongue") else []), EVIDENCE_TEXTBOOK),
        ("病机", [str(item.get("description", ""))], EVIDENCE_TEXTBOOK),
        ("脉象", [str(item.get("pulse", ""))], EVIDENCE_TEXTBOOK),
    ]
    index = 0
    for label, raw, evidence in slots:
        leaves = _leaves([value for value in raw if _meaningful(value)], index, evidence)
        if leaves:
            branches.append(_branch(label, index, leaves))
            index += 1
    return branches


def _exam(pack: EvidencePack) -> list[MindmapNode]:
    if not pack.herbs:
        return _concept(pack)
    confusions: list[str] = []
    exam_points: list[str] = []
    for doc in pack.herbs:
        for name in doc["common_confusions"][:2]:
            confusions.append(f"{doc['name']}↔{name}")
        for item in doc["differentiation"][:1]:
            exam_points.append(f"{item['compare_name']}：{item['key_points']}")
    branches: list[MindmapNode] = []
    if exam_points:
        branches.append(_branch("鉴别要点", 0, _leaves(exam_points, 0, EVIDENCE_TEXTBOOK)))
    if confusions:
        branches.append(_branch("易混药对", 1, _leaves(confusions, 1, EVIDENCE_GRAPH)))
    branches.append(_branch("常考药", 2, _leaves(pack.herb_names[:MAX_LEAVES], 2, EVIDENCE_TEXTBOOK)))
    return branches


def _overview(pack: EvidencePack) -> list[MindmapNode]:
    """整门课 / 整章：一级分支就是教材目录里的篇或节。

    这个原型存在的理由是「库里明明有」。以前这类题落到 ``_concept``，长出
    「概念界定 / 核心内容 / 与中药学的联系 / 学习建议」四支——那是没有资料时
    的写法，不是《中药学》的样子。《中药学》的样子是总论七章 + 各论二十一类。

    所以这里不造任何分支：槽位由 ``catalog.branch_slots`` 给（真实的篇名或节名），
    叶子由 ``catalog.groups`` 给（真实的章名或条目名）。目录里没有的一律不编。
    """

    slots = [slot for slot in pack.branch_slots if slot][:MAX_BRANCHES]
    if not slots:
        return _concept(pack)
    branches: list[MindmapNode] = []
    for index, label in enumerate(slots):
        names = pack.groups.get(label) or _chapters_under(pack, label)
        # 装不下的部分**说出来**，不要空着也不要悄悄丢：各论 21 类药，
        # 一屏只放得下 10 个，读者得知道「还有 11 类在书上，只是这张图没画」。
        # 那句说明要占一格，所以要给它留位置——否则会被 ``_branch`` 的
        # children[:MAX_LEAVES] 截掉，等于什么都没说。
        rest = max(len(names) - MAX_LEAVES, 0)
        room = MAX_LEAVES - 1 if rest else MAX_LEAVES
        leaves = _leaves(names[:room], index, EVIDENCE_TEXTBOOK)
        if rest:
            leaves.append(MindmapNode(id=f"b{index}-rest", topic=f"另 {rest} 类见教材", meta=MindmapMeta(evidence=EVIDENCE_MODEL)))
        branches.append(_branch(label, index, leaves, evidence=EVIDENCE_TEXTBOOK))
    return branches


def _chapters_under(pack: EvidencePack, part: str) -> list[str]:
    """某一篇下面有哪些章。``pack.nodes`` 里章的 ``parent_id`` 就是篇。"""

    return [
        str(row.get("name") or "")
        for row in pack.nodes
        if row.get("level") == "domain" and str(row.get("parent_id") or "").endswith(part)
    ]


def _concept(pack: EvidencePack) -> list[MindmapNode]:
    """域外兜底：给的是**怎么学**，不是编内容。

    刻意不写「定义：…」这类空话——那是假装知道。四个轴都是学习动作，
    模型可在其下补自己的理解，前端会整棵树标「推断」。
    """

    slots: list[tuple[str, list[str]]] = [
        ("概念界定", ["术语出处", "与相邻概念的区别"]),
        ("核心内容", ["主要分支", "关键术语"]),
        ("与中药学的联系", ["相关药类", "相关治法"]),
        ("学习建议", ["对照教材核对", "整理成自己的脉络"]),
    ]
    return [_branch(label, index, _leaves(items, index, EVIDENCE_MODEL), evidence=EVIDENCE_MODEL) for index, (label, items) in enumerate(slots)]


_BUILDERS: dict[str, Any] = {
    "monograph": _monograph,
    "taxonomy": _taxonomy,
    "comparison": _comparison,
    "reverse": _reverse,
    "filter": _filter,
    "relation": _relation,
    "syndrome": _syndrome,
    "exam": _exam,
    "concept": _concept,
    # 整门课 / 整章：不是「一类题」，是同一句话落在库里某门课时的形态。
    "overview": _overview,
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _branch(label: str, index: int, children: list[MindmapNode], *, evidence: str = EVIDENCE_TEXTBOOK) -> MindmapNode:
    return MindmapNode(
        id=f"b{index}",
        topic=_clip(label),
        direction=index % 2,
        meta=MindmapMeta(evidence=evidence),
        children=children[:MAX_LEAVES],
    )


def _leaves(values: list[str], branch_index: int, evidence: str) -> list[MindmapNode]:
    leaves: list[MindmapNode] = []
    seen: set[str] = set()
    for value in values:
        topic = clean_topic(str(value))
        if not topic or topic in seen:
            continue
        seen.add(topic)
        leaves.append(
            MindmapNode(
                id=f"b{branch_index}-{len(leaves)}",
                topic=_clip(topic),
                meta=MindmapMeta(evidence=evidence),
            )
        )
        if len(leaves) >= MAX_LEAVES:
            break
    return leaves


def _nature_line(doc: Any) -> str:
    """性味归经的标准写法：``辛、微苦，温·肺、膀胱经``。

    顺序不能照抄字段——库里 ``flavor`` 是散着存的，直接拼会得到
    「温、微苦、辛」这种读着别扭的串。中药学教材里药性在后、味在前，
    味的次序也有定式（辛甘酸涩苦咸淡），这里按那个定式排。
    """

    flavor = "、".join(_ordered_flavors(doc))
    nature = str(doc.get("nature", "")).strip()
    if flavor and nature:
        line = f"{flavor}，{nature}"
    else:
        line = flavor or nature
    meridians = doc.get("meridians") or []
    if meridians:
        line += f"·{'、'.join(meridians)}经"
    return line


#: 教材并列药味时的固定次序。五味之外补了涩与淡，凑齐七味。
_FLAVOR_ORDER = ("辛", "甘", "酸", "涩", "苦", "咸", "淡")


def _ordered_flavors(doc: Any) -> list[str]:
    flavors = [str(item).strip() for item in (doc.get("flavor") or []) if str(item).strip()]

    def rank(flavor: str) -> int:
        # 「微苦」「大辛」这类前缀不影响它在定式里的位置。
        core = flavor.lstrip("微大")
        for position, name in enumerate(_FLAVOR_ORDER):
            if core.startswith(name):
                return position
        return len(_FLAVOR_ORDER)

    return sorted(dict.fromkeys(flavors), key=rank)


def _join(values: list[str], sep: str) -> str:
    return sep.join(value for value in values if value)


def _meaningful(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text not in {"待补充", "待补充资料", "暂无", "知识库暂未覆盖", "None"}


def _clip(text: str) -> str:
    """节点文字硬上限 30 字：超了 ``MindmapNode`` 会直接 raise。"""

    cleaned = clean_topic(text) or str(text or "").strip()
    if len(cleaned) > MAX_TOPIC:
        cleaned = cleaned[:MAX_TOPIC]
    return cleaned or "要点"
