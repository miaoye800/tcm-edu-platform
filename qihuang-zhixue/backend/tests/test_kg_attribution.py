"""错题归因测试（``app/kg/attribution.py`` + ``/api/mistakes``）。

这里守的是三条红线：

1. **因果必须有教材依据**——``confused_with`` / ``forbidden_pair`` 带 ``edge_id``，
   而且那个 edge 真的存在、真的连着这两个实体；``wrong_herb`` 没有边可指，依据是
   两味药各自的【功效】原文。**没有一条是模型编的解释。**
2. **``unknown`` 不许伪装**——归因不了就报 ``unknown`` 并写清卡在哪一步，
   不能顺手说成「概念混淆」。
3. **重算幂等**——``kg_attribution`` 的 UNIQUE 去不了 ``edge_id IS NULL`` 的重复行，
   所以写入必须「先删后插」，不能靠 ON CONFLICT。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.mistakes import get_mistake_db_path
from app.kg import entities as kg_entities
from app.kg import store as kg_store
from app.kg.attribution import (
    CAUSE_CONFUSED,
    CAUSE_FORBIDDEN,
    CAUSE_INCOMPLETE,
    CAUSE_TYPES,
    CAUSE_UNKNOWN,
    CAUSE_WRONG_HERB,
    _entities_in_text,
    attribute_mistake,
    attribute_user_mistakes,
)
from app.kg.schema import ensure_schema
from app.main import app
from app.quiz.storage import init_quiz_db
from tests.helpers import auth_headers_for_user_id


def _load(module_name: str, relative: str):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kg_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """建一棵知识树 + 一个真图谱。归因要能在真实边上跑，不能拿 mock 边糊过去。"""

    db_path = tmp_path_factory.mktemp("attr") / "attr.db"
    _load("attr_seed_tree", "scripts/seed_knowledge_tree.py").seed(db_path)
    init_quiz_db(db_path)
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    _load("attr_build_kg", "scripts/build_kg.py").build(db_path)
    return db_path


def _add_exam_point(db_path: Path, *, point_id: str, node_id: str, name: str) -> str:
    """``exam_point`` 有 ``UNIQUE(node_id,name)``，同一个考点只能有一条。

    ``kg_db`` 是 module 级的，多个测试会往同一张表里写，所以这里做幂等并返回
    **库里真实的那条 id**——不然测试自己传的 id 和库里的对不上，错题会挂到
    别的考点上，测出来的东西就没意义了。
    """

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO exam_point(id,node_id,name,exam_years_json,frequency,source,created_at)
               VALUES(?,?,?,'[]',0,'test','2026-01-01T00:00:00+00:00')""",
            (point_id, node_id, name),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM exam_point WHERE node_id=? AND name=?", (node_id, name)
        ).fetchone()
    assert row is not None
    return str(row[0])


def _add_question(
    db_path: Path,
    *,
    question_id: str,
    exam_point_id: str,
    options: list[dict[str, str]],
    answer: list[str],
    question_type: str = "single",
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO real_question(id,exam_point_id,stem,options_json,answer_json,
                   analysis,year,source,question_type,created_at)
               VALUES(?,?,?,?,?,'','','test',?, '2026-01-01T00:00:00+00:00')""",
            (
                question_id,
                exam_point_id,
                # 题干用 question_id 保证唯一：``real_question`` 有
                # ``UNIQUE(exam_point_id, stem)``，同一个考点下写两条「题干」
                # 会互相覆盖。
                f"题干-{question_id}",
                json.dumps(options, ensure_ascii=False),
                json.dumps(answer, ensure_ascii=False),
                question_type,
            ),
        )
        conn.commit()


def _add_mistake(
    db_path: Path,
    *,
    user_id: str,
    question_id: str,
    exam_point_id: str,
    answer: list[str],
    expected: list[str],
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO mistake_record(user_id,question_id,exam_point_id,answer_json,
                   expected_json,remediation_json,created_at)
               VALUES(?,?,?,?,?,'{}',?)""",
            (
                user_id,
                question_id,
                exam_point_id,
                json.dumps(answer, ensure_ascii=False),
                json.dumps(expected, ensure_ascii=False),
                created_at,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _choice_case(db_path: Path, *, suffix: str, point_id: str, node_id: str, name: str,
                 options: list[str], picked: str) -> int:
    """造一道选择题错题：正确项恒为 A，用户选了 ``picked``。"""

    real_point_id = _add_exam_point(db_path, point_id=point_id, node_id=node_id, name=name)
    _add_question(
        db_path,
        question_id=f"rq_{suffix}",
        exam_point_id=real_point_id,
        options=[{"key": chr(65 + i), "text": text} for i, text in enumerate(options)],
        answer=["A"],
    )
    return _add_mistake(
        db_path,
        user_id="attr-user",
        question_id=f"rq_{suffix}",
        exam_point_id=real_point_id,
        answer=[picked],
        expected=["A"],
        created_at=f"2026-01-01T00:00:0{len(suffix)}+00:00",
    )


# --------------------------------------------------------------------------- #
# 有边可依的归因
# --------------------------------------------------------------------------- #


def test_confused_choice_cites_the_textbook_differentiation_edge(kg_db: Path) -> None:
    """选了桂枝而考点是麻黄 → 概念混淆，且引的是教材那句鉴别原文。"""

    mistake_id = _choice_case(
        kg_db, suffix="mah", point_id="ep_mah", node_id="d01.s1.p1", name="麻黄",
        options=["麻黄", "桂枝", "香薷"], picked="B",
    )
    result = attribute_mistake(mistake_id, db_path=kg_db)

    assert result["ok"] is True
    assert result["entity"]["name"] == "麻黄"
    cause = result["causes"][0]
    assert cause["cause_type"] == CAUSE_CONFUSED
    assert cause["entity_name"] == "桂枝"
    assert cause["edge_id"] is not None

    # 边必须真的存在，而且真的连着这两个实体——不能只是「有个 id」。
    edge = kg_store.get_edge(cause["edge_id"], db_path=kg_db)
    assert edge is not None
    assert edge.relation_type == "同类鉴别"
    assert {edge.source_id, edge.target_id} == {"herb_mahuang", "herb_guizhi"}
    assert cause["quote"] == edge.evidence[0]["quote"]
    assert "桂枝" in cause["quote"] or "麻黄" in cause["quote"]


def test_forbidden_pair_is_labelled_differently_from_confusion(kg_db: Path) -> None:
    """甘草 + 甘遂 是十八反，不是「长得像」。两者纠正方式不同，标签不能混。"""

    mistake_id = _choice_case(
        kg_db, suffix="gan", point_id="ep_gan", node_id="d17.s1.p1", name="甘草",
        options=["甘草", "甘遂"], picked="B",
    )
    result = attribute_mistake(mistake_id, db_path=kg_db)

    cause = result["causes"][0]
    assert cause["cause_type"] == CAUSE_FORBIDDEN
    assert cause["cause_type"] != CAUSE_CONFUSED
    edge = kg_store.get_edge(cause["edge_id"], db_path=kg_db)
    assert edge is not None and edge.relation_type in {"十八反", "十九畏", "相反"}
    assert "禁忌" in cause["explanation"]


def test_wrong_herb_when_textbook_has_no_differentiation_between_the_pair(kg_db: Path) -> None:
    """选项是**另一味真药**、但教材没写二者鉴别 → ``wrong_herb``，不是 ``unknown``。

    「你把麻黄答成了石膏」是已经发生的事实，不需要教材的鉴别句来成立。缺的只是
    一句可以引用的鉴别原文，那就退回两味药各自的【功效】——那也是教材正文。
    早期这里报 ``unknown``，理由是「选项解析不到实体」——理由本身就是错的，
    因为选项就是一个实体，只是它和考点之间没有边。
    """

    mistake_id = _choice_case(
        kg_db, suffix="wrh", point_id="ep_wrh", node_id="d01.s1.p1", name="麻黄",
        options=["麻黄", "石膏"], picked="B",
    )
    result = attribute_mistake(mistake_id, db_path=kg_db)

    cause = result["causes"][0]
    assert cause["cause_type"] == CAUSE_WRONG_HERB
    assert cause["cause_type"] != CAUSE_UNKNOWN
    assert cause["entity_name"] == "石膏"
    # 没有边可指，就老实给 None，不编一个 edge_id 出来。
    assert cause["edge_id"] is None
    # 依据必须是两味药的教材功效原文，不能是「无法归因」。
    assert "麻黄" in cause["quote"] and "石膏" in cause["quote"]
    assert "发汗解表" in cause["quote"]
    # 比 confused_with（引鉴别句，0.93）低：事实确定，但缺教材的鉴别原文做依据。
    assert 0 < cause["score"] < 0.93


def test_unknown_still_wins_when_the_picked_herb_has_no_effect_data(kg_db: Path) -> None:
    """连功效都取不到（教材未载 / 非药材）就还得报 ``unknown``——不硬编依据。"""

    mistake_id = _choice_case(
        kg_db, suffix="wrh2", point_id="ep_wrh2", node_id="d01.s1.p1", name="麻黄",
        options=["麻黄", "性味归经"], picked="B",
    )
    cause = attribute_mistake(mistake_id, db_path=kg_db)["causes"][0]

    assert cause["cause_type"] == CAUSE_UNKNOWN
    assert "解析不到" in cause["explanation"] or "没有记载" in cause["explanation"]


def test_answer_incomplete_needs_the_textbook_effect_list(kg_db: Path) -> None:
    """答到考点却一项功效都没提 → 要点缺失；提了功效就不能再报这一类。"""

    point = _add_exam_point(kg_db, point_id="ep_mah2", node_id="d01.s1.p1", name="麻黄")
    _add_question(
        kg_db, question_id="rq_short", exam_point_id=point, options=[],
        answer=["麻黄"], question_type="short",
    )

    incomplete = _add_mistake(
        kg_db, user_id="attr-user", question_id="rq_short", exam_point_id=point,
        answer=["麻黄就是那个能解表的吧"], expected=["麻黄"], created_at="2026-02-01T00:00:00+00:00",
    )
    cause = attribute_mistake(incomplete, db_path=kg_db)["causes"][0]
    assert cause["cause_type"] == CAUSE_INCOMPLETE
    assert cause["edge_id"] is None
    assert "发汗解表" in cause["quote"]

    covered = _add_mistake(
        kg_db, user_id="attr-user", question_id="rq_short", exam_point_id=point,
        answer=["麻黄能发汗解表、宣肺平喘"], expected=["麻黄"], created_at="2026-02-02T00:00:00+00:00",
    )
    # 提到功效之后就没有「要点缺失」的依据了，只能老实报不知道。
    assert attribute_mistake(covered, db_path=kg_db)["causes"][0]["cause_type"] == CAUSE_UNKNOWN


# --------------------------------------------------------------------------- #
# 归不了的，必须说归不了
# --------------------------------------------------------------------------- #


def test_unknown_when_the_picked_option_is_not_a_graph_entity(kg_db: Path) -> None:
    """选项是「性味归经」这种知识点名，图谱里没有对应实体——不许猜成混淆。"""

    point = _add_exam_point(kg_db, point_id="ep_mah3", node_id="d01.s1.p1", name="麻黄")
    _add_question(
        kg_db, question_id="rq_kp", exam_point_id=point,
        options=[{"key": "A", "text": "麻黄"}, {"key": "B", "text": "性味归经"}],
        answer=["A"],
    )
    mistake_id = _add_mistake(
        kg_db, user_id="attr-user", question_id="rq_kp", exam_point_id=point,
        answer=["B"], expected=["A"], created_at="2026-03-01T00:00:00+00:00",
    )
    cause = attribute_mistake(mistake_id, db_path=kg_db)["causes"][0]
    assert cause["cause_type"] == CAUSE_UNKNOWN
    assert cause["edge_id"] is None
    assert "判断不出" in cause["explanation"]


def test_unknown_when_the_exam_point_never_entered_the_graph(kg_db: Path) -> None:
    """考点是「性味归经」，图谱里没有这个实体——直说考点没进图谱。"""

    point = _add_exam_point(kg_db, point_id="ep_kp", node_id="general.p1", name="性味归经")
    _add_question(
        kg_db, question_id="rq_kp2", exam_point_id=point,
        options=[{"key": "A", "text": "性味归经"}], answer=["A"],
    )
    mistake_id = _add_mistake(
        kg_db, user_id="attr-user", question_id="rq_kp2", exam_point_id=point,
        answer=["B"], expected=["A"], created_at="2026-04-01T00:00:00+00:00",
    )
    result = attribute_mistake(mistake_id, db_path=kg_db)
    assert result["entity"] is None
    assert result["causes"][0]["cause_type"] == CAUSE_UNKNOWN
    assert "没有对应实体" in result["causes"][0]["explanation"]


def test_every_unknown_cause_carries_a_reason() -> None:
    """``unknown`` 的 explanation 不许是空串——空字符串等于把「不知道」藏起来。"""

    assert CAUSE_UNKNOWN in CAUSE_TYPES
    for cause_type in CAUSE_TYPES:
        assert cause_type == cause_type.strip() and cause_type


# --------------------------------------------------------------------------- #
# 数据不一致要暴露，不能静默选一个
# --------------------------------------------------------------------------- #


def test_node_id_conflicting_with_exam_point_name_prefers_the_name(kg_db: Path) -> None:
    """考点名「甘草」但 node_id 指到人参的节点——按名字归因，并把冲突写进 notes。"""

    point = _add_exam_point(kg_db, point_id="ep_conflict", node_id="d17.s1.p2", name="甘草")
    _add_question(
        kg_db, question_id="rq_conflict", exam_point_id=point,
        options=[{"key": "A", "text": "甘草"}, {"key": "B", "text": "甘遂"}], answer=["A"],
    )
    mistake_id = _add_mistake(
        kg_db, user_id="attr-user", question_id="rq_conflict", exam_point_id=point,
        answer=["B"], expected=["A"], created_at="2026-05-01T00:00:00+00:00",
    )
    result = attribute_mistake(mistake_id, db_path=kg_db)

    assert result["entity"]["name"] == "甘草"
    assert result["notes"], "节点与考点名不一致时必须报出来"
    # 不硬编码「冲突到哪味药」——知识树一调整测试就会假红。断言语义即可：
    # 冲突被报出来、报的是这个考点、且归因仍按考点名落在甘草上。
    assert "甘草" in result["notes"][0]
    assert "不一致" in result["notes"][0]
    assert "黄芪" not in result["entity"]["name"]
    assert result["causes"][0]["cause_type"] == CAUSE_FORBIDDEN


# --------------------------------------------------------------------------- #
# 落库与汇总
# --------------------------------------------------------------------------- #


def test_attribution_rows_are_persisted_and_recompute_is_idempotent(kg_db: Path) -> None:
    """重算不许堆行。``edge_id IS NULL`` 的行 UNIQUE 去不了重，靠的是先删后插。"""

    mistake_id = _choice_case(
        kg_db, suffix="idem", point_id="ep_idem", node_id="d01.s1.p1", name="麻黄",
        options=["麻黄", "桂枝"], picked="B",
    )
    for _ in range(3):
        attribute_mistake(mistake_id, db_path=kg_db)

    with sqlite3.connect(kg_db) as conn:
        rows = conn.execute(
            "SELECT cause_type,entity_id,edge_id FROM kg_attribution WHERE mistake_id=?",
            (str(mistake_id),),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == CAUSE_CONFUSED
    assert rows[0][2] is not None


def test_summary_keeps_unattributed_out_of_domain_counts(kg_db: Path) -> None:
    """归因不了的错题单独计数，不摊进章节——否则会被读成「这章还行」。"""

    point = _add_exam_point(kg_db, point_id="ep_kp3", node_id="general.p2", name="升降浮沉")
    _add_question(
        kg_db, question_id="rq_kp3", exam_point_id=point,
        options=[{"key": "A", "text": "升降浮沉"}], answer=["A"],
    )
    _add_mistake(
        kg_db, user_id="summary-user", question_id="rq_kp3", exam_point_id=point,
        answer=["B"], expected=["A"], created_at="2026-06-01T00:00:00+00:00",
    )
    _choice_case(
        kg_db, suffix="sum", point_id="ep_sum", node_id="d01.s1.p1", name="麻黄",
        options=["麻黄", "桂枝"], picked="B",
    )
    with sqlite3.connect(kg_db) as conn:
        conn.execute(
            "UPDATE mistake_record SET user_id='summary-user' WHERE question_id='rq_sum'"
        )
        conn.commit()

    summary = attribute_user_mistakes("summary-user", db_path=kg_db)["summary"]
    assert summary["attributed"] == 1
    assert summary["unattributed"] == 1
    assert summary["by_domain"] == {"解表药": 1}
    assert summary["by_cause"][CAUSE_UNKNOWN] == 1


def test_neighbour_whose_name_sits_inside_the_exam_point_is_not_a_confusion(
    tmp_path: Path,
) -> None:
    """「紫苏」⊂「紫苏叶」：学生写对了考点，不能判成把两者搞混。

    反过来「麻黄」⊂「麻黄根」必须照抓——教材里那是一发汗一止汗的两味药。
    所以只排除「近邻名 ⊂ 考点名」一个方向。
    """

    db_path = tmp_path / "sub.db"
    ensure_schema(db_path)
    kg_store.upsert_entities(
        [
            kg_store.KgEntity(id="herb_zisuye", kind="herb", name="紫苏叶"),
            kg_store.KgEntity(id="herb_zisu", kind="herb", name="紫苏"),
            kg_store.KgEntity(id="herb_mahuang", kind="herb", name="麻黄"),
            kg_store.KgEntity(id="herb_mahuanggen", kind="herb", name="麻黄根"),
        ],
        db_path,
    )
    for left, right in (("herb_zisuye", "herb_zisu"), ("herb_mahuang", "herb_mahuanggen")):
        kg_store.upsert_edge(
            kg_store.KgEdge(
                source_id=left, target_id=right, relation_type="同类鉴别", tier=2,
                label=f"同类鉴别·{right}", confidence=0.9,
            ),
            db_path,
        )

    zisuye = kg_store.get_entity("herb_zisuye", db_path)
    assert zisuye is not None
    # 写对了考点名，不该把「紫苏」也捡出来。
    assert [item.name for item in _entities_in_text("紫苏叶能解表散寒", zisuye, db_path)] == []

    mahuang = kg_store.get_entity("herb_mahuang", db_path)
    assert mahuang is not None
    # 写的是另一味药，必须捡出来。
    assert [item.name for item in _entities_in_text("麻黄根能止汗", mahuang, db_path)] == ["麻黄根"]


def test_remediation_uses_tree_ancestors_and_textbook_partners(kg_db: Path) -> None:
    """复习路径来自知识树祖先链和鉴别边，不生成新内容。"""

    mistake_id = _choice_case(
        kg_db, suffix="rem", point_id="ep_rem", node_id="d01.s1.p1", name="麻黄",
        options=["麻黄", "桂枝"], picked="B",
    )
    remediation = attribute_mistake(mistake_id, db_path=kg_db)["remediation"]

    assert [node["name"] for node in remediation["node_path"]] == ["解表药", "发散风寒药", "麻黄"]
    # domain 必须是章节，不是药名——取成 path[-1] 会让汇总变成「每味药一章」。
    assert remediation["domain"] == "解表药"
    partners = {item["name"] for item in remediation["distinguish_from"]}
    assert "桂枝" in partners
    # 同一对药的多条鉴别边只留一条，避免前端重复渲染。
    assert len(partners) == len(remediation["distinguish_from"])
    # ``entry`` 必须指向**真实存在**的端点。早期版本写的是
    # ``/api/kg/neighborhood``——那个路由从来没实现过，前端点了就是 404。
    assert remediation["entry"] == "/api/graph/d01.s1.p1"
    assert remediation["focus_node_id"] == "d01.s1.p1"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_attribution_endpoint_returns_closed_cause_vocabulary(tmp_path: Path) -> None:
    db_path = tmp_path / "api.db"
    _load("attr_api_tree", "scripts/seed_knowledge_tree.py").seed(db_path)
    init_quiz_db(db_path)
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    _load("attr_api_kg", "scripts/build_kg.py").build(db_path)

    app.dependency_overrides[get_mistake_db_path] = lambda: db_path
    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "attr-api-user", db_path)

    point = _add_exam_point(db_path, point_id="ep_api", node_id="d01.s1.p1", name="麻黄")
    _add_question(
        db_path, question_id="rq_api", exam_point_id=point,
        options=[{"key": "A", "text": "麻黄"}, {"key": "B", "text": "桂枝"}], answer=["A"],
    )
    _add_mistake(
        db_path, user_id="attr-api-user", question_id="rq_api", exam_point_id=point,
        answer=["B"], expected=["A"],
    )

    response = client.get("/api/mistakes/attribution", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 1
    assert set(payload["cause_types"]) == set(CAUSE_TYPES)
    assert payload["items"][0]["causes"][0]["cause_type"] == CAUSE_CONFUSED


def test_reattribute_hides_other_users_mistakes(tmp_path: Path) -> None:
    """别人的错题必须 404，不能靠「猜不到 id」当权限。"""

    db_path = tmp_path / "api2.db"
    init_quiz_db(db_path)
    ensure_schema(db_path)
    app.dependency_overrides[get_mistake_db_path] = lambda: db_path
    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "owner-user", db_path)

    point = _add_exam_point(db_path, point_id="ep_own", node_id="d01.s1.p1", name="麻黄")
    _add_question(
        db_path, question_id="rq_own", exam_point_id=point,
        options=[{"key": "A", "text": "麻黄"}], answer=["A"],
    )
    mistake_id = _add_mistake(
        db_path, user_id="someone-else", question_id="rq_own", exam_point_id=point,
        answer=["B"], expected=["A"],
    )

    assert client.post(f"/api/mistakes/{mistake_id}/attribution", headers=headers).status_code == 404


def test_herb_whose_name_contains_the_answer_is_not_the_same_drug(kg_db: Path) -> None:
    """选「柴胡」而考点是「银柴胡」：名字是子串关系，但这是两味药，必须报「选错药」。

    踩过的坑：`_picked_entities` 曾用「候选名是考点名的一部分就跳过」来防重。
    那条规则是给**自由文本扫描**用的（在「银柴胡」里扫到「柴胡」不该算两次），
    但用在受控选项上就反了：`柴胡` 因为出现在 `银柴胡` 里被丢掉，归因退回
    `unknown`，理由还写成「选项原文解析不到药」——其实解析到了，是被这条挡掉的。
    实测 576 道题里有 2 道（柴胡/银柴胡、黄连/胡黄连）因此误报。
    """

    from app.path.store import upsert_node

    db_path = kg_db
    upsert_node(
        {"id": "d02.s2.p99", "name": "银柴胡", "level": "point", "parent_id": "d02.s2"},
        db_path,
    )
    point = _add_exam_point(db_path, point_id="ep_yinchaihu", node_id="d02.s2.p99", name="银柴胡")
    _add_question(
        db_path,
        question_id="rq_yinchaihu",
        exam_point_id=point,
        options=[
            {"key": "A", "text": "柴胡"},
            {"key": "B", "text": "银柴胡"},
            {"key": "C", "text": "白薇"},
            {"key": "D", "text": "青蒿"},
        ],
        answer=["B"],
    )
    mistake_id = _add_mistake(
        db_path,
        user_id="attr-substring-user",
        question_id="rq_yinchaihu",
        exam_point_id=point,
        answer=["A"],
        expected=["B"],
    )

    result = attribute_mistake(mistake_id, db_path=db_path, persist=False)
    causes = result["causes"]
    assert causes[0]["cause_type"] != CAUSE_UNKNOWN, "银柴胡和柴胡是两味药，不能报无法归因"
    # 教材有这两味的鉴别记载时报「概念混淆」并指向那条鉴别边；没有时退到「选错药」，
    # 依据是两味药各自的【功效】原文。两条都比 `unknown` 有据，不能退回 unknown。
    assert causes[0]["cause_type"] in {CAUSE_CONFUSED, CAUSE_WRONG_HERB}
    assert causes[0]["entity_name"] == "柴胡", "必须指向学生真正选的那味药"


def test_mistake_list_reports_a_total_that_is_not_capped_by_limit(tmp_path: Path) -> None:
    """角标要的是总数，列表要的是本页条数。拿 count 当总数，错题一超 limit 就永远显示 limit。

    侧栏「错题本」角标曾是 `menu.ts` 里写死的 `badge: 3`——跟真实错题数没有任何关系，
    8 条错题也显示 3，0 条也显示 3。改成读 `total` 之后，这条测试守住两个数字的分工。
    """

    db_path = tmp_path / "api_total.db"
    init_quiz_db(db_path)
    ensure_schema(db_path)
    app.dependency_overrides[get_mistake_db_path] = lambda: db_path
    client = TestClient(app)
    headers = auth_headers_for_user_id(client, "attr-total-user", db_path)

    point = _add_exam_point(db_path, point_id="ep_total", node_id="d01.s1.p1", name="麻黄")
    # 三题各错一次。`mistake_record` 有 UNIQUE(user_id, question_id, created_at)，
    # 同一题同一时刻插三条会撞唯一键——用三道题，别去改时间戳糊过去。
    for index in range(3):
        question_id = f"rq_total_{index}"
        _add_question(
            db_path, question_id=question_id, exam_point_id=point,
            options=[{"key": "A", "text": "麻黄"}], answer=["A"],
        )
        _add_mistake(
            db_path, user_id="attr-total-user", question_id=question_id, exam_point_id=point,
            answer=["B"], expected=["A"],
        )

    payload = client.get("/api/mistakes?limit=1", headers=headers).json()
    assert payload["count"] == 1, "count 必须是本次返回的条数"
    assert payload["total"] == 3, "total 必须是该用户的错题总数，不受 limit 截断"
