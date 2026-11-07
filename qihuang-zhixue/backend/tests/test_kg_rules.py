"""P4.5 关系发现闸门 R001–R011 的回归测试。

这些闸门的作用是**挡住**东西，所以每条测试都断言「该挡的挡住了」，
而不是「跑通了」。一条闸门失效不会报错，只会让噪声悄悄进图谱——
这正是旧图 915 条相似边（占 58%）的成因。
"""

from __future__ import annotations

from app.kg.rules import MAX_PARTNERS, QIQING, RelationCandidate, evaluate


def _cand(**overrides: object) -> RelationCandidate:
    """一条**本该通过**的候选。每条测试只破坏一个字段，看闸门认不认。"""

    base = RelationCandidate(
        source_name="麻黄",
        target_name="桂枝",
        source_quote="治外感风寒，常与桂枝同用，以开宣肺气。",
        quote="治外感风寒，常与桂枝同用，以开宣肺气。",
        field="应用",
        page=150,
        relation_type="配伍",
        source_id="herb_mahuang",
        target_id="herb_guizhi",
        target_has_monograph=True,
        already_exists=False,
        t1_conflict=False,
        partner_count=3,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_baseline_passes() -> None:
    """不破坏任何字段时必须是 pass，否则后面的测试都没意义。"""

    verdict = evaluate(_cand())

    assert verdict.status == "pass", verdict.describe()


def test_r001_rejects_unresolved_name() -> None:
    """「通鼻窍药」这种描述性短语解析不到实体，不能当药名。"""

    verdict = evaluate(_cand(target_name="通鼻窍药", target_id=None))

    assert verdict.status == "fail"
    assert "R001" in verdict.failed


def test_r002_rejects_self_pair() -> None:
    verdict = evaluate(_cand(target_name="麻黄", target_id="herb_mahuang"))

    assert verdict.status == "fail"
    assert "R002" in verdict.failed


def test_r003_skips_target_without_monograph() -> None:
    """没有结构化条目的药：不建边，但也不算「错」——skip 不是 fail。"""

    verdict = evaluate(_cand(target_has_monograph=False))

    assert verdict.status == "skip"
    assert "R003" not in verdict.failed


def test_r004_rejects_fabricated_quote() -> None:
    """证据原句不在该药原文里 → 一定是拼出来的，拦住。"""

    verdict = evaluate(_cand(quote="常与桂枝、阿胶同用。"))

    assert verdict.status == "fail"
    assert "R004" in verdict.failed


def test_r005_rejects_invented_qiqing() -> None:
    """原文没写「相须」，就不能标相须——七情类别是教材的判断，不是我们的。"""

    verdict = evaluate(_cand(relation_type="相须"))

    assert verdict.status == "fail"
    assert "R005" in verdict.failed


def test_r005_accepts_qiqing_present_in_text() -> None:
    verdict = evaluate(
        _cand(
            relation_type="相须",
            quote="常与桂枝相须为用。",
            source_quote="常与桂枝相须为用。",
        )
    )

    assert verdict.status == "pass", verdict.describe()


def test_r006_skips_existing_edge() -> None:
    verdict = evaluate(_cand(already_exists=True))

    assert verdict.status == "skip"
    assert "R006" in [hit.code for hit in verdict.hits if hit.status != "pass"]


def test_r007_rejects_formula_name() -> None:
    """「如麻黄汤」里的麻黄汤是方剂，不是药。"""

    verdict = evaluate(_cand(target_name="麻黄汤"))

    assert verdict.status == "fail"
    assert "R007" in verdict.failed


def test_r009_rejects_missing_page() -> None:
    verdict = evaluate(_cand(page=None))

    assert verdict.status == "fail"
    assert "R009" in verdict.failed


def test_r010_rejects_t1_conflict() -> None:
    """川乌配半夏在十八反里，T1 已经明令禁止，不能再建一条「配伍」边。"""

    verdict = evaluate(
        _cand(
            source_name="川乌",
            target_name="半夏",
            source_id="herb_chuanwu",
            target_id="herb_banxia",
            source_quote="常与半夏同用。",
            quote="常与半夏同用。",
            t1_conflict=True,
        )
    )

    assert verdict.status == "fail"
    assert "R010" in verdict.failed


def test_r011_catches_runaway_recall() -> None:
    """一味药召回对象数超过熔断阈值，说明正则把方剂组成吃进来了。"""

    verdict = evaluate(_cand(partner_count=MAX_PARTNERS + 1))

    assert verdict.status == "fail"
    assert "R011" in verdict.failed


def test_status_precedence_fail_beats_skip() -> None:
    """同时命中 fail 与 skip 时报 fail——不能拿 skip 把硬错误盖掉。"""

    verdict = evaluate(_cand(target_name="通鼻窍药", target_id=None, target_has_monograph=False))

    assert verdict.status == "fail"


def test_qiqing_words_are_the_seven_relations_subset() -> None:
    """七情里能作为**关系类型**的只有这五个；「相反」由 T1 负责，不在这条管道里。"""

    assert QIQING == ("相须", "相使", "相畏", "相杀", "相恶")
