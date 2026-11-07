"""KG 实体层与 /api/kg 契约测试。

重点验证「同一个东西在不同表里有不同 id」能被统一解析——这是 P1 的全部意义。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.kg import get_kg_db_path
from app.kg import entities as kg_entities
from app.kg import store
from app.kg.schema import ensure_schema
from app.main import app
from app.path.store import upsert_node
from tests.helpers import auth_session


def _seed(db_path: Path) -> None:
    """把真实注册表写进临时库，并补一条知识树节点 + 一条旧哈希实体。"""

    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    store.upsert_entities(entity_list, db_path)
    store.upsert_aliases(alias_list, db_path)

    upsert_node({"id": "d17.s1.p1", "name": "麻黄", "level": "point"}, db_path)
    # 旧图谱用名字哈希当 id，前端缓存里可能还留着
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS graph_entity (id TEXT PRIMARY KEY, name TEXT, "
            "entity_type TEXT, knowledge_node_id TEXT, meta_json TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO graph_entity(id,name,entity_type,created_at) "
            "VALUES ('herb_9045eba288aeb1','桂枝','herb','now')"
        )
        conn.commit()


def test_registry_covers_herbs_formulas_syndromes() -> None:
    entity_list, alias_list = kg_entities.build_registry()

    kinds = {entity.kind for entity in entity_list}
    assert {"herb", "formula", "syndrome"} <= kinds

    herbs = [entity for entity in entity_list if entity.kind == "herb"]
    approved = [e for e in herbs if e.status == "approved"]
    orphans = [e for e in herbs if e.status == "orphan"]
    # approved 的数量**等于 data/herbs 里有结构化条目的药材数**——这是不变量，
    # 不是快照。P4 之前是 76 味人工精编 + 86 味孤儿补全 = 162；P4 之后
    # promote_draft 把 289 味 draft 也补齐了，所以现在跟着 data/herbs 走。
    # 写死数字的测试每加一批药材就要改一次，改成断言不变量才守得住。
    from app.kb.ingest import load_herbs

    assert len(approved) == len(load_herbs())
    # 剩下的 14 味是**教材真的没写**，不是抽取失败：
    #   3 味教材仅目录标注「附药：X」，正文无独立条目（草乌、红大戟、狼毒）
    #   6 味是大类名或药用部位，教材按正名/拆条收录（贝母、瓜蒌皮、瓜蒌子…）
    #   5 味教材未收录（钩吻、密陀僧、水银、犀角、羊血）
    # 详见 data/curated/herb_alias_map.json 与 deliverables/textbook_herbs.md
    assert len(orphans) == 14
    # 孤儿也必须有中文正名——显示裸 id 是旧图谱最刺眼的问题之一
    assert all(entity.name and not entity.name.startswith("herb_") for entity in orphans)

    # 别名表必须覆盖药材正名，否则 search 会漏
    alias_names = {alias for alias, _, _ in alias_list}
    assert "麻黄" in alias_names


def test_resolve_handles_every_historical_id_form(tmp_path: Path) -> None:
    db_path = tmp_path / "kg-resolve.db"
    _seed(db_path)

    # 1. 药材语义 id
    by_id = kg_entities.resolve("herb_mahuang", db_path)
    assert by_id is not None and by_id.name == "麻黄"
    # 2. 中文名
    by_name = kg_entities.resolve("麻黄", db_path)
    assert by_name is not None and by_name.id == "herb_mahuang"
    # 3. 知识树 id → 节点名 → 实体
    by_tree = kg_entities.resolve("d17.s1.p1", db_path)
    assert by_tree is not None and by_tree.name == "麻黄"
    # 4. 旧名字哈希 id → 名字 → 实体
    by_hash = kg_entities.resolve("herb_9045eba288aeb1", db_path)
    assert by_hash is not None and by_hash.name == "桂枝"


def test_resolve_returns_none_instead_of_guessing(tmp_path: Path) -> None:
    db_path = tmp_path / "kg-resolve-miss.db"
    _seed(db_path)
    assert kg_entities.resolve("不存在的药材", db_path) is None
    assert kg_entities.resolve("", db_path) is None


def test_display_name_never_leaks_raw_id(tmp_path: Path) -> None:
    db_path = tmp_path / "kg-display.db"
    _seed(db_path)

    assert kg_entities.display_name("herb_mahuang", db_path) == "麻黄"
    assert kg_entities.display_name("d17.s1.p1", db_path) == "麻黄"
    assert kg_entities.display_name("herb_9045eba288aeb1", db_path) == "桂枝"
    # 完全查不到时给「教材未收录」，而不是把 id 直接显示给用户
    assert kg_entities.display_name("herb_deadbeef", db_path) == "教材未收录"


def test_orphan_herbs_are_marked_not_fabricated(tmp_path: Path) -> None:
    db_path = tmp_path / "kg-orphan.db"
    _seed(db_path)

    # 草乌是教材**真的没有独立条目**的药（只在目录标注「附药：草乌」），
    # 所以它必须一直是 orphan——如果哪天变成 approved，说明有人拿模型
    # 编了一个条目出来，那正是要防的事。
    orphan = store.get_entity("herb_caowu", db_path)
    assert orphan is not None
    assert orphan.name == "草乌"
    assert orphan.status == "orphan"
    assert orphan.extra.get("has_monograph") is False


def test_textbook_entries_without_data_are_draft_not_missing() -> None:
    """教材立了条目的药，注册表里**必须有一席之地**，不能当作不存在。

    反例（修复前）：``三七`` 在知识树上有节点、教材有独立条目，但注册表只读
    ``data/herbs/*.json``，于是 ``/api/graph/{三七的树id}`` 返回「教材未收录」——
    把「我们没做」说成「教材没有」。

    P4 把 289 味 draft 补成了 approved（``scripts/textbook/promote_draft.py``），
    所以现在这个不变量换了个形式来守：**442 条教材条目一条都不能在注册表里查不到**。
    将来再加教材条目时，这条断言会立刻发现漏登。
    """

    entity_list, alias_list = kg_entities.build_registry()
    by_name = {entity.name: entity for entity in entity_list}

    textbook = kg_entities.load_textbook_entries()
    missing = sorted(name for name in textbook if name not in by_name)
    assert not missing, f"教材有条目但注册表里没有身份：{missing[:5]}"

    # 教材有、知识库也有数据 → approved，且带结构化内容
    assert by_name["三七"].status == "approved"
    assert by_name["三七"].extra.get("has_monograph") is True
    assert by_name["三七"].knowledge_node_id, "教材条目必须对齐到知识树节点"
    assert by_name["人参"].status == "approved"
    # 教材真的没立条 → orphan（和 draft 是两件事，不能合并成一个数字）
    assert by_name["草乌"].status == "orphan"

    # draft 是「教材有条目、我们还没抽数据」的暂存态。P4 之后应当为 0；
    # 若将来出现，它不许伪造结构化内容。
    draft = [e for e in entity_list if e.status == "draft"]
    assert all(e.payload_ref is None for e in draft)


def test_preparation_aliases_come_from_textbook_evidence() -> None:
    """炮制品别名必须有教材依据，不能「前缀 × 全部药名」硬凑。"""

    _, alias_list = kg_entities.build_registry()
    aliases = {alias: entity_id for alias, entity_id, _ in alias_list}

    # 教材【用法用量】写明「宜生用 / 多炙用」
    assert aliases.get("生麻黄") == "herb_mahuang"
    assert aliases.get("炙麻黄") == "herb_mahuang"
    # 教材原文直接写了「焦栀子及栀子炭入血分」
    assert aliases.get("焦栀子") == "herb_zhizi"
    # 没有依据的一律不生成——「炙知母」不是教材里的词
    assert "炙知母" not in aliases
    assert "盐人参" not in aliases


def test_display_name_accepts_names_and_aliases(tmp_path: Path) -> None:
    """``display_name`` 必须和 ``resolve`` 一样认得名字/别名。

    修复前它只查 ``kg_entity.id``：``display_name('人参')`` 返回「教材未收录」，
    而同一时刻 ``resolve('人参')`` 明明解析得到——自相矛盾，界面上就是「有却说没有」。
    """

    db_path = tmp_path / "kg-display-alias.db"
    _seed(db_path)

    assert kg_entities.display_name("麻黄", db_path) == "麻黄"
    assert kg_entities.display_name("炙麻黄", db_path) == "麻黄"
    assert kg_entities.display_name("herb_mahuang", db_path) == "麻黄"


def test_tier3_never_persisted(tmp_path: Path) -> None:
    """T3 相似边按设计不落库；一旦有人写进去，统计就会重新被污染。"""

    db_path = tmp_path / "kg-tier.db"
    _seed(db_path)
    assert store.stats(db_path)["tier3_persisted"] == 0


def test_kg_health_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "kg-health.db"
    _seed(db_path)
    app.dependency_overrides[get_kg_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        response = client.get("/api/kg/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["entities"]["total"] > 0
        assert payload["edges"]["by_tier"] == {"T1": 0, "T2": 0, "T3": 0}
        assert payload["tier3_persisted"] == 0
        assert payload["alignment"]["herbs_orphan"] == 14
        # 还没建边，所以前端拿不到真图
        assert payload["kg_ready"] is False
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_kg_health_reports_dangling_tree_links(tmp_path: Path) -> None:
    """挂在「已删除节点」上的实体必须被单独报出来。

    真实事故：当归的实体指向 ``d12.s2.p1``，知识树迁移把这个节点淘汰了（教材里
    当归只在补血药下，存活的是 ``d17.s2.p1``），指向没跟着改。而
    ``tree_points_linked`` 只数「``knowledge_node_id`` 非空」，照样把它算作已对齐
    ——于是 54 + 156 = 210，比 209 个 point 多出一个，没有任何字段说得清多的是谁。
    聚合计数只回答「有多少」，回答不了「对不对」，这个字段就是补这一刀。
    """

    db_path = tmp_path / "kg-dangling.db"
    _seed(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        # 一个真指向（麻黄 → d17.s1.p1）+ 一个悬空指向（桂枝 → gone.node）
        conn.execute("UPDATE kg_entity SET knowledge_node_id='d17.s1.p1' WHERE id='herb_mahuang'")
        conn.execute("UPDATE kg_entity SET knowledge_node_id='gone.node' WHERE id='herb_guizhi'")
        conn.commit()

    app.dependency_overrides[get_kg_db_path] = lambda: db_path
    try:
        payload = TestClient(app).get("/api/kg/health").json()
        assert payload["alignment"]["dangling_links"] == 1
        # 悬空引用仍然被算进 linked —— 这正是它需要单独一个字段的原因
        assert payload["alignment"]["tree_points_linked"] == 2
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)


def test_kg_search_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "kg-search.db"
    _seed(db_path)
    app.dependency_overrides[get_kg_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        session = auth_session(client, "kg-search-user", db_path)

        response = client.get("/api/kg/search?q=麻黄", headers=session["headers"])
        assert response.status_code == 200
        items = response.json()["items"]
        assert items and items[0]["name"] == "麻黄"
        assert items[0]["matched_by"] == "name"

        response = client.get("/api/kg/search?q=ma huang", headers=session["headers"])
        assert response.status_code == 200

        # kind 过滤后只返回该类型；「麻黄」模糊命中「麻黄汤」是预期行为
        response = client.get("/api/kg/search?q=麻黄&kind=formula", headers=session["headers"])
        formula_items = response.json()["items"]
        assert formula_items and all(item["kind"] == "formula" for item in formula_items)
        assert formula_items[0]["name"] == "麻黄汤"
    finally:
        app.dependency_overrides.pop(get_kg_db_path, None)
