"""错题归因：把「这题错了」落成图谱上的一条事实。

**为什么不调模型。** 归因要回答的是「他为什么错」，而这个问题的答案在教学场景里
几乎总有确定来源——教材本身就写明了哪两味药必须鉴别（``同类鉴别`` 边，带原文
鉴别句），哪两味不能一起用（``十八反``/``十九畏`` 边）。让模型解释错因，它会顺着
题干编一段听起来很像回事的话，而那段话**无法回溯到教材任何一句**。旧图谱被叫
「大乱炖」，正是因为这种「合理但无出处」的表述被当成了知识。

**因果只从边来。** 每一条非 ``unknown`` 的归因都必须带 ``edge_id``，指向图上真实
存在的一条边；``answer_incomplete`` 不带边，但它的依据是教材验证过的功效清单
（``Herb.effects``，G008 原文子串校验通过）。没有依据就报 ``unknown``。

**``unknown`` 是一等公民。** 图谱里没有可用的鉴别事实时，必须明说「归因不了」以及
**卡在哪一步**：考点没进图谱、答案里没出现考点也没出现它的近邻、选项压根没指向
另一个实体。把「不知道」写成「概念混淆」比不归因更坏——它会骗老师去补一个不存在
的漏洞，也会骗学生去重学一个他其实会的东西。

**归因是派生数据，重算即覆盖。** ``kg_attribution`` 的 UNIQUE 约束是
``(mistake_id, entity_id, edge_id, cause_type)``，而 SQLite 里 ``NULL != NULL``，
``edge_id`` 为空的 ``unknown`` 行靠 UNIQUE 去不了重。所以写入前先按 mistake_id
删干净，而不是依赖 ON CONFLICT——那会静默堆出重复行。
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.kb.ingest import load_herbs
from app.kb.models import Herb
from app.kg import store
from app.kg.constants import Tier
from app.kg.entities import resolve
from app.kg.schema import ensure_schema
from app.kg.store import KgEdge, KgEntity
from app.profile.store import DB_PATH, connect

# --------------------------------------------------------------------------- #
# 归因类型（闭集）
# --------------------------------------------------------------------------- #

#: 把两个教材要求鉴别的实体混为一谈。**最强的一类**，因为教材原文明写了差别。
CAUSE_CONFUSED = "confused_with"
#: 把配伍禁忌的一方当成了另一方。方向不同：不是「像」，是「反」，纠正方式也不同。
CAUSE_FORBIDDEN = "forbidden_pair"
#: 答到了考点本身，但教材列的功效要点一项没提。这是**对答案的观察**，不是对
#: 心理过程的猜测——所以叫 incomplete 而不叫 misunderstand。
CAUSE_INCOMPLETE = "answer_incomplete"
#: 选了另一味**图谱里有实体的药**，但教材没写这两味的鉴别点。
#:
#: 为什么单列一类：选择题里选项就是药名，学生选错的那一刻，「把 A 答成了 B」是
#: 已经发生的事实，不是推断。只因为教材没写鉴别句就报 `unknown`，等于把知道的
#: 事说成不知道——而且学生会收到「无法归因」，明明我们清楚他选了什么。
#: 依据退回两味药各自的教材【功效】原文：那是正文，能说清「为什么不是它」。
CAUSE_WRONG_HERB = "wrong_herb"
#: 图谱无法归因。必须带 ``unknown_reason``。
CAUSE_UNKNOWN = "unknown"

CAUSE_TYPES: tuple[str, ...] = (
    CAUSE_CONFUSED,
    CAUSE_FORBIDDEN,
    CAUSE_INCOMPLETE,
    CAUSE_WRONG_HERB,
    CAUSE_UNKNOWN,
)

CAUSE_LABELS: dict[str, str] = {
    CAUSE_CONFUSED: "概念混淆",
    CAUSE_FORBIDDEN: "禁忌错认",
    CAUSE_INCOMPLETE: "要点缺失",
    CAUSE_WRONG_HERB: "选错药",
    CAUSE_UNKNOWN: "无法归因",
}

#: 每个 cause 的默认置信度。``confused_with`` 用边自身的 confidence（教材原文
#: 鉴别句给 0.93），所以这里只给另外几类定值。
#: ``wrong_herb`` 比 ``confused_with`` 低：事实确定，但缺教材的鉴别句做依据。
_CAUSE_SCORE: dict[str, float] = {
    CAUSE_FORBIDDEN: 1.0,
    CAUSE_INCOMPLETE: 0.6,
    CAUSE_WRONG_HERB: 0.5,
    CAUSE_UNKNOWN: 0.0,
}

#: 禁忌类关系。与 ``constants.CURATED_ONLY_RELATIONS`` 一致，这里再列一次是为了
#: 在归因里表达「这些边说明的不是相似而是相反」，语义不同，不要合并常量。
_FORBIDDEN_RELATIONS: tuple[str, ...] = ("十八反", "十九畏", "相反")

_DIFFERENTIATION = "同类鉴别"


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def attribute_mistake(
    mistake_id: str | int, *, db_path: Path = DB_PATH, persist: bool = True
) -> dict[str, Any]:
    """给一条错题算归因。``persist=True`` 时写进 ``kg_attribution``。

    返回值里的 ``causes`` 至少有一条；一条都算不出来时是 ``unknown``。
    """

    ensure_schema(db_path)
    row = _load_mistake(mistake_id, db_path)
    if row is None:
        return {
            "ok": False,
            "reason": f"找不到错题记录 {mistake_id}",
            "mistake_id": str(mistake_id),
            "causes": [],
            "notes": [],
        }

    entity, miss_reason, notes = _exam_point_entity(row, db_path)
    if entity is None:
        causes = [_unknown(miss_reason or "考点未进入图谱")]
        if persist:
            _persist(row["id"], causes, db_path)
        return _envelope(row, None, causes, remediation=None, notes=notes)

    causes = _causes_for(row, entity, db_path)
    if persist:
        _persist(row["id"], causes, db_path)
    return _envelope(
        row, entity, causes, remediation=_remediation(entity, row, db_path), notes=notes
    )


def attribute_user_mistakes(
    user_id: str, *, limit: int = 20, db_path: Path = DB_PATH, persist: bool = True
) -> dict[str, Any]:
    """批量归因 + 汇总。给前端「错题本」页用。

    汇总里的「薄弱章节」来自知识树祖先链（``knowledge_node.parent_id``），
    不是按药名猜的；归因不了的错题单独计数，**不摊进任何章节**——否则
    「归因失败」会被伪装成「这个章节掌握得还行」。
    """

    rows = list_mistakes(user_id, limit=limit, db_path=db_path)
    items = [
        attribute_mistake(row["id"], db_path=db_path, persist=persist) for row in rows
    ]

    by_cause: dict[str, int] = {cause: 0 for cause in CAUSE_TYPES}
    by_domain: dict[str, int] = {}
    for item in items:
        for cause in item.get("causes") or []:
            by_cause[cause["cause_type"]] = by_cause.get(cause["cause_type"], 0) + 1
        domain = (item.get("remediation") or {}).get("domain")
        if domain:
            by_domain[domain] = by_domain.get(domain, 0) + 1

    return {
        "ok": True,
        "user_id": user_id,
        "count": len(items),
        "items": items,
        "summary": {
            "by_cause": by_cause,
            "by_domain": dict(sorted(by_domain.items(), key=lambda kv: (-kv[1], kv[0]))),
            "attributed": sum(1 for item in items if not _is_unattributed(item)),
            "unattributed": sum(1 for item in items if _is_unattributed(item)),
        },
    }


# --------------------------------------------------------------------------- #
# 错题读取
# --------------------------------------------------------------------------- #


def list_mistakes(user_id: str, *, limit: int = 20, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """某个用户最近的错题（新的在前）。"""

    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT id,user_id,question_id,exam_point_id,answer_json,expected_json,created_at
               FROM mistake_record WHERE user_id=? ORDER BY id DESC LIMIT ?""",
            (user_id, int(limit)),
        ).fetchall()
    return [_mistake_row(row) for row in rows]


def count_mistakes(user_id: str, db_path: Path = DB_PATH) -> int:
    """错题总数。

    侧栏角标要的是「一共有多少条」，而 ``list_mistakes`` 返回的是**被 limit 截断后**
    的长度——拿它当总数，错题超过 limit 就永远显示 limit。这两件事必须分开算。
    """

    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM mistake_record WHERE user_id=?", (user_id,)
        ).fetchone()
    return int(row[0]) if row else 0


def get_mistake(mistake_id: str | int, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """SELECT id,user_id,question_id,exam_point_id,answer_json,expected_json,created_at
               FROM mistake_record WHERE id=?""",
            (_as_int(mistake_id),),
        ).fetchone()
    return _mistake_row(row) if row else None


def _mistake_row(row: Any) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "user_id": str(row[1]),
        "question_id": str(row[2]),
        "exam_point_id": str(row[3]),
        "answer": json.loads(row[4] or "[]"),
        "expected": json.loads(row[5] or "[]"),
        "created_at": str(row[6]),
    }


def _as_int(value: str | int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _load_mistake(mistake_id: str | int, db_path: Path) -> dict[str, Any] | None:
    """错题 + 题目 + 考点，一次查完。

    ``LEFT JOIN`` 而不是 ``JOIN``：题库可能被重建过（``real_question`` 是
    ``UNIQUE(exam_point_id, stem)`` 覆盖写），错题记录却还在。内连接会让这些
    历史错题**整条消失**，看起来像「没做错过」。查不到就带着空字段继续，
    归因那边会老实报 ``unknown``。
    """

    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """SELECT m.id,m.user_id,m.question_id,m.exam_point_id,m.answer_json,
                      m.expected_json,m.created_at,
                      q.options_json,q.question_type,q.stem,
                      p.node_id,p.name
               FROM mistake_record m
               LEFT JOIN real_question q ON q.id=m.question_id
               LEFT JOIN exam_point p ON p.id=m.exam_point_id
               WHERE m.id=?""",
            (_as_int(mistake_id),),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "user_id": str(row[1]),
        "question_id": str(row[2]),
        "exam_point_id": str(row[3]),
        "answer": json.loads(row[4] or "[]"),
        "expected": json.loads(row[5] or "[]"),
        "created_at": str(row[6]),
        "options": json.loads(row[7] or "[]"),
        "question_type": row[8],
        "stem": row[9],
        "node_id": row[10],
        "exam_point_name": row[11],
    }


# --------------------------------------------------------------------------- #
# 考点 → 图谱实体
# --------------------------------------------------------------------------- #


def _exam_point_entity(
    row: dict[str, Any], db_path: Path
) -> tuple[KgEntity | None, str, list[str]]:
    """考点落到哪个图谱实体上。返回 ``(实体, 失败原因, 数据告警)``。

    顺序固定：**先按 ``knowledge_node_id`` 直连，再按名字解析**。直连是建库时写死的
    外键，同名不同物也串不了；按名字解析要靠 ``kg_alias`` 兜底，能成但不是首选。

    **但两者打架时以名字为准。** 知识树被重建过、或者节点的 ``node_id`` 被复用到
    另一味药上时，直连会指到一个**名字对不上**的实体上——不报错，静默地把
    「甘草这道题」的归因记到「人参」名下。考点名字是学生看到的、题目问的东西，
    节点 id 只是元数据，冲突时它不能赢。冲突本身记进 ``notes`` 暴露出来，
    而不是悄悄按一个规则挑完就算。
    """

    node_id = str(row.get("node_id") or "")
    name = str(row.get("exam_point_name") or "")

    by_node_hits = store.find_by_knowledge_node(node_id, db_path) if node_id else []
    by_node = by_node_hits[0] if by_node_hits else None
    by_name = resolve(name, db_path) if name else None

    notes: list[str] = []
    if by_node is not None and by_name is not None and by_node.id != by_name.id:
        notes.append(
            f"考点「{name}」的节点 id 指向实体「{by_node.name}」，与考点名不一致；"
            f"已按名字归到「{by_name.name}」。这是数据不一致，建议核对 exam_point.node_id。"
        )
        return by_name, "", notes
    if by_node is not None:
        return by_node, "", notes
    if by_name is not None:
        return by_name, "", notes

    if not node_id and not name:
        return None, "错题没有关联到考点（题目或考点记录已不存在），无法定位知识点", notes
    return None, f"考点「{name or node_id}」在教材图谱里没有对应实体，无从归因", notes


# --------------------------------------------------------------------------- #
# 归因判定
# --------------------------------------------------------------------------- #


def _causes_for(row: dict[str, Any], entity: KgEntity, db_path: Path) -> list[dict[str, Any]]:
    answer_text = " ".join(str(item) for item in row.get("answer") or [])
    picked = _picked_entities(row, entity, answer_text, db_path)

    causes: list[dict[str, Any]] = []
    for other in picked:
        cause = _confusion_cause(entity, other, db_path)
        if cause is not None:
            causes.append(cause)
    if causes:
        return causes

    # 要点缺失只对**自由作答**成立。选择题不要求写出功效，拿「没提功效」去说
    # 选择题错因，等于用简答题的评分标准判选择题。
    if str(row.get("question_type") or "") not in {"single", "multi"}:
        gap = _answer_incomplete(entity, answer_text)
        if gap is not None:
            return [gap]

    return [_unknown(_unknown_reason(row, entity, answer_text, picked))]


def _picked_entities(
    row: dict[str, Any], entity: KgEntity, answer_text: str, db_path: Path
) -> list[KgEntity]:
    """用户的答案指向了哪些**别的**图谱实体。

    选择题：把选项 key 换成选项原文再解析。文本解析不出来就跳过——**解析不出来
    不等于答错了别的东西**，硬猜会把「选了「性味归经」」说成「和某味药混淆」。
    简答/病例：只在考点的近邻里找字面出现，不扫全图。
    """

    question_type = str(row.get("question_type") or "")
    found: dict[str, KgEntity] = {}

    if question_type in {"single", "multi"}:
        # 选择题的选项是**受控文本**（我们出的题，选项就是药名），直接按名字解析，
        # 不走近邻扫描。走近邻扫描会漏掉「有实体但和考点没有边」的选项——那正是
        # 大多数情况，结果 `picked` 为空，`_unknown_reason` 还错报成「解析不到实体」。
        option_text = {
            str(item.get("key")): str(item.get("text"))
            for item in row.get("options") or []
            if item.get("key") is not None
        }
        for key in row.get("answer") or []:
            candidate = resolve(option_text.get(str(key), str(key)), db_path)
            if candidate is None or candidate.id == entity.id:
                continue
            # **不能**在这里用「名字是考点名的一部分就跳过」——那是自由文本扫描的
            # 防重规则，用在受控选项上会把最该抓的混淆对丢掉：
            # 选「柴胡」而考点是「银柴胡」、选「黄连」而考点是「胡黄连」，
            # 名字确实是子串关系，但这是两味药，正是「选错药」。实测 576 道题里
            # 有 2 道因此被判成 unknown，理由还写成「解析不到药」——解析到了，是被这条挡掉的。
            found.setdefault(candidate.id, candidate)
        return list(found.values())

    # 简答/病例：只在考点的近邻里找字面出现。自由文本里什么词都可能出现，
    # 扫全图会把「提到过茯苓」当成「把麻黄和茯苓搞混了」。
    for candidate in _entities_in_text(answer_text, entity, db_path):
        if candidate.id != entity.id:
            found.setdefault(candidate.id, candidate)
    return list(found.values())


def _entities_in_text(text: str, entity: KgEntity, db_path: Path) -> list[KgEntity]:
    """``text`` 里字面出现的、与考点有 T1/T2 关系的实体。

    只查近邻而不是全表 1419 个实体，有两个原因：一是快，二是**归因只在近邻里
    才有意义**——考生把「麻黄」和八竿子打不着的「茯苓」搞混，那是没学会，不是
    概念混淆，我们没有依据去说它俩像。
    """

    cleaned = (text or "").strip()
    if not cleaned:
        return []

    result: list[KgEntity] = []
    for edge in store.edges_touching(
        [entity.id],
        tiers=(int(Tier.CONSTRAINT), int(Tier.FACT)),
        db_path=db_path,
    ):
        other_id = edge.target_id if edge.source_id == entity.id else edge.source_id
        if other_id == entity.id:
            continue
        other = store.get_entity(other_id, db_path)
        if other is None or not other.name:
            continue
        # 近邻名是考点名的一部分时跳过：那是同一个东西的不同叫法，不是混淆对象。
        # 「紫苏」⊂「紫苏叶」，学生写对了「紫苏叶」，不该被判成「把紫苏叶和紫苏
        # 搞混了」。
        #
        # 反过来**不能**用「互为子串就跳过」：教材里「麻黄」和「麻黄根」是两味
        # 药（一发汗一止汗），正是最该抓的混淆对。所以只排除「近邻名 ⊂ 考点名」
        # 这一个方向，不排除「考点名 ⊂ 近邻名」。
        if other.name in entity.name:
            continue
        if other.name in cleaned:
            result.append(other)
    return result


def _confusion_cause(entity: KgEntity, other: KgEntity, db_path: Path) -> dict[str, Any] | None:
    """``other`` 与 ``entity`` 之间有没有能解释这次错误的边。没有就返回 ``None``。"""

    forbidden = store.find_edge(entity.id, other.id, _FORBIDDEN_RELATIONS, db_path=db_path)
    if forbidden:
        edge = forbidden[0]
        return _cause_from_edge(
            CAUSE_FORBIDDEN, entity, other, edge, db_path,
            f"你把「{entity.name}」和「{other.name}」当成了同类，"
            f"但教材记载二者属「{edge.relation_type}」，是配伍禁忌而不是相近。",
        )

    differentiation = store.find_edge(entity.id, other.id, (_DIFFERENTIATION,), db_path=db_path)
    if differentiation:
        edge = differentiation[0]
        return _cause_from_edge(
            CAUSE_CONFUSED, entity, other, edge, db_path,
            f"你选的是「{other.name}」，而教材明确要求把它和「{entity.name}」区分开。",
        )

    # 教材没写这两味的关系。但「你选了另一味药」是**已经发生的事实**，不是推断；
    # 缺的只是教材的鉴别句。退回两味药各自的【功效】原文——那是正文，能说清
    # 「为什么不是它」。只有连功效都取不到（方剂/证候，或教材未载）才让调用方
    # 落到 ``unknown``。
    herbs = _herb_index()
    mine, theirs = herbs.get(entity.id), herbs.get(other.id)
    if mine is None or theirs is None or not mine.effects or not theirs.effects:
        return None
    return {
        "cause_type": CAUSE_WRONG_HERB,
        "label": CAUSE_LABELS[CAUSE_WRONG_HERB],
        "entity_id": other.id,
        "entity_name": other.name,
        "edge_id": None,
        "relation_type": "",
        "score": _CAUSE_SCORE[CAUSE_WRONG_HERB],
        "quote": f"{entity.name}：{'、'.join(mine.effects)}；{other.name}：{'、'.join(theirs.effects)}",
        "page": None,
        "ref": "教材【功效】栏",
        "explanation": (
            f"你把「{entity.name}」答成了「{other.name}」。教材没有这两味的直接鉴别记载，"
            f"但功效取向不同：{entity.name}「{'、'.join(mine.effects)}」，"
            f"{other.name}「{'、'.join(theirs.effects)}」。按功效对照记，比记结论可靠。"
        ),
    }


def _cause_from_edge(
    cause_type: str,
    entity: KgEntity,
    other: KgEntity,
    edge: KgEdge,
    db_path: Path,
    explanation: str,
) -> dict[str, Any]:
    evidence = edge.evidence[0] if edge.evidence else {}
    return {
        "cause_type": cause_type,
        "label": CAUSE_LABELS[cause_type],
        "entity_id": other.id,
        "entity_name": other.name,
        "edge_id": edge.id,
        "relation_type": edge.relation_type,
        "score": float(edge.confidence),
        "quote": str(evidence.get("quote") or edge.label),
        "page": evidence.get("page"),
        "ref": str(evidence.get("ref") or ""),
        "explanation": explanation,
    }


def _answer_incomplete(entity: KgEntity, answer_text: str) -> dict[str, Any] | None:
    """答到了考点，但教材列的功效一项没提。

    依据是 ``Herb.effects``——那是教材【功效】栏抽出来、逐条过了原文子串校验的
    闭集，不是模型总结。非药材实体（方剂/证候）没有功效清单，直接返回 ``None``，
    让调用方落到 ``unknown``。
    """

    if not answer_text or entity.name not in answer_text:
        return None
    herb = _herb_index().get(entity.id)
    if herb is None or not herb.effects:
        return None
    if any(effect and effect in answer_text for effect in herb.effects):
        return None
    return {
        "cause_type": CAUSE_INCOMPLETE,
        "label": CAUSE_LABELS[CAUSE_INCOMPLETE],
        "entity_id": entity.id,
        "entity_name": entity.name,
        "edge_id": None,
        "relation_type": "",
        "score": _CAUSE_SCORE[CAUSE_INCOMPLETE],
        "quote": "、".join(herb.effects),
        "page": None,
        "ref": "教材【功效】栏",
        "explanation": (
            f"答案提到了「{entity.name}」，但教材列出的功效（{'、'.join(herb.effects)}）"
            "一项都没有出现。这是对答案内容的判断，不是对思路的猜测。"
        ),
    }


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "cause_type": CAUSE_UNKNOWN,
        "label": CAUSE_LABELS[CAUSE_UNKNOWN],
        "entity_id": None,
        "entity_name": "",
        "edge_id": None,
        "relation_type": "",
        "score": _CAUSE_SCORE[CAUSE_UNKNOWN],
        "quote": "",
        "page": None,
        "ref": "",
        "explanation": reason,
    }


def _unknown_reason(
    row: dict[str, Any], entity: KgEntity, answer_text: str, picked: list[KgEntity]
) -> str:
    """写清**卡在哪一步**。这句会直接展示给用户，不能写成「归因失败」。"""

    question_type = str(row.get("question_type") or "")
    if picked:
        names = "、".join(item.name for item in picked)
        return (
            f"答案指向了「{names}」，但教材没有记载它与「{entity.name}」的鉴别点，"
            "图谱里也没有二者之间的禁忌关系——说成「概念混淆」是没有依据的。"
        )
    if not answer_text:
        return "错题记录里没有保存作答内容，无法判断错在哪一步。"
    if entity.name in answer_text:
        return (
            f"答案提到了考点「{entity.name}」本身，也没有提到图谱中与它相近的实体。"
            "失分可能出在表述或要点组织上，但教材没有给出可对照的鉴别点，图谱无法归因。"
        )
    if question_type in {"single", "multi"}:
        return (
            f"你选的选项没有指向图谱中的另一个实体（选项原文解析不到药/方/证），"
            f"因此判断不出你把「{entity.name}」和什么搞混了。"
        )
    return (
        f"答案里既没有出现考点「{entity.name}」，也没有出现图谱中与它相近的实体，"
        "无法判断错在知识点层面还是表述层面。"
    )


# --------------------------------------------------------------------------- #
# 复习建议
# --------------------------------------------------------------------------- #


def _remediation(entity: KgEntity, row: dict[str, Any], db_path: Path) -> dict[str, Any]:
    """复习路径：知识树祖先链 + 教材鉴别句。**全部来自已有数据，不生成新内容。**"""

    node_id = str(row.get("node_id") or entity.knowledge_node_id or "")
    path = _node_path(node_id, db_path)

    partners: list[dict[str, Any]] = []
    seen: set[str] = set()
    for edge in store.edges_of(entity.id, tiers=(int(Tier.FACT),), db_path=db_path):
        if edge.relation_type != _DIFFERENTIATION:
            continue
        other_id = edge.target_id if edge.source_id == entity.id else edge.source_id
        # 同一对药可能有多条鉴别边（教材在不同条目下各写了一句）。按实体去重，
        # 留第一条——edges_of 已按 confidence DESC 排过，留下的是最可信的那句。
        if other_id in seen:
            continue
        seen.add(other_id)
        other = store.get_entity(other_id, db_path)
        evidence = edge.evidence[0] if edge.evidence else {}
        partners.append(
            {
                "entity_id": other_id,
                # 取不到名字就留空，不要退回 id 冒充名字。
                "name": other.name if other else "",
                "edge_id": edge.id,
                "quote": str(evidence.get("quote") or edge.label),
                "page": evidence.get("page"),
            }
        )
    return {
        "entity_id": entity.id,
        "entity_name": entity.name,
        "node_path": path,
        # 章节 = 祖先链最外层。**不是 path[-1]**——那样取到的是药名本身，
        # 汇总「薄弱章节」时会变成每味药各算一章，等于没汇总。
        "domain": path[0]["name"] if path else "",
        "distinguish_from": partners,
        # 指向**真实存在**的端点：旧图谱子图接口按 ``knowledge_node.id`` 取子图
        # （``graph_cache.center_id`` 就是 ``d01.s1.p1`` 这种），不是按
        # ``herb_mahuang`` 这种 kg id。早期版本这里写的是 ``/api/kg/neighborhood``，
        # 那个端点根本不存在——前端点了只会 404。
        "entry": f"/api/graph/{node_id}" if node_id else "",
        # 前端用它深链到 ``/atlas?focus=<node_id>``。
        "focus_node_id": node_id,
    }


def _node_path(node_id: str, db_path: Path) -> list[dict[str, str]]:
    """从叶子往上走到 domain，返回时按 domain → point 排列（学习顺序）。"""

    if not node_id:
        return []
    chain: list[dict[str, str]] = []
    current: str | None = node_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        with closing(connect(db_path)) as conn:
            row = conn.execute(
                "SELECT id,name,level,parent_id FROM knowledge_node WHERE id=?", (current,)
            ).fetchone()
        if row is None:
            break
        chain.append({"id": str(row[0]), "name": str(row[1]), "level": str(row[2])})
        current = row[3]
    return list(reversed(chain))


# --------------------------------------------------------------------------- #
# 落库
# --------------------------------------------------------------------------- #


def _persist(mistake_id: int, causes: list[dict[str, Any]], db_path: Path) -> None:
    """先删后插。理由见模块 docstring 末尾（NULL 去不了重）。"""

    now = datetime.now(UTC).isoformat()
    with closing(connect(db_path)) as conn:
        conn.execute("DELETE FROM kg_attribution WHERE mistake_id=?", (str(mistake_id),))
        for cause in causes:
            conn.execute(
                """INSERT INTO kg_attribution
                   (mistake_id,entity_id,edge_id,cause_type,score,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    str(mistake_id),
                    cause.get("entity_id"),
                    cause.get("edge_id"),
                    cause["cause_type"],
                    float(cause.get("score") or 0),
                    now,
                ),
            )
        conn.commit()


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _envelope(
    row: dict[str, Any],
    entity: KgEntity | None,
    causes: list[dict[str, Any]],
    *,
    remediation: dict[str, Any] | None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "mistake_id": str(row["id"]),
        "question_id": row["question_id"],
        "exam_point": {
            "id": row["exam_point_id"],
            "name": row.get("exam_point_name") or "",
            "node_id": row.get("node_id") or "",
        },
        "entity": (
            None
            if entity is None
            else {"id": entity.id, "name": entity.name, "kind": entity.kind}
        ),
        "causes": causes,
        "remediation": remediation,
        # 数据不一致等「不影响结论但必须让人看见」的提示。
        "notes": notes or [],
    }


def _is_unattributed(item: dict[str, Any]) -> bool:
    """只有 ``unknown`` 的错题算「没归因上」。混着一条真的就算归因上了。"""

    return all(cause["cause_type"] == CAUSE_UNKNOWN for cause in item.get("causes") or [])


_HERB_INDEX: dict[str, Herb] | None = None


def _herb_index() -> dict[str, Herb]:
    global _HERB_INDEX
    if _HERB_INDEX is None:
        _HERB_INDEX = {herb.id: herb for herb in load_herbs()}
    return _HERB_INDEX


def reset_herb_index() -> None:
    """知识库重载后清缓存（与 retriever/ask 里的同名函数一致）。"""

    global _HERB_INDEX
    _HERB_INDEX = None


__all__ = [
    "CAUSE_TYPES",
    "CAUSE_LABELS",
    "CAUSE_CONFUSED",
    "CAUSE_FORBIDDEN",
    "CAUSE_INCOMPLETE",
    "CAUSE_WRONG_HERB",
    "CAUSE_UNKNOWN",
    "attribute_mistake",
    "attribute_user_mistakes",
    "list_mistakes",
    "get_mistake",
    "reset_herb_index",
]
