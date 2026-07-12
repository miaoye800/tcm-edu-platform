from app.profile.radar import to_radar
from app.profile.schema import WeakPoint, create_blank_profile
from app.profile.update import profile_to_response


def test_radar_uses_numeric_scores_and_category_derived_scores_with_confidence() -> None:
    profile = create_blank_profile("student-radar")
    profile.knowledge_base.score = 72
    profile.knowledge_base.confidence = 0.82
    profile.learning_goal.label = "应试提分"
    profile.learning_goal.distribution = {"应试提分": 0.7, "临床实用": 0.3}
    profile.learning_goal.confidence = 0.76
    profile.learning_style.label = "视觉型"
    profile.learning_style.distribution = {"视觉型": 0.6, "文字型": 0.4}
    profile.learning_style.confidence = 0.66
    profile.weak_points.items = [WeakPoint(label="清热药·剂量", strength=2)]
    profile.weak_points.confidence = 0.9
    profile.pace.score = 45
    profile.pace.confidence = 0.51
    profile.motivation.score = 78
    profile.motivation.label = "内驱"
    profile.motivation.confidence = 0.72
    profile.resource_preference.label = "知识图谱"
    profile.resource_preference.distribution = {"知识图谱": 0.625, "汤头歌诀": 0.375}  # type: ignore[dict-item]
    profile.resource_preference.confidence = 0.8

    radar = to_radar(profile)
    axes = {axis["key"]: axis for axis in radar["axes"]}

    assert radar["values"] == [72, 70, 60, 50, 45, 78, 62]
    assert axes["knowledge_base"]["kind"] == "numeric"
    assert axes["knowledge_base"]["value"] == 72
    assert axes["knowledge_base"]["lit"] is True
    assert axes["knowledge_base"]["status"] == "solid"
    assert axes["learning_goal"]["metric"] == "目标清晰度"
    assert axes["learning_goal"]["kind"] == "derived"
    assert axes["learning_goal"]["value"] == 70
    assert axes["learning_goal"]["raw_label"] == "应试提分"
    assert axes["resource_preference"]["value"] == 62
    assert axes["resource_preference"]["raw"] == "知识图谱"
    assert axes["weak_points"]["raw_label"] == "清热药·剂量"
    assert all("confidence" in axis for axis in radar["axes"])
    assert radar["confidences"][0] == 0.82


def test_radar_empty_and_low_confidence_profile_returns_placeholders() -> None:
    profile = create_blank_profile("student-radar-empty")
    profile.learning_goal.label = "学术深造"
    profile.learning_goal.confidence = 0.2

    radar = to_radar(profile)
    axes = {axis["key"]: axis for axis in radar["axes"]}

    assert len(radar["axes"]) == 7
    assert len(radar["values"]) == 7
    assert axes["knowledge_base"]["value"] == 0
    assert axes["knowledge_base"]["raw_label"] == "待补充"
    assert axes["knowledge_base"]["confidence"] == 0
    assert axes["knowledge_base"]["low_confidence"] is True
    assert axes["knowledge_base"]["lit"] is False
    assert axes["knowledge_base"]["status"] == "dark"
    assert axes["learning_goal"]["raw_label"] == "学术深造"
    assert axes["learning_goal"]["value"] == 0
    assert axes["learning_goal"]["placeholder"] is True
    assert axes["learning_goal"]["lit"] is True
    assert axes["learning_goal"]["status"] == "dashed"


def test_radar_anchor_matches_dimension_text() -> None:
    profile = create_blank_profile("student-radar-anchor")
    profile.knowledge_base.score = 72
    profile.learning_goal.label = "应试提分"
    profile.learning_goal.distribution = {"应试提分": 0.7, "临床实用": 0.3}
    profile.learning_style.label = "视觉型"
    profile.learning_style.distribution = {"视觉型": 0.6, "文字型": 0.4}
    profile.weak_points.items = [WeakPoint(label="清热药·剂量", strength=2)]
    profile.pace.score = 45
    profile.motivation.score = 78
    profile.motivation.label = "内驱"
    profile.resource_preference.label = "知识图谱"
    profile.resource_preference.distribution = {"知识图谱": 0.625, "汤头歌诀": 0.375}  # type: ignore[dict-item]

    radar = to_radar(profile)
    axes = {axis["key"]: axis for axis in radar["axes"]}
    expected = {
        "knowledge_base": "功效与配伍较熟，可进入病例迁移",
        "learning_goal": "由交互选择与资源使用分布派生",
        "learning_style": "由交互选择与资源使用分布派生",
        "weak_points": "由错题、问答和开药模拟共同取证",
        "pace": "中节奏：可保持日常练习",
        "motivation": "主动性较强，适合安排挑战任务",
        "resource_preference": "由交互选择与资源使用分布派生",
    }

    for key, anchor in expected.items():
        assert axes[key]["anchor"] == anchor
        assert axes[key]["anchor"] != axes[key]["label"]


def test_profile_response_radar_exposes_axes_confidence_for_frontend() -> None:
    profile = create_blank_profile("student-radar-response")
    profile.knowledge_base.score = 64
    profile.knowledge_base.confidence = 0.7

    payload = profile_to_response(profile)

    assert payload["radar"]["axes"][0]["key"] == "knowledge_base"
    assert payload["radar"]["axes"][0]["value"] == 64
    assert payload["radar"]["axes"][0]["confidence"] == 0.7
