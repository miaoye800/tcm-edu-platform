"""把「章 → 节」铺到 14 条经脉上。

**这是资产，不是用户数据。** 同一份结构、同一版知识树，跑出来必须完全一样——
"为什么解表药在肺经"要能答上来，答案就写在这张映射表和这段游走算法里。

两条规则决定最终落点：

1. **章的归经偏好**（``hints.CHANNEL_HINTS``）：中医语义上的对应关系，人工定的，
   **按学科分表**。解表药走肺经（肺主皮毛，解表从表解），利水渗湿走三焦经
   （三焦通调水道），温里药走督脉（督脉总督诸阳）。不是按章节顺序硬排。
   漏一章直接报错，不兜底——兜底会让整章静默挤到第一条经上。
   例外是「一节就是一条经」的学科（针灸学各论）：``hints.SECTION_HINTS`` 逐节指定，
   章级表只作兜底；覆盖表里有不存在的节同样报错。
2. **章内节占连续穴位**：一章的若干节沿经线依次排开，走完一条经再走表里经。
   不允许"一章吃满整条经、下一章跳远"，那在地图上是碎的。

一穴可挂 1–3 节（``slot_ord``）：只有当某条经剩余穴位不够时才启用第 2、3 槽，
不主动塞。首版中药学 58 节 / 362 穴，占用率不到 20%，不需要启用。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.meridian import hints as hints_mod
from app.meridian import store
from app.meridian.schema import (
    AssignResult,
    AssignSlot,
    BalanceReport,
    ChannelUsage,
    StructureProposal,
)

_SPILL_ORDER: tuple[str, ...] = (
    "LU", "LI", "ST", "SP", "HT", "SI", "BL", "KI", "PC", "TE", "GB", "LR", "GV", "CV",
)


def next_channel(channel_id: str) -> str:
    try:
        i = _SPILL_ORDER.index(channel_id)
    except ValueError:
        return _SPILL_ORDER[0]
    return _SPILL_ORDER[(i + 1) % len(_SPILL_ORDER)]


def _section_no(section_key: str) -> int:
    """``c09.s3`` → 3。教材节号，前端显示「第三节 清热解毒药」用。"""

    tail = section_key.rsplit(".s", 1)
    return int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else 0


class ChannelCursor:
    """沿经线往前走的游标。一章的节占连续穴位，本经排满就顺流注顺序溢到下一经。

    ``allow_reuse=False`` 时**绝不回头加槽**：本经走到头就返回空，由 ``claim``
    顺延到下一条经。全学科共用一具身体时必须关掉复用——一穴一节是这个模式的
    定义（293 节 / 362 穴，余量足够，复用只会把两个学科挤到同一个点上）。
    """

    def __init__(
        self,
        channels: Sequence[dict[str, Any]],
        acupoints: Sequence[dict[str, Any]],
        *,
        allow_reuse: bool = True,
    ):
        self.order = [c["id"] for c in channels]
        self.title = {c["id"]: c["title"] for c in channels}
        self.allow_reuse = allow_reuse
        self.pool: dict[str, list[dict[str, Any]]] = {ch: [] for ch in self.order}
        for ap in acupoints:
            self.pool.setdefault(ap["channel_id"], []).append(ap)
        for arr in self.pool.values():
            arr.sort(key=lambda a: a["t"])
        self.cursor = {ch: 0 for ch in self.pool}
        #: 该经被占用的**穴位集合**，不是槽位数。一穴挂 3 节仍算占用 1 个穴位，
        #: 否则占用率会算出 300%，看着像爆了其实只是复用。
        self.used: dict[str, set[str]] = {ch: set() for ch in self.pool}
        self.slot_of: dict[str, int] = {}

    def _take_in(self, ch: str, want: int) -> list[tuple[dict[str, Any], int]]:
        """在本经里取 ``want`` 个槽位。

        先按"一穴一节"往后走；本经走到头了，才回头给已有穴位加第 2、3 槽。
        顺序不能反：先复用会在图上表现为「前三个穴挤满、后面的全空」，
        转起来像一条断断续续的虚线。
        """

        out: list[tuple[dict[str, Any], int]] = []
        pool = self.pool.get(ch, [])

        while len(out) < want and self.cursor[ch] < len(pool):
            ap = pool[self.cursor[ch]]
            self.cursor[ch] += 1
            slot = self.slot_of.get(ap["id"], 0) + 1
            self.slot_of[ap["id"]] = slot
            self.used[ch].add(ap["id"])
            out.append((ap, slot))

        if self.allow_reuse and len(out) < want:  # 本经已走到头，回头加槽：先补满 2 槽，再补 3 槽
            while len(out) < want:
                progressed = False
                for ap in pool:
                    if len(out) >= want:
                        break
                    slot = self.slot_of.get(ap["id"], 0) + 1
                    if slot > 3:
                        continue
                    self.slot_of[ap["id"]] = slot
                    self.used[ch].add(ap["id"])
                    out.append((ap, slot))
                    progressed = True
                if not progressed:
                    break
        return out

    def claim(self, channel_id: str, want: int) -> tuple[list[tuple[dict[str, Any], int]], str | None]:
        """取 ``want`` 个槽位。返回 (槽位列表, 溢出后实际到达的经)；取不满时第二个值非空。"""

        got: list[tuple[dict[str, Any], int]] = []
        ch = channel_id
        for _ in range(len(self.order) + 1):
            need = want - len(got)
            if need <= 0:
                break
            got.extend(self._take_in(ch, need))
            if len(got) >= want:
                return got, ch if ch != channel_id else None
            ch = next_channel(ch)
        return got, ch if len(got) < want else None


def _chapter_tables(proposal: StructureProposal, subject: str):
    """取出本学科的「章 → 经」表并做完整性检查。

    缺一章直接抛，不兜底：兜底会让整章静默挤到第一条经上，而"为什么解表药在肺经"
    这个问题的答案就写在表里。
    """

    hint_table = hints_mod.hints_for(subject)
    missing = [c.key for c in proposal.chapters if c.key not in hint_table]
    if missing:
        raise KeyError(
            f"学科 {subject!r} 的这些章没有归经偏好：{missing}。"
            f"请在 app/meridian/hints.py 补上，或在 syllabus 里核对章号。"
        )
    # 节级覆盖（针灸学各论那种"一节就是一条经"）。章级表仍在，作兜底与缺章检查用。
    section_table = hints_mod.section_hints_for(subject)
    all_section_keys = [sec.key for c in proposal.chapters for sec in c.sections]
    orphans = hints_mod.orphan_section_hints(subject, all_section_keys)
    if orphans:
        raise KeyError(
            f"学科 {subject!r} 的节级归经表里有不存在的节：{orphans}。"
            "多半是章号/节号写错，改对之前不许跑，否则会静默落回章级经脉。"
        )
    return hint_table, section_table


def assign_chapters(
    proposal: StructureProposal,
    cursor: ChannelCursor,
    *,
    subject: str,
    layer_id: str,
    ord_start: int = 0,
) -> tuple[list[AssignSlot], list[dict[str, Any]], list[str]]:
    """把一份提案铺到**已有的**游标上。

    游标由调用方决定是"本层专用"还是"六科共享"：

    * ``assign_sections`` 给单科造图用，游标是新起的，行为与旧版一致；
    * ``assign_all`` 给"一具身体装全部学科"用，六科共享同一个游标——
      这时穴位才真正全局唯一，一个点只属于一个小节。
    """

    hint_table, section_table = _chapter_tables(proposal, subject)
    slots: list[AssignSlot] = []
    rows: list[dict[str, Any]] = []
    unmapped: list[str] = []

    ord_counter = ord_start
    for chapter in proposal.chapters:
        chapter_hint = hint_table[chapter.key]
        for sec in chapter.sections:
            ord_counter += 1
            hint = section_table.get(sec.key, chapter_hint)
            got, spill = cursor.claim(hint, 1)
            if not got:
                unmapped.append(sec.key)
                continue
            ap, slot_ord = got[0]
            # 小节 id 必须带学科前缀：``c01.s1`` 在两门学科里都叫这个名字，
            # 而 map_section.id 是全局主键。中药学先占位后，方剂学第一条就撞
            # UNIQUE constraint failed: map_section.id。
            section_id = f"{layer_id}:{sec.key}"
            slots.append(
                AssignSlot(
                    section_id=section_id,
                    chapter_key=chapter.key,
                    acupoint_id=ap["id"],
                    channel_id=ap["channel_id"],
                    slot_ord=slot_ord,
                    t=float(ap["t"]),
                )
            )
            rows.append(
                {
                    "id": section_id,
                    "layer_id": layer_id,
                    "chapter_key": chapter.key,
                    "title": sec.title,
                    "acupoint_id": ap["id"],
                    "slot_ord": slot_ord,
                    "point_count": sec.point_count,
                    "ord": ord_counter,
                    "node_ids": list(sec.node_ids),
                    # 完整教材定位，地图自己就能答「第几章第几节」
                    "chapter_no": chapter.no,
                    "book_chapter_no": (
                        chapter.book_no if chapter.book_no is not None else chapter.no
                    ),
                    "chapter_title": chapter.title,
                    "part": chapter.part,
                    "section_no": _section_no(sec.key),
                    "virtual": sec.virtual,
                }
            )
    return slots, rows, unmapped


def assign_sections(
    proposal: StructureProposal,
    *,
    layer_id: str | None = None,
    subject: str | None = None,
    db_path: Path | str = store.DB_PATH,
    threshold: float = 0.8,
) -> tuple[AssignResult, list[dict[str, Any]]]:
    """把**一门学科**的节铺到穴位上。返回 (分配结果, 可直接落 ``map_section`` 的行)。

    ⚠ 这条路只适合"一次只造一门课"的调试场景。六门课都走它，每门课都会从
    手太阴肺经的第一个穴位重新开始排 —— 于是同一个「中府」既是中药学第 1 节、
    又是内科学第 1 节。要"一具身体装全部学科"必须用 ``assign_all``。

    ``subject`` 决定用哪张章→经表，``layer_id`` 决定小节 id 的学科前缀。
    """

    subject = subject or layer_id or "herb"
    layer_id = layer_id or subject

    channels = store.list_channels(db_path)
    acupoints = store.list_acupoints(db_path)
    if not channels or not acupoints:
        raise RuntimeError("穴位名录为空：先跑 `python -m app.meridian.seed` 或 seed.seed_catalog()")

    cursor = ChannelCursor(channels, acupoints)
    slots, rows, unmapped = assign_chapters(
        proposal, cursor, subject=subject, layer_id=layer_id
    )
    result = AssignResult(
        layer_id=layer_id,
        assigned=slots,
        unmapped=unmapped,
        balance=_balance(cursor, threshold=threshold),
    )
    return result, rows


#: 分配顺序 **≠** 展示顺序（展示按 ``map_layer.ord``）。
#:
#: 一穴一节之后，五条经装不下（肺 30 需 / 11 穴，心 23/9，肝 20/14，任 35/24，
#: 肾 32/27），必然有一批节要沿流注顺序顺延到邻经。**谁先取，谁就保住自己的
#: 归经语义**，所以顺序按「归经依赖度」排：
#:
#: 1. ``acup`` 针灸学——节名本身就是"某经某穴"，落错经等于内容错误，必须最先取；
#: 2. ``internal`` 中医内科学——肺系病证/心系病证/脾胃病证，脏腑归经是它的骨架；
#: 3. ``herb`` 中药学——各论按功效归经（解表→肺、利水渗湿→三焦）；
#: 4. ``formula`` 方剂学——按功效归，稍松；
#: 5. ``diagnosis`` / ``basic``——中诊与中基是理论课，章是"阴阳五行/藏象/四诊"
#:    这类，归到哪条经本来就带象征性，让它们吸收顺延最不伤内容。
#:
#: 实测（293 节，六科合计）：这个顺序能保住 231 节的原始归经；换成"中药学优先"
#: 只保住 231 节但内科学会掉到 24/54（44%），得不偿失。
ASSIGN_PRIORITY: tuple[str, ...] = ("acup", "internal", "herb", "formula", "diagnosis", "basic")


def assign_all(
    proposals: dict[str, StructureProposal],
    *,
    db_path: Path | str = store.DB_PATH,
    threshold: float = 0.8,
) -> tuple[AssignResult, list[dict[str, Any]], dict[str, int]]:
    """把**全部学科**铺到**同一具身体**上：一穴一节，穴位全局唯一。

    这是地图的正解：293 个小节 / 362 个穴位，余 69 个，不需要任何一穴多节。
    每门课仍按自己的"章 → 经"表选经，所以解表药优先落到肺经；只是肺经装满
    （11 穴，六科合计想用 30 个）之后，后面的节沿流注顺序溢到表里经——肺与
    大肠相表里，顺延不是乱扔，但它确实不在教科书说的那条经上了。
    """

    channels = store.list_channels(db_path)
    acupoints = store.list_acupoints(db_path)
    if not channels or not acupoints:
        raise RuntimeError("穴位名录为空：先跑 `python -m app.meridian.seed` 或 seed.seed_catalog()")

    # 共享游标 + 禁止复用 = 一穴一节。
    cursor = ChannelCursor(channels, acupoints, allow_reuse=False)

    order = [s for s in ASSIGN_PRIORITY if s in proposals]
    order += [s for s in proposals if s not in ASSIGN_PRIORITY]

    all_slots: list[AssignSlot] = []
    all_rows: list[dict[str, Any]] = []
    unmapped: list[str] = []
    per_layer: dict[str, int] = {}

    for subject in order:
        slots, rows, missing = assign_chapters(
            proposals[subject], cursor, subject=subject, layer_id=subject
        )
        all_slots.extend(slots)
        all_rows.extend(rows)
        unmapped.extend(f"{subject}:{key}" for key in missing)
        per_layer[subject] = len(rows)

    # 一穴一节的硬约束：宁可在这里炸，也不要把两个小节塞进同一个点——
    # 那正是这个模式要消灭的现象，静默发生比报错难查一百倍。
    seen: dict[str, str] = {}
    for row in all_rows:
        prior = seen.get(row["acupoint_id"])
        if prior is not None:
            raise AssertionError(
                f"穴位 {row['acupoint_id']} 被两个小节同时占用：{prior} / {row['id']}"
            )
        seen[row["acupoint_id"]] = row["id"]

    result = AssignResult(
        layer_id="all",
        assigned=all_slots,
        unmapped=unmapped,
        balance=_balance(cursor, threshold=threshold),
    )
    return result, all_rows, per_layer


def _balance(cursor: ChannelCursor, *, threshold: float) -> BalanceReport:
    usages: list[ChannelUsage] = []
    for ch in cursor.order:
        capacity = len(cursor.pool.get(ch, []))
        used = len(cursor.used.get(ch, ()))
        usages.append(
            ChannelUsage(
                channel_id=ch,
                title=cursor.title.get(ch, ch),
                capacity=capacity,
                used=used,
                ratio=round(used / capacity, 4) if capacity else 0.0,
            )
        )
    max_ratio = max((u.ratio for u in usages), default=0.0)
    return BalanceReport(
        channels=usages,
        max_ratio=max_ratio,
        over_threshold=[u.channel_id for u in usages if u.ratio > threshold],
        triple_slots=sum(1 for v in cursor.slot_of.values() if v >= 3),
        threshold=threshold,
    )


# --------------------------------------------------------------------------- 审计


def audit(db_path: Path | str = store.DB_PATH) -> dict[str, Any]:
    """已有学习进度有多少能落到地图刻度上。

    ``user_path_state`` 里是**学习进度**（谁学了哪个节点），地图上是**刻度**
    （``map_point_slot``）。两者不对齐时，用户会看到"已经学了 16 个，地图一片漆黑"。
    所以上线前必须跑一次，把没落地的节点列出来——那是需要补 backfill 的部分，
    不能靠"看起来有进度"判断。
    """

    from app.meridian import store as _store

    conn = _store.connect(Path(db_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM user_path_state").fetchone()[0]
        rows = conn.execute(
            """
            SELECT u.node_id, u.status
              FROM user_path_state u
              LEFT JOIN map_point_slot p ON p.node_id = u.node_id
             WHERE p.node_id IS NULL
            """
        ).fetchall()
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM user_path_state GROUP BY status"
        ).fetchall()
    finally:
        conn.close()
    return {
        "total": int(total),
        "mapped": int(total) - len(rows),
        "unmapped": [str(r[0]) for r in rows],
        "by_status": {str(s): int(n) for s, n in by_status},
    }


# --------------------------------------------------------------------------- CLI


def _print_result(result: AssignResult, rows: Sequence[dict[str, Any]]) -> None:
    print(f"分配 {len(rows)} 节 → 穴位 {len({r['acupoint_id'] for r in rows})} 个")
    if result.unmapped:
        print(f"  ⚠ 落不到穴位 {len(result.unmapped)} 节：{'、'.join(result.unmapped[:8])}")
    b = result.balance
    print(f"  最高占用率 {b.max_ratio:.0%}（阈值 {b.threshold:.0%}）· 三槽穴位 {b.triple_slots} 个")
    for u in sorted(b.channels, key=lambda x: -x.ratio)[:6]:
        if u.used:
            print(f"    {u.channel_id:<3} {u.title:<8} {u.used:>2}/{u.capacity:<2} {u.ratio:>5.1%}")
    if b.over_threshold:
        print(f"  ⚠ 超阈值经脉：{'、'.join(b.over_threshold)}")


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    from app.meridian import authoring as authoring_mod

    pa = argparse.ArgumentParser(description="造图 + 分配：教材目录 → 章/节 → 穴位")
    pa.add_argument("--subject", default="herb")
    pa.add_argument("--all", action="store_true", help="全学科一次性分配（一穴一节，整表重建）")
    pa.add_argument("--source", choices=("syllabus", "graph", "llm"), default="syllabus")
    pa.add_argument("--db", default=None)
    pa.add_argument("--refresh", action="store_true", help="忽略造图快照，重新调模型（仅 --source llm）")
    pa.add_argument("--no-save", action="store_true", help="只打印，不写库")
    pa.add_argument("--audit", action="store_true", help="只审计已有进度能不能落到地图刻度")
    args = pa.parse_args(argv)

    db = Path(args.db) if args.db else store.DB_PATH
    store.init_db(db)

    if args.audit:
        report = audit(db)
        print(
            f"学习进度 {report['mapped']}/{report['total']} 能落到地图刻度"
            f"（{report['by_status']}）"
        )
        if report["unmapped"]:
            print(f"  ⚠ 落不到刻度的节点 {len(report['unmapped'])} 个：")
            for nid in report["unmapped"][:20]:
                print("   ", nid)
        return 0

    if args.all:
        outcome = asyncio.run(
            authoring_mod.author_all(source=args.source, db_path=db, refresh=args.refresh)
        )
        result = outcome["assign"]
        print(
            f"全学科分配：{sum(outcome['per_layer'].values())} 节 / "
            f"{len(outcome['rows'])} 个不同穴位"
        )
        for subject, count in outcome["per_layer"].items():
            print(f"    {subject:<10} {count:>3} 节")
        _print_result(result, outcome["rows"])
        if args.no_save:
            print("（--no-save：未写库）")
        else:
            print(f"已整体替换 map_section：{outcome['written']} 行")
        return 0

    validated = asyncio.run(
        authoring_mod.author_subject(args.subject, source=args.source, db_path=db, refresh=args.refresh)
    )
    authoring_mod._print_report(validated)
    if not validated.ok:
        print("结构校验未通过，停止分配")
        return 1

    result, rows = assign_sections(validated.proposal, layer_id=args.subject, db_path=db)
    _print_result(result, rows)

    if not args.no_save:
        authoring_mod.save_layer(validated, subject=args.subject, db_path=db)
        n = store.replace_sections(args.subject, rows, db_path=db)
        print(f"已写入 {n} 个小节到 map_section")
        print("统计:", store.layer_stats(args.subject, db_path=db))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
