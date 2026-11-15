"""幻灯工坊：从「药材八股 + 一堆要点页」到「按读懂的那句话排」。

这一组断言针对**病根**，不是某个用例：

- 不管用户说什么，大纲都按「封面→导览→性味归经→功效→主治→鉴别→用量禁忌→总结」
  排——那是提示词写死的药材八股；
- 版式由关键词猜，猜不中一律要点页，九页稿子七页长得一样；
- 每页的素材需求都是 ``topic[:40]``，也就是用户的整句话：九页拿同一句去搜图；
- 方案卡的「目标」是 ``围绕「{原句}」做一次{受众}演示``，封面标题是用户原句。
"""

from __future__ import annotations

import json

from app.mindmap import catalog
from app.ppt.agent import _compose_payload
from app.ppt.design import propose_design
from app.ppt.params import PptParams
from app.ppt.spec import (
    DeckSpec,
    baseline_spec,
    compose_deck_spec,
    deck_conformance,
    deck_params_from_plan,
    deck_spec_of,
)

#: 用户原始用例（截图里那一句）：给新生做的入门试讲，还带一个情绪目标。
REQUEST = "我想给新生学弟学妹讲解一份中药学的入门试讲，让他们提前了解中药学的相关内容，并且激励他们更好的适应大学生活"


def _spec_reply(overrides: dict | None = None) -> str:
    payload = {
        "title": "从一味药开始，认识中药学",
        "subtitle": "新生入门试讲",
        "genre": "orientation",
        "audience": "中药学专业新生",
        "occasion": "新生入学第一周的入门试讲",
        "organization": "先勾住人，再讲这门学问是什么，最后落到他自己怎么过这四年",
        "organization_kind": "topic",
        "thesis": "中药学不是背不完的药名，而是一套从药性到配伍的决策系统；新生第一年要练的是「看一味药」的手感。",
        "arc": ["先勾住人", "说清是什么", "给一个抓手", "落到他自己", "鼓励行动"],
        "target_pages": 9,
        "duration_min": 20,
        "pages": [
            {"heading": "从一味药开始，认识中药学", "role": "cover", "goal": "用封面定调", "must_cover": ["这是一门从具体药物出发的学问"], "material": "水墨远山与药碾"},
            {"heading": "今天走这四步", "role": "agenda", "goal": "给新生一张路线图", "must_cover": ["路线"], "material": ""},
            {"heading": "中药学：从药性到成方的学问", "role": "section", "goal": "一句话说清边界", "must_cover": ["药性", "成方"], "material": "《本草纲目》金陵本刻本书影"},
            {"heading": "总论七章，各论二十一章", "role": "matrix", "goal": "让新生看清骨架", "must_cover": ["总论", "各论"], "material": ""},
            {"heading": "认识一味药的四把钥匙", "role": "process", "goal": "给一个可操作的顺序", "must_cover": ["性味", "归经", "功效", "主治"], "material": "黄芪、党参、陈皮饮片实拍"},
            {"heading": "寒热温凉不是玄学", "role": "compare", "goal": "用对照打消隔膜", "must_cover": ["四气", "五味"], "material": "《本草品汇精要》明抄彩绘药图"},
            {"heading": "新生最容易踩的三个坑", "role": "warning", "goal": "提前说清误区", "must_cover": ["只背药名", "不看剂量"], "material": ""},
            {"heading": "第一年怎么过", "role": "points", "goal": "落到他自己的处境", "must_cover": ["每周两小时", "跟一次门诊", "激励他们适应大学生活"], "material": ""},
            {"heading": "只记住一句", "role": "summary", "goal": "收束成一句", "must_cover": ["先学会看一味药"], "material": ""},
        ],
        "emphasis": ["给新生讲", "激励他们适应大学生活"],
        "interpretation": [
            "你要的不是一份教材串讲，而是一场让新生愿意学下去的入门试讲。",
            "所以全篇先讲「为什么要学」，再给一个能马上上手的抓手。",
        ],
    }
    payload.update(overrides or {})
    return json.dumps(payload, ensure_ascii=False)


def _llm(reply: str):
    async def fake(messages: list[dict[str, str]]) -> str:
        return reply

    fake.calls = 1  # type: ignore[attr-defined]
    return fake


# ── 基线：诚实，且不单调 ────────────────────────────────────────────────────


def test_baseline_is_honest_and_not_the_request() -> None:
    spec = baseline_spec(REQUEST)

    assert spec.basis == "rules", "没有模型时必须说是按规则拟的"
    assert spec.title != REQUEST
    assert len(spec.title) <= 30
    assert "·讲稿" not in spec.title
    assert spec.pages, "基线也要有页，不能只给一行占位"


def test_baseline_pages_are_not_one_archetype() -> None:
    """「排版单一」的底线：基线本身就不能是封面 + 一堆要点。"""

    spec = baseline_spec(REQUEST)
    roles = spec.roles
    assert len(set(roles)) >= 4, roles
    longest, run = 1, 1
    for index in range(1, len(roles)):
        run = run + 1 if roles[index] == roles[index - 1] else 1
        longest = max(longest, run)
    assert longest <= 2, f"连续 {longest} 页同版式：{roles}"


def test_timeline_request_uses_time_pages() -> None:
    """「人参的发展历史」不能长成性味归经——教材没按历史编排。"""

    spec = baseline_spec("人参的发展历史", params={"page_count": 10})

    assert "时间" in spec.organization
    assert any(word in "".join(spec.headings) for word in ("源流", "演变", "起源", "转折"))
    assert not any("性味归经" in heading for heading in spec.headings)


def test_course_request_uses_real_chapter_names() -> None:
    """整门课的讲稿：页标题就该是教材章名，而不是「核心内容」。"""

    coverage = catalog.match("中药学介绍")
    assert coverage is not None
    spec = baseline_spec("中药学介绍", coverage=coverage)

    headings = "".join(spec.headings)
    assert "炮制" in headings or "性能" in headings, spec.headings
    assert "核心内容" not in headings


# ── 读整句话 ────────────────────────────────────────────────────────────────


def test_model_spec_replaces_the_fixed_pages() -> None:
    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply())))

    assert spec.basis == "model"
    assert spec.title == "从一味药开始，认识中药学"
    assert [page.clean_heading for page in spec.pages][:3] == [
        "从一味药开始，认识中药学",
        "今天走这四步",
        "中药学：从药性到成方的学问",
    ]
    # 药材八股那五页一个都不该出现
    assert not any("性味归经" in heading for heading in spec.headings)


def test_user_constraints_land_in_must_cover() -> None:
    """「激励他们适应大学生活」这种原话里的要求必须有一页接着。"""

    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply())))
    covers = [item for page in spec.pages for item in page.must_cover]

    assert any("适应大学生活" in item for item in covers), covers
    assert any("新生" in item or "激励" in item for item in spec.emphasis + covers)


def test_audience_is_read_not_defaulted() -> None:
    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply())))
    assert spec.audience == "中药学专业新生"
    assert spec.occasion.startswith("新生")


def test_title_that_is_the_request_is_replaced() -> None:
    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply({"title": REQUEST}))))

    assert spec.title != REQUEST
    assert any("标题" in note for note in spec.notes), spec.notes


def test_field_names_never_leak_into_interpretation() -> None:
    import asyncio

    reply = _spec_reply({"interpretation": ["role=compare 的页放在第三页", "全篇先讲为什么，再给抓手"]})
    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(reply)))

    assert not any("role" in line or "must_cover" in line for line in spec.interpretation)
    assert spec.interpretation == ["全篇先讲为什么，再给抓手"]


def test_monotone_pages_are_diversified() -> None:
    """模型交回七页「要点」也要被重排——单调是缺陷，不是风格。"""

    import asyncio

    pages = [
        {"heading": f"第 {index} 讲", "role": "points", "goal": "讲一段", "must_cover": []}
        for index in range(1, 8)
    ]
    reply = _spec_reply({"pages": pages, "target_pages": 7, "genre": "lecture"})
    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(reply)))

    assert spec.variety >= 3, spec.roles
    assert any("去单调" in note for note in spec.notes), spec.notes
    roles = spec.roles
    longest, run = 1, 1
    for index in range(1, len(roles)):
        run = run + 1 if roles[index] == roles[index - 1] else 1
        longest = max(longest, run)
    assert longest <= 2, roles


def test_pages_are_capped_and_counted_honestly() -> None:
    import asyncio

    pages = [{"heading": f"第 {index} 页", "role": "points", "goal": "g", "must_cover": []} for index in range(1, 26)]
    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply({"pages": pages, "target_pages": 25}))))

    assert len(spec.pages) == 20
    assert spec.target_pages == 20


# ── 素材：一页一主体 ────────────────────────────────────────────────────────


def test_material_subject_is_per_page_not_the_whole_request() -> None:
    """九页拿同一句话去搜图，就是「素材库里有图却用不上」的现场。"""

    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply())))
    subjects = [page.material.subject for page in spec.pages if page.material and not page.material.empty]

    assert len(subjects) >= 4
    assert len(set(subjects)) == len(subjects), subjects
    assert not any(REQUEST in subject for subject in subjects)


def test_design_slots_follow_the_spec_material() -> None:
    """设计单的每个图位都该有自己的一句话主体，而不是整句话。"""

    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply())))
    plan = {
        "title": spec.title,
        "spec": spec.model_dump(mode="json"),
        "units": [{"id": f"u{index}", "label": page.clean_heading, "intent": page.goal} for index, page in enumerate(spec.pages)],
    }
    design = propose_design(spec.title, PptParams(), plan)

    subjects = [slot.subject for page in design.pages for slot in page.slots if slot.role in ("主图", "配图", "证据图")]
    assert subjects, "至少封面要有主图"
    assert all(subject and subject != "通用" for subject in subjects), subjects
    assert not any(REQUEST in subject for subject in subjects)
    # 版式也来自施工图：矩阵页就是矩阵，流程页就是流程。
    assert [page.archetype for page in design.pages][:5] == ["cover", "agenda", "section", "matrix", "process"]


# ── 卡片 ⇒ 生成 ─────────────────────────────────────────────────────────────


def _plan_with_spec(spec: DeckSpec) -> dict:
    return {
        "title": spec.title,
        "goal": spec.thesis,
        "audience": spec.audience,
        "organization": spec.organization,
        "presentation": f"共 {len(spec.pages)} 页（含封面），paper 主题，{spec.variety} 种版式，逐页可改",
        "spec": spec.model_dump(mode="json"),
        "units": [
            {"id": f"u{index}", "label": page.clean_heading, "intent": page.goal, "detail": "；".join(page.must_cover)}
            for index, page in enumerate(spec.pages)
        ],
    }


def test_panel_renames_and_page_count_reach_generation() -> None:
    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply())))
    plan = _plan_with_spec(spec)
    # 用户在卡片上改名、删页、把「共 9 页」改成 12
    plan["units"][1]["label"] = "今天只走三步"
    plan["units"] = plan["units"][:8]
    plan["presentation"] = "共 12 页（含封面），paper 主题，逐页可改"

    params = deck_params_from_plan(plan)

    assert params["page_count"] == 12
    assert params["expected"]["pages"][1] == "今天只走三步"
    assert len(params["expected"]["pages"]) == 8
    assert params["deck_spec"]["pages"][1]["heading"] == "今天只走三步"
    # 改页名不该把这一页的版式改掉
    assert params["deck_spec"]["pages"][1]["role"] == "agenda"


def test_spec_is_recognised_and_not_confused_with_doc_or_mindmap() -> None:
    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply())))
    assert deck_spec_of({"spec": spec.model_dump(mode="json")}) is not None
    assert deck_spec_of({"spec": {"sections": [], "target_words": 1200}}) is None
    assert deck_spec_of({"spec": {"branches": [], "depth": 3}}) is None
    assert deck_spec_of({"units": []}) is None


# ── 成品核对 ────────────────────────────────────────────────────────────────


def test_conformance_reports_monotone_deck() -> None:
    """七页全是要点：必须报「最长连续 7 页」而不是含糊地说「排版有点单一」。"""

    spec = DeckSpec.model_validate(
        {
            "title": "测试",
            "pages": [
                {"heading": "封面", "role": "cover"},
                {"heading": "对照", "role": "compare"},
                {"heading": "流程", "role": "process"},
            ],
        }
    )
    content = {"slides": [{"title": f"第 {index} 页", "purpose": "points", "bullets": ["一"]} for index in range(1, 8)]}
    stats = deck_conformance(content, spec)

    assert stats["longest_run"] == 7
    assert stats["role_variety"] == 1
    assert stats["monotone"] is True
    assert set(stats["missing_roles"]) == {"cover", "compare", "process"}


def test_conformance_counts_pages_and_figures() -> None:
    spec = DeckSpec.model_validate(
        {
            "title": "测试",
            "pages": [
                {"heading": "封面", "role": "cover", "material": {"subject": "水墨远山"}},
                {"heading": "对照", "role": "compare", "material": {"subject": "两张药图"}},
                {"heading": "流程", "role": "process"},
            ],
        }
    )
    content = {
        "slides": [
            {"title": "封面", "purpose": "cover"},
            {
                "title": "对照",
                "purpose": "compare",
                "blocks": [{"type": "matrix"}],
                "asset_refs": [
                    # 纸纹也是 asset_refs，但它不是「这一页的图」。
                    {"slot": "ground", "asset_id": "paper-plain", "result": "parametric"},
                    {"slot": "figure", "asset_id": "plate-1", "result": "hit", "file": "assets/pictorial/plate-1.webp"},
                ],
            },
            {"title": "流程", "purpose": "process", "blocks": [{"type": "steps", "items": ["一"]}]},
        ]
    }
    stats = deck_conformance(content, spec)

    assert stats["longest_run"] == 1
    # 只数真的配了图的页：底纹/角饰不算，空引用也不算。
    assert stats["with_figure"] == 1
    assert stats["figure_pages"] == 2
    assert stats["monotone"] is False


# ── 敷演提示词：每页都带版式与写作契约 ──────────────────────────────────────


def test_compose_payload_carries_role_and_contract() -> None:
    """以前 design 是单独一列、与 outline 平行，模型根本对不上号。"""

    import asyncio

    spec = asyncio.run(compose_deck_spec(REQUEST, llm_call=_llm(_spec_reply())))
    design = {
        "pages": [{"archetype": page.role, "density": page.density} for page in spec.pages],
    }
    outline = [{"title": page.clean_heading, "points": []} for page in spec.pages]

    payload = _compose_payload("中药学入门", [], [], PptParams(), outline, design=design, spec=spec)
    first = payload["outline"][0]
    compare = payload["outline"][5]

    assert first["role"] == "cover" and "subtitle" in first["write"]
    assert compare["role"] == "compare" and "columns" in compare["write"]
    # must_cover 也要给到模型，否则用户写进方案的硬要求到不了正文
    assert payload["outline"][7]["must_cover"]
