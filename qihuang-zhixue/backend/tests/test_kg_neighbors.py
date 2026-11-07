"""``/api/kg/neighbors``：图谱的查询入口（P7）+ 可信层筛选（P8）。

**为什么单独一个文件**：这条接口不是「又一个取子图的接口」，它是 P7 的全部内容——
前端 ``graphApi.ts`` 只改一处指向，改动之所以能这么小，前提是这里返回的结构
必须和旧 ``GraphContent`` 逐字兼容（``center_id/nodes/edges/sources``）。
所以断言的重点不是「能取到数据」，而是**「取到的数据能直接喂给不认识的组件」**。

三层语义也要在这里钉住：

- T1 = 十八反/十九畏这类硬约束，错一次就是教学事故；
- T2 = 教材明文记载的事实关系；
- T3 = 按属性算出的相似聚类，**按设计不落库**，请求它必须拿到空集合而不是报错。

``tiers`` 传 ``3`` 时不做 400。理由：调用方（前端筛选器）把「层」当普通筛选维度，
一个维度没有数据是常态；把常态做成错误，前端就得为它写一条错误分支，
而那条分支永远不会被真正测到。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.kg import get_kg_db_path
from app.kg import entities as kg_entities
from app.kg import store as kg_store
from app.kg.adapters import placeholder_for_unresolved, to_graph_content
from app.kg.entities import resolve
from app.kg.schema import ensure_schema
from app.kg.traverse import neighbors
from app.main import app
from tests.helpers import auth_session


def _build_kg_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kg.py"
    spec = importlib.util.spec_from_file_location("build_kg_for_nb", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kg_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = _build_kg_module().build
    db_path = tmp_path_factory.mktemp("nb") / "nb.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    build(db_path)
    return db_path


def _client_for(db_path: Path):
    app.dependency_overrides[get_kg_db_path] = lambda: db_path
    return TestClient(app)


def test_neighbors_payload_is_drop_in_for_old_graph_contract(kg_db: Path) -> None:
    """四个顶层字段一个都不能少：前端组件是按旧契约写的，少了就整块崩。"""

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "nb-user", kg_db)
        response = client.get(
            "/api/kg/neighbors/herb_mahuang", headers=session["headers"]
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert set(["center_id", "nodes", "edges", "sources"]).issubset(payload), payload.keys()
        assert payload["center_id"] == "herb_mahuang"
        assert payload["nodes"] and payload["edges"]

        # 每条边都要能指到 sources 里的一条——这是「所有断言可溯源」红线的运行期检查。
        for edge in payload["edges"]:
            assert 0 <= edge["source_ref"] < len(payload["sources"])
            assert payload["sources"][edge["source_ref"]]["原文摘录"]

        # 节点字段名不能改：GraphView 读的是 id/name/type/brief。
        node = payload["nodes"][0]
        assert set(["id", "name", "type", "brief"]).issubset(node)

        # 真实数据必须自报 using_mock=false，前端 dev 面板就是看这个值。
        assert payload["meta"]["using_mock"] is False
        assert payload["meta"]["center_resolved"] is True
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_neighbors_accepts_chinese_name_not_only_id(kg_db: Path) -> None:
    """传「麻黄」和传 ``herb_mahuang`` 必须得到同一张图。

    解析能力在 ``entities.resolve``，接口只负责不把它绕过去。曾经的坑是
    直接用 ``store.get_entity(id)``，于是中文名一律「教材未收录」。
    """

    entity = resolve("麻黄", kg_db)
    assert entity is not None and entity.id == "herb_mahuang"
    by_id = to_graph_content(neighbors("herb_mahuang", hops=1, db_path=kg_db), kg_db)
    by_name = to_graph_content(neighbors(entity.id, hops=1, db_path=kg_db), kg_db)
    assert by_id["center_id"] == by_name["center_id"]
    assert len(by_id["edges"]) == len(by_name["edges"])


def test_neighbors_never_returns_tier3(kg_db: Path) -> None:
    """T3 不落库，所以请求它只能拿到空。拿到非空说明有人又把相似度写进图里了。"""

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "nb-tier3", kg_db)
        response = client.get(
            "/api/kg/neighbors/herb_mahuang",
            params={"tiers": "3"},
            headers=session["headers"],
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        # tiers=3 被解析成空集合后回落到默认 T1+T2，但任何一条边都不许标成 T3。
        assert all(edge.get("tier") in (1, 2) for edge in payload["edges"])
        assert "3" not in payload["meta"]["tier_counts"]
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_neighbors_tier1_only_is_pure_constraints(kg_db: Path) -> None:
    """只要 T1，剩下的边必须全是配伍禁忌。

    P8 的筛选器就是靠这条语义成立的：关掉 T2 之后画布上剩的那几条线，
    必须真的都是「不能一起用」，否则这个开关就是在骗人。
    """

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "nb-tier1", kg_db)
        response = client.get(
            "/api/kg/neighbors/herb_renshen",
            params={"tiers": "1"},
            headers=session["headers"],
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        tiers = {edge.get("tier") for edge in payload["edges"]}
        assert tiers <= {1}, tiers
        if payload["edges"]:
            assert payload["meta"]["tier_counts"].get("1") == len(payload["edges"])
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_unresolved_center_returns_placeholder_not_404(kg_db: Path) -> None:
    """查不到就明说「教材未收录」，绝不 404。

    404 会让 ``stores/graph.ts`` 回落 15 个硬编码节点的麻黄假图——
    宁可显示「没有」，也不要显示「假的」。
    """

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "nb-missing", kg_db)
        response = client.get(
            "/api/kg/neighbors/not-a-real-node", headers=session["headers"]
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["nodes"]) == 1
        assert payload["edges"] == []
        assert payload["nodes"][0]["brief"] == "教材未收录"
        assert payload["meta"]["center_resolved"] is False
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_tree_chapter_node_is_typed_category(kg_db: Path) -> None:
    """章节节点 / 总论知识点不能被打成药材。

    ``kind_of`` 对不认识的 id 一律返回 ``herb``，于是「性味归经」会顶着
    「药材」的字段表出现在详情面板里——把概念说成药，比缺失更误导。

    夹具只跑 ``build_kg``，不建知识树，所以这里显式补一个总论节点：
    本用例要验的就是「节点在树里、但图中没有对应实体」这条分支。
    """

    from app.path.store import upsert_node

    # knowledge_node 的 parent_id 有外键约束，先建父节点再建子节点。
    upsert_node(
        {"id": "general", "name": "中药学总论", "level": "domain", "parent_id": None},
        kg_db,
    )
    upsert_node(
        {"id": "general.p1", "name": "性味归经", "level": "point", "parent_id": "general"},
        kg_db,
    )

    payload = placeholder_for_unresolved("general.p1", kg_db)
    node = payload["nodes"][0]
    assert node["type"] == "category", node
    assert node["brief"] == "总论知识点·暂无图谱关系"
    assert node["name"] == "性味归经"


# ---------------------------------------------------------------------------
# /api/kg/entity/{id}：详情 + 分 tier 的边
# ---------------------------------------------------------------------------


def test_entity_detail_groups_edges_by_tier(kg_db: Path) -> None:
    """边必须按 tier 分组，而不是一锅平铺。

    ``同类鉴别``（考试会考的辨析）和 ``十八反``（不能一起开）在界面上长得一样，
    但在决策里差一个数量级。平铺返回等于把「怎么判」推给调用方去比字符串。
    """

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "nb-entity", kg_db)
        response = client.get("/api/kg/entity/herb_mahuang", headers=session["headers"])
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["id"] == "herb_mahuang"
        assert payload["kind"] == "herb"
        assert payload["aliases"], "麻黄没有别名，解析层会退化"
        # 分组的 key 只能是请求过的层。
        assert set(payload["edges"]).issubset({"1", "2"})
        assert sum(payload["edge_counts"].values()) > 0

        for tier, items in payload["edges"].items():
            for item in items:
                assert str(item["edge_id"]), "边缺 id：前端的「依据」按钮点不开"
                assert item["direction"] in ("out", "in")
                assert item["peer"]["name"]
                assert item["evidence"], "边没有教材依据——违反可溯源红线"
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_entity_detail_404s_on_unknown(kg_db: Path) -> None:
    """实体不存在时**必须** 404，与 neighbors 的「返回占位图」相反。

    两条接口的取舍不同：图要能显示「这里没有」，而详情面板拿不到内容就该报错，
    否则前端会渲染一张空白的药材卡，看起来像「这味药什么功效都没有」。
    """

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "nb-entity-404", kg_db)
        response = client.get("/api/kg/entity/not-a-real-node", headers=session["headers"])
        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


# ---------------------------------------------------------------------------
# /api/kg/reverse：症 → 证 → 方 → 药
# ---------------------------------------------------------------------------


def test_reverse_maps_symptom_to_syndrome_and_herbs(kg_db: Path) -> None:
    """一句症状描述要能走到具体药材，且每一步都带得回边的 id。"""

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "nb-reverse", kg_db)
        response = client.get(
            "/api/kg/reverse", params={"target": "气虚欲脱"}, headers=session["headers"]
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["ok"] is True, payload
        assert payload["chain"] == ["症", "证", "方", "药"]
        first = payload["syndromes"][0]
        # 证候名原样命中 → name 层，分数最高。
        assert first["layer"] == "name"
        assert first["name"] == "气虚欲脱"
        assert first["herbs"], "证候下一味药都没有，这条路径是断的"
        # 药证链的方向是 herb → syndrome，返回时必须给出「药」这一端。
        herb = first["herbs"][0]
        assert herb["relation_type"] == "药证链"
        assert herb["edge_id"], "缺 edge_id：这条结论无法回溯"
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_reverse_says_nothing_when_lexicon_has_no_match(kg_db: Path) -> None:
    """词表对不上就明说，不给「看着像」的证名。

    这条用例守的是整条链路上最容易失守的地方：模型在这种场景下不会说「不知道」，
    它会就近编一个证。那个证一旦进到处方审查，错的就不是一条展示，是一次开方。
    """

    app.dependency_overrides[get_kg_db_path] = lambda: kg_db
    try:
        client = TestClient(app)
        session = auth_session(client, "nb-reverse-empty", kg_db)
        response = client.get(
            "/api/kg/reverse", params={"target": "zzzz"}, headers=session["headers"]
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is False
        assert payload["syndromes"] == []
        assert "没有能对上" in payload["reason"]
        # 失败时给的是「怎么说才对」，不是道歉。
        assert payload["hint"]
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)
