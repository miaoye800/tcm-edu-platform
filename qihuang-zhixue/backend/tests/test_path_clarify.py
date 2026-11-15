"""排之前先问一轮：缺什么问什么，问几个够，答案怎么回到诉求里。

这一组盯三件事：
1. **不缺就不问**——用户已经说了期限、范围、起点，就不该再弹一屏；
2. **缺了必问**——没说的不能被默认值悄悄填掉，默认值要写在 ``assumptions`` 里；
3. **答了要生效**——答案必须拼回诉求文本，因为提案 prompt 读的就是那一段。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.path.clarify import clarify, compose_request, subject_hints, tailor

#: 用户原话。范围说清了（中药学），期限与起点都没说。
SAMPLE = (
    "老师教过我什么药和什么药可以组成什么剂，我想多学习一下别的内容，"
    "所以帮我安排别的内容，先把中药学完，然后才是其他内容"
)


def _context(*, mastery: dict[str, float] | None = None, completed: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        mastery={key: SimpleNamespace(p_mastery=value) for key, value in (mastery or {}).items()},
        route_state={key: "completed" for key in (completed or [])},
    )


# ── 缺什么问什么 ──────────────────────────────────────────────────────────


def test_the_sample_request_asks_only_what_is_missing() -> None:
    """范围已经说了（中药学），就不要再问"覆盖哪些内容"。"""

    result = clarify(SAMPLE)

    assert result.needs is True
    assert set(result.missing) == {"deadline", "baseline"}
    assert [question.id for question in result.questions] == ["deadline", "baseline"]
    assert any("范围" in item for item in result.understood)
    # 没答的那两项，默认值必须写在纸上
    assert len(result.assumptions) == 2


def test_a_fully_specified_request_asks_nothing() -> None:
    result = clarify("明年考研，六门课都要，基本零基础")

    assert result.needs is False
    assert result.questions == ()
    assert result.assumptions == ()


def test_evidence_from_the_profile_replaces_the_baseline_question() -> None:
    """图上已经有掌握度证据，就别再问"你现在什么水平"。"""

    result = clarify("帮我排一条中药学的路线", context=_context(mastery={"d01.s1.p1": 0.8}))

    assert "baseline" not in result.missing
    assert "起点有依据" in result.understood


def test_completed_route_counts_as_evidence_too() -> None:
    result = clarify("帮我排一条中药学的路线", context=_context(completed=["general"]))

    assert "baseline" not in result.missing


def test_answers_suppress_the_same_question_next_time() -> None:
    """回答过的项不再问：用户回头改目标，不该把同一组题再弹一遍。"""

    result = clarify("帮我排一条路线", answers={"deadline": "exam_far", "scope": "herb"})

    assert [question.id for question in result.questions] == ["baseline"]
    assert "deadline" not in result.missing


def test_empty_answers_do_not_count_as_answered() -> None:
    """界面上点开又没选，传上来的是空串——那不算答过。"""

    result = clarify("帮我排一条路线", answers={"deadline": "  "})

    assert "deadline" in result.missing


def test_at_most_three_questions() -> None:
    assert len(clarify("").questions) <= 3


def test_bare_day_or_year_is_not_a_deadline() -> None:
    """「三天」「两年」不是期限词——它们多半是"每天学多久/学几年"的意思。"""

    assert "deadline" in clarify("我每天能学两小时，想学三年").missing


def test_the_scope_question_offers_all_six_subjects() -> None:
    scope = next(question for question in clarify("帮我排计划").questions if question.id == "scope")

    assert scope.multi is True
    assert {option.value for option in scope.options} == {"herb", "formula", "basic", "diagnosis", "acup", "internal"}


# ── 答案回到诉求里 ────────────────────────────────────────────────────────


def test_answers_are_folded_into_the_request_as_phrases() -> None:
    result = clarify(SAMPLE)
    text = compose_request(SAMPLE, {"deadline": "exam_far", "baseline": "some"}, questions=result.questions)

    assert text.startswith(SAMPLE)
    assert "一年内考研，系统学完" in text
    assert "上过课但没系统学" in text
    assert text.count("补充：") == 1


def test_free_text_answers_are_labelled() -> None:
    text = compose_request(SAMPLE, {"deadline": "下个月 15 号"}, questions=clarify(SAMPLE).questions)

    assert "期限：下个月 15 号" in text


def test_no_answers_leaves_the_request_untouched() -> None:
    assert compose_request(SAMPLE, {}) == SAMPLE
    assert compose_request(SAMPLE, {"deadline": "  "}, questions=clarify(SAMPLE).questions) == SAMPLE


def test_duplicate_phrases_appear_once() -> None:
    text = compose_request("排计划", {"scope": "herb", "baseline": "herb"}, questions=clarify("排计划").questions)

    assert text.count("重点学中药学") == 1


def test_subject_hints_read_the_raw_request() -> None:
    assert subject_hints(SAMPLE) == ["herb"]
    assert subject_hints("帮我把中基和中诊过一遍") == ["basic", "diagnosis"]
    assert subject_hints("随便排") == []


def test_multi_select_answers_are_each_translated() -> None:
    """范围是多选：前端以逗号连起来传，每个值都要翻成选项自带的说法。

    否则模型看到的是 `herb,formula` 这串英文 id，关键词表也认不出"中药学"。
    """

    text = compose_request("排计划", {"scope": "herb,formula"}, questions=clarify("排计划").questions)

    assert "重点学中药学" in text and "重点学方剂学" in text
    assert "herb" not in text


# ── 按他的话问，而不是弹一张与他无关的表单 ──────────────────────────────

#: 用户真实会写的一句话：科目、时间、目的都在里面。
EXAM = "我想通过这学期中药学期末考试"


def test_an_exam_sentence_asks_only_the_one_thing_it_does_not_say() -> None:
    """"这学期中药学期末考试" 已经答了期限和范围，只剩起点没答。

    以前的实现三条题照弹：一个已经说了"期末"的人还会被问"你打算什么时候用上
    这些内容？"——那不是追问，是没有读他的话。
    """

    result = clarify(EXAM)

    assert result.missing == ("baseline",)
    assert [question.id for question in result.questions] == ["baseline"]
    assert result.subjects == ("herb",)


def test_questions_speak_about_the_subject_he_named() -> None:
    """缺的那题要问"中药学你什么水平"，不是"这几门课你什么水平"。"""

    question = clarify(EXAM).questions[0]

    assert "中药学" in question.question
    assert "这几门课" not in question.question
    # 自由输入框的示例必须和这道题相关：问水平却举例"明年 3 月"会把人带偏
    assert "中药" in question.placeholder or "药性" in question.placeholder
    assert "明年 3 月" not in question.placeholder


def test_each_question_carries_its_own_placeholder() -> None:
    """三题共用一个 placeholder 是写死的 bug（复制粘贴漏改）。

    一个"帮我安排一下"的用户会看到三道题，如果三处示例都是"明年 3 月"，
    问到"什么水平"那一栏就是在教他填一个日期。
    """

    questions = clarify("帮我安排一下").questions
    assert len(questions) == 3
    placeholders = [question.placeholder for question in questions]
    assert all(placeholder.strip() for placeholder in placeholders)
    assert len(set(placeholders)) == len(placeholders) == 3


def test_an_empty_request_does_not_dump_three_questions() -> None:
    """一句话都没写时不弹题。

    以前空输入也会判成"三条都缺"，于是用户打开向导、还没打字就点了按钮，
    屏幕上出现三道和他毫无关系的问题——这正是"根本没分析用户需求"的来源。
    """

    result = clarify("")

    assert result.needs is False
    assert result.questions == ()
    assert result.assumptions  # 但要告诉他该先写一句什么


def test_tailor_keeps_values_when_the_model_answers_garbage() -> None:
    """模型改写题面失败/乱写时，题面与选项值必须原样不动。

    追问的措辞是锦上添花；值域一乱，``compose_request`` 就拼不回"重点学中药学"，
    用户答了等于没答。
    """

    import asyncio

    base = clarify(EXAM)

    async def broken(_messages: list[dict[str, str]]) -> str:
        return "这不是 JSON"

    fallback = asyncio.run(tailor(base, EXAM, llm_call=broken))
    assert [question.to_dict() for question in fallback.questions] == [
        question.to_dict() for question in base.questions
    ]

    async def rewrite(_messages: list[dict[str, str]]) -> str:
        return (
            '{"questions":[{"id":"baseline","question":"中药学你背到哪了？",'
            '"hint":"背过的不再从零排","placeholder":"例如：药性背过一轮",'
            '"options":[{"value":"zero","label":"没背过"},{"value":"invented","label":"编的"}]}]}'
        )

    tuned = asyncio.run(tailor(base, EXAM, llm_call=rewrite))
    question = tuned.questions[0]
    assert question.question == "中药学你背到哪了？"
    assert question.placeholder == "例如：药性背过一轮"
    # 模型只能改 label，不能新增选项值——否则后端拼不出 phrase
    assert [option.value for option in question.options] == ["zero", "some", "exam_ready"]
    assert [option.phrase for option in question.options] == [
        option.phrase for option in base.questions[0].options
    ]


def test_mastered_subject_is_not_a_scope() -> None:
    """「已经掌握中药学」不是"要学中药学"。

    范围现在是**硬边界**：只做子串匹配的话，这句话会把整份计划裁成一门课，
    而用户要的是别的课——正好是"只考中药学却拿到六门先修链"的反向故障。
    """

    assert subject_hints("我已经掌握中药学，想补方剂学") == ["formula"]
    # 只说"已经会了"、没说要学什么：范围退回全图，交给提案按证据取舍
    assert subject_hints("我已经掌握中药学") == []
    # 目标语气（"学完""掌握"）不是过去式，不能误伤
    assert subject_hints("先把中药学完，再学方剂") == ["herb", "formula"]


def test_mastered_subject_does_not_shrink_the_scope_question() -> None:
    result = clarify("我已经掌握中药学，想补方剂学，三个月后考试")

    assert result.subjects == ("formula",)
    assert "scope" not in result.missing
