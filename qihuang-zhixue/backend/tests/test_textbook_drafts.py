"""P4.4 草稿层（herb_draft / patch / sample）的回归测试。

这一层的作用是**拦住**东西，所以每条测试都断言「该拦的拦住了」：

- 终态不许退回（否则一次误点让已入库的药凭空消失）
- 有未看样本不许发布（否则抽样规则等于一句建议）
- 新增超过阈值不许写盘（否则一次正则改错把整库换掉）
- ``_pending/`` 不许被读进来（否则隔离层形同虚设）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.kg import draft
from app.kg.schema import ensure_schema


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "draft.db"
    ensure_schema(path)
    return path


def _seed_one(db_path: Path, name: str = "麻黄", hits: list[dict] | None = None) -> int:
    draft_id, _ = draft.upsert_draft(
        name,
        {"name": name, "toxicity": "无毒"},
        block_id=f"herb:{name}",
        page_start=147,
        page_end=147,
        gate_report={"hits": hits or []},
        db_path=db_path,
    )
    return draft_id


# --------------------------------------------------------------- 状态机


def test_clean_draft_starts_auto_passed(db: Path) -> None:
    draft_id = _seed_one(db)
    assert draft.get_draft(draft_id, db).status == "auto_passed"


def test_warn_draft_starts_pending(db: Path) -> None:
    """有告警就等人看——机器不替人签字。"""

    draft_id = _seed_one(db, hits=[{"code": "G007", "severity": "warn", "detail": "剂量缺单位"}])
    assert draft.get_draft(draft_id, db).status == "pending"


def test_fail_draft_is_rejected_immediately(db: Path) -> None:
    """门禁判死的条目直接 rejected，不占复核队列。"""

    draft_id = _seed_one(db, hits=[{"code": "G008", "severity": "fail", "detail": "引文不在原文"}])
    assert draft.get_draft(draft_id, db).status == "rejected"


def test_approved_is_terminal(db: Path) -> None:
    """已签字的草稿不能退回 pending——这是最重要的一条。

    如果 ``approved → pending`` 走得通，一次误操作就能让已经进了处方审查的药
    突然消失，而且库里没有任何痕迹说明是谁干的。
    """

    draft_id = _seed_one(db)
    draft.transition(draft_id, "approved", reviewer="t", db_path=db)
    with pytest.raises(draft.IllegalTransition):
        draft.transition(draft_id, "pending", db_path=db)
    with pytest.raises(draft.IllegalTransition):
        draft.transition(draft_id, "auto_passed", db_path=db)


def test_reopen_is_explicit_and_traced(db: Path) -> None:
    """退回只能走 reopen()，而且必须留一条 patch。"""

    draft_id = _seed_one(db)
    draft.transition(draft_id, "approved", reviewer="t", db_path=db)
    assert draft.reopen(draft_id, db) == "pending"
    assert draft.get_draft(draft_id, db).status == "pending"
    assert [p.field for p in draft.patches(draft_id, db)] == ["status"]


def test_unknown_status_rejected(db: Path) -> None:
    draft_id = _seed_one(db)
    with pytest.raises(draft.IllegalTransition):
        draft.transition(draft_id, "published", db_path=db)


def test_rerun_does_not_overwrite_signed_draft(db: Path) -> None:
    """重跑抽取不能把人签过的字抹掉——否则幂等性一破，重跑等于删数据。"""

    draft_id = _seed_one(db)
    draft.transition(draft_id, "approved", reviewer="t", db_path=db)
    again, is_new = draft.upsert_draft(
        "麻黄", {"name": "麻黄", "toxicity": "有毒"}, block_id="herb:麻黄", db_path=db
    )
    assert again == draft_id and is_new is False
    record = draft.get_draft(draft_id, db)
    assert record.status == "approved"
    assert record.payload["toxicity"] == "无毒"


# --------------------------------------------------------------- 抽样


def test_sample_rejects_unknown_reason(db: Path) -> None:
    draft_id = _seed_one(db)
    with pytest.raises(ValueError):
        draft.add_sample(draft_id, "b1", "vibes", db_path=db)


def test_pending_samples_blocks_until_verdict(db: Path) -> None:
    draft_id = _seed_one(db)
    draft.add_sample(draft_id, "b1", "first50", db_path=db)
    assert len(draft.pending_samples("b1", db)) == 1
    draft.set_sample_verdict(draft_id, "b1", "first50", "ok", db_path=db)
    assert draft.pending_samples("b1", db) == []


def test_add_sample_is_idempotent(db: Path) -> None:
    """同一批次同一理由重复登记不该翻倍，否则抽样数会虚高。"""

    draft_id = _seed_one(db)
    assert draft.add_sample(draft_id, "b1", "toxic", db_path=db) is True
    assert draft.add_sample(draft_id, "b1", "toxic", db_path=db) is False
    assert len(draft.samples("b1", db)) == 1


def test_sample_verdict_accepts_only_known_values(db: Path) -> None:
    draft_id = _seed_one(db)
    draft.add_sample(draft_id, "b1", "flag", db_path=db)
    with pytest.raises(ValueError):
        draft.set_sample_verdict(draft_id, "b1", "flag", "looks-fine", db_path=db)


# --------------------------------------------------------------- 抽样计划


def test_sample_plan_skips_rejected() -> None:
    """已判死的草稿不该占抽样名额——否则分母被撑大，信号被稀释。"""

    from scripts.textbook.sample_plan import plan

    records = [
        draft.Draft(
            id=index,
            herb_name=f"药{index}",
            block_id="",
            page_start=index,
            page_end=index,
            payload={"toxicity": "无毒"},
            gate_report={"hits": []},
            status="rejected" if index < 60 else "auto_passed",
            model="",
            prompt_hash="",
            created_at="",
            reviewed_by=None,
            reviewed_at=None,
            review_note=None,
        )
        for index in range(120)
    ]
    picks = plan(records, seed=1)
    picked = {draft_id for draft_id, _ in picks}
    assert all(draft_id >= 60 for draft_id in picked)


def test_sample_plan_is_reproducible() -> None:
    """固定种子：两个人、两次跑必须看到同一批。"""

    from scripts.textbook.sample_plan import plan

    records = [
        draft.Draft(
            id=index,
            herb_name=f"药{index}",
            block_id="",
            page_start=index,
            page_end=index,
            payload={"toxicity": "无毒"},
            gate_report={"hits": []},
            status="auto_passed",
            model="",
            prompt_hash="",
            created_at="",
            reviewed_by=None,
            reviewed_at=None,
            review_note=None,
        )
        for index in range(200)
    ]
    assert plan(records, seed=7) == plan(records, seed=7)
    assert plan(records, seed=7) != plan(records, seed=8)


def test_sample_plan_covers_first_page_window_and_toxic() -> None:
    """首批 50 味必须全检；毒性药必须全检——这两条不给概率。"""

    from scripts.textbook.sample_plan import FIRST_BATCH, plan

    records = [
        draft.Draft(
            id=index,
            herb_name=f"药{index}",
            block_id="",
            page_start=index,
            page_end=index,
            payload={"toxicity": "有毒" if index % 10 == 0 else "无毒"},
            gate_report={"hits": []},
            status="auto_passed",
            model="",
            prompt_hash="",
            created_at="",
            reviewed_by=None,
            reviewed_at=None,
            review_note=None,
        )
        for index in range(300)
    ]
    picks = plan(records, seed=3)
    first = {draft_id for draft_id, reason in picks if reason == "first50"}
    assert first == set(range(FIRST_BATCH))
    toxic = {draft_id for draft_id, reason in picks if reason == "toxic"}
    assert toxic == {index for index in range(300) if index % 10 == 0}


def test_sample_plan_is_stable_across_approval(db: Path) -> None:
    """抽样不能依赖审批进度。

    反例：``reverse`` 原来从 ``status == 'auto_passed'`` 里抽。签完字状态变成
    ``approved``，重跑一次抽样就抽到另一批——「我抽过了」这句话没法验证。
    """

    from scripts.textbook.sample_plan import plan

    def build(status: str) -> list[draft.Draft]:
        return [
            draft.Draft(
                id=index,
                herb_name=f"药{index}",
                block_id="",
                page_start=index,
                page_end=index,
                payload={"toxicity": "无毒"},
                gate_report={"hits": []},
                status=status,
                model="",
                prompt_hash="",
                created_at="",
                reviewed_by=None,
                reviewed_at=None,
                review_note=None,
            )
            for index in range(200)
        ]

    assert plan(build("auto_passed"), seed=11) == plan(build("approved"), seed=11)


# --------------------------------------------------------------- 隔离层


def test_pending_dir_is_not_loaded(tmp_path: Path) -> None:
    """``data/herbs/_pending/`` 必须读不进来——第 3 层隔离就靠「只 glob 不递归」。"""

    from app.kb.ingest import load_herbs

    herbs_dir = tmp_path / "herbs"
    (herbs_dir / "_pending").mkdir(parents=True)
    sample = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "herbs" / "textbook_promoted.json")
        .read_text(encoding="utf-8")
    )[0]
    (herbs_dir / "ok.json").write_text(json.dumps([sample], ensure_ascii=False), encoding="utf-8")
    (herbs_dir / "_pending" / "hidden.json").write_text(
        json.dumps([{**sample, "id": "herb_hidden", "name": "隐藏药"}], ensure_ascii=False),
        encoding="utf-8",
    )
    names = {herb.name for herb in load_herbs(herbs_dir)}
    assert sample["name"] in names
    assert "隐藏药" not in names


# --------------------------------------------------------------- diff 阈值


def test_diff_counts_added_and_changed() -> None:
    from scripts.textbook.promote_draft import diff_against

    base = {"麻黄": {"name": "麻黄", "effects": ["发汗解表"]}}
    fresh = [
        {"name": "麻黄", "effects": ["发汗解表"]},
        {"name": "桂枝", "effects": ["发汗解肌"]},
    ]
    added, changed, removed = diff_against(base, fresh)
    assert added == ["桂枝"]
    assert changed == []
    assert removed == []


def test_diff_ignores_field_order() -> None:
    """五味/归经/功效的顺序不携带信息，排法变了不算「修改」。

    不这么处理，教材自己把「微甘、辛」写成「辛、微甘」就会天天触发阈值。
    """

    from scripts.textbook.promote_draft import diff_against

    base = {"升麻": {"name": "升麻", "flavor": ["微甘", "辛"]}}
    fresh = [{"name": "升麻", "flavor": ["辛", "微甘"]}]
    _, changed, _ = diff_against(base, fresh)
    assert changed == []


def test_diff_flags_real_change() -> None:
    from scripts.textbook.promote_draft import diff_against

    base = {"细辛": {"name": "细辛", "toxicity": "小毒"}}
    fresh = [{"name": "细辛", "toxicity": "无毒"}]
    _, changed, _ = diff_against(base, fresh)
    assert changed == [("细辛", ["toxicity"])]


def test_diff_report_marks_threshold_breach() -> None:
    from scripts.textbook.promote_draft import MAX_ADDED, render_diff

    text = render_diff([f"药{i}" for i in range(MAX_ADDED + 1)], [], [], MAX_ADDED + 1)
    assert "--force" in text
    assert "超过阈值" in text


# --------------------------------------------------------------- slug 校正


def test_slug_fix_keeps_entity_id_stable() -> None:
    """拼音丢了字母的药必须走校正表，否则同一味药会变成两个实体。

    茯苓的 PDF 拼音是 ``Fúílng``（丢了 l），不校正就是 ``herb_fuilng``，
    而库里早就有 ``herb_fuling``——两个同名实体撞 ``UNIQUE(kind, name)``，
    整个注册表写库失败。
    """

    from app.kg.entities import load_textbook_entries

    entries = load_textbook_entries()
    assert entries["茯苓"]["slug"] == "fuling"
    assert entries["白及"]["slug"] == "baiji"
    assert entries["莪术"]["slug"] == "ezhu"


def test_registry_has_no_duplicate_names() -> None:
    """同名实体是硬错误：``UNIQUE(kind, name)`` 会让整批写入失败。"""

    from collections import Counter

    from app.kg.entities import build_registry

    entities, _ = build_registry()
    counts = Counter((entity.kind, entity.name) for entity in entities)
    assert [key for key, value in counts.items() if value > 1] == []


def test_orphan_never_shadows_a_monograph() -> None:
    """有 monograph 的药不可能同时是 orphan。

    只按 id 去重会漏掉「正规 id 是 herb_baibiandou、引用里叫 herb_biandou」
    这种情况，于是建出两个同名实体。
    """

    from app.kg.entities import build_registry

    entities, _ = build_registry()
    approved = {entity.name for entity in entities if entity.status == "approved"}
    orphans = {entity.name for entity in entities if entity.status == "orphan"}
    assert approved & orphans == set()
