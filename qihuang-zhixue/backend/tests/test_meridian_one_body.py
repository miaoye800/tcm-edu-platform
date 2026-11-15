"""一具身体装全部学科：**一穴一节**，穴位全局唯一。

这一组守的是用户直接看见的那条性质：身上一个光点只对应一节课。

旧实现里每门课各跑一遍 ``assign_sections``，每次都从手太阴肺经的第一个穴位
（中府）开始排，于是同一个「中府」既是中药学第 1 节、又是内科学第 1 节——
点开一个穴位，右侧抽屉里冒出六门课的小节。这里把"一穴只挂一节"钉死，
以后谁想改回按学科各排一遍，先看这一组为什么红。
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from app.meridian import assign as assign_mod
from app.meridian import seed, store, syllabus


@pytest.fixture(scope="module")
def odb(tmp_path_factory: pytest.TempPathFactory) -> object:
    db = tmp_path_factory.mktemp("onebody") / "meridian.db"
    store.init_db(db)
    seed.seed_catalog(db)
    # map_section.layer_id 有外键，落库前六门课必须先有 map_layer 行。
    for order, subject in enumerate(assign_mod.ASSIGN_PRIORITY, start=1):
        store.upsert_layer(
            layer_id=subject,
            title=subject,
            kind="container",
            graph_version="test",
            color_token="--qh-accent",
            ord=order,
            visible_default=1,
            db_path=db,
        )
    return db


@pytest.fixture(scope="module")
def proposals(odb: object) -> dict:
    out = {}
    for subject in assign_mod.ASSIGN_PRIORITY:
        doc = syllabus.load(subject)
        out[subject] = syllabus.to_proposal(doc, subject=subject, db_path=odb)  # type: ignore[arg-type]
    return out


def test_every_section_gets_its_own_acupoint(odb: object, proposals: dict) -> None:
    _, rows, per_layer = assign_mod.assign_all(proposals, db_path=odb)  # type: ignore[arg-type]

    total = sum(len([s for c in p.chapters for s in c.sections]) for p in proposals.values())
    assert sum(per_layer.values()) == total == len(rows)
    # 一穴一节：行的数量和不同穴位的数量必须相等
    assert len({r["acupoint_id"] for r in rows}) == len(rows)
    # 全部落在第 1 槽：出现 2、3 槽就说明有穴位被复用了
    assert {r["slot_ord"] for r in rows} == {1}


def test_no_acupoint_is_shared_between_subjects(odb: object, proposals: dict) -> None:
    """同一个穴位不许在两门课里各挂一节。"""

    _, rows, _ = assign_mod.assign_all(proposals, db_path=odb)  # type: ignore[arg-type]

    owners: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        owners[row["acupoint_id"]].add(row["layer_id"])

    shared = {acupoint: subjects for acupoint, subjects in owners.items() if len(subjects) > 1}
    assert shared == {}, f"这些穴位同时属于多门课：{shared}"


def test_capacity_is_not_exhausted(odb: object, proposals: dict) -> None:
    """293 节 / 362 穴：装得下，而且必须有余量——不然下一次教材改版就爆。"""

    _, rows, _ = assign_mod.assign_all(proposals, db_path=odb)  # type: ignore[arg-type]
    acupoints = store.list_acupoints(odb)  # type: ignore[arg-type]
    assert len(rows) < len(acupoints)
    assert len(acupoints) - len(rows) >= 39


def test_needle_sections_keep_their_own_channel(odb: object, proposals: dict) -> None:
    """针灸学的节名就是"某经某穴"，必须落在自己那条经上。

    它的优先级排在第一位，就是为了这条：如果让它最后取，经已占满，
    「足阳明胃经」这一章会掉到手太阴肺经上——那不是位置不理想，是内容错。
    """

    _, rows, _ = assign_mod.assign_all(proposals, db_path=odb)  # type: ignore[arg-type]
    channel_of = {ap["id"]: ap["channel_id"] for ap in store.list_acupoints(odb)}  # type: ignore[arg-type]
    needle = [r for r in rows if r["layer_id"] == "acup"]
    assert needle

    from app.meridian import hints

    section_table = hints.section_hints_for("acup")
    chapter_table = hints.hints_for("acup")
    for row in needle:
        want = section_table.get(
            f"{row['chapter_key']}.s{row['section_no']}", chapter_table.get(row["chapter_key"])
        )
        assert channel_of[row["acupoint_id"]] == want, f"{row['id']} 该在 {want}"


def test_replace_all_sections_rebuilds_the_whole_table(odb: object, proposals: dict) -> None:
    """落库必须整表替换：按层替换会让第二门课又从头占一遍穴位。"""

    _, rows, _ = assign_mod.assign_all(proposals, db_path=odb)  # type: ignore[arg-type]
    written = store.replace_all_sections(rows, db_path=odb)  # type: ignore[arg-type]
    assert written == len(rows)

    with store.connect(odb) as conn:  # type: ignore[arg-type]
        total = conn.execute("SELECT COUNT(*) FROM map_section").fetchone()[0]
        distinct = conn.execute("SELECT COUNT(DISTINCT acupoint_id) FROM map_section").fetchone()[0]
        layers = conn.execute("SELECT COUNT(DISTINCT layer_id) FROM map_section").fetchone()[0]
    assert total == distinct == len(rows)
    assert layers == len(proposals)
