"""判据表验收（``app/kg/contract.py::plan``）。

**这张表才是路由的真考卷**。前面几个测试文件验的是「函数返回什么形状」，
这里验的是「问题来了该不该调图谱、该调哪一个」。三件事必须分开算：

- 漏调（该调没调）——用户问了关系/禁忌，我们却当普通检索答，图谱白建；
- 误调（不该调却调）——用户只问功效，我们却拉一张邻域图，答非所问还更慢；
- 选错（调了但选错能力）——问区别走了 ask，拿到一堆边而不是对照表。

阈值（开发集 60 条正向 + 20 条反向）：

===== ================================ =======
指标   含义                              上限
===== ================================ =======
漏调   ``should_call`` 却判成 None        10%
误调   ``not should_call`` 却调了         8%
选错   调了但不是 ``expected_capability`` 12%
===== ================================ =======

holdout 20 条**只报数不设闸**——它存在的意义是暴露「规则是照着题面编的」
这件事，一旦拿它调规则就失去意义了。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.kg import entities as kg_entities
from app.kg import store as kg_store
from app.kg.constants import Tier
from app.kg.contract import CAPABILITY_ORDER, plan
from app.kg.schema import ensure_schema
from app.profile.store import DB_PATH

SET_PATH = Path(__file__).with_name("kg_judgement_set.json")

#: 已知的判定缺陷。**不许往里加第二条**——加之前先想想是不是该改规则。
#: ``strict=True`` 的意思很硬：哪天这个 bug 修了，测试会变红逼着把这条删掉，
#: 不会留着一条假装还在出问题的空记录。
KNOWN_LIMITATIONS = {
    "none-19": (
        "「藏红花」不在库，实体识别把「藏红花」当成了「红花」的原文提及，"
        "于是凑出第二味药，构不成「缺实体」降级。"
    ),
}


def _load_items() -> list[dict]:
    payload = json.loads(SET_PATH.read_text(encoding="utf-8"))
    return payload["items"]


ITEMS = _load_items()
DEV_POSITIVE = [item for item in ITEMS if item["should_call"] and not item["holdout"]]
HOLDOUT_POSITIVE = [item for item in ITEMS if item["should_call"] and item["holdout"]]
NEGATIVE = [item for item in ITEMS if not item["should_call"]]


def _build_kg_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kg.py"
    spec = importlib.util.spec_from_file_location("build_kg_for_judgement", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kg_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """优先用生产库；没有才现建一份。

    **判据表必须在产品真正会用的那张图上量**。``scripts/build_kg.py`` 只覆盖
    了部分关系类型——大蓟×小蓟、稻芽×麦芽、莪术×三棱 这几对在生产库里是相须，
    在现建库里连边都没有。拿现建库打分，等于给一个用户永远见不到的精简图
    验收，指标全绿也没意义。

    真库缺失时才退回现建（此时判据表仍能验路由，但证据断言会容忍缺边）。
    """

    if DB_PATH.exists() and kg_store.stats(DB_PATH).get("edges_total", 0) > 0:
        return DB_PATH

    build = _build_kg_module().build

    db_path = tmp_path_factory.mktemp("judgement") / "judgement.db"
    ensure_schema(db_path)
    entity_list, alias_list = kg_entities.build_registry(db_path)
    kg_store.upsert_entities(entity_list, db_path)
    kg_store.upsert_aliases(alias_list, db_path)
    build(db_path)
    return db_path


def _route(item: dict, kg_db: Path):
    return plan(item["question"], db_path=kg_db)


def _describe(item: dict, route) -> str:
    """失败信息要能直接定位到「哪条规则接住的」，不然只能靠猜。"""

    return (
        f"\n问题：{item['question']}"
        f"\n期望：{item['expected_capability']!r}（should_call={item['should_call']}）"
        f"\n实际：{route.capability!r}"
        f"\n命中规则：{route.rule} / 触发词：{route.marker or '（无）'}"
        f"\n理由：{route.reason}"
        f"\n种子：{[seed.name for seed in route.seeds]}"
    )


# --------------------------------------------------------------------------- #
# 1. 表本身要立得住
# --------------------------------------------------------------------------- #


def test_set_shape() -> None:
    assert len(ITEMS) == 100
    assert len(DEV_POSITIVE) == 64, "开发集正向必须是 64 条"
    assert len(HOLDOUT_POSITIVE) == 16, "holdout 正向必须正好 16 条"
    assert len(NEGATIVE) == 20, "反向必须 20 条"
    assert len(HOLDOUT_POSITIVE) + len(DEV_POSITIVE) == 80, "正向合计 80 条"

    ids = [item["id"] for item in ITEMS]
    assert len(set(ids)) == len(ids), "id 有重复"

    required = {
        "id",
        "question",
        "question_type",
        "should_call",
        "expected_capability",
        "expected_entity_ids",
        "evidence",
        "holdout",
    }
    for item in ITEMS:
        assert required <= set(item), f"{item['id']} 字段不全"
        assert isinstance(item["should_call"], bool)
        assert isinstance(item["holdout"], bool)
        assert isinstance(item["expected_entity_ids"], list)
        assert item["question"].strip(), f"{item['id']} 没有问句"
        # 该调就必须说清调哪个；不该调就必须是 None。二者不能含糊。
        if item["should_call"]:
            assert item["expected_capability"] in CAPABILITY_ORDER, item["id"]
            assert item["evidence"], f"{item['id']} 该调却没说证据在哪"
        else:
            assert item["expected_capability"] is None, item["id"]
            assert not item["expected_entity_ids"], (
                f"{item['id']} 不该调，却写死了期望实体——"
                "「缺实体」类问题本来就不该有实体"
            )


#: 判据表只考「从教材里查东西」这三类。另外三个能力（lookup / review /
#: attribute）判的是**输入形状**而不是意图——有没有剂量数字、有没有错题号，
#: 一句就够验，塞进 100 条里反而把 80/20 的比例搅乱。它们留在 §5 单独验。
TABLE_CAPABILITIES = {"ask", "compare", "path"}


def test_every_capability_has_cases() -> None:
    """表里的每类意图都得有题。没题的规则等于没验过。"""

    covered = {item["expected_capability"] for item in ITEMS if item["should_call"]}
    assert covered == TABLE_CAPABILITIES, (
        f"覆盖的能力不对：多了 {covered - TABLE_CAPABILITIES}，"
        f"少了 {TABLE_CAPABILITIES - covered}"
    )


def test_expected_entities_exist(kg_db: Path) -> None:
    """期望实体必须真在库里。

    写错一个 id 会让「选错」指标虚高，而且掩盖真问题——所以要建完图一条条点。
    """

    missing: list[str] = []
    for item in ITEMS:
        for entity_id in item["expected_entity_ids"]:
            if kg_store.get_entity(entity_id, db_path=kg_db) is None:
                missing.append(f"{item['id']} -> {entity_id}")
    assert not missing, "判据表里的实体在库里查不到：\n" + "\n".join(missing)


def test_should_call_items_have_evidence(kg_db: Path) -> None:
    """该调的每一条，都得真有边可查。

    否则「该调」本身是假的：调了也只能拿到空结果，用户还以为图谱坏了。
    """

    empty: list[str] = []
    for item in ITEMS:
        if not item["should_call"]:
            continue
        ids = item["expected_entity_ids"]
        if len(ids) < 2:
            continue
        edges = kg_store.find_edge(ids[0], ids[1], db_path=kg_db)
        fact_edges = [edge for edge in edges if edge.tier in (Tier.CONSTRAINT, Tier.FACT)]
        if not fact_edges:
            empty.append(f"{item['id']} {ids} 无边")
    assert not empty, "这些该调的问题在库里查不到边：\n" + "\n".join(empty)


# --------------------------------------------------------------------------- #
# 2. 逐条判定
# --------------------------------------------------------------------------- #


def _param(item: dict):
    if item["id"] in KNOWN_LIMITATIONS:
        return pytest.param(
            item,
            marks=pytest.mark.xfail(strict=True, reason=KNOWN_LIMITATIONS[item["id"]]),
            id=item["id"],
        )
    return pytest.param(item, id=item["id"])


@pytest.mark.parametrize("item", [_param(item) for item in ITEMS])
def test_route_item(item: dict, kg_db: Path) -> None:
    """一条判据一次判定。失败信息里带规则名与触发词，方便直接改词表。"""

    route = _route(item, kg_db)
    assert route.capability == item["expected_capability"], _describe(item, route)


def test_known_limitation_is_real(kg_db: Path) -> None:
    """把已知缺陷钉成断言。

    哪天实体识别会区分「藏红花」与「红花」了，这条会先红——比等着
    ``xfail(strict)`` 泛泛地提示更能说清到底改了什么。
    """

    from app.kg.ask import detect_seeds

    seeds = detect_seeds("人参和藏红花能一起用吗", db_path=kg_db, fuzzy=False)
    assert [seed.name for seed in seeds] == ["人参", "红花"], (
        "实体识别的行为变了，先回头看 none-19 的已知缺陷还在不在"
    )


# --------------------------------------------------------------------------- #
# 3. 汇总指标
# --------------------------------------------------------------------------- #


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _evaluate(items: list[dict], kg_db: Path) -> dict[str, list[str]]:
    misses: list[str] = []
    wrong: list[str] = []
    false_calls: list[str] = []
    for item in items:
        route = _route(item, kg_db)
        if not item["should_call"]:
            if route.capability is not None:
                false_calls.append(f"{item['id']} {route.capability} {item['question']}")
            continue
        if route.capability is None:
            misses.append(f"{item['id']} {item['question']}（{route.rule}）")
        elif route.capability != item["expected_capability"]:
            wrong.append(
                f"{item['id']} 期望 {item['expected_capability']} 实际 {route.capability}"
                f" {item['question']}"
            )
    return {"miss": misses, "wrong": wrong, "false": false_calls}


def test_metrics(kg_db: Path) -> None:
    """三个指标一起看。

    分开算的原因：漏调和误调的补救方向完全相反。漏调要把门槛放宽（多加
    触发词），误调要把门槛收紧（要求更明确的意图）。混成一个「准确率」
    会让人一边加词一边踩另一边，永远调不平。
    """

    dev = _evaluate(DEV_POSITIVE, kg_db)
    neg = _evaluate(NEGATIVE, kg_db)
    holdout = _evaluate(HOLDOUT_POSITIVE, kg_db)

    miss_rate = _rate(len(dev["miss"]), len(DEV_POSITIVE))
    false_rate = _rate(len(neg["false"]), len(NEGATIVE))
    wrong_rate = _rate(len(dev["wrong"]), len(DEV_POSITIVE))

    report = (
        f"\n开发集漏调 {miss_rate:.1%}（{len(dev['miss'])}/{len(DEV_POSITIVE)}）"
        f"\n反向误调 {false_rate:.1%}（{len(neg['false'])}/{len(NEGATIVE)}）"
        f"\n开发集选错 {wrong_rate:.1%}（{len(dev['wrong'])}/{len(DEV_POSITIVE)}）"
        f"\nholdout 漏调 {len(holdout['miss'])}/{len(HOLDOUT_POSITIVE)}"
        f"，选错 {len(holdout['wrong'])}/{len(HOLDOUT_POSITIVE)}"
        f"\n漏调明细：{dev['miss']}"
        f"\n误调明细：{neg['false']}"
        f"\n选错明细：{dev['wrong']}"
        f"\nholdout 明细：{holdout['miss'] + holdout['wrong']}"
    )

    assert miss_rate <= 0.10, "漏调超标" + report
    assert false_rate <= 0.08, "误调超标" + report
    assert wrong_rate <= 0.12, "选错超标" + report


def test_holdout_reported(kg_db: Path) -> None:
    """holdout 单独报数。

    不设紧闸，但也不能放任——超过 30% 说明规则在开发集上过拟合到没边了。
    """

    holdout = _evaluate(HOLDOUT_POSITIVE, kg_db)
    bad = len(holdout["miss"]) + len(holdout["wrong"])
    assert _rate(bad, len(HOLDOUT_POSITIVE)) <= 0.30, (
        f"holdout 崩了：{holdout}"
    )


# --------------------------------------------------------------------------- #
# 4. 反过拟合
# --------------------------------------------------------------------------- #


def _docstring_lines(source: str) -> set[int]:
    """所有文档字符串占用的行号（1 起）。

    文档字符串里就该写真实问句——「如「人参和藜芦能一起用吗」必须走 ask」
    是在解释优先级，不是在背题面。要查的是**判定逻辑**，不是说明文字。
    """

    tree = ast.parse(source)
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                start = first.lineno
                end = getattr(first, "end_lineno", start) or start
                lines.update(range(start, end + 1))
    return lines


def test_no_question_literal_in_source() -> None:
    """路由代码里不许出现判据表的题面片段。

    只看**判定逻辑**：词表元组 + ``plan()`` 的函数体，且剔掉文档字符串。
    真正要防的是把「这张表里有哪些句子」硬编成 if 条件。

    5 字以上原样重合基本只有一种解释：抄题了。
    """

    source_path = Path(plan.__code__.co_filename)
    source = source_path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    skip = _docstring_lines(source)

    head = source[: source.index("@dataclass")].splitlines(keepends=True)
    # 词表从文件头开始，前半段没有能力声明，全部保留
    words = "".join(head)

    start = source.index("# 3. 路由判定")
    start_line = source[:start].count("\n") + 1
    router_lines = [
        line
        for offset, line in enumerate(lines[start_line:], start=start_line + 1)
        if offset not in skip
    ]
    scanned = words + "".join(router_lines)

    leaked: list[str] = []
    for item in ITEMS:
        question = re.sub(r"\s+", "", item["question"])
        for size in (5, 6, 7):
            for offset in range(0, max(0, len(question) - size) + 1):
                fragment = question[offset : offset + size]
                if fragment in scanned:
                    leaked.append(f"{item['id']} -> {fragment!r}")
    assert not leaked, "路由代码里出现了判据题面片段：\n" + "\n".join(sorted(set(leaked)))


def test_counterfactual_intent(kg_db: Path) -> None:
    """同一对药，只换意图词，判定必须跟着变。

    如果「为什么」和「有什么区别」都走同一条分支，说明根本没看懂意图，
    只是撞上了某个词。
    """

    assert plan("麻黄和桂枝为什么常一起用", db_path=kg_db).capability == "ask"
    assert plan("麻黄和桂枝有什么区别", db_path=kg_db).capability == "compare"
    assert plan("麻黄和桂枝怎么扯上关系的", db_path=kg_db).capability == "path"


def test_counterfactual_entity(kg_db: Path) -> None:
    """换个库里没有的药，同一句问法必须降级。

    这条守的是「图谱答不了就别硬答」：判定依赖的是库里有没有这味药，
    不是句子里有没有「为什么」。
    """

    assert plan("麻黄和桂枝为什么常一起用", db_path=kg_db).capability == "ask"
    assert plan("阿司匹林和桂枝为什么常一起用", db_path=kg_db).capability is None


def test_counterfactual_plain(kg_db: Path) -> None:
    """纯属性问题不该调图谱。"""

    for question in ("麻黄的功效是什么", "桂枝的用量是多少", "黄芪归什么经"):
        route = plan(question, db_path=kg_db)
        assert route.capability is None, _describe(
            {"id": question, "question": question, "should_call": False, "expected_capability": None},
            route,
        )


#: 每个触发词至少要接住几条判据。1 次意味着「这条规则只服务这一道题」——那是
#: 抄题，不是抽规则；3 次才够格叫「一类问法」。
MIN_MARKER_HITS = 3


def test_triggers_are_not_one_off_words(kg_db: Path) -> None:
    """触发词必须服务一类问法，不能一条问句配一个专属词。

    7.5 的第四法，但口径换了：不数词表里「出现过几次」，而是数 ``plan()`` 实际
    用它接住了几道题。词表里躺着、没人命中的词不构成过拟合；**只服务一两道题
    的词**才是抄题的痕迹。
    """

    counts: dict[str, int] = {}
    for item in ITEMS:
        if not item["should_call"]:
            continue
        route = plan(item["question"], db_path=kg_db)
        if route.marker:
            counts[route.marker] = counts.get(route.marker, 0) + 1

    one_off = {marker: hits for marker, hits in counts.items() if hits < MIN_MARKER_HITS}
    assert not one_off, f"这些触发词只接住了一两条问句（抄题风险）：{one_off}"
    assert counts, "一条判据都没被触发词接住，规则等于没生效"


def test_contra_beats_diff(kg_db: Path) -> None:
    """禁忌压过鉴别：安全结论不能输给辨析需求。"""

    route = plan("人参和藜芦能一起用吗，有什么区别", db_path=kg_db)
    assert route.capability == "ask", _describe(
        {
            "id": "contra-over-diff",
            "question": "人参和藜芦能一起用吗，有什么区别",
            "should_call": True,
            "expected_capability": "ask",
        },
        route,
    )
    assert route.rule == "contra_over_diff"


def test_causal_beats_relation(kg_db: Path) -> None:
    """因果压过普通关系词：问「为什么」要的是边与原文，不是配伍清单。"""

    route = plan("麻黄和桂枝为什么能一起用", db_path=kg_db)
    assert route.capability == "ask"
    assert route.rule == "causal_over_diff"


def test_empty_question_routes_to_nothing() -> None:
    route = plan("   ")
    assert route.capability is None
    assert route.rule == "empty"


# --------------------------------------------------------------------------- #
# 5. 表外补充：靠形状判定的三个能力
# --------------------------------------------------------------------------- #
#
# 这三类的判据不是「意图词」，是**输入长什么样**：有没有症状词、有没有剂量
# 数字、有没有错题号。一条正向 + 一条反向就够把门槛钉住，也顺手验了它们
# 不会去抢普通问题的活。


def test_lookup_needs_symptom_or_archive_words(kg_db: Path) -> None:
    """反查问的是「什么症状用什么药」，查收录问的是「这味药在不在」。"""

    assert plan("咳嗽用什么药", db_path=kg_db).capability == "lookup"
    assert plan("麻黄在教材里收录了吗", db_path=kg_db).capability == "lookup"
    # 反向：只是提了症状但没问「用什么药」，不该反查
    assert plan("咳嗽有哪些类型", db_path=kg_db).capability is None


def test_review_needs_prescription_shape(kg_db: Path) -> None:
    """审查必须有剂量或明确的「处方/方子」字样，否则只是一句闲聊。"""

    assert plan("麻黄10g 桂枝6g 杏仁9g 这副药有问题吗", db_path=kg_db).capability == "review"
    assert plan("这张处方超量了吗", db_path=kg_db).capability == "review"
    # 反向：问了「方子」但没有剂量也没有「这副药」的指向
    assert plan("麻黄汤是什么方子", db_path=kg_db).capability is None


def test_attribute_needs_mistake_words(kg_db: Path) -> None:
    assert plan("这道错题为什么错了", db_path=kg_db).capability == "attribute"
    assert plan("这题我错了错在哪", db_path=kg_db).capability == "attribute"
    assert plan("麻黄和桂枝为什么常一起用", db_path=kg_db).capability == "ask"
