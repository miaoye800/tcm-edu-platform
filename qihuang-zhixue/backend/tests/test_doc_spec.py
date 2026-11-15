"""文稿工坊：从「关键词套模板」到「按读懂的那句话写」。

这一组断言针对的是**病根**，不是某个用例：

- 用户说「我过四级又要学中药学，应该怎么做」，得到的不该是同一套
  「目标与现状 / 前置准备 / 阶段路线」——那是命中「计划」二字查表查出来的；
- 标题不是用户原句加后缀；
- 面板上改的章节名、篇幅、体裁必须进生成参数（改了不生效 = 骗用户）；
- 主题落在教材某门课里时，章节名来自库里目录，并且真的标成教材依据；
- 整节都是大段落、方案要的表格一张没有，都要如实报出来。
"""

import asyncio
import json

from app.docs import agent as doc_agent
from app.docs.agent import DocAgent
from app.docs.evidence import build_doc_evidence
from app.docs.spec import (
    baseline_spec,
    compose_doc_spec,
    doc_conformance,
    doc_params_from_plan,
    doc_spec_of,
)
from app.resources import planning

#: 用户原始用例：同时含现实约束（四级）、学科（中药学）与「怎么做」。
REQUEST = "我们现在制定一份计划，我通过英语四级并且学习中药学，我应该怎么做"


def _spec_reply(overrides: dict | None = None) -> str:
    payload = {
        "title": "四级与中药学双线备考计划",
        "subtitle": "计划",
        "genre": "plan",
        "audience": "中医药专业在校学生",
        "organization": "先算时间账，再按周次把两条线编进同一张表",
        "organization_kind": "stage",
        "thesis": "两条线共用一张周表：四级拆成每日小块，中药学按总论→各论分阶段推进。",
        "target_words": 2400,
        "sections": [
            {"heading": "双线目标与时间盘点", "goal": "算清截止日期与每周可用时长", "must_cover": ["四级考试日期", "每周可用时长"], "form": "table"},
            {"heading": "四级每日固定动作", "goal": "把四级拆成每天的小块", "must_cover": ["词汇", "听力"], "form": "checklist"},
            {"heading": "中药学总论七章", "goal": "先把术语底座打完", "must_cover": ["中药的性能", "中药的配伍"], "form": "steps"},
            {"heading": "各论二十一章分块", "goal": "按功效分块推进", "must_cover": ["解表药", "补虚药"], "form": "table"},
            {"heading": "周计划与自检", "goal": "把比例变成时段", "must_cover": ["周表模板", "每两周自检"], "form": "checklist"},
        ],
        "devices": ["table", "checklist", "timeline"],
        "emphasis": ["同时过四级", "同时学中药学"],
        "interpretation": [
            "你要的不是两份计划，而是一张把两条线编在一起的周表。",
            "我会先算时间账，再把四级拆成每天固定的小动作，中药学按教材分块推进。",
        ],
    }
    payload.update(overrides or {})
    return json.dumps(payload, ensure_ascii=False)


def _llm(spec: str, body: str = "## 双线目标与时间盘点\n\n正文。\n"):
    """按提示词分流：施工图一次、落笔一次。"""

    calls: list[str] = []

    async def fake(messages: list[dict[str, str]]) -> str:
        system = messages[0]["content"]
        calls.append(system)
        return spec if "写作策划" in system else body

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


# ── 读整句话 ────────────────────────────────────────────────────────────────


def test_baseline_is_honest_and_not_the_request() -> None:
    """无模型时给的是「按规则拟的初稿」，并且标题不能是用户原句。"""

    spec = baseline_spec(REQUEST)
    assert spec.basis == "rules"
    assert spec.title and spec.title != REQUEST
    assert REQUEST not in spec.title
    assert len(spec.sections) >= 3


def test_model_spec_replaces_the_fixed_five_sections() -> None:
    """同一句话，模型读出来的章节不是「目标与现状/前置准备/阶段路线」那套。"""

    async def run():
        return await planning.propose("doc", REQUEST, {}, llm_call=_llm(_spec_reply()))

    plan = asyncio.run(run())
    labels = [unit["label"] for unit in plan["units"]]
    assert plan["spec"]["basis"] == "model"
    assert plan["title"] == "四级与中药学双线备考计划"
    # 旧蓝图那五节是查表查出来的，这里一节都不该出现。
    assert "前置准备" not in labels and "调整与复盘" not in labels
    covered = " ".join(
        item for unit in plan["units"] for item in (unit["detail"] or "").split("；")
    )
    # 用户原话里的两个硬约束必须落到某一节。
    assert "四级考试日期" in covered
    assert "中药的性能" in covered
    assert plan["goal"].startswith("两条线共用一张周表")


def test_title_is_not_the_raw_request_even_if_model_copies_it() -> None:
    """模型把原句当标题时，系统要换掉它，并把这件事记下来。"""

    async def run():
        return await compose_doc_spec(REQUEST, llm_call=_llm(_spec_reply({"title": REQUEST})))

    spec = asyncio.run(run())
    assert spec.title != REQUEST
    assert REQUEST not in spec.title
    assert any("标题" in note for note in spec.notes)


def test_genre_prefix_is_stripped_from_title() -> None:
    """「…·规划」是旧实现的机械拼接，不该再活下来。"""

    async def run():
        return await compose_doc_spec(REQUEST, llm_call=_llm(_spec_reply({"title": "四级与中药学双线备考·计划"})))

    assert asyncio.run(run()).title == "四级与中药学双线备考"


def test_each_genre_gets_its_own_skeleton() -> None:
    """四种交付物的章节必须不一样，否则「支持多种文档形式」是空话。"""

    plan = baseline_spec("帮我做一个中药学复习计划")
    compare = baseline_spec("麻黄与桂枝的区别")
    guide = baseline_spec("如何煎煮中药")
    qa = baseline_spec("中药为什么要先煎后下，常见问题有哪些")
    headings = [set(item.heading for item in spec.sections) for spec in (plan, compare, guide, qa)]
    assert plan.genre == "plan" and compare.genre == "compare"
    assert guide.genre == "guide" and qa.genre == "qa"
    for index in range(len(headings)):
        for other in range(index + 1, len(headings)):
            assert headings[index] != headings[other], (headings[index], headings[other])


def test_explicit_history_request_uses_timeline() -> None:
    """用户说「发展历史」，就按时间排，而不是套「是什么/为什么/怎么用」。"""

    spec = baseline_spec("人参的发展历史")
    assert spec.organization_kind == "timeline"
    joined = " ".join(spec.headings)
    assert "源流" in joined or "演变" in joined
    assert spec.title != "人参的发展历史" or "历史" in spec.title


def test_interpretation_never_leaks_field_names() -> None:
    """解读是给用户看的，不能把内部键名念给他听。"""

    async def run():
        return await compose_doc_spec(
            REQUEST,
            llm_call=_llm(
                _spec_reply(
                    {
                        "interpretation": [
                            "must_cover 里点名了四级与中药学。",
                            "genre=plan，form=table。",
                            "我按两条线排周表，四级拆小、中药学按章推进。",
                        ]
                    }
                )
            ),
        )

    spec = asyncio.run(run())
    assert spec.interpretation == ["我按两条线排周表，四级拆小、中药学按章推进"]


def test_too_many_sections_are_capped_with_a_note() -> None:
    """模型给 14 节也不能照单全收，压到上限并说明。"""

    sections = [{"heading": f"第{i}节内容", "goal": "", "must_cover": [], "form": "prose"} for i in range(14)]
    async def run():
        return await compose_doc_spec(REQUEST, llm_call=_llm(_spec_reply({"sections": sections})))

    spec = asyncio.run(run())
    assert len(spec.sections) <= 10
    assert any("章节" in note for note in spec.notes)


# ── 方案 → 生成 ─────────────────────────────────────────────────────────────


def test_panel_word_count_reaches_generation() -> None:
    """面板上把「约 2400 字」改成 4000，生成参数必须跟着变。"""

    async def run():
        return await planning.propose("doc", REQUEST, {}, llm_call=_llm(_spec_reply()))

    plan = asyncio.run(run())
    plan["presentation"] = str(plan["presentation"]).replace("2400 字", "4000 字")
    params = doc_params_from_plan(plan, {})
    assert params["target_words"] == 4000
    assert params["expected"]["target_words"] == 4000


def test_panel_heading_edit_is_what_gets_written() -> None:
    """面板上把一节改名，成品的小节标题就该是改后的名字。"""

    body = "## 上编：先把时间算清\n\n正文一。\n\n## 四级每日动作\n\n正文二。\n"
    async def run():
        plan = await planning.propose("doc", REQUEST, {}, llm_call=_llm(_spec_reply()))
        plan["units"][0]["label"] = "上编：先把时间算清"
        plan["units"] = plan["units"][:2]
        return await DocAgent(llm_call=_llm(_spec_reply(), body)).generate(REQUEST, [], plan=plan, planned=doc_params_from_plan(plan, {}))

    result = asyncio.run(run())
    headings = [block["text"] for block in result.blocks if block["type"].startswith("heading")]
    assert headings[:1] == ["上编：先把时间算清"]


def test_spec_survives_a_confirmed_plan_without_spec() -> None:
    """老方案没有 spec 时，用 units 拼一份，别让生成侧重拟一套。"""

    plan = {
        "kind": "doc",
        "title": "人参学习计划",
        "goal": "两周学完人参",
        "organization": "按阶段推进",
        "audience": "中医专业学生",
        "presentation": "约 1800 字",
        "units": [
            {"label": "第一周：性味归经", "intent": "", "detail": "性味归经"},
            {"label": "第二周：功效主治", "intent": "", "detail": "功效"},
            {"label": "自测", "intent": "", "detail": ""},
        ],
    }
    spec = doc_agent._spec_from_plan(plan, "人参学习计划", [], build_doc_evidence("人参学习计划"))
    assert spec is not None
    assert [item.heading for item in spec.sections] == ["第一周：性味归经", "第二周：功效主治", "自测"]
    assert spec.target_words == 1800
    assert spec.genre == "plan"


# ── 知识库 ──────────────────────────────────────────────────────────────────


def test_catalog_supplies_the_chapters_and_counts_as_evidence() -> None:
    """「中药学介绍」的章节名来自教材目录，并且方案里不该一节依据都没有。"""

    # 模型这一版照教材目录排（这才是「读懂了 + 用上了库」该有的样子）。
    reply = _spec_reply(
        {
            "title": "中药学入门",
            "genre": "explain",
            "organization_kind": "topic",
            "sections": [
                {"heading": "中药的炮制", "goal": "炮制的目的与方法", "must_cover": ["炒炭", "酒炙"], "form": "steps"},
                {"heading": "中药的性能", "goal": "四气五味归经", "must_cover": ["四气", "五味"], "form": "table"},
                {"heading": "中药的配伍", "goal": "七情", "must_cover": ["相须", "相使"], "form": "table"},
                {"heading": "各论二十一类药", "goal": "按功效分块", "must_cover": ["解表药", "补虚药"], "form": "table"},
            ],
        }
    )

    async def run():
        return await planning.propose("doc", "中药学介绍", {}, llm_call=_llm(reply, "## 中药的炮制\n\n正文。\n"))

    plan = asyncio.run(run())
    labels = [unit["label"] for unit in plan["units"]]
    assert "中药的炮制" in labels and "中药的性能" in labels
    assert plan["evidence"]["knowledge_base"], "整门课的方案必须带上目录这条依据"
    assert plan["spec"]["basis"] == "model"
    # 目录里的章名要被认成「教材依据」，而不是整份都标成模型补充。
    grounded = [unit for unit in plan["units"] if unit["source"] == "knowledge_base"]
    assert grounded, plan["units"]


def test_baseline_uses_the_real_chapter_names() -> None:
    """无模型时也不该现编章节：整门课的基线直接用教材目录的章名。"""

    spec = baseline_spec("中药学介绍", coverage=build_doc_evidence("中药学介绍").coverage)
    joined = " ".join(spec.headings)
    for chapter in ("中药的炮制", "中药的性能", "中药的配伍"):
        assert chapter in joined, joined
    assert spec.basis == "rules"


def test_evidence_says_what_the_library_has() -> None:
    """取证结果要能说清「库里有哪几门课」，而不是一句「没检索到」。"""

    evidence = build_doc_evidence("中药学介绍")
    assert evidence.grounded
    assert "中药学" in evidence.library
    assert evidence.subjects == ("herb",)
    unknown = build_doc_evidence("量子计算导论")
    assert unknown.coverage is None
    assert any("本库" in note for note in unknown.notes)


# ── 成品核对 ────────────────────────────────────────────────────────────────


def _para(text: str) -> dict:
    return {"id": "p", "type": "paragraph", "text": text, "props": {}}


def test_dense_sections_are_reported() -> None:
    """整节都是段落要指出来，而不是只报「章节齐了」。"""

    blocks = [
        {"id": "h1", "type": "heading2", "text": "第一节", "props": {}},
        _para("第一段话。" * 20),
        _para("第二段话。" * 20),
    ]
    spec = baseline_spec("随便一个主题")
    conf = doc_conformance(blocks, spec)
    assert conf["dense"] == ["第一节"]
    assert conf["devices_missing"], "一张表都没有，方案要的表格都得报缺"


def test_structured_section_is_not_dense() -> None:
    """有表格的一节不该被误判成「大段落」。"""

    blocks = [
        {"id": "h1", "type": "heading2", "text": "第一节", "props": {}},
        _para("一句话结论。"),
        {"id": "t1", "type": "table", "text": "", "props": {"columns": ["a", "b"], "rows": [["1", "2"]]}},
        _para("表后解读。"),
    ]
    conf = doc_conformance(blocks, baseline_spec("随便一个主题"))
    assert conf["dense"] == []


def test_word_count_gap_is_reported() -> None:
    """写了 300 字却标 2400 字的目标，要能看出来。"""

    blocks = [{"id": "h1", "type": "heading2", "text": "第一节", "props": {}}, _para("很短的一段。")]
    spec = baseline_spec("主题", params={"target_words": 2400})
    conf = doc_conformance(blocks, spec)
    assert conf["words"] < conf["expected_words"] / 3
    assert conf["expected_words"] == 2400
