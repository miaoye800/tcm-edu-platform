from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.agents.path_agent import build_skill_tree, next_recommendations, validate_skill_tree
from app.mastery.bkt import DEFAULT_BKT_PARAMS, get_mastery, record_mastery_event, update_p_mastery
from app.mastery.review import due_reviews, record_review_result


def test_repeated_wrong_answers_lower_bkt_and_enter_recommendations(tmp_path: Path) -> None:
    db_path = tmp_path / "path.db"
    user_id = "u_wrong"
    kc_id = "herb_mahuang"

    baseline = DEFAULT_BKT_PARAMS.p_known_initial
    for index in range(3):
        record_mastery_event(
            user_id=user_id,
            kc_id=kc_id,
            correct=False,
            event_type="quiz",
            evidence={"source": "test", "index": index},
            db_path=db_path,
        )

    mastery = get_mastery(user_id, db_path=db_path)
    tree = build_skill_tree(mastery=mastery)
    recommendations = next_recommendations(
        user_id,
        skill_tree=tree,
        mastery=mastery,
        reviews={"items": [], "suggested_count": 0},
        db_path=db_path,
    )

    assert mastery[kc_id]["p_mastery"] < baseline
    assert any(item["kc_id"] == kc_id for item in recommendations)
    target = next(item for item in recommendations if item["kc_id"] == kc_id)
    assert "薄弱证据" in target["reason"]


def test_prerequisite_not_mastered_keeps_child_locked() -> None:
    mastery = {
        "category:解表药": {"p_mastery": 0.9, "attempts": 4, "last_seen": None},
        "subcategory:解表药:发散风寒药": {"p_mastery": 0.3, "attempts": 2, "last_seen": None},
    }

    tree = build_skill_tree(mastery=mastery)
    mahuang = next(node for node in tree if node["id"] == "herb_mahuang")

    assert mahuang["status"] == "locked"
    assert "subcategory:解表药:发散风寒药" in mahuang["prerequisites"]


def test_skill_tree_dag_validation_passes() -> None:
    tree = build_skill_tree(mastery={})

    validate_skill_tree(tree)

    assert any(node["kind"] == "differentiation" for node in tree)


def test_fsrs_due_item_appears_in_review_list(tmp_path: Path) -> None:
    db_path = tmp_path / "path.db"
    user_id = "u_review"
    kc_id = "herb_guizhi"
    reviewed_at = datetime.now(UTC) - timedelta(days=1)

    record_review_result(
        user_id=user_id,
        kc_id=kc_id,
        rating="again",
        reviewed_at=reviewed_at,
        evidence={"source": "test"},
        db_path=db_path,
    )

    reviews = due_reviews(user_id, today=datetime.now(UTC), db_path=db_path)

    assert reviews["suggested_count"] == 1
    assert [item["kc_id"] for item in reviews["items"]] == [kc_id]


def test_bkt_single_wrong_update_moves_down_from_initial() -> None:
    updated = update_p_mastery(DEFAULT_BKT_PARAMS.p_known_initial, correct=False)

    assert updated < DEFAULT_BKT_PARAMS.p_known_initial



def test_knowledge_node_ddl_has_exactly_one_owner(tmp_path: Path) -> None:
    """knowledge_node 的建表语句只能有一份，且必须带约束。

    踩过：``app/resources/storage.py`` 曾复制过一份更宽松的 DDL，靠
    ``CREATE TABLE IF NOT EXISTS``「先到先得」。资源库先初始化时（比如用户第一次存
    资源），path 那份权威 DDL 变成空操作，``level`` 与 ``exam_frequency`` 的 CHECK
    被静默丢掉——同一份代码，schema 取决于谁先跑。
    """

    import sqlite3
    from contextlib import closing

    from app.resources import storage as resource_storage

    db_path = tmp_path / "ddl-owner.db"
    resource_storage.init_db(db_path)  # 只跑资源库，模拟「它先建表」
    with closing(sqlite3.connect(db_path)) as conn:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='knowledge_node'"
        ).fetchone()[0]

    assert "CHECK(level IN" in ddl, "level 的 CHECK 丢了：说明有第二份 DDL 抢先生效"
    assert "exam_frequency <= 1" in ddl, "exam_frequency 的 CHECK 丢了"
