"""造图管线：把教材目录落成「章 → 节 → 知识点」，并逐条校验。

三条来源，同一个产物 ``StructureProposal``：

- ``syllabus``（**默认**）：读 ``data/syllabus/<subject>.json``。目录来自教材
  本身——各论来自 442 条药材条目的 ``chapter_no / chapter / section / page``，
  总论来自印刷目录。确定性、可复现、不调模型，且能一指头戳回教材页码。
- ``graph``：读 ``knowledge_node``。留作对照，问题很明显：它把《中药学》总论
  压成 1 章 1 节，教材前 7 章全丢。
- ``llm``：在 graph 结果上让模型细化总论。**已废弃**，仅留作回溯；
  实测同一提示词冷启动两次给出 11 节 / 12 节，教学场景不能这样。

落库前必须过 ``validate_structure``。``syllabus`` 源走
``_validate_against_syllabus``：比的不是"结构合不合理"，而是"有没有偏离教材"。
校验结果要回给前端，不许静默丢弃。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.meridian import store, syllabus, whitelist
from app.meridian.schema import (
    AutoFix,
    ChapterProposal,
    RejectedItem,
    SectionProposal,
    StructureProposal,
    ValidateResult,
)

logger = logging.getLogger(__name__)

#: 中药学的学科元信息。其余学科（P9）走同一套，只是换这些值。
SUBJECT_META: dict[str, dict[str, Any]] = {
    "herb": {"title": "中药学", "color_token": "--qh-accent", "ord": 1, "kind": "container"},
    "formula": {"title": "方剂学", "color_token": "--qh-positive", "ord": 2, "kind": "container"},
    # 第三个图层用暖铜色：前两个已占朱砂（中药学）与青碧（方剂学），
    # 第三个必须一眼能分开，而 --qh-color-gold 是全站已有的暖色令牌，不再新造颜色。
    "basic": {"title": "中医基础理论", "color_token": "--qh-color-gold", "ord": 3, "kind": "container"},
    # 第四到第六门：已有三个令牌（朱砂 / 青碧 / 暖铜）之外，蓝、紫、深青各占一个。
    # 六层同屏时相邻层不能撞色，否则图例形同虚设；--qh-color-blue 与
    # --qh-color-violet / --qh-color-pine 都在 tokens.scss 里，不在这里写死色值。
    "diagnosis": {"title": "中医诊断学", "color_token": "--qh-color-blue", "ord": 4, "kind": "container"},
    "acup": {"title": "针灸学", "color_token": "--qh-color-violet", "ord": 5, "kind": "container"},
    "internal": {"title": "中医内科学", "color_token": "--qh-color-pine", "ord": 6, "kind": "container"},
}

#: 校验区间。宽一点没关系，但**下限必须挡住"只留 5 章"**那种过度裁剪。
#: 下限按**最短的规划教材**定，不按中药学定：中药学 65 节、方剂学 76 节，
#: 中医基础理论 38 节（绪论 + 九章），取 20 才不会把一门正常的课判成"裁剪过度"。
MIN_CHAPTERS, MAX_CHAPTERS = 6, 30
MIN_SECTIONS, MAX_SECTIONS = 20, 90
#: 总论细化的目标节数。规划教材总论一般 8–14 节。
GENERAL_SECTION_RANGE = (8, 14)


# --------------------------------------------------------------------------- 读取现有树


def _connect(db_path: Path | str) -> sqlite3.Connection:
    return store.connect(Path(db_path))


def tree_digest(db_path: Path | str = store.DB_PATH) -> str:
    """知识树的指纹。既是 ``graph_version``，也是提案的 ``input_hash``。

    树一变指纹就变，于是"这份提案是基于哪一版树造出来的"永远答得上。
    """

    h = hashlib.sha256()
    with closing(_connect(db_path)) as conn:
        for row in conn.execute(
            "SELECT id, level, parent_id, name FROM knowledge_node ORDER BY id"
        ):
            h.update("|".join(str(x or "") for x in row).encode("utf-8"))
    return h.hexdigest()[:16]


def read_graph(db_path: Path | str = store.DB_PATH, *, subject: str = "herb") -> StructureProposal:
    """把 ``knowledge_node`` 读成结构提案：domain → 章，subtype → 节，point → 知识点。"""

    with closing(_connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        domains = conn.execute(
            "SELECT id, name, sort_hint FROM knowledge_node WHERE level='domain' ORDER BY sort_hint, id"
        ).fetchall()
        subtypes = conn.execute(
            "SELECT id, name, parent_id, sort_hint FROM knowledge_node WHERE level='subtype' "
            "ORDER BY parent_id, sort_hint, id"
        ).fetchall()
        points = conn.execute(
            "SELECT id, parent_id, sort_hint FROM knowledge_node WHERE level='point' "
            "ORDER BY parent_id, sort_hint, id"
        ).fetchall()

    by_parent_sub: dict[str, list[sqlite3.Row]] = {}
    for row in subtypes:
        by_parent_sub.setdefault(row["parent_id"], []).append(row)

    by_parent_pt: dict[str, list[sqlite3.Row]] = {}
    for row in points:
        by_parent_pt.setdefault(row["parent_id"], []).append(row)

    chapters: list[ChapterProposal] = []
    for dom in domains:
        sections: list[SectionProposal] = []
        for sub in by_parent_sub.get(dom["id"], []):
            node_ids = [p["id"] for p in by_parent_pt.get(sub["id"], [])]
            sections.append(
                SectionProposal(
                    key=sub["id"],
                    chapter_key=dom["id"],
                    title=sub["name"],
                    point_count=len(node_ids),
                    node_ids=node_ids,
                )
            )
        chapters.append(ChapterProposal(key=dom["id"], title=dom["name"], sections=sections))

    return StructureProposal(
        subject=subject,  # type: ignore[arg-type]
        edition="规划教材",
        source="graph",
        chapters=chapters,
        input_hash=tree_digest(db_path),
    )


# --------------------------------------------------------------------------- 模型细化总论


_GENERAL_PROMPT = """你在为《中药学》教材的总论编排小节结构。

现在总论只有一个节「基本理论」，下挂 3 个知识点：{points}。
请按规划教材《中药学》总论的真实章节结构，把它细分为 {lo}–{hi} 个节。

要求：
1. 节名用教材原词（例如「四气五味」「升降浮沉」「归经」「配伍」「用药禁忌」「中药的炮制」「剂量与用法」「中药的采收与贮藏」），不要自创分类。
2. key 依次为 general.s1、general.s2 …… 不得跳号。
3. sample_terms 只填**具体药名或方剂名**；总论多数小节没有代表药，留空数组即可，不要填「四气」这类概念词。
4. 把上面 3 个既有知识点逐个分配到语义最贴近的节，用 node_assign 给出「知识点名 → 节 key」。
   **按标题字面判断**：「升降浮沉」就该归「升降浮沉」，不要归到「四气五味」。
5. 不要新增、删除、重命名任何**章**；只处理总论这一章。

只输出 JSON，不要解释：
{{"sections":[{{"key":"general.s1","title":"...","sample_terms":[]}}],"node_assign":{{"性味归经":"general.s6"}}}}
"""


def _json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("模型没有返回 JSON 对象")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return parsed


async def refine_chapter_with_llm(
    proposal: StructureProposal,
    chapter_key: str,
    *,
    temperature: float = 0,
    db_path: Path | str = store.DB_PATH,
) -> tuple[StructureProposal, str, str, list[RejectedItem]]:
    """让模型细化指定章（默认总论）。返回新提案、模型名、提示词哈希、拒收项。

    **只替换这一章**。模型的输出里出现别的章一律忽略——它没有权限动全图。
    """

    from app.llm.router import ModelRouter  # 延迟导入：read_graph 不该依赖 LLM 栈

    chapter = next((c for c in proposal.chapters if c.key == chapter_key), None)
    if chapter is None:
        raise ValueError(f"提案里没有章 {chapter_key!r}")

    point_ids = [n for s in chapter.sections for n in s.node_ids]
    # ★ 提示词里必须给「名称（id）」。只给 id 的话模型看到的是 general.p1 这种串，
    #   它只能靠猜去填 node_assign —— 实测会把「升降浮沉」分到「四气五味」。
    with closing(_connect(db_path)) as conn:
        names = {
            r[0]: r[1]
            for r in conn.execute(
                f"SELECT id, name FROM knowledge_node WHERE id IN ({','.join('?' * len(point_ids))})",
                point_ids,
            )
        } if point_ids else {}
    name_to_id = {names.get(pid, pid): pid for pid in point_ids}

    lo, hi = GENERAL_SECTION_RANGE
    prompt = _GENERAL_PROMPT.format(
        points="、".join(f"{names.get(pid, pid)}（{pid}）" for pid in point_ids) or "（无）",
        lo=lo,
        hi=hi,
    )
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

    router = ModelRouter()
    raw = await router.chat(
        "reason",
        [{"role": "user", "content": prompt}],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    payload = _json_object(raw)

    rejected: list[RejectedItem] = []
    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ValueError("模型没有给出 sections")

    # 知识点名 → 节 key；用于把既有 point 挂回新的节
    node_assign = payload.get("node_assign") or {}

    def _resolve(name: str) -> str | None:
        """模型可能回名称、回 id、或把「名称（id）」整串抄回来，三种都认。"""

        if name in name_to_id:
            return name_to_id[name]
        if name in point_ids:
            return name
        bare = re.sub(r"[（(].*?[）)]", "", str(name)).strip()
        return name_to_id.get(bare)

    new_sections: list[SectionProposal] = []
    for i, item in enumerate(raw_sections, start=1):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or f"{chapter_key}.s{i}").strip()
        title = str(item.get("title") or "").strip()
        if not title:
            rejected.append(RejectedItem(kind="section", key=key, reason="节标题为空"))
            continue
        terms = [str(t).strip() for t in (item.get("sample_terms") or []) if str(t).strip()]
        new_sections.append(
            SectionProposal(
                key=key,
                chapter_key=chapter_key,
                title=title,
                sample_terms=terms,
            )
        )

    if not new_sections:
        raise ValueError("模型给出的节全部被拒收")

    # 按 node_assign 把既有知识点放回去；没被指派的，落到第一节，避免知识点凭空消失
    section_keys = {s.key for s in new_sections}
    assigned: dict[str, list[str]] = {s.key: [] for s in new_sections}
    for name, target in node_assign.items():
        node_id = _resolve(str(name))
        if node_id is None:
            rejected.append(RejectedItem(kind="term", key=str(name), reason="不是本节的既有知识点"))
            continue
        if target not in section_keys:
            rejected.append(
                RejectedItem(kind="section", key=str(target), reason="node_assign 指向了不存在的节")
            )
            target = new_sections[0].key
        assigned[target].append(node_id)

    # 兜底：几何上完全同名的（知识点「升降浮沉」↔ 节「升降浮沉」）不该被分到别处。
    # 只在"完全相等"时才纠正，避免把模型的合理判断改坏。
    for node_id in point_ids:
        node_name = names.get(node_id, "")
        twin = next((s.key for s in new_sections if s.title == node_name), None)
        if twin is None:
            continue
        for lst in assigned.values():
            if node_id in lst:
                lst.remove(node_id)
        assigned[twin].append(node_id)

    placed = {n for lst in assigned.values() for n in lst}
    for node_id in [p for p in point_ids if p not in placed]:
        assigned[new_sections[0].key].append(node_id)

    rebuilt = [
        SectionProposal(
            key=s.key,
            chapter_key=chapter_key,
            title=s.title,
            point_count=len(assigned[s.key]),
            node_ids=assigned[s.key],
            sample_terms=s.sample_terms,
        )
        for s in new_sections
    ]

    chapters = [ChapterProposal(key=c.key, title=c.title, sections=rebuilt) if c.key == chapter_key else c
                for c in proposal.chapters]
    out = proposal.model_copy(
        update={
            "chapters": chapters,
            "source": "llm",
            "model": getattr(router, "last_model", "") or "",
            "prompt_hash": prompt_hash,
        }
    )
    return out, out.model, prompt_hash, rejected


# --------------------------------------------------------------------------- 校验


def _validate_against_syllabus(
    proposal: StructureProposal,
    doc: dict[str, Any],
    db_path: Path | str,
) -> ValidateResult:
    """以教材目录为基准校验提案。

    这里检查的不是「结构合不合理」，而是**「结构有没有偏离教材」**：
    章号连不连续、节数对不对、虚拟节标记有没有丢、每节知识点数量与教材条目
    是否一致。没有这一层，``syllabus.py`` 少读一个字段就能悄悄让某一章
    从 5 节变成 1 节，而地图上看起来只是"这一章药少"。
    """

    rejected: list[RejectedItem] = []
    fixes: list[AutoFix] = []

    by_key = {c.key: c for c in proposal.chapters}
    expected_keys = [syllabus.chapter_key(c["no"]) for c in doc["chapters"]]
    missing = [k for k in expected_keys if k not in by_key]
    extra = [k for k in by_key if k not in set(expected_keys)]
    for k in missing:
        rejected.append(RejectedItem(kind="chapter", key=k, reason="教材有此章，提案里缺失"))
    for k in extra:
        rejected.append(RejectedItem(kind="chapter", key=k, reason="教材没有这一章"))

    clean_chapters: list[ChapterProposal] = []
    for ch in doc["chapters"]:
        key = syllabus.chapter_key(ch["no"])
        got = by_key.get(key)
        if got is None:
            continue
        if got.title != ch["title"]:
            rejected.append(
                RejectedItem(
                    kind="chapter",
                    key=key,
                    reason="章名与教材不符",
                    detail=f"教材「{ch['title']}」/ 提案「{got.title}」",
                )
            )
        want_secs = {syllabus.section_key(ch["no"], s["no"]): s for s in ch["sections"]}
        got_secs = {s.key: s for s in got.sections}
        for sk, spec in want_secs.items():
            sec = got_secs.get(sk)
            if sec is None:
                rejected.append(
                    RejectedItem(kind="section", key=sk, reason="教材有此节，提案里缺失", detail=spec["title"])
                )
                continue
            if sec.title != spec["title"]:
                rejected.append(
                    RejectedItem(
                        kind="section",
                        key=sk,
                        reason="节名与教材不符",
                        detail=f"教材「{spec['title']}」/ 提案「{sec.title}」",
                    )
                )
            if sec.virtual != bool(spec.get("virtual")):
                rejected.append(
                    RejectedItem(
                        kind="section",
                        key=sk,
                        reason="虚拟节标记与教材不符",
                        detail=f"教材 {bool(spec.get('virtual'))} / 提案 {sec.virtual}",
                    )
                )
            want_n = max(1, len(spec["entries"]))
            if sec.point_count != len(sec.node_ids):
                fixes.append(
                    AutoFix(
                        kind="section",
                        key=sk,
                        detail=f"point_count {sec.point_count} 与 node_ids {len(sec.node_ids)} 不一致，已按后者修正",
                    )
                )
                sec = sec.model_copy(update={"point_count": len(sec.node_ids)})
            if sec.point_count != want_n:
                fixes.append(
                    AutoFix(
                        kind="section",
                        key=sk,
                        detail=f"知识点 {sec.point_count} ≠ 教材条目 {want_n}，已按教材补齐/裁剪",
                    )
                )
        for sk in got_secs:
            if sk not in want_secs:
                rejected.append(RejectedItem(kind="section", key=sk, reason="教材没有这一节"))
        # ★ 用 model_copy 而不是重新构造 ChapterProposal。
        #   重新构造会**悄悄丢掉没写出来的字段**：章号、分部、书上章号都吃过这个亏
        #   （落库后 65 节的 chapter_no=0 / part=''，地图上显示「第 0 章」，
        #   而校验是通过的——因为校验只看结构不看这两列）。
        #   model_copy 只改指定字段，加新字段时不会重演。
        clean_chapters.append(
            got.model_copy(
                update={
                    "key": key,
                    "no": ch["no"],
                    "book_no": int(ch["no"]) if ch.get("book_no") is None else int(ch["book_no"]),
                    "part": ch["part"],
                    "sections": [s for s in got.sections if s.key in want_secs],
                }
            )
        )

    total_sections = sum(len(c.sections) for c in clean_chapters)
    pending = whitelist.pending_of_sections(
        (s for c in clean_chapters for s in c.sections), db_path=db_path
    )

    cleaned = proposal.model_copy(update={"chapters": clean_chapters})
    fatal = [r for r in rejected if r.kind == "chapter"]
    return ValidateResult(
        ok=not fatal,
        proposal=cleaned,
        rejected=rejected,
        auto_fixed=fixes,
        pending_terms=pending,
        stats={
            "chapters": len(clean_chapters),
            "sections": total_sections,
            "real_sections": sum(1 for c in clean_chapters for s in c.sections if not s.virtual),
            "virtual_sections": sum(1 for c in clean_chapters for s in c.sections if s.virtual),
            "points": sum(s.point_count for c in clean_chapters for s in c.sections),
            "source": proposal.source,
            "input_hash": proposal.input_hash,
            "prompt_hash": proposal.prompt_hash,
            "pending_terms": len(pending),
            "textbook": doc["textbook"],
            "syllabus_digest": syllabus.digest(doc),
        },
    )


def validate_structure(
    proposal: StructureProposal,
    db_path: Path | str = store.DB_PATH,
    *,
    doc: dict[str, Any] | None = None,
) -> ValidateResult:
    """结构校验。**不修改入参**，返回修正后的副本与拒收清单。

    ``doc`` 给了就以教材目录为基准（``source='syllabus'`` 的正常路径）；
    不给则退回到"以知识树为基准"的旧行为，只用于 ``source='graph'`` 的对照。
    """

    if doc is not None:
        return _validate_against_syllabus(proposal, doc, db_path)

    rejected: list[RejectedItem] = []
    fixes: list[AutoFix] = []

    with closing(_connect(db_path)) as conn:
        known_chapters = {r[0] for r in conn.execute("SELECT id FROM knowledge_node WHERE level='domain'")}
        known_subs = {r[0] for r in conn.execute("SELECT id FROM knowledge_node WHERE level='subtype'")}

    # ① 章：数量与身份
    chapters = [c for c in proposal.chapters if c.key in known_chapters]
    for c in proposal.chapters:
        if c.key not in known_chapters:
            rejected.append(
                RejectedItem(kind="chapter", key=c.key, reason="章不在知识树里", detail=c.title)
            )
    if len(chapters) < MIN_CHAPTERS:
        rejected.append(
            RejectedItem(
                kind="chapter",
                key="*",
                reason=f"章数 {len(chapters)} 少于下限 {MIN_CHAPTERS}（疑似过度裁剪）",
            )
        )
    if len(chapters) > MAX_CHAPTERS:
        rejected.append(
            RejectedItem(kind="chapter", key="*", reason=f"章数 {len(chapters)} 超过上限 {MAX_CHAPTERS}")
        )

    # ② 节：key 归属、去重、空章
    seen: set[str] = set()
    clean_chapters: list[ChapterProposal] = []
    for c in chapters:
        secs: list[SectionProposal] = []
        for s in c.sections:
            if s.key in seen:
                rejected.append(RejectedItem(kind="section", key=s.key, reason="节 key 重复"))
                continue
            if not s.key.startswith(c.key + "."):
                rejected.append(
                    RejectedItem(kind="section", key=s.key, reason=f"节不属于章 {c.key}")
                )
                continue
            seen.add(s.key)
            secs.append(s)
        if not secs:
            rejected.append(RejectedItem(kind="chapter", key=c.key, reason="章下没有任何节"))
            continue

        # 节 key 与知识树 subtype 对不上时，记一条自动修正（模型新造的节）
        fresh = [s for s in secs if s.key not in known_subs]
        if fresh:
            fixes.append(
                AutoFix(
                    kind="section",
                    key=c.key,
                    detail=f"新增 {len(fresh)} 个原树没有的节：" + "、".join(s.title for s in fresh[:6]),
                )
            )
        clean_chapters.append(c.model_copy(update={"sections": secs}))

    total_sections = sum(len(c.sections) for c in clean_chapters)
    if total_sections < MIN_SECTIONS:
        rejected.append(
            RejectedItem(kind="section", key="*", reason=f"节数 {total_sections} 少于下限 {MIN_SECTIONS}")
        )
    if total_sections > MAX_SECTIONS:
        rejected.append(
            RejectedItem(kind="section", key="*", reason=f"节数 {total_sections} 超过上限 {MAX_SECTIONS}")
        )

    # ③ 名称白名单：只查具体的药名/方名（sample_terms），概念词本来就不在名录里
    pending = whitelist.pending_of_sections(
        (s for c in clean_chapters for s in c.sections), db_path=db_path
    )

    cleaned = proposal.model_copy(update={"chapters": clean_chapters})
    fatal = [r for r in rejected if r.key == "*"]
    return ValidateResult(
        ok=not fatal and total_sections >= MIN_SECTIONS,
        proposal=cleaned,
        rejected=rejected,
        auto_fixed=fixes,
        pending_terms=pending,
        stats={
            "chapters": len(clean_chapters),
            "sections": total_sections,
            "points": sum(s.point_count for c in clean_chapters for s in c.sections),
            "source": proposal.source,
            "input_hash": proposal.input_hash,
            "prompt_hash": proposal.prompt_hash,
            "pending_terms": len(pending),
        },
    )


# --------------------------------------------------------------------------- 落库


def save_layer(
    result: ValidateResult,
    *,
    subject: str = "herb",
    db_path: Path | str = store.DB_PATH,
) -> dict[str, Any]:
    """把校验通过的提案写成 ``map_layer``。小节本身要等 assign 之后才有穴位。"""

    meta = SUBJECT_META.get(subject, {"title": subject, "color_token": "--qh-accent", "ord": 99, "kind": "container"})
    store.upsert_layer(
        layer_id=subject,
        title=meta["title"],
        kind=meta["kind"],
        graph_version=result.stats.get("input_hash") or tree_digest(db_path),
        color_token=meta["color_token"],
        ord=int(meta["ord"]),
        visible_default=1,
        authoring_meta={
            "source": result.stats.get("source"),
            "textbook": result.stats.get("textbook") or {},
            "syllabus_digest": result.stats.get("syllabus_digest"),
            "model": result.proposal.model,
            "prompt_hash": result.proposal.prompt_hash,
            "input_hash": result.proposal.input_hash,
            "source_edition": result.proposal.edition,
            "validated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "pending_terms": result.pending_terms,
            "rejected": [r.model_dump() for r in result.rejected],
            "auto_fixed": [f.model_dump() for f in result.auto_fixed],
            "sections": result.stats.get("sections"),
            "real_sections": result.stats.get("real_sections"),
            "virtual_sections": result.stats.get("virtual_sections"),
            "chapters": result.stats.get("chapters"),
            "points": result.stats.get("points"),
        },
        db_path=db_path,
    )
    return store.get_layer(subject, db_path) or {}


async def author_subject(
    subject: str = "herb",
    *,
    source: str = "syllabus",
    db_path: Path | str = store.DB_PATH,
    refresh: bool = False,
) -> ValidateResult:
    """一次完整的造图：读教材目录 → 校验。

    ``source='syllabus'`` 是正常路径，**不调模型**：目录是教材的复印件，
    不是模型的创作。``graph`` / ``llm`` 保留作对照，后者需要快照才可复现
    （``temperature=0`` 也挡不住 11 节 / 12 节的抖动）。
    """

    if source == "syllabus":
        # 先查目录再查注册表：目录这条路的失败原因几乎总是"目录没到"，
        # 报「未接入的学科」等于把人送去翻代码，报「缺哪个文件」才可操作。
        # **不许退回读知识树**——知识树把中药学总论压成 1 章 1 节，
        # 退回去等于把"按教材分章"这件事悄悄取消掉。
        if subject not in syllabus.available():
            raise NotImplementedError(
                f"学科 {subject!r} 还没有教材目录（data/syllabus/{subject}.json）。"
                f"当前已就绪：{syllabus.available() or '无'}。"
                f"接入步骤见 app/meridian/syllabus.py 顶部说明。"
            )
        doc = syllabus.load(subject)
        proposal = syllabus.to_proposal(doc, db_path=db_path, subject=subject)
        return validate_structure(proposal, db_path, doc=doc)

    if subject not in SUBJECT_META:
        raise NotImplementedError(f"未接入的学科：{subject!r}")

    proposal = read_graph(db_path, subject=subject)
    extra_rejected: list[RejectedItem] = []

    if source == "llm":
        cached = None if refresh else load_cached_proposal(db_path, subject, source, proposal.input_hash)
        if cached is not None:
            logger.info("命中造图快照 %s，跳过模型调用", cached.prompt_hash)
            proposal = cached
        else:
            proposal, _model, _ph, extra_rejected = await refine_chapter_with_llm(
                proposal, "general", db_path=db_path
            )
            save_cached_proposal(db_path, subject, source, proposal)

    result = validate_structure(proposal, db_path)
    if extra_rejected:
        result = result.model_copy(update={"rejected": list(result.rejected) + extra_rejected})
    return result


# --------------------------------------------------------------------------- 快照
#
# 造图结果必须可复现：同一版知识树 + 同一份提示词，跑出来的地图要一模一样。
# 存快照比"指望模型稳定"可靠，也比每次重算省钱。


def cache_path(db_path: Path | str, subject: str, source: str) -> Path:
    return Path(db_path).parent / "meridian" / f"authoring-{subject}-{source}.json"


def save_cached_proposal(db_path: Path | str, subject: str, source: str, proposal: StructureProposal) -> Path:
    path = cache_path(db_path, subject, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(proposal.model_dump_json(indent=1), encoding="utf-8")
    return path


def load_cached_proposal(
    db_path: Path | str, subject: str, source: str, input_hash: str
) -> StructureProposal | None:
    """只有树指纹一致才复用。树变了还拿旧快照，等于把旧章节结构硬套新知识点。"""

    path = cache_path(db_path, subject, source)
    if not path.exists():
        return None
    try:
        proposal = StructureProposal.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # 快照损坏就当没有，重跑一次比报错好
        logger.warning("造图快照读取失败（%s），将重新生成", exc)
        return None
    if input_hash and proposal.input_hash != input_hash:
        logger.info("知识树已变（%s → %s），旧快照作废", proposal.input_hash, input_hash)
        return None
    return proposal


# --------------------------------------------------------------------------- CLI


def _print_report(result: ValidateResult) -> None:
    st = result.stats
    print(f"来源 {st.get('source')} · 模型 {result.proposal.model or '—'} · prompt {st.get('prompt_hash') or '—'}")
    print(f"章 {st.get('chapters')} · 节 {st.get('sections')} · 知识点 {st.get('points')}")
    print(f"校验 {'通过' if result.ok else '未通过'}")
    if result.rejected:
        print(f"  拒收 {len(result.rejected)} 条：")
        for r in result.rejected[:8]:
            print(f"    - [{r.kind}] {r.key}: {r.reason}")
    if result.auto_fixed:
        print(f"  自动修正 {len(result.auto_fixed)} 条：")
        for f in result.auto_fixed:
            print(f"    - [{f.kind}] {f.key}: {f.detail}")
    if result.pending_terms:
        print(f"  名称待核 {len(result.pending_terms)} 个：{'、'.join(result.pending_terms[:10])}")


async def author_all(
    subjects: Sequence[str] | None = None,
    *,
    source: str = "syllabus",
    db_path: Path | str = store.DB_PATH,
    refresh: bool = False,
) -> dict[str, Any]:
    """全部学科一次造完，然后**对整张表做一次**分配：一具身体，一穴一节。

    为什么不能"每门课各造各的再落库"：分配是有状态的（哪个穴位被占了），
    分两次跑就等于两次都从空图开始，第二门课会把第一门课的位置再占一遍。
    所以分配必须一次性拿到全部提案（``assign.assign_all``），落库也必须
    ``store.replace_all_sections`` 整体替换。

    结构校验不通过就整体中止：宁可一张图都不更新，也不要出现"六门课里少了
    一门，剩下的还错位"的中间态。
    """

    from app.meridian import assign as assign_mod

    targets = list(subjects or assign_mod.ASSIGN_PRIORITY)
    validated: dict[str, Any] = {}
    for subject in targets:
        result = await author_subject(subject, source=source, db_path=db_path, refresh=refresh)
        if not result.ok:
            raise RuntimeError(
                f"学科 {subject!r} 的结构校验未通过（{len(result.rejected)} 项被拒），停止分配"
            )
        validated[subject] = result

    assigned, rows, per_layer = assign_mod.assign_all(
        {subject: item.proposal for subject, item in validated.items()},
        db_path=db_path,
    )
    for subject, item in validated.items():
        save_layer(item, subject=subject, db_path=db_path)
    written = store.replace_all_sections(rows, db_path=db_path)
    return {
        "assign": assigned,
        "rows": rows,
        "per_layer": per_layer,
        "written": written,
    }


def main(argv: Sequence[str] | None = None) -> int:
    pa = argparse.ArgumentParser(description="中药学造图：知识树 → 章 / 节")
    pa.add_argument("--subject", default="herb")
    pa.add_argument("--source", choices=("syllabus", "graph", "llm"), default="syllabus")
    pa.add_argument("--db", default=None)
    pa.add_argument("--refresh", action="store_true", help="忽略造图快照，重新调模型（仅 --source llm）")
    pa.add_argument("--save", action="store_true", help="把结果写入 map_layer（小节仍需 assign）")
    args = pa.parse_args(argv)

    db = Path(args.db) if args.db else store.DB_PATH
    store.init_db(db)
    result = asyncio.run(author_subject(args.subject, source=args.source, db_path=db, refresh=args.refresh))
    _print_report(result)
    if args.save:
        layer = save_layer(result, subject=args.subject, db_path=db)
        print(f"已写入 map_layer: {layer.get('id')} graph_version={layer.get('graph_version')}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
