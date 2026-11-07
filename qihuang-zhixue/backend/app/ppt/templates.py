"""确定性讲稿模板：无模型密钥时的兜底，也是「接模型前的骨架」。

这份模板有三个用途，必须同时成立：

1. **无 key 时的产物**：``mock_factory`` 直接返回它，整条流水线照常跑完，
   产物还要能讲、好看、可导出——否则演示环境一片空白。
2. **模型的骨架**：真模型走 compose 时把这份大纲喂进 prompt，模型只做「补肉」，
   版式结构不会被带偏。
3. **结构不变量**：封面在首、使用注意必在、总结在尾，由 ``_fit`` 保证；
   模型与模板走同一条收敛逻辑，所以「有没有模型」不影响结构。

所有文案都从检索到的 ``ResourceDoc`` 字段生成，不写死任何药名。
"""

from __future__ import annotations

from typing import Any

from app.agents.resource_context import ResourceDoc, formula_name_by_id

from .draft import GEN_BULLET, GEN_COLUMN_ITEM, DeckDraft, DraftColumn, DraftSlide, OutlineDraft, OutlineDraftSlide, clean_text
from .schemas import MAX_TITLE

#: 五味在教材里的书写顺序：辛甘酸苦咸。检索结果的字段顺序不可靠，这里统一。
_FLAVOR_ORDER = {"辛": 0, "甘": 1, "酸": 2, "涩": 3, "苦": 4, "咸": 5, "淡": 6}


def deck_title_for(topic: str, docs: list[ResourceDoc]) -> str:
    doc = docs[0] if docs else None
    name = clean_text(doc["name"], 12) if doc else clean_text(topic, 12)
    name = name or clean_text(topic, 12) or "中医药讲稿"
    effects = [clean_text(item, 10) for item in (doc["effects"] if doc else []) if item]
    if len(effects) >= 2:
        return clean_text(f"{name}：{effects[0]}，{effects[1]}", MAX_TITLE) or name
    if effects:
        return clean_text(f"{name}：{effects[0]}", MAX_TITLE) or name
    return clean_text(f"{name}：一节课讲透", MAX_TITLE) or name


def deterministic_outline(topic: str, docs: list[ResourceDoc], params: dict[str, Any] | None = None) -> OutlineDraft:
    candidates = _candidates(topic, docs)
    picked = _fit(candidates, _page_limit(params))
    return OutlineDraft(
        deck_title=deck_title_for(topic, docs),
        slides=[OutlineDraftSlide(title=item["title"], points=item["points"], source_indexes=item["source_indexes"]) for item in picked],
    )


def deterministic_deck(
    topic: str,
    docs: list[ResourceDoc],
    params: dict[str, Any] | None = None,
    outline: list[dict[str, Any]] | None = None,
) -> DeckDraft:
    """按（可能被用户改过的）大纲铺页。

    大纲标题被改过不能直接丢——那样用户改的东西就白改了。策略是：标题能对上
    模板就复用模板内容，对不上就用大纲里的要点自己成页。
    """

    candidates = _candidates(topic, docs)
    # 按「去掉章号后的标题」匹配：大纲里的章号已被重排（一、二、三），
    # 而模板候选页还带着原始章号，直接按全等匹配会全部落空。
    by_title = {_title_key(item["title"]): item for item in candidates}

    if outline:
        slides: list[DraftSlide] = []
        # 大纲首页按约定就是封面。用户把这页标题改得面目全非时按标题匹配会落空，
        # 若照「对不上就自己成页」处理，它会变成一页带要点的正文，然后被
        # normalize 在前面再补一张模板封面——用户改的标题就成了第二页的标题。
        cover_candidate = next((item for item in candidates if item["kind"] == "cover"), None)
        for index, entry in enumerate(outline):
            title = clean_text(str(entry.get("title") or ""), MAX_TITLE)
            if not title:
                continue
            if index == 0:
                draft = _to_draft(cover_candidate) if cover_candidate is not None else DraftSlide(kind="cover")
                draft.title = title
                # 封面只放标题：大纲里的要点属于正文，搬到封面上会变成两行小字压标题。
                draft.bullets = []
                draft.columns = []
                slides.append(draft)
                continue
            matched = by_title.get(_title_key(title))
            if matched is not None:
                draft = _to_draft(matched)
                # 标题以大纲为准：用户在大纲面板改过的字不能被模板覆盖回去。
                draft.title = title
                slides.append(draft)
                continue
            points = [clean_text(str(point), GEN_BULLET) for point in (entry.get("points") or [])]
            slides.append(
                DraftSlide(
                    title=title,
                    bullets=[point for point in points if point][:4],
                    source_indexes=list(entry.get("source_indexes") or []),
                )
            )
        if slides:
            return DeckDraft(deck_title=deck_title_for(topic, docs), slides=slides)

    picked = _fit(candidates, _page_limit(params))
    return DeckDraft(deck_title=deck_title_for(topic, docs), slides=[_to_draft(item) for item in picked])


def _to_draft(item: dict[str, Any]) -> DraftSlide:
    return DraftSlide(
        kind=item["kind"],
        title=item["title"],
        subtitle=item["subtitle"],
        bullets=list(item["bullets"]),
        columns=[DraftColumn(heading=column["heading"], items=list(column["items"])) for column in item["columns"]],
        note=item["note"],
        source_indexes=list(item["source_indexes"]),
    )


def _page_limit(params: dict[str, Any] | None) -> int:
    try:
        value = int((params or {}).get("page_count"))
    except (TypeError, ValueError):
        return 12
    return min(max(value, 6), 16)


def _candidates(topic: str, docs: list[ResourceDoc]) -> list[dict[str, Any]]:
    """按讲授顺序铺出候选页。

    ``essential=True`` 的页在任何裁剪策略下都不会被丢：封面、用量、使用注意、
    总结是讲稿的四件套，少了「使用注意」学生拿到的就是一份有风险的笔记。
    """

    doc = docs[0] if docs else None
    if doc is None:
        return _generic_candidates(topic)

    name = clean_text(doc["name"], 12) or clean_text(topic, 12) or "本药"
    flavor = _flavor_text(doc)
    nature = clean_text(doc["nature"], 6) or "—"
    meridians = "、".join(clean_text(item, 6) for item in doc["meridians"] if clean_text(item, 6)) or "—"
    effects = [clean_text(item, 18) for item in doc["effects"] if clean_text(item, 18)][:4]
    syndromes = [clean_text(item.get("syndrome", ""), 18) for item in doc["indications"] if clean_text(item.get("syndrome", ""), 18)][:4]
    cautions = [clean_text(item, GEN_BULLET) for item in doc["cautions"] if item][:3]
    dosage = clean_text(str(doc["dosage"].get("text") or ""), 20)
    usage_notes = clean_text(str(doc["dosage"].get("usage_notes") or ""), GEN_BULLET)
    combinations = _combination_lines(doc)
    compare = _compare_columns(name, doc, effects, syndromes)

    items: list[dict[str, Any]] = []

    def add(kind: str, title: str, *, subtitle: str = "", bullets: list[str] | None = None, columns: list[dict[str, Any]] | None = None, note: str = "", points: list[str] | None = None, essential: bool = False) -> None:
        items.append(
            {
                "seq": len(items),
                "kind": kind,
                "title": clean_text(title, MAX_TITLE),
                "subtitle": subtitle,
                "bullets": [item for item in (bullets or []) if item],
                "columns": columns or [],
                "note": note,
                "points": [item for item in (points if points is not None else (bullets or [])) if item][:3],
                "essential": essential,
                "source_indexes": [0],
            }
        )

    add(
        "cover",
        deck_title_for(topic, docs),
        subtitle=f"{clean_text(doc['category'], 14)} · {flavor}，{nature} · 归{meridians}经",
        points=[f"{flavor}，{nature}", f"归{meridians}经"],
        essential=True,
    )
    add(
        "agenda",
        "本讲导览",
        bullets=["性味归经：先把药性这层底子说清", "功效主治：一条功效对一个病证", "配伍与分用：和谁配、跟谁别混", "用量与禁忌：什么情况下不能用"],
        note="开场 30 秒：先说这一课解决什么问题，再进正文。",
    )

    add("section", "一、性味归经", subtitle="药性决定方向，归经决定部位", points=[f"性：{nature}", f"味：{flavor}", f"归经：{meridians}"])
    add(
        "bullets",
        f"性味：{flavor}，{nature}",
        bullets=[f"性：{nature}", f"味：{flavor}", f"归经：{meridians}经", f"分类：{clean_text(doc['category'], 14)}"],
        note="板书：性味决定功效方向，归经决定作用部位。",
    )
    if effects:
        add("bullets", "核心功效", bullets=effects, points=effects, note="每条功效后面必须跟一个「所以能治什么」。")

    if compare or syndromes:
        add("section", "二、主治与配伍", subtitle="一条功效对一类证，一配伍讲一个理由", points=syndromes[:3] or ["主治病证"])
    if syndromes:
        add("bullets", f"主治：{'、'.join(syndromes[:3])}", bullets=syndromes, note="主治只讲高频证型，冷门证型留给课后拓展。")
    if compare:
        add("compare", compare["title"], columns=compare["columns"], note="左右分栏：左边本药，右边对照药，只讲差别不讲共性。")
    if combinations:
        add("bullets", "配伍：和谁配、为什么配", bullets=combinations[:4], note="配伍讲「为什么配」，不讲「配了治什么」——后者学生自己能推。")

    add("section", "三、用量与禁忌", subtitle="这一节讲得慢一点，安全信息不能省", points=[dosage or "常用量", usage_notes or "煎服方法"])
    add(
        "bullets",
        "用量与煎服",
        bullets=[f"常用量：{dosage or '参见药典'}", usage_notes or "常规水煎，分次温服"],
        note="剂量只讲药典范围，不延伸临床经验用量。",
        essential=True,
    )
    if cautions:
        add("bullets", "使用注意", bullets=cautions, note="这一页必须讲慢：禁忌说漏了，学生是要出事的。", essential=True)
    add(
        "summary",
        f"一页记住{name}",
        bullets=_summary_bullets(name, flavor, nature, meridians, effects, cautions),
        note="收尾：留 10 秒让学生拍照，不要在这一页讲新内容。",
        essential=True,
    )
    return items


def _generic_candidates(topic: str) -> list[dict[str, Any]]:
    """检索不到药材时的骨架：不留空白页，也不编造药性。"""

    title = clean_text(topic, MAX_TITLE) or "中医药讲稿"
    return [
        {"seq": 0, "kind": "cover", "title": title, "subtitle": "中医药学讲稿", "bullets": [], "columns": [], "note": "", "points": [], "essential": True, "source_indexes": []},
        {"seq": 1, "kind": "agenda", "title": "本讲导览", "bullets": ["概念与出处", "核心内容", "常见误区", "课后延伸"], "columns": [], "note": "", "points": ["概念与出处", "核心内容", "常见误区"], "essential": False, "source_indexes": []},
        {"seq": 2, "kind": "bullets", "title": "概念与出处", "bullets": ["知识库暂未收录该主题的原文", "建议先在「中药图谱」中确认药材名"], "columns": [], "note": "", "points": ["知识库暂未收录"], "essential": False, "source_indexes": []},
        {"seq": 3, "kind": "summary", "title": "课后延伸", "bullets": ["回到图谱查看关联方证", "用导图工坊整理本节结构"], "columns": [], "note": "", "points": [], "essential": True, "source_indexes": []},
    ]


def _fit(candidates: list[dict[str, Any]], page_count: int) -> list[dict[str, Any]]:
    """页数不够时的裁剪顺序：先砍纯章节页，再从尾部砍非必需页。

    不能简单 ``[:page_count]``：那样 8 页的稿子会把「使用注意」砍掉。
    章节页只承担分隔作用、不承载事实，是最该先让位的。
    """

    if len(candidates) <= page_count:
        return _renumber_sections(candidates)
    kept = list(candidates)
    for item in candidates:
        if len(kept) <= page_count:
            break
        if item["kind"] == "section" and not item["essential"]:
            kept.remove(item)
    while len(kept) > page_count:
        removable = [index for index, item in enumerate(kept) if not item["essential"]]
        if not removable:
            break
        kept.pop(removable[-1])
    return _renumber_sections(kept)


def _title_key(title: str) -> str:
    """去掉「一、」这类章号后的标题，用作跨阶段匹配键。"""

    text = str(title or "").strip()
    if "、" in text[:3]:
        return text.split("、", 1)[1].strip()
    return text


def _renumber_sections(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """裁剪后重排章号。

    页数不够时会整章砍掉，若只删「一、」而留下「二、三、」，讲稿翻开第一页就是
    「二、主治」，看着像缺页。这里按存活顺序重写成 一、二、三。
    """

    ordered = sorted(items, key=lambda item: int(item["seq"]))
    index = 0
    for item in ordered:
        if item["kind"] != "section":
            continue
        body = str(item["title"])
        if "、" in body[:3]:
            body = body.split("、", 1)[1]
        prefix = "一二三四五六七八九十"[index] if index < 10 else str(index + 1)
        item["title"] = clean_text(f"{prefix}、{body}", MAX_TITLE)
        index += 1
    return ordered


def _flavor_text(doc: ResourceDoc) -> str:
    flavors = [clean_text(item, 6) for item in doc["flavor"] if clean_text(item, 6)]
    flavors.sort(key=lambda item: _FLAVOR_ORDER.get(item[0], 9))
    return "、".join(flavors) or "—"


def _combination_lines(doc: ResourceDoc) -> list[str]:
    lines: list[str] = []
    for combo in doc["combinations"]:
        partners = "、".join(name for name in (combo.get("with_names") or []) if name)
        purpose = clean_text(combo.get("purpose", ""), 16)
        formula = _formula_label(str(combo.get("formula_ref") or ""))
        if not partners:
            continue
        text = f"配{partners}"
        if purpose:
            text += f"：{purpose}"
        if formula:
            text += f"（{formula}）"
        lines.append(clean_text(text, GEN_BULLET))
    return [line for line in lines if line][:4]


def _formula_label(value: str) -> str:
    """``formula_mahuangtang`` 这类内部 id 不能直接投屏，换成方名。"""

    if not value:
        return ""
    if value.startswith("formula_"):
        try:
            resolved = formula_name_by_id(value)
        except Exception:
            resolved = value
        return clean_text(resolved, 10) if not str(resolved).startswith("formula_") else ""
    return clean_text(value, 10)


def _compare_columns(name: str, doc: ResourceDoc, effects: list[str], syndromes: list[str]) -> dict[str, Any] | None:
    """对比页：左栏本药，右栏对照药。取不到对照就整页不生成，不做无意义的空对比。"""

    entries = [item for item in doc["differentiation"] if item.get("compare_name")]
    if not entries:
        return None
    partner = clean_text(str(entries[0]["compare_name"]), 12)
    if not partner:
        return None
    left: list[str] = []
    if effects:
        left.append(f"功效：{'、'.join(effects[:2])}")
    if syndromes:
        left.append(f"主治：{'、'.join(syndromes[:2])}")
    clauses = _split_clauses(str(entries[0].get("key_points") or ""))
    # 对照段落常写成「麻黄……；桂枝……」，前半句讲的是本药，放进右栏会自相矛盾。
    keys = [clause for clause in clauses if not clause.startswith(name)] or clauses
    if not keys:
        return None
    return {
        "title": clean_text(f"{name}与{partner}：怎么分用", MAX_TITLE),
        "columns": [
            {"heading": name, "items": [clean_text(item, GEN_COLUMN_ITEM) for item in left][:3]},
            {"heading": partner, "items": [clean_text(item, GEN_COLUMN_ITEM) for item in keys][:3]},
        ],
    }


def _split_clauses(text: str) -> list[str]:
    cleaned = clean_text(text, GEN_COLUMN_ITEM * 6)
    if not cleaned:
        return []
    for separator in ("；", ";", "。"):
        if separator in cleaned:
            parts = [clean_text(part, GEN_COLUMN_ITEM) for part in cleaned.split(separator)]
            return [part for part in parts if part][:4]
    return [clean_text(cleaned, GEN_COLUMN_ITEM)]


def _summary_bullets(name: str, flavor: str, nature: str, meridians: str, effects: list[str], cautions: list[str]) -> list[str]:
    bullets = [f"性味：{flavor}，{nature}；归{meridians}经"]
    if effects:
        bullets.append(f"功效：{'、'.join(effects[:3])}")
    if cautions:
        bullets.append(f"禁忌：{cautions[0]}")
    bullets.append(f"一句话：{name}管的是「{effects[0] if effects else '本经主治'}」这一路")
    return [clean_text(item, GEN_BULLET) for item in bullets if item]
